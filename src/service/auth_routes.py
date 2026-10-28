"""Authentication API routes for the Agent Service Toolkit."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from schema.auth_schema import (
    RegisterResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    ViewerLogin,
    ViewerRegister,
    ViewerTokenResponse,
    ViewerUserResponse,
)
from service import viewer_auth
from service.auth import (
    HOST_COOKIE,
    HOST_TOKEN_EXPIRE_SECONDS,
    authenticate_user,
    get_user_count,
    get_user_from_token,
    is_admin_initialized,
    register_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Dependency: extract and validate current user from JWT token."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await get_user_from_token(credentials.credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Dependency: require admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_current_viewer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Dependency: extract and validate a viewer JWT.

    Uses its own bearer extractor instead of ``get_current_user`` so a host token
    can never satisfy a viewer-only endpoint (and vice versa).
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = viewer_auth.verify_viewer_token(credentials.credentials)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired viewer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    row = await viewer_auth.get_viewer_row(user_id)
    if not row or not row.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Viewer account not found or disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return row


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(input: UserCreate) -> RegisterResponse:
    """Register a new user account."""
    try:
        user = await register_user(
            username=input.username,
            email=input.email,
            password=input.password,
        )
        return RegisterResponse(
            message="Registration successful. Please log in.",
            user=UserResponse(
                id=user["id"],
                username=user["username"],
                email=user["email"],
                role=user["role"],
                is_active=bool(user["is_active"]),
                created_at=user["created_at"],
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=TokenResponse)
async def login(input: UserLogin, response: Response) -> TokenResponse:
    """Login and get access token."""
    try:
        result = await authenticate_user(username=input.username, password=input.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    # 同时下发一份 HttpOnly cookie：运营台的静态页闸门（service.host_console_page）
    # 靠它认人。前端拿不到它，所以退出登录走后端 /logout 清。
    response.set_cookie(
        HOST_COOKIE,
        result["access_token"],
        max_age=HOST_TOKEN_EXPIRE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return TokenResponse(
        access_token=result["access_token"],
        token_type=result["token_type"],
        expires_in=result["expires_in"],
        role="host",
        user=UserResponse(**result["user"]),
    )


# ── 观众端 HTTP 登录 / 注册 ─────────────────────────────────────
# 运营台的 /auth/login 保持不变；观众走 /auth/viewer/*，两套身份与 token
# 完全分开。WebSocket 里的 viewer_login 仍然保留，用于已建立连接后的续登。

def _viewer_response(result: dict) -> ViewerTokenResponse:
    u = result["user"]
    return ViewerTokenResponse(
        access_token=result["token"],
        expires_in=result["expires_in"],
        role="viewer",
        user=ViewerUserResponse(
            userId=u["userId"],
            userName=u["userName"],
            avatar=u.get("avatar") or "",
            isActive=u.get("isActive", True),
            createdAt=u.get("createdAt", ""),
        ),
    )


@router.post(
    "/viewer/register",
    response_model=ViewerTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def viewer_register(input: ViewerRegister) -> ViewerTokenResponse:
    """Register a viewer account and issue a viewer JWT."""
    try:
        result = await viewer_auth.register_viewer(
            nickname=input.nickname,
            password=input.password,
            avatar=input.avatar,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _viewer_response(result)


@router.post("/viewer/login", response_model=ViewerTokenResponse)
async def viewer_login(input: ViewerLogin) -> ViewerTokenResponse:
    """Viewer login: nickname + password → viewer JWT (24 hours)."""
    try:
        result = await viewer_auth.authenticate_viewer(
            nickname=input.nickname,
            password=input.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    return _viewer_response(result)


@router.get("/viewer/me", response_model=ViewerUserResponse)
async def get_viewer_me(
    user: Annotated[dict, Depends(get_current_viewer)],
) -> ViewerUserResponse:
    """Current viewer profile (viewer token only)."""
    return ViewerUserResponse(
        userId=user["userId"],
        userName=user["userName"],
        avatar=user.get("avatar") or "",
        isActive=user.get("isActive", True),
        createdAt=user.get("createdAt", ""),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[dict, Depends(get_current_user)]) -> UserResponse:
    """Get current authenticated user's profile."""
    return UserResponse(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
    )


@router.get("/check", response_model=dict)
async def check_auth_status() -> dict:
    """Check if authentication system is ready (admin initialized)."""
    initialized = await is_admin_initialized()
    user_count = await get_user_count()
    return {
        "admin_initialized": initialized,
        "user_count": user_count,
        "auth_enabled": True,
        "first_run": user_count == 0,
    }


@router.get("/session", response_model=dict)
async def gate_session(request: Request) -> dict:
    """闸门 cookie 是否还有效。

    登录页拿它决定要不要显示"已登录 · 进入控制台"。这个判断只能问后端：
    cookie 是 HttpOnly 的，JS 读不到，而 localStorage 里那份 token 已经完全
    不能代表能不能进运营台（过期了 cookie 也在、反之亦然）。
    """
    token = request.cookies.get(HOST_COOKIE, "")
    user = await get_user_from_token(token) if token else None
    return {
        "authenticated": bool(user),
        "username": user["username"] if user else None,
    }
