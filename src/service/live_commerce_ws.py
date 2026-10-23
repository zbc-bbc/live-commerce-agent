"""Live Commerce WebSocket 基础服务入口。

本轮（Round 1）只实现最小可运行骨架：

- 客户端连接后收到 ``connected`` 事件；
- 发送 ``{"type": "ping"}`` 返回 ``pong``；
- 其它消息返回 ``error``，提示该消息类型尚未实现。

直播状态、商品、弹幕、订单、私信等业务消息会在后续迭代中逐步加入。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


def _envelope(msg_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造统一的 WebSocket 消息信封。"""
    return {
        "type": msg_type,
        "data": data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class LiveCommerceConnectionManager:
    """最小连接管理器，为后续广播能力预留统一入口。"""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def broadcast(self, msg_type: str, data: dict[str, Any] | None = None) -> None:
        payload = _envelope(msg_type, data)
        stale: list[WebSocket] = []
        for websocket in list(self.active):
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(websocket)


manager = LiveCommerceConnectionManager()


async def live_commerce_ws_handler(websocket: WebSocket) -> None:
    """处理单条直播间 WebSocket 连接。"""
    await manager.connect(websocket)
    client = getattr(websocket, "client", None)
    logger.info("Live Commerce WebSocket 已连接: %s", client)

    try:
        await websocket.send_json(
            _envelope("connected", {"message": "Live Commerce WebSocket connected"})
        )
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    _envelope("error", {"message": "消息必须是合法的 JSON"})
                )
                continue

            msg_type = message.get("type")
            if msg_type == "ping":
                await websocket.send_json(_envelope("pong"))
            else:
                await websocket.send_json(
                    _envelope("error", {"message": f"消息类型暂未实现: {msg_type!r}"})
                )
    except WebSocketDisconnect:
        logger.info("Live Commerce WebSocket 已断开: %s", client)
    finally:
        manager.disconnect(websocket)
