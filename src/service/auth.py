"""
Authentication service for the Agent Service Toolkit.

Provides user registration, login, JWT-based authentication, and
role-based access control using Python stdlib only (no external deps).
Uses PBKDF2-HMAC-SHA256 for password hashing and HMAC-SHA256 for JWT signing.

Database: a separate SQLite file (auth.db) in the project root.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Optional

import aiosqlite

from core import settings

logger = logging.getLogger(__name__)

# Default to project root for auth DB
AUTH_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "auth.db")

# ── 双角色 JWT 约定 ──────────────────────────────────────────────
# 主播（运营台）与观众各有一套 token，靠 `scope` 声明区分：
#   host   → 运营台账号（auth.db），有效期 7 天
#   viewer → 观众账号（viewers.db），有效期见 viewer_auth.VIEWER_TOKEN_EXPIRE_SECONDS
# 两套 token 共用签名密钥，因此**任何鉴权入口都必须校验 scope**，不能只看签名。
HOST_TOKEN_SCOPE = "host"
HOST_TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60  # 7 days
# 兼容旧代码中的常量名（token 表过期时间也沿用它）
TOKEN_EXPIRE_SECONDS = HOST_TOKEN_EXPIRE_SECONDS

# 运营台静态页闸门用的 cookie 名。登录时由 /auth/login 下发（HttpOnly）。
# 浏览器地址栏导航带不了 Authorization 头，所以进门只能靠它；前端 localStorage
# 里的 access_token 继续负责 WS 和带鉴权的 fetch，两者并存、互不替代。
HOST_COOKIE = "lc_host_token"

# ── Synchronous DB setup (run once at startup) ──

_schema_lock = threading.Lock()


def _get_sync_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_auth_db() -> None:
    """Create auth tables if they don't exist. Called once at service startup."""
    with _schema_lock:
        conn = _get_sync_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_auth_tokens_token ON auth_tokens(token);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id ON auth_tokens(user_id);
            """)
            conn.commit()
            logger.info(f"Auth database initialized at {AUTH_DB_PATH}")
        finally:
            conn.close()


# ── Async DB helpers ──

@asynccontextmanager
async def _get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(AUTH_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


# ── Password utilities (PBKDF2-HMAC-SHA256) ──

def _hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 and a random salt."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    # Format: algorithm$iterations$salt_b64$hash_b64
    return f"pbkdf2_sha256$600000${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against the stored hash."""
    try:
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        _algo, iterations_str, salt_b64, hash_b64 = parts
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(key, expected)
    except (ValueError, Exception) as e:
        logger.warning(f"Password verification error: {e}")
        return False


# ── JWT-like token utilities (HMAC-SHA256, stdlib only) ──

def _get_jwt_secret() -> str:
    """Get the JWT signing key. Falls back to AUTH_SECRET, then auto-generates."""
    if settings.AUTH_SECRET:
        return settings.AUTH_SECRET.get_secret_value()
    # Fallback: use a derived key
    return hashlib.sha256(b"agent-service-toolkit-jwt-secret").hexdigest()


def _create_jwt(payload: dict[str, Any]) -> str:
    """Create a signed JWT token. Format: header.payload.signature (all base64)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_bytes = base64.urlsafe_b64encode(json.dumps(payload, default=str).encode()).rstrip(b"=").decode()
    signing_input = f"{header}.{payload_bytes}"
    secret = _get_jwt_secret()
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{signing_input}.{signature}"


def _verify_jwt(token: str) -> Optional[dict[str, Any]]:
    """Verify and decode a JWT token. Returns payload dict or None if invalid/expired."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}"
        secret = _get_jwt_secret()
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(signature_b64, expected_sig):
            return None
        # Decode payload
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
        payload = json.loads(payload_bytes)
        # Check expiry
        exp = payload.get("exp", 0)
        if time.time() > exp:
            return None
        return payload
    except (ValueError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"JWT verification error: {e}")
        return None


def create_host_token(user: dict[str, Any]) -> str:
    """签发主播（运营台）JWT，有效期 7 天。

    载荷里同时保留 ``role="host"`` 与 ``account_role``（auth.db 的 admin/user），
    前者是前端/WS 用于角色隔离的稳定角色，后者仅描述运营台内部权限。
    """
    now = time.time()
    return _create_jwt({
        "sub": str(user["id"]),
        "user_id": user["id"],          # 整数：auth.get_user_from_token 依赖它
        "username": user["username"],
        "role": HOST_TOKEN_SCOPE,
        "account_role": user.get("role", "user"),
        "scope": HOST_TOKEN_SCOPE,      # 双角色隔离的关键声明
        "token_type": "access",
        "exp": now + HOST_TOKEN_EXPIRE_SECONDS,
        "iat": now,
    })


