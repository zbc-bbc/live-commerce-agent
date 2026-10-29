"""商品图 AI 生成接口：POST /api/product/{product_id}/generate-image。

生成结果落盘到 uploads 目录（与头像上传同目录，由 service.py 以 StaticFiles
挂载到 /uploads 对外读取），并默认**直接写回商品**的 images[0] / imageUrl。

写回策略：新图放在最前当主图，旧图保留在后面（最多再留 4 张、且不与新图重复）。
这样反复生成不会把商品原有的图直接抹掉，要撤回也还有旧图可用。
"""
import logging
import os
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from data.live_commerce.data_manager import get_data_manager
from service.auth_routes import get_current_user
from service.image_gen import DEFAULT_SIZE, ImageGenError, generate_product_image
from service.upload_routes import UPLOADS_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

# 写回时保留的旧图数量上限（新图本身不计入）
_KEEP_OLD_IMAGES = 4


@router.post("/api/product/{product_id}/generate-image")
async def generate_product_image_route(
    product_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    save: bool = Query(True, description="是否写回商品（false 则只生成并返回 URL）"),
    size: int = Query(DEFAULT_SIZE, ge=256, le=1024, description="出图边长（正方形）"),
):
    """给指定商品生成一张 AI 商品图。

    生成是同步等待的（免费端点排队可能几十秒），前端调用时要有 loading 状态。
    """
    dm = get_data_manager()
    product = dm.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"商品不存在: {product_id}")

    try:
        data, ext, prompt = await generate_product_image(product, width=size, height=size)
    except ImageGenError as e:
        # 502：上游图像服务的问题，不是本服务的 bug。
        # detail 只放原因本身，「生成失败」这层上下文由调用方（前端 toast）补，
        # 否则前端那句「AI 生成失败：」会和这里的「生成失败:」撞成两个前缀。
        logger.warning(f"[product_image] 生成失败 product={product_id}: {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[product_image] 生成异常 product={product_id}")
        raise HTTPException(status_code=500, detail=f"生成异常: {e}") from e

    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        filename = f"ai_{product_id}_{uuid4().hex[:8]}{ext}"
        with open(os.path.join(UPLOADS_DIR, filename), "wb") as f:
            f.write(data)
    except OSError as e:
        logger.exception(f"[product_image] 写盘失败 product={product_id}")
        raise HTTPException(status_code=500, detail=f"图片保存失败: {e}") from e

    url = f"/uploads/{filename}"

    if save:
        old = [u for u in (getattr(product, "images", None) or []) if u and u != url]
        dm.update_product(product_id, {
            "images": [url] + old[:_KEEP_OLD_IMAGES],
            "imageUrl": url,
        })
        logger.info(f"[product_image] 已写回商品 {product_id} → {url}")

    return {
        "success": True,
        "productId": product_id,
        "url": url,
        "saved": save,
        "bytes": len(data),
        "prompt": prompt,
    }
