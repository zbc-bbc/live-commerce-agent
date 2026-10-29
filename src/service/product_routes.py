"""商品上架管理模块：主播端 HTTP 接口 + 观众端商品查询。

权限模型（与全站双角色 JWT 保持一致）：
  * 写操作（新增 / 修改 / 上下架）只接受**主播 JWT**（scope=host）。
    观众 JWT 会被拒绝（403），无效 / 过期 token 返回 401。
  * 查询接口：主播看全量商品；观众（scope=viewer）只看到 ``isOnSale=True``
    的商品，未上架商品对观众既不在列表里、也不能通过详情接口拿到。

上下架变动由 ``DataManager.set_on_sale`` 广播 ``product_updated`` 事件，
直播间所有在线观众会实时收到并重新拉取商品列表（见 live_commerce_ws）。
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from data.live_commerce.data_manager import get_data_manager
from schema.live_commerce_schema import ProductItem
from schema.product_schema import (
    ProductCreateRequest,
    ProductOnSaleRequest,
    ProductUpdateRequest,
)
from service import auth as host_auth
from service import viewer_auth
from service.auth_middleware import verify_any_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/products", tags=["Products"])
_bearer = HTTPBearer(auto_error=False)


def _extract_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str:
    """按 Authorization → 主播 cookie → 观众 cookie 的顺序取 token。"""
    if credentials is not None and credentials.credentials:
        return credentials.credentials.strip()
    for name in (host_auth.HOST_COOKIE, "lc_viewer_token"):
        token = (request.cookies.get(name) or "").strip()
        if token:
            return token
    return ""


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_host(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """Dependency：只放行有效的主播 JWT。

    不能直接复用 ``get_current_user``：它会把观众 token 报成 401（「无效
    token」），语义上应是「身份有效但角色不够」的 403。这里先按 host scope
    校验，再区分观众 token 与彻底无效的 token。
    """
    token = _extract_token(request, credentials)
    if not token:
        raise _unauthorized("Not authenticated")

    claims = host_auth.verify_host_claims(token)
    if claims:
        user = await host_auth.get_user_by_id(claims["user_id"])
        if user and user.get("is_active"):
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="host account disabled")

    if viewer_auth.verify_viewer_payload(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="host role required",
        )
    raise _unauthorized("Invalid or expired token")


async def get_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """Dependency：读取主播或观众身份（只读接口用）。"""
    token = _extract_token(request, credentials)
    identity = verify_any_token(token)
    if identity is None:
        raise _unauthorized("Not authenticated")
    return identity


def _product_payload(product: ProductItem) -> dict:
    return product.model_dump()


@router.get("")
async def list_products(identity: Annotated[dict, Depends(get_identity)]) -> dict:
    """商品列表：主播返回全量，观众只返回已上架商品。"""
    dm = get_data_manager()
    is_host = identity["role"] == "host"
    products = dm.get_all_products()
    if not is_host:
        products = [p for p in products if p.isOnSale]
    return {
        "success": True,
        "role": identity["role"],
        "total": len(products),
        "products": [_product_payload(p) for p in products],
    }


@router.get("/{product_id}")
async def get_product_detail(
    product_id: str,
    identity: Annotated[dict, Depends(get_identity)],
) -> dict:
    """商品详情：观众请求未上架商品与不存在商品一样返回 404。"""
    dm = get_data_manager()
    product = dm.get_product(product_id)
    is_host = identity["role"] == "host"
    if product is None or (not is_host and not product.isOnSale):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return {"success": True, "product": _product_payload(product)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreateRequest,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """新增商品（主播）。"""
    dm = get_data_manager()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="商品名称不能为空")
    product_id = (payload.productId or "").strip()
    if product_id and dm.get_product(product_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"商品ID已存在: {product_id}",
        )
    if not product_id:
        product_id = dm.next_product_id()

    product = ProductItem(
        productId=product_id,
        name=name,
        originalPrice=payload.originalPrice if payload.originalPrice is not None else payload.price,
        price=payload.price,
        stock=payload.stock,
        category=payload.category,
        description=payload.description,
        specs=payload.specs,
        imageUrl=payload.imageUrl,
        tags=payload.tags,
        sellingPoints=payload.sellingPoints,
        isOnSale=payload.isOnSale,
        addedTime=datetime.now().isoformat(),
        isInLibrary=payload.isInLibrary,
    )
    dm.add_product(product)
    logger.info("主播 %s 新增商品 %s（上架=%s）", host_user.get("username"), product_id, product.isOnSale)
    return {"success": True, "product": _product_payload(product)}


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    payload: ProductUpdateRequest,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """修改商品（主播）。只更新请求里出现的字段；``isOnSale`` 走上下架逻辑。"""
    dm = get_data_manager()
    if dm.get_product(product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    # update_product 直接 setattr，绕过 Pydantic 校验；显式传 null 会把
    # 非空字段（name/price/stock…）改成 None。这里一律过滤 None，清空可选
    # 文本字段请传空字符串。
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    on_sale = changes.pop("isOnSale", None)

    product: ProductItem | None = dm.get_product(product_id)
    if changes:
        product = dm.update_product(product_id, changes)
    if on_sale is not None:
        # 复用 set_on_sale：下架当前讲解商品时会清空 current_product_id 并广播
        product = dm.set_on_sale(product_id, on_sale)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    logger.info("主播 %s 修改商品 %s", host_user.get("username"), product_id)
    return {"success": True, "product": _product_payload(product)}


@router.put("/{product_id}/on-sale")
async def set_product_on_sale(
    product_id: str,
    payload: ProductOnSaleRequest,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """上架 / 下架商品（主播）。

    变动会经 DataManager 实时广播给直播间所有在线观众。
    """
    dm = get_data_manager()
    product = dm.set_on_sale(product_id, payload.onSale)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    logger.info(
        "主播 %s %s商品 %s",
        host_user.get("username"),
        "上架" if payload.onSale else "下架",
        product_id,
    )
    return {"success": True, "product": _product_payload(product)}
