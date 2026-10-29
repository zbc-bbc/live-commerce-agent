"""Admin API routes for the Agent Service Toolkit."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from schema.auth_schema import (
    MessageResponse,
    UpdateUserRoleRequest,
    UserListResponse,
    UserResponse,
)
from service.auth_routes import require_admin
from service.auth import delete_user, list_users, update_user_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/users", response_model=UserListResponse)
async def admin_list_users(
    user: Annotated[dict, Depends(require_admin)],
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> UserListResponse:
    """List all users (admin only)."""
    result = await list_users(page=page, per_page=per_page)
    return UserListResponse(
        users=[UserResponse(**u) for u in result["users"]],
        total=result["total"],
    )


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def admin_update_user_role(
    user: Annotated[dict, Depends(require_admin)],
    user_id: int,
    input: UpdateUserRoleRequest,
) -> UserResponse:
    """Update a user's role (admin only). Cannot change own role."""
    if user["id"] == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own role")
    updated = await update_user_role(user_id, input.role)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserResponse(**updated)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def admin_delete_user(
    user: Annotated[dict, Depends(require_admin)],
    user_id: int,
) -> MessageResponse:
    """Delete a user (admin only). Cannot delete yourself."""
    if user["id"] == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    success = await delete_user(user_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return MessageResponse(message=f"User {user_id} deleted successfully")
