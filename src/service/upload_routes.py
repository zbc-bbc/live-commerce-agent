"""头像/图片上传接口：POST /api/avatar/upload → {"url": "/uploads/<name>"}。

图片落盘到 src/data/live_commerce/data/uploads/（已在 run_service reload 排除范围内，写图不触发热重载），
由 service.py 中以 StaticFiles 挂载 /uploads 对外提供读取。
"""
import os
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile

router = APIRouter()

# 上传目录（与 data_manager 的 DATA_DIR 同根：src/data/live_commerce/data/uploads）
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "live_commerce", "data", "uploads")

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
# 读取上限：3MB
_MAX_BYTES = 3 * 1024 * 1024


@router.post("/api/avatar/upload")
async def upload_avatar(file: UploadFile):
    """接收一张头像图片，返回可访问的 /uploads/<name> 相对 URL"""
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持上传图片")

    # 扩展名白名单（防止任意文件）
    orig = (file.filename or "").lower()
    ext = os.path.splitext(orig)[1]
    if ext not in _ALLOWED_EXTS:
        # 依据 content_type 兜底一个扩展名
        fallback = {
            "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg",
        }.get(content_type)
        ext = fallback or ".png"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    name = f"av_{uuid4().hex}{ext}"
    dest = os.path.join(UPLOADS_DIR, name)

    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_BYTES:
                out.close()
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise HTTPException(status_code=413, detail="图片过大（上限 3MB）")
            out.write(chunk)

    return {"url": f"/uploads/{name}", "size": size}