def verify_host_claims(token: str) -> Optional[dict[str, Any]]:
    """校验主播 JWT，返回 claims；观众 token / 过期 / 签名错误一律返回 None。

    历史 token（本次改动前签发）没有 ``scope``，但有整数 ``user_id``；为兼容
    已登录的运营台会话，这类 token 仍按主播 token 接受。观众 token 永远带
    ``scope="viewer"`` 且 ``sub`` 是字符串，不可能误入这里。
    """
    payload = _verify_jwt(token)
    if not payload:
        return None
    scope = payload.get("scope")
    if scope is not None and scope != HOST_TOKEN_SCOPE:
        return None
    if not isinstance(payload.get("user_id"), int):
        return None
    return payload


# ── Token management ──

async def _save_token(user_id: int, token: str) -> None:
    """Save a token to the database."""
    expires_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + TOKEN_EXPIRE_SECONDS))
    async with _get_db() as db:
        await db.execute(
            "INSERT INTO auth_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
        await db.commit()


async def _revoke_old_tokens(user_id: int) -> None:
    """Revoke expired tokens for a user."""
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time()))
    async with _get_db() as db:
        await db.execute(
            "DELETE FROM auth_tokens WHERE user_id = ? AND expires_at < ?",
            (user_id, now),
        )
        await db.commit()


# ── Public API ──

async def register_user(username: str, email: str, password: str) -> dict[str, Any]:
    """Register a new user. Returns user data dict or raises ValueError."""
    if not username or len(username) < 3:
        raise ValueError("Username must be at least 3 characters")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if not email or "@" not in email:
        raise ValueError("Invalid email address")

    password_hash = _hash_password(password)

    # Determine role: first user becomes admin, rest are regular users
    async with _get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
        user_count = row["cnt"] if row else 0
        role = "admin" if user_count == 0 else "user"

    async with _get_db() as db:
        try:
            cursor = await db.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (username, email, password_hash, role),
            )
            await db.commit()
            user_id = cursor.lastrowid

            # Fetch the created user
            cursor = await db.execute(
                "SELECT id, username, email, role, is_active, created_at FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Failed to create user")
            return dict(row)
        except aiosqlite.IntegrityError:
            raise ValueError("Username or email already exists")


async def authenticate_user(username: str, password: str) -> dict[str, Any]:
    """Authenticate a user. Returns user data dict with token or raises ValueError."""
    if not username or not password:
        raise ValueError("Username and password are required")

    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT id, username, email, password_hash, role, is_active, created_at FROM users WHERE username = ?",
            (username,),
        )
        row = await cursor.fetchone()

    if not row:
        raise ValueError("USER_NOT_FOUND")

    user = dict(row)
    if not user["is_active"]:
        raise ValueError("Account is disabled")

    if not _verify_password(password, user["password_hash"]):
        raise ValueError("WRONG_PASSWORD")

    # Generate host token（scope=host，7 天有效期）
    token = create_host_token(user)

    # Save token and clean up old ones
    await _save_token(user["id"], token)
    await _revoke_old_tokens(user["id"])

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": HOST_TOKEN_EXPIRE_SECONDS,
        "role": HOST_TOKEN_SCOPE,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "is_active": bool(user["is_active"]),
            "created_at": user["created_at"],
        },
    }


async def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    """Get user by ID."""
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_user_from_token(token: str) -> Optional[dict[str, Any]]:
    """Validate a JWT token and return the user. Returns None if invalid.

    运营台和观众端用同一个密钥签发 JWT（见 viewer_auth._make_token），所以
    「签名对」并不等于「这是运营台的人」，还得看载荷长什么样：观众 token 是
    ``scope="viewer"`` + 字符串 ``sub``，没有 ``user_id`` 这个键。

    这里两道检查缺一不可 —— 少了它们，观众 token 打到运营台接口会在
    ``payload["user_id"]`` 上抛 KeyError，本该答 401 的地方变成 500。
    """
    payload = verify_host_claims(token)
    if not payload:
        return None
    return await get_user_by_id(payload["user_id"])


async def get_user_count() -> int:
    """Get total number of registered users."""
    async with _get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        row = await cursor.fetchone()
    return row["cnt"] if row else 0


async def list_users(page: int = 1, per_page: int = 20) -> dict[str, Any]:
    """List all users (admin only)."""
    offset = (page - 1) * per_page
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        rows = await cursor.fetchall()
        cursor2 = await db.execute("SELECT COUNT(*) as total FROM users")
        total_row = await cursor2.fetchone()
    return {
        "users": [dict(r) for r in rows],
        "total": total_row["total"] if total_row else 0,
    }


async def update_user_role(user_id: int, new_role: str) -> Optional[dict[str, Any]]:
    """Update a user's role (admin only)."""
    async with _get_db() as db:
        await db.execute(
            "UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?",
            (new_role, user_id),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id, username, email, role, is_active, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_user(user_id: int) -> bool:
    """Delete a user and their tokens."""
    async with _get_db() as db:
        await db.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return row["cnt"] == 0 if row else True


async def get_admin_count() -> int:
    """Get the number of admin users."""
    async with _get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'")
        row = await cursor.fetchone()
    return row["cnt"] if row else 0


async def is_admin_initialized() -> bool:
    """Check if at least one admin user exists."""
    count = await get_admin_count()
    return count > 0
