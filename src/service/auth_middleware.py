"""全局 JWT 校验中间件。

在路由之前统一处理 HTTP 请求的 token：
  * 公开路径（登录页、/auth/login、/health、静态资源、WebSocket）不做拦截；
  * 受保护路径必须携带有效 JWT，并按角色校验：
      - /admin/*                  → 仅主播（host）
      - 运营台首页                 → 仅主播（host，支持 HttpOnly cookie）
      - /auth/me                  → 仅主播
      - /auth/viewer/me           → 仅观众（viewer）
      - /api/*                    → 主播或观众均可（具体接口再按角色收紧）
  * 其它路径若携带 token，也会先校验并把 claims 挂到 request.state.auth，
    便于下游按需使用；无效 token 不会影响公开路径。

WebSocket 握手不走这里：浏览器无法设置 Authorization 头，token 由
``live_commerce_ws`` 在 accept 前校验（query 参数或 Authorization 头）。
"""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from typing import Any, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from service import auth as host_auth
from service import viewer_auth

# 运营台首页：必须登录才能拿到整页 HTML（与 service.host_console_page 双保险）
_HOST_CONSOLE_PATHS = {
    "/live-commerce",
    "/live-commerce/",
    "/live-commerce/index.html",
}


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/") or "/"
    return path


def _required_role(path: str) -> Optional[str]:
    """返回受保护路径要求的角色：host / viewer / any；公开路径返回 None。"""
    p = _normalize_path(path)

    # 先判更具体的 /auth/viewer/me，避免被 /auth/me 规则误伤
    if p == "/auth/viewer/me":
        return "viewer"
    if p == "/auth/me":
        return "host"
    if p == "/admin" or p.startswith("/admin/"):
        return "host"
    if p in _HOST_CONSOLE_PATHS:
        return "host"
    if p == "/api" or p.startswith("/api/"):
        return "any"
    return None


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _cookie(scope: Scope, name: str) -> str:
    raw = _header(scope, b"cookie")
    if not raw:
        return ""
    try:
        jar = SimpleCookie()
        jar.load(raw)
        morsel = jar.get(name)
        return morsel.value if morsel else ""
    except Exception:
        return ""


def _extract_token(scope: Scope) -> str:
    """按 Authorization → 运营台 cookie → 观众 cookie 的顺序取 token。"""
    header = _header(scope, b"authorization").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return token
    for cookie_name in (host_auth.HOST_COOKIE, "lc_viewer_token"):
        token = _cookie(scope, cookie_name)
        if token:
            return token
    return ""


def verify_any_token(token: str) -> Optional[dict[str, Any]]:
    """校验任意一种 JWT，返回统一的身份信息；无效返回 None。"""
    if not token:
        return None
    host_claims = host_auth.verify_host_claims(token)
    if host_claims:
        return {"role": "host", "claims": host_claims}
    viewer_claims = viewer_auth.verify_viewer_payload(token)
    if viewer_claims:
        return {"role": "viewer", "claims": viewer_claims}
    return None


async def _send_json(send: Send, status_code: int, detail: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    base_headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if headers:
        base_headers.extend(headers)
    await send({"type": "http.response.start", "status": status_code, "headers": base_headers})
    await send({"type": "http.response.body", "body": body})


class JWTAuthMiddleware:
    """纯 ASGI 中间件：同时覆盖 HTTP 路由与静态页，不依赖 Depends。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 交给各自处理器（WS 在 accept 前自行校验 JWT）
            await self.app(scope, receive, send)
            return

        if scope.get("method", "").upper() == "OPTIONS":
            # CORS 预检不能要求 token
            await self.app(scope, receive, send)
            return

        identity = verify_any_token(_extract_token(scope))
        if identity is not None:
            # Starlette Request.state 读的就是 scope["state"]
            scope.setdefault("state", {})["auth"] = identity

        required = _required_role(scope.get("path", ""))
        if required is None:
            await self.app(scope, receive, send)
            return

        if identity is None:
            await _send_json(
                send,
                401,
                "Not authenticated",
                [(b"www-authenticate", b"Bearer")],
            )
            return

        if required != "any" and identity["role"] != required:
            await _send_json(send, 403, f"{required} role required")
            return

        await self.app(scope, receive, send)
