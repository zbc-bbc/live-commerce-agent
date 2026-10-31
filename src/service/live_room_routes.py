"""直播间商品挂载 / 商品推送 / 基础下单 / 本场数据统计（HTTP 接口）。

与 WebSocket 消息是同一条业务链路的两种入口：
  * 运营台页面主要走 WS（``mount_live_product`` / ``push_product_card`` …），
    因为挂载、推送的结果需要实时广播给所有在线观众；
  * 这里提供等价的 HTTP 接口，便于第三方对接、自动化脚本与接口测试。
两者调用同一个 ``LiveCommerceDataManager``，广播也由数据层统一发出，
因此不会出现「WS 挂上去了、HTTP 查不到」的两套状态。

权限模型沿用全站双角色 JWT：
  * 挂载 / 撤下 / 推送 / 查看统计 → 仅主播（scope=host），观众 token 返回 403；
  * 商品列表 / 点击埋点 / 下单     → 仅观众（scope=viewer）。
观众下单只生成「待付款」订单记录，不扣钱包、不扣库存；支付仍由既有的
``place_order → pay_order`` 链路负责（本批次按需求不接入真实支付）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from data.live_commerce.data_manager import get_data_manager
from schema.live_commerce_schema import ProductItem
from service import viewer_auth
from service.product_routes import get_identity, require_host

router = APIRouter(prefix="/api/live-room", tags=["Live Room"])


def _product_payload(dm, product: ProductItem) -> dict:
    """商品 + 观众端展示销量，与 WS 的 _products_payload 保持同口径。"""
    data = product.model_dump()
    sales = dm.get_product_sales_map().get(product.productId) or {}
    data["salesQty"] = sales.get("qty", 0) + (product.soldBase or 0)
    data["salesAmount"] = sales.get("amount", 0.0)
    return data


def _require_viewer_identity(identity: dict) -> str:
    """从已校验的身份里取出观众 userId；主播调用观众接口返回 403。"""
    if identity.get("role") != "viewer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="viewer role required",
        )
    user_id = (identity.get("claims") or {}).get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return str(user_id)


class CreateLiveOrderRequest(BaseModel):
    """观众在直播间下单（基础版：只生成待付款订单记录）。"""

    productId: str = Field(..., min_length=1, max_length=64, description="商品ID")
    quantity: int = Field(default=1, ge=1, le=99, description="购买数量")


@router.get("/products")
async def list_live_products(
    identity: Annotated[dict, Depends(get_identity)],
) -> dict:
    """当前直播间已挂载的商品（主播与观众拿到的都是已上架集合）。"""
    dm = get_data_manager()
    products = dm.get_mounted_products()
    return {
        "success": True,
        "role": identity["role"],
        "total": len(products),
        "products": [_product_payload(dm, p) for p in products],
    }


@router.post("/products/{product_id}/mount")
async def mount_live_product(
    product_id: str,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """主播把商品挂载到直播间（挂载后观众端立即可见）。"""
    dm = get_data_manager()
    product, reason = dm.mount_live_product(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    return {
        "success": True,
        "message": "商品已挂载到直播间",
        "product": _product_payload(dm, product),
    }


@router.delete("/products/{product_id}/mount")
async def unmount_live_product(
    product_id: str,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """主播把商品从直播间撤下（观众端立即可见地消失，不能再下单）。"""
    dm = get_data_manager()
    product, reason = dm.unmount_live_product(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    return {
        "success": True,
        "message": "商品已从直播间撤下",
        "productId": product_id,
    }


@router.post("/products/{product_id}/push")
async def push_product_card(
    product_id: str,
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """主播向所有在线观众推送商品卡片弹窗（只允许已挂载商品）。"""
    dm = get_data_manager()
    card, reason = dm.push_product_card(product_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    return {"success": True, "message": "商品卡片已推送", "card": card}


@router.post("/products/{product_id}/click")
async def click_live_product(
    product_id: str,
    identity: Annotated[dict, Depends(get_identity)],
) -> dict:
    """观众端商品点击埋点（仅直播中计入本场统计）。"""
    user_id = _require_viewer_identity(identity)
    dm = get_data_manager()
    result = dm.record_product_click(product_id, user_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message") or "点击记录失败",
        )
    return result


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_live_order(
    payload: CreateLiveOrderRequest,
    identity: Annotated[dict, Depends(get_identity)],
) -> dict:
    """观众在直播间下单：生成「待付款」订单记录（不扣钱包/库存，不做支付）。"""
    user_id = _require_viewer_identity(identity)
    row = await viewer_auth.get_viewer_row(user_id)
    if not row or not row.get("isActive", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="观众账号不存在或已停用")

    dm = get_data_manager()
    dm.get_or_create_viewer(
        user_id,
        row.get("userName") or "观众",
        row.get("avatar") or "",
    )
    order, reason = dm.create_viewer_order(
        payload.productId,
        user_id,
        row.get("userName") or "观众",
        payload.quantity,
    )
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=reason or "下单失败",
        )
    return {"success": True, "message": "下单成功，订单已记录", "order": order.model_dump()}


@router.get("/orders")
async def list_my_live_orders(
    identity: Annotated[dict, Depends(get_identity)],
) -> dict:
    """观众查看自己的订单记录（本场 + 历史，按时间倒序）。"""
    user_id = _require_viewer_identity(identity)
    dm = get_data_manager()
    orders = dm.get_user_orders(user_id)
    return {"success": True, "total": len(orders), "orders": [o.model_dump() for o in orders]}


@router.get("/stats")
async def get_live_room_stats(
    host_user: Annotated[dict, Depends(require_host)],
) -> dict:
    """主播查看本场直播数据：商品点击、下单数量、成交金额与单品转化。"""
    dm = get_data_manager()
    return {"success": True, "stats": dm.get_live_room_stats()}
