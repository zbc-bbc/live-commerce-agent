"""Auth-related Pydantic models for user management."""

from typing import Literal

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """User registration request."""
    username: str = Field(..., min_length=3, max_length=50, description="Username")
    password: str = Field(..., min_length=6, max_length=128, description="Password")
    email: str = Field(..., description="Email address")


class UserLogin(BaseModel):
    """User login request."""
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class UserResponse(BaseModel):
    """User info returned to client."""
    id: int = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email")
    role: str = Field(default="user", description="User role: user or admin")
    is_active: bool = Field(default=True, description="Whether user is active")
    created_at: str = Field(..., description="Account creation time")


class TokenResponse(BaseModel):
    """Host login success response with token."""
    access_token: str = Field(..., description="Host JWT access token (7 days)")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(default=7 * 24 * 60 * 60, description="Token lifetime in seconds")
    role: Literal["host"] = Field(default="host", description="Token role")
    user: UserResponse = Field(..., description="User info")


# ── Viewer (audience) auth ──────────────────────────────────────

class ViewerLogin(BaseModel):
    """Viewer login request. Nickname is also the login identifier."""
    nickname: str = Field(..., min_length=3, max_length=20, description="Viewer nickname")
    password: str = Field(..., min_length=6, max_length=128, description="Password")


class ViewerRegister(BaseModel):
    """Viewer registration request."""
    nickname: str = Field(..., min_length=3, max_length=20, description="Viewer nickname")
    password: str = Field(..., min_length=6, max_length=128, description="Password")
    avatar: str = Field(default="", description="Optional avatar URL")


class ViewerUserResponse(BaseModel):
    """Viewer account info returned to the client."""
    userId: str = Field(..., description="Server-generated viewer id")
    userName: str = Field(..., description="Nickname (login identifier)")
    avatar: str = Field(default="", description="Avatar URL")
    isActive: bool = Field(default=True, description="Whether the account is active")
    createdAt: str = Field(..., description="Account creation time")


class ViewerTokenResponse(BaseModel):
    """Viewer login/register success response with token."""
    access_token: str = Field(..., description="Viewer JWT access token (24 hours)")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(default=24 * 60 * 60, description="Token lifetime in seconds")
    role: Literal["viewer"] = Field(default="viewer", description="Token role")
    user: ViewerUserResponse = Field(..., description="Viewer info")


class RegisterResponse(BaseModel):
    """Registration success response."""
    message: str = Field(default="Registration successful")
    user: UserResponse = Field(..., description="User info")


class UpdateUserRoleRequest(BaseModel):
    """Update user role request (admin only)."""
    role: Literal["user", "admin"] = Field(..., description="New role")


class UserListResponse(BaseModel):
    """List of users for admin panel."""
    users: list[UserResponse]
    total: int = Field(..., description="Total number of users")


class MessageResponse(BaseModel):
    """Simple message response."""
    message: str = Field(..., description="Response message")
