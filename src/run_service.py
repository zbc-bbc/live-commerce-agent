import asyncio
import logging
import sys

import uvicorn
from dotenv import load_dotenv

from core import settings

load_dotenv()

if __name__ == "__main__":
    root_logger = logging.getLogger()
    if root_logger.handlers:
        print(
            f"Warning: Root logger already has {len(root_logger.handlers)} handler(s) configured. "
            f"basicConfig() will be ignored. Current level: {logging.getLevelName(root_logger.level)}"
        )

    logging.basicConfig(level=settings.LOG_LEVEL.to_logging_level())
    # Set Compatible event loop policy on Windows Systems.
    # On Windows systems, the default ProactorEventLoop can cause issues with
    # certain async database drivers like psycopg (PostgreSQL driver).
    # The WindowsSelectorEventLoopPolicy provides better compatibility and prevents
    # "RuntimeError: Event loop is closed" errors when working with database connections.
    # This needs to be set before running the application server.
    # Refer to the documentation for more information.
    # https://www.psycopg.org/psycopg3/docs/advanced/async.html#asynchronous-operations
    if sys.platform == "win32" and sys.version_info < (3, 14):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # 数据文件（运行时会被频繁写入）不参与热重载，否则每次写 JSON/DB 都触发 reload，
    # 导致内存状态被反复清空，Live Commerce 开播状态等丢失。
    _reload_excludes = [
        "*/data/live_commerce/data/*",   # Live Commerce JSON 数据
        "*.db", "*.db-shm", "*.db-wal",  # SQLite 数据库文件
        "*/__pycache__/*",
    ]
    # service:app 内注册了 /ws/live-commerce 基础 WebSocket 端点：
    # 启动后可先访问 /health 检查 HTTP 服务，再连接 ws://<HOST>:<PORT>/ws/live-commerce。
    uvicorn.run(
        "service:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.is_dev(),
        reload_excludes=_reload_excludes,
        timeout_graceful_shutdown=settings.GRACEFUL_SHUTDOWN_TIMEOUT,
        # WebSocket 心跳：及时发现断线，避免观众端/运营台假在线。
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
