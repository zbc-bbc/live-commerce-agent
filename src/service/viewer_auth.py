"""
Viewer (audience) account service for the live-commerce viewer front-end.

Kept strictly separate from the ops-console auth in ``auth.py``:
  * ops console  → ``auth.db``    (email + role, admin panel users)
  * viewers      → ``viewers.db`` (nickname only, no email, no admin role)

The two systems must not share tables: the ops ``users`` table requires a
NOT NULL UNIQUE email and carries an admin role, neither of which applies to a
viewer, and mixing them would surface viewers inside ``/admin/users``.

Password hashing and JWT signing are reused verbatim from ``auth.py``
(PBKDF2-HMAC-SHA256, 600k rounds / HMAC-SHA256) so there is exactly one
crypto implementation in the project.

Tokens are stateless: logout is a client-side token discard, and there is no
server-side revocation table (a stolen token stays valid for its lifetime —
accepted for this demo). Viewer tokens are deliberately shorter-lived than the
7-day host token: the audience can silently re-login, the ops console cannot.
"""

import asyncio
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Optional

import aiosqlite

from service.auth import (
    _create_jwt,
    _hash_password,
    _verify_jwt,
    _verify_password,
)

logger = logging.getLogger(__name__)

# Same directory as auth.db (project root)
VIEWER_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "viewers.db")

# 观众 JWT：与主播 token 用同一个 scope 字段区分，有效期 24 小时。
# 主播 7 天、观众 24 小时 —— 两套 token 的生命周期和权限边界都不同。
VIEWER_TOKEN_SCOPE = "viewer"
VIEWER_TOKEN_EXPIRE_SECONDS = 24 * 60 * 60

NICKNAME_MIN = 3
NICKNAME_MAX = 20
PASSWORD_MIN = 6

_schema_lock = threading.Lock()


def _get_sync_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(VIEWER_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_viewer_db() -> None:
    """Create the viewer account table if it doesn't exist. Called once at startup."""
    with _schema_lock:
        conn = _get_sync_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS viewer_accounts (
                    user_id       TEXT PRIMARY KEY,
                    nickname      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    avatar        TEXT NOT NULL DEFAULT '',
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    last_login_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_viewer_accounts_nickname
                    ON viewer_accounts(nickname);
            """)
            conn.commit()
            logger.info(f"Viewer database initialized at {VIEWER_DB_PATH}")
        finally:
            conn.close()


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(VIEWER_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


# ── Validation ──

def _clean_nickname(nickname: str) -> str:
    return (nickname or "").strip()


def _validate_nickname(nickname: str) -> str:
    nickname = _clean_nickname(nickname)
    if len(nickname) < NICKNAME_MIN:
        raise ValueError(f"昵称至少 {NICKNAME_MIN} 个字符")
    if len(nickname) > NICKNAME_MAX:
        raise ValueError(f"昵称最多 {NICKNAME_MAX} 个字符")
    return nickname


def _validate_password(password: str) -> str:
    if not password or len(password) < PASSWORD_MIN:
        raise ValueError(f"密码至少 {PASSWORD_MIN} 位")
    return password


def _new_user_id() -> str:
    """Server-generated viewer id. The client never gets to pick its own identity."""
    return "v_" + secrets.token_hex(4)


def _make_token(user_id: str, nickname: str, avatar: str) -> str:
    now = time.time()
    return _create_jwt({
        "sub": user_id,
        "nickname": nickname,
        "avatar": avatar,
        "role": VIEWER_TOKEN_SCOPE,
        "scope": VIEWER_TOKEN_SCOPE,  # asserted on every verify — see verify_viewer_token
        "token_type": "access",
        "exp": now + VIEWER_TOKEN_EXPIRE_SECONDS,
        "iat": now,
    })


def issue_token(user_id: str, nickname: str, avatar: str = "") -> str:
    """Mint a viewer token (public alias, used for sliding re-login by token)."""
    return _make_token(user_id, nickname, avatar)


def _row_to_user(row: Any) -> dict[str, Any]:
    return {
        "userId": row["user_id"],
        "userName": row["nickname"],
        "avatar": row["avatar"] or "",
        "createdAt": row["created_at"],
        "isActive": bool(row["is_active"]),
    }


# ── Public API (all async; hashing is offloaded so the event loop never stalls) ──

async def register_viewer(nickname: str, password: str, avatar: str = "") -> dict[str, Any]:
    """Create a viewer account. Returns ``{token, user}`` or raises ValueError.

    The PBKDF2 hash is 600k rounds (~200ms of CPU); it runs in a worker thread
    so a single registration cannot freeze every open WebSocket.
    """
    nickname = _validate_nickname(nickname)
    _validate_password(password)

    password_hash = await asyncio.to_thread(_hash_password, password)
    user_id = _new_user_id()

    db = await _get_db()
    try:
        try:
            await db.execute(
                "INSERT INTO viewer_accounts (user_id, nickname, password_hash, avatar) "
                "VALUES (?, ?, ?, ?)",
                (user_id, nickname, password_hash, avatar or ""),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError("昵称已被占用")

        cursor = await db.execute(
            "SELECT * FROM viewer_accounts WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row:
        raise ValueError("注册失败，请重试")

    user = _row_to_user(row)
    return {
        "token": _make_token(user_id, nickname, avatar or ""),
        "expires_in": VIEWER_TOKEN_EXPIRE_SECONDS,
        "user": user,
    }


async def authenticate_viewer(nickname: str, password: str) -> dict[str, Any]:
    """Log a viewer in. Returns ``{token, user}`` or raises ValueError.

    Raises ValueError("账号或密码错误") for both unknown-nickname and
    wrong-password so the response does not reveal which accounts exist.
    """
    nickname = _clean_nickname(nickname)
    if not nickname or not password:
        raise ValueError("请输入昵称和密码")

    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM viewer_accounts WHERE nickname = ?", (nickname,)
        )
        row = await cursor.fetchone()

        if not row:
            raise ValueError("账号或密码错误")
        if not row["is_active"]:
            raise ValueError("账号已被停用")

        ok = await asyncio.to_thread(_verify_password, password, row["password_hash"])
        if not ok:
            raise ValueError("账号或密码错误")

        await db.execute(
            "UPDATE viewer_accounts SET last_login_at = datetime('now') WHERE user_id = ?",
            (row["user_id"],),
        )
        await db.commit()
    finally:
        await db.close()

    user = _row_to_user(row)
    return {
        "token": _make_token(user["userId"], user["userName"], user["avatar"]),
        "expires_in": VIEWER_TOKEN_EXPIRE_SECONDS,
        "user": user,
    }


def verify_viewer_payload(token: str) -> Optional[dict[str, Any]]:
    """Validate a viewer JWT and return its claims, or None if invalid.

    ``auth._verify_jwt`` only checks the signature and expiry — it does **not**
    look at any scope claim — so an ops-console token would otherwise pass here
    and be accepted as a viewer id ("1", "2", …). The explicit scope assertion
    is what keeps the two token families apart.
    """
    if not token or not isinstance(token, str):
        return None
    payload = _verify_jwt(token)
    if not payload:
        return None
    if payload.get("scope") != VIEWER_TOKEN_SCOPE:
        return None
    user_id = payload.get("sub")
    if not user_id or not isinstance(user_id, str):
        return None
    return payload


def verify_viewer_token(token: str) -> Optional[str]:
    """Validate a viewer JWT. Returns the viewer userId, or None if invalid."""
    payload = verify_viewer_payload(token)
    return payload["sub"] if payload else None


async def get_viewer_row(user_id: str) -> Optional[dict[str, Any]]:
    """Fetch a viewer account by id (without the password hash)."""
    if not user_id:
        return None
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT user_id, nickname, avatar, is_active, created_at, last_login_at "
            "FROM viewer_accounts WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        return None
    user = _row_to_user(row)
    user["lastLoginAt"] = row["last_login_at"]
    return user


async def get_viewer_nickname(user_id: str) -> Optional[str]:
    row = await get_viewer_row(user_id)
    return row["userName"] if row else None


async def rename_viewer(user_id: str, nickname: str) -> dict[str, Any]:
    """Change a viewer's nickname. The nickname is the login identifier, so it
    must stay unique; returns the updated user dict or raises ValueError."""
    if not user_id:
        raise ValueError("未登录")
    nickname = _validate_nickname(nickname)

    db = await _get_db()
    try:
        try:
            await db.execute(
                "UPDATE viewer_accounts SET nickname = ? WHERE user_id = ?",
                (nickname, user_id),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError("昵称已被占用")

        cursor = await db.execute(
            "SELECT * FROM viewer_accounts WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row:
        raise ValueError("账号不存在")
    return _row_to_user(row)


async def set_viewer_avatar(user_id: str, avatar: str) -> None:
    """Persist the avatar so it survives a re-login on another device."""
    if not user_id:
        return
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE viewer_accounts SET avatar = ? WHERE user_id = ?",
            (avatar or "", user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def count_viewers() -> int:
    db = await _get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM viewer_accounts")
        row = await cursor.fetchone()
    finally:
        await db.close()
    return row["cnt"] if row else 0
