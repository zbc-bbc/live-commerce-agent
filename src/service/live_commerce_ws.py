"""
直播电商 WebSocket 处理器（Live Commerce WebSocket Handler）
处理所有实时双向通信，包括弹幕推送、状态更新、智能提示等
"""

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import WebSocket, WebSocketDisconnect

from data.live_commerce import llm_endpoint_store
from data.live_commerce.data_manager import get_data_manager, is_question_like
from schema.live_commerce_schema import (
    LIVE_SCRIPT_TYPES, DanmakuItem, LiveCommerceSettings, LiveStatsData, OrderItem,
    PresetPhraseItem, PrivateMessageItem, ProductItem, QuickReplyItem, ReviewItem,
    SmartAlert, UserSessionItem, WSMessage,
)
from service import ai_endpoints
from service import viewer_auth
from service import auth as ops_auth

logger = logging.getLogger(__name__)

# 观众端弹幕自动回复的并发上限：LLM 单次最长 20s，限流避免观众刷屏打爆 API
_MAX_CONCURRENT_DANMAKU_REPLIES = 3

# 观众连接（含未登录游客）允许调用的消息类型。**白名单制：未列出的类型一律要求 host 角色。**
# 之所以是白名单而不是黑名单 —— 旧 connect() 不带 role 即判为 host，导致全部主播专用消息
# 对匿名裸连可达；黑名单会随以后新增的消息类型不断漏，白名单只会「默认更严」。
_VIEWER_ALLOWED: frozenset = frozenset({
    # 公开只读（数据本身就是直播间公开展示的）
    # 注意：get_stats 不在此列 —— 它含 AI 回复量、意图分、私信量等运营指标，
    # 属于主播侧数据；观众端只通过 init_state 拿 liveStatus，不需要这份统计。
    "get_products", "get_product_detail", "get_reviews", "get_product_reviews",
    # 直播间挂载商品列表：与 get_products 同为公开只读数据（只含已挂载/已上架）
    "get_live_products", "click_live_product",
    # 弹幕历史：新观众中途进入时拉取本场最近 20 条（公开数据）
    "get_danmaku_history",
    # 账号：游客也需要能注册/登录
    "viewer_login", "viewer_register", "viewer_logout",
    # 需登录的操作：handler 内用服务端绑定身份二次校验，未登录回 viewer_auth_required
    "send_danmaku", "update_viewer_profile", "recharge", "place_order",
    "pay_order", "confirm_receipt", "cancel_order", "submit_review",
    "get_user_orders", "get_my_messages",
    # 观众 → 主播私信。方向与 send_viewer_msg（主播 → 观众）相反，
    # 是观众的**主动**发言，所以必须在白名单里；身份仍走 _require_viewer 二次校验。
    "send_host_msg",
})

# 单个连接两次充值之间的最小间隔（秒），防止前端连点重复入账
_RECHARGE_MIN_INTERVAL = 1.0
# 充值金额上下限
_RECHARGE_MIN, _RECHARGE_MAX = 1.0, 50000.0

# 观众私信正文长度上限（与 viewer.html 的 maxlength 对齐）。
# 弹幕那边后端**没有**长度校验、只靠前端 maxlength，这个疏漏不沿用到私信上。
_PM_MAX_LEN = 300
# 单个连接两次发私信之间的最小间隔（秒）。弹幕是公屏广播，没设闸；
# 私信会直插主播工作台的收件箱，不设闸等于开了一条刷屏通道。
_PM_MIN_INTERVAL = 1.0

# 观众端两次商品点击埋点的最小间隔（秒）：详情弹层反复开合不该刷高本场点击数。
_PRODUCT_CLICK_MIN_INTERVAL = 0.5
# 主播两次商品卡片推送的最小间隔（秒）：卡片弹窗会打断观众，连点等于刷屏。
_CARD_PUSH_MIN_INTERVAL = 2.0


def _products_payload(dm, on_sale_only: bool = False) -> list[dict]:
    """把商品列表转换为可下发的字典，并为每个商品附带按订单聚合的「销售情况」。

    on_sale_only=True 时只下发已上架商品 —— **观众端专用**。这是「下架」对观众真正
    生效的地方：运营台必须传 False，否则商品管理页会看不到已下架商品，直接空掉。

    附加字段（不下发到 JSON 文件）：
      salesQty           —— 观众端「已售 N 件」读这个 = 真实销量 + 商品自带的 soldBase 展示基数
      realSalesQty/realSalesAmount/realTodayQty —— 只有真实观众订单（运营台读这三个）

    ⚠️ 为什么 salesQty 要加 soldBase：模拟单已全面停用（成交只能来自真实注册观众），
    若观众端还按订单聚合算「已售」，12 件商品会集体显示「已售 0 件」，观感垮掉。
    soldBase 是商品自带的固定展示基数，与订单无关 —— 它**只**出现在 salesQty 里，
    realSalesQty 永远不掺，所以运营台看到的一直是纯真实成交。
    注意连 salesAmount 也不加基数：观众端从不展示金额，凭空造 GMV 没有收益、只有口径风险。
    """
    sales_map = dm.get_product_sales_map()
    # 真实口径（只有真实观众订单）——运营台读这个，观众端不读
    real_map = dm.get_real_product_sales_map()
    payload = []
    for p in dm.get_all_products():
        if on_sale_only and not p.isOnSale:
            continue
        d = p.model_dump()
        s = sales_map.get(p.productId) or {}
        d["salesQty"] = s.get("qty", 0) + (p.soldBase or 0)
        d["salesAmount"] = s.get("amount", 0.0)
        d["todayQty"] = s.get("todayQty", 0)
        r = real_map.get(p.productId) or {}
        d["realSalesQty"] = r.get("qty", 0)
        d["realSalesAmount"] = r.get("amount", 0.0)
        d["realTodayQty"] = r.get("todayQty", 0)
        payload.append(d)
    return payload


def _viewer_safe_current(dm, role: str):
    """当前讲解商品的观众安全版：观众拿到的一定是已上架商品，否则 None。

    不变量本来就是「下架当前商品时会清空 current_product_id」，这里只是把它兜住 ——
    万一哪天有别的路径破坏了它，观众端不至于显示一个看不到也买不到的商品。
    运营台不裁剪：商品管理页要如实显示。
    """
    cur = dm.get_current_product()
    if cur is None:
        return None
    if role != "host" and not cur.isOnSale:
        return None
    return cur.model_dump()


# 主播发言在弹幕流里使用的固定 userId（与 DataManager 的既有约定一致）
HOST_DANMAKU_USER_ID = "host_001"


# 观众可见的直播状态字段。DataManager.get_live_status() 里还带着 totalDanmaku /
# totalPrivateMessages / totalAIReplies 三个**运营指标**，它们只应出现在运营台。
# 观众端实际消费的只有开播状态、阶段、开播时间、时长、在线人数。
_VIEWER_LIVE_FIELDS = ("status", "currentStage", "startTime", "liveDuration", "onlineCount")

# 这些广播事件对观众要做负载裁剪（其余事件本来就是公开信息，原样下发）。
_VIEWER_TRIMMED_EVENTS = frozenset({"status_update", "live_started", "live_ended", "stage_changed"})


def _viewer_safe_live_status(status: dict) -> dict:
    """直播状态的观众安全版：只保留公开字段，剥掉运营统计。"""
    return {k: status[k] for k in _VIEWER_LIVE_FIELDS if k in status}


def _viewer_safe_event(msg_type: str, data: dict) -> dict:
    """把主播侧事件裁剪成观众可见版本。

    * status_update    —— 三个运营统计剥掉
    * live_started     —— 内嵌的 liveStatus 同样裁剪
    * live_ended       —— **复盘报告不下发**（含 GMV、转化、AI 统计等运营数据）
    * stage_changed    —— 阶段策略话术（strategy）是运营台内部提示，观众只要阶段名
    """
    if msg_type == "status_update":
        return _viewer_safe_live_status(data)
    if msg_type == "live_started":
        return {
            "startTime": data.get("startTime"),
            "liveStatus": _viewer_safe_live_status(data.get("liveStatus") or {}),
        }
    if msg_type == "live_ended":
        return {"endTime": data.get("endTime"), "duration": data.get("duration")}
    if msg_type == "stage_changed":
        return {"stage": data.get("stage")}
    return data


class LiveCommerceWebSocketManager:
    """
    WebSocket连接管理器
    管理多个主播端连接的消息广播
    """

    def __init__(self):
        self._connections: list[WebSocket] = []
        # 连接角色：host(运营台) / viewer(观众端，含游客)。**由鉴权决定**，见 connect()
        self._roles: dict[WebSocket, str] = {}
        # 已鉴权观众：连接 → userId，以及 userId → 其全部连接
        # （同一账号可开多个标签页，互不踢下线；在线人数按去重 userId 计）
        self._user_ids: dict[WebSocket, str] = {}
        self._user_conns: dict[str, set[WebSocket]] = {}
        # 每连接最近一次充值时间（防连点重复入账）
        self._last_recharge: dict[WebSocket, float] = {}
        # 每连接最近一次发私信时间（防刷屏）—— 断开时必须一并 pop，见 _cleanup
        self._last_pm: dict[WebSocket, float] = {}
        # 每连接最近一次商品点击埋点 / 主播商品卡片推送时间（限流用）
        self._last_product_click: dict[WebSocket, float] = {}
        self._last_card_push: dict[WebSocket, float] = {}
        # 每连接一个发送队列（由后台线程/事件循环 put，主循环发送 task 串行消费）
        self._send_queues: dict[WebSocket, "asyncio.Queue[dict | None]"] = {}
        self._send_tasks: dict[WebSocket, "asyncio.Task"] = {}
        self._data_manager = get_data_manager()
        # 观众端弹幕自动回复的后台任务（持有引用防止被 GC；结束即移除）
        self._reply_tasks: set[asyncio.Task] = set()
        # 并发闸门惰性创建，见 _reply_semaphore_instance()
        self._reply_semaphore: Optional[asyncio.Semaphore] = None
        # 注册数据变更回调
        self._data_manager.register_ws_callback(self._on_data_event)

    @staticmethod
    def _ws_token(websocket: WebSocket) -> str:
        """从握手请求里取 JWT。

        浏览器 WebSocket API 不能设置 Authorization 头，所以前端优先用 query
        参数；同时兼容非浏览器客户端（curl / 测试）的 Authorization 与 cookie。
        参数优先级：host_token（运营台旧参数）→ token（通用）→ Authorization。
        """
        params = websocket.query_params
        for key in ("host_token", "token", "access_token"):
            token = (params.get(key) or "").strip()
            if token:
                return token

        header = (websocket.headers.get("authorization") or "").strip()
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            if token:
                return token

        for cookie_name in (ops_auth.HOST_COOKIE, "lc_viewer_token"):
            token = (websocket.cookies.get(cookie_name) or "").strip()
            if token:
                return token
        return ""

    async def _resolve_identity(
        self, websocket: WebSocket
    ) -> Optional[tuple[str, Optional[str]]]:
        """握手阶段校验 JWT，返回 ``(role, viewer_user_id)`` 或 None（拒绝连接）。

        角色由**凭据**决定，而不是客户端自称：
          * 有效主播 JWT（scope=host）          → host
          * 有效观众 JWT（scope=viewer）        → viewer，并绑定账号
          * 没带 token + ``role=viewer``/无角色 → 游客 viewer（只能看公开数据）
          * 带了 token 但签名/过期/scope 不对    → None，握手阶段直接关闭
          * 自称 ``role=host`` 却没有有效主播 JWT → None

        旧实现只看 ``role=viewer`` 参数，且非 viewer 一律当 host：裸连即可拿主播权限。
        """
        role_hint = (websocket.query_params.get("role") or "").strip().lower()
        token = self._ws_token(websocket)

        if not token:
            if role_hint == "host":
                logger.warning("WebSocket 自称 host 但未携带 JWT，拒绝连接")
                return None
            return ("viewer", None)

        # 1) 主播 token
        try:
            host_claims = ops_auth.verify_host_claims(token)
        except Exception as e:  # noqa: BLE001
            logger.warning("WebSocket 主播 token 校验异常: %s", e)
            host_claims = None
        if host_claims:
            user = await ops_auth.get_user_by_id(host_claims["user_id"])
            if user and user.get("is_active"):
                return ("host", None)
            logger.warning("WebSocket 主播 token 对应账号不存在或已停用，拒绝连接")
            return None

        # 2) 观众 token（scope 必须是 viewer，主播 token 不可能走到这里）
        viewer_claims = viewer_auth.verify_viewer_payload(token)
        if not viewer_claims:
            logger.warning("WebSocket token 无效、已过期或角色 scope 不匹配，拒绝连接")
            return None

        user_id = viewer_claims["sub"]
        row = await viewer_auth.get_viewer_row(user_id)
        if not row or not row.get("isActive", True):
            logger.warning("WebSocket 观众 token 对应账号不存在或已停用，拒绝连接")
            return None

        # 确保钱包账户存在，随后 connect() 会绑定该连接
        self._data_manager.get_or_create_viewer(
            user_id, row.get("userName") or "观众", row.get("avatar") or ""
        )
        return ("viewer", user_id)

    async def connect(self, websocket: WebSocket) -> bool:
        """接受WebSocket连接（握手前先校验 JWT / 角色）。校验失败返回 False。"""
        identity = await self._resolve_identity(websocket)
        if identity is None:
            # 1008 = policy violation。前端收到后应清本地 token 并回登录页/游客态。
            await websocket.close(code=1008, reason="invalid or expired token")
            return False

        role, bound_user_id = identity
        await websocket.accept()
        self._connections.append(websocket)
        self._roles[websocket] = role
        # 为每个连接建立独立的发送队列与发送任务，确保 socket 只被一个 task 串行写入
        q: "asyncio.Queue[dict | None]" = asyncio.Queue()
        self._send_queues[websocket] = q
        loop = asyncio.get_running_loop()
        self._send_tasks[websocket] = loop.create_task(self._send_loop(websocket, q))
        # 握手时已通过观众 JWT 的连接直接绑定身份，后续操作无需再发 viewer_login
        if bound_user_id:
            self._bind_user(websocket, bound_user_id)
        logger.info(f"Live Commerce WebSocket {role} 已连接，当前连接数: {len(self._connections)}")
        # 发送初始状态
        await self._send_initial_state(websocket)
        return True

    def disconnect(self, websocket: WebSocket):
        """断开WebSocket连接（同步入口，调度清理）"""
        # 通知发送循环结束（若还在运行）
        q = self._send_queues.get(websocket)
        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                pass
        else:
            # 无队列（异常路径），直接清理
            loop = asyncio.get_event_loop()
            if loop and loop.is_running():
                loop.create_task(self._cleanup_connection(websocket))
            else:
                self._cleanup_sync(websocket)

    async def _cleanup_connection(self, websocket: WebSocket):
        """从管理器移除连接并清理资源（事件循环中调用）。
        注意：可能由该连接自己的发送 task 在结束时调用，因此不 cancel 当前 task。"""
        if websocket in self._connections:
            self._connections.remove(websocket)
        self._roles.pop(websocket, None)
        self._last_recharge.pop(websocket, None)
        self._last_pm.pop(websocket, None)
        self._last_product_click.pop(websocket, None)
        self._last_card_push.pop(websocket, None)
        self._unbind_user(websocket)
        # 若发送 task 仍在运行（外部清理场景），取消它
        task = self._send_tasks.pop(websocket, None)
        if task is not None and not task.done() and task != asyncio.current_task():
            task.cancel()
        q = self._send_queues.pop(websocket, None)
        if q is not None:
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass
        logger.info(f"Live Commerce WebSocket 客户端已断开，当前连接数: {len(self._connections)}")

    def _cleanup_sync(self, websocket: WebSocket):
        """同步清理（无运行事件循环时兜底）"""
        if websocket in self._connections:
            self._connections.remove(websocket)
        self._roles.pop(websocket, None)
        self._last_recharge.pop(websocket, None)
        self._last_pm.pop(websocket, None)
        self._last_product_click.pop(websocket, None)
        self._last_card_push.pop(websocket, None)
        self._unbind_user(websocket)
        task = self._send_tasks.pop(websocket, None)
        q = self._send_queues.pop(websocket, None)
        logger.info(f"Live Commerce WebSocket 客户端已断开（同步清理），当前连接数: {len(self._connections)}")

    # ============================================================
    # 观众身份绑定与定向推送
    # ============================================================
    def _bind_user(self, websocket: WebSocket, user_id: str) -> None:
        """把连接绑定到已鉴权的观众账号（登录/注册成功后调用）。"""
        self._user_ids[websocket] = user_id
        self._user_conns.setdefault(user_id, set()).add(websocket)
        # 记入本场真实观众（复盘报告「本场真实观众数」的唯一来源）
        self._data_manager.note_session_viewer(user_id)
        self._sync_online_viewers()

    def _unbind_user(self, websocket: WebSocket) -> None:
        """解绑（断开连接时调用）。同一账号的其他连接不受影响。"""
        user_id = self._user_ids.pop(websocket, None)
        if user_id:
            conns = self._user_conns.get(user_id)
            if conns is not None:
                conns.discard(websocket)
                if not conns:
                    self._user_conns.pop(user_id, None)
        self._sync_online_viewers()

    def _sync_online_viewers(self) -> None:
        """把去重后的真实在线观众数告知 DataManager，由其重算并广播在线人数。"""
        self._data_manager.set_online_viewers(len(self._user_conns))

    async def _require_viewer(self, websocket: WebSocket, action: str) -> Optional[str]:
        """取本连接已绑定的观众身份。未登录则回 viewer_auth_required 并返回 None。

        **所有观众操作的身份一律走这里**，不再读 msg_data["userId"] ——
        旧实现采信客户端自报的 userId，填别人的 id 就能读其余额、订单并替他消费。
        """
        user_id = self._user_ids.get(websocket)
        if not user_id:
            await self._send(websocket, "viewer_auth_required", {
                "action": action,
                "message": "请先登录",
            })
            return None
        return user_id

    async def send_to_user(self, user_id: str, msg_type: str, data: dict) -> None:
        """给某观众的全部连接定向推送（从事件循环调用）。"""
        msg = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        for ws in list(self._user_conns.get(user_id, ())):
            self._put(ws, msg)

    async def _send_initial_state(self, websocket: WebSocket):
        """发送初始状态数据。

        **按角色裁剪**：观众连接只拿直播间公开展示的五个键（正是 viewer.html 的
        init_state 分支实际消费的那五个）。旧实现把 sessions（全部观众的私信会话）、
        settings（含敏感词表）、alerts、stats 一并下发给每个观众连接。
        """
        try:
            dm = self._data_manager
            role = self._roles.get(websocket, "viewer")
            live_status = dm.get_live_status()
            payload = {
                # role 必须下发：运营台 token 过期后若静默退化成只读观众端会极难排查，
                # 前端据此弹阻断层并跳登录页
                "role": role,
                # 观众只拿公开字段：totalAIReplies / totalPrivateMessages / totalDanmaku
                # 属于运营指标，不该随初始状态一起下发。
                "liveStatus": live_status if role == "host" else _viewer_safe_live_status(live_status),
                "currentProduct": _viewer_safe_current(dm, role),
                "products": _products_payload(dm, on_sale_only=(role != "host")),
                "hostProfile": dm.get_host_profile(),
                "allDanmaku": [d.model_dump() for d in dm.get_danmaku_list(100)],
                # 置顶弹幕是公开信息：主播和观众都要看到同一条置顶
                "pinnedDanmaku": dm.get_pinned_danmaku(),
            }
            if role == "host":
                payload.update({
                    "stats": dm.get_stats().model_dump(),
                    "settings": dm.get_settings().model_dump(),
                    "sessions": [s.model_dump() for s in dm.get_all_sessions()],
                    "alerts": [a.model_dump() for a in dm.alerts],
                    "highFreqQuestions": dm.get_high_freq_questions(),
                    # 只发真实观众评价：运营台的「用户评价管理」不掺模拟数据。
                    # （观众端要看全量评价走 get_reviews 这条消息，不受此处影响。）
                    "reviews": [r.model_dump() for r in dm.get_reviews(source="user")],
                    "onlineBreakdown": dm.get_online_breakdown(),
                    # 本场直播数据（商品点击 / 下单数量 / GMV），仅运营台可见
                    "liveRoomStats": dm.get_live_room_stats(),
                })
            await self._send(websocket, "init_state", payload)
        except Exception as e:
            logger.error(f"发送初始状态失败: {e}")

    async def _send_loop(self, ws: WebSocket, q: "asyncio.Queue[dict | None]"):
        """每连接专用发送循环：串行消费队列写 socket，避免并发写与事件循环堆积"""
        try:
            while True:
                item = await q.get()
                if item is None:
                    # None 为关闭信号
                    break
                try:
                    await ws.send_json(item)
                except Exception:
                    # 写失败：连接已失效，丢弃该连接上的剩余消息并结束
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            # 连接结束，从管理器移除并回收资源
            await self._cleanup_connection(ws)

    def _put(self, ws: WebSocket, msg: dict) -> bool:
        """线程安全地入队（后台线程调用）。返回是否成功放入。"""
        q = self._send_queues.get(ws)
        if q is None:
            return False
        try:
            q.put_nowait(msg)
            return True
        except Exception:
            return False

    async def _send(self, ws: WebSocket, msg_type: str, data: dict):
        """发送消息到单个客户端（从事件循环调用：入队，由发送 task 写出）"""
        msg = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        self._put(ws, msg)

    def _llm_endpoints_payload(self) -> dict:
        """端口列表的统一下发形态。

        一律经 `ai_endpoints.list_public()`：明文 apiKey 只活在
        `llm_endpoint_store` 和真正构造 client 的那一步，任何回给浏览器的
        路径都拿不到它。
        """
        active_id = llm_endpoint_store.get_active_id()
        return {
            "endpoints": ai_endpoints.list_public(),
            "activeId": active_id,
            "activeLabel": ai_endpoints.describe_active(),
        }

    async def broadcast(self, msg_type: str, data: dict):
        """广播消息到所有客户端（从事件循环调用）"""
        msg = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        for ws in list(self._connections):
            self._put(ws, msg)

    async def broadcast_to(self, role: str, msg_type: str, data: dict):
        """仅向指定角色的连接广播（从事件循环调用）。用于摄像头画面帧只推给观众端。"""
        msg = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        for ws in list(self._connections):
            if self._roles.get(ws) == role:
                self._put(ws, msg)

    def _on_data_event(self, msg: dict):
        """数据变更时的回调（从后台线程调用）：只做线程安全的入队，不直接写 socket。

        按 DataManager._broadcast 附在 msg 上的 "_toUser"/"_hostOnly" 标记分流；
        标记在**下发给客户端前剥离**，客户端看不到它们。
        这里不修改入参 msg —— 同一份 msg 会交给所有已注册回调，就地 pop 会坑到别的回调。
        """
        if not self._connections:
            return
        to_user = msg.get("_toUser")
        host_only = bool(msg.get("_hostOnly"))
        if to_user is None and not host_only:
            payload = msg
        else:
            payload = {k: v for k, v in msg.items() if k not in ("_toUser", "_hostOnly")}
        # 弹幕广播补上比赛文档里的扁平字段 sender / role（与原 userName / userId 并存）。
        # 角色取自弹幕本身的 userId，不采信发送方自报，伪造不了。
        if to_user is None and not host_only and msg.get("type") == "new_danmaku":
            d = dict(payload.get("data") or {})
            d["sender"] = d.get("userName", "")
            d["role"] = "host" if d.get("userId") == HOST_DANMAKU_USER_ID else "viewer"
            payload = {**payload, "data": d}
        # 观众版的同一事件：只对公开广播且类型在裁剪清单里时生成一次，循环里复用。
        viewer_payload = None
        if to_user is None and not host_only and msg.get("type") in _VIEWER_TRIMMED_EVENTS:
            viewer_payload = {
                **payload,
                "data": _viewer_safe_event(msg.get("type", ""), payload.get("data") or {}),
            }
        for ws in list(self._connections):
            # 运营台始终可见全部（它是工作台，订单/私信都要看）
            if self._roles.get(ws) != "host":
                if host_only:
                    continue
                if to_user is not None and self._user_ids.get(ws) != to_user:
                    continue
                if viewer_payload is not None:
                    self._put(ws, viewer_payload)
                    continue
            self._put(ws, payload)

    # ============================================================
    # 弹幕自动回复
    # ============================================================
    def _reply_semaphore_instance(self) -> asyncio.Semaphore:
        """惰性创建并发闸门（首次调用时必然处于运行中的事件循环内）"""
        if self._reply_semaphore is None:
            self._reply_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_DANMAKU_REPLIES)
        return self._reply_semaphore

    async def _generate_danmaku_reply(self, danmaku_id: str, content: str) -> str:
        """对一条弹幕生成回复并落库广播。

        智能 AI 优先（回复同样过敏感词校验），AI 关闭 / 无结果 / 回复违规时回退规则话术。
        两条路径都会 record_question 计入高频问题统计（真实/虚拟分开计），且互斥，不会重复计数。

        注意：内部可能 await LLM 最长 20s，观众端路径务必经 _spawn_danmaku_reply 走后台任务。
        """
        dm = self._data_manager
        # 真伪由弹幕记录本身决定，不采信调用方传入的值
        is_real = dm.danmaku_is_real(danmaku_id)
        reply = ""
        # 若开启"智能 AI 回复"，先尝试大模型生成（失败自动回退规则话术）
        if getattr(dm.settings.aiReply, "useLLM", True):
            try:
                from service.ai_live import ai_reply
                product = dm.get_current_product()
                ai_text = await ai_reply(content, product)
            except Exception as e:
                logger.warning("AI 弹幕回复异常，回退规则话术: %s", e)
                ai_text = None
            if ai_text:
                # AI 回复同样经敏感词检查，合规才落库
                is_violation, _reason = dm.check_sensitive(ai_text)
                if not is_violation:
                    dm.record_question(content, is_real=is_real)
                    dm.reply_danmaku(danmaku_id, ai_text)
                    reply = ai_text
        # 规则话术兜底（AI 关闭 / AI 无结果 / AI 回复违规时）
        if not reply:
            reply = dm.auto_reply_danmaku(danmaku_id, content, is_real=is_real)
        return reply

    def _spawn_danmaku_reply(self, danmaku_id: str, content: str) -> None:
        """把观众端弹幕的回复挪到后台任务执行，避免阻塞该连接的收包循环。"""
        async def _run():
            async with self._reply_semaphore_instance():
                try:
                    await self._generate_danmaku_reply(danmaku_id, content)
                except Exception as e:  # noqa: BLE001
                    logger.warning("观众端弹幕自动回复失败: %s", e)

        task = asyncio.create_task(_run())
        self._reply_tasks.add(task)
        task.add_done_callback(self._reply_tasks.discard)

    def _spawn_danmaku_summary_ai(self, websocket: WebSocket) -> None:
        """把 AI 弹幕摘要挪到后台任务，回包定向发给发起的那条连接。

        不 await：`_llm_chat` 最长 20 秒，在 handle_message 里 await 会让这条连接
        20 秒内收不到也发不出任何其它消息（含心跳与其它按钮）。与 _spawn_danmaku_reply
        同一范式 —— 复用同一个并发闸门，避免 AI 摘要把弹幕自动回复挤掉。
        """
        summary = self._data_manager.get_danmaku_summary()

        async def _run():
            try:
                async with self._reply_semaphore_instance():
                    payload = await self._build_danmaku_summary_ai(summary)
            except Exception as e:  # noqa: BLE001
                logger.warning("AI 弹幕总结失败: %s", e)
                payload = {"success": False, "message": "AI 摘要生成失败"}
            try:
                await self._send(websocket, "danmaku_summary_ai_result", payload)
            except Exception:  # noqa: BLE001
                pass  # 连接已断开，丢弃

        task = asyncio.create_task(_run())
        self._reply_tasks.add(task)
        task.add_done_callback(self._reply_tasks.discard)

    async def _build_danmaku_summary_ai(self, summary: dict) -> dict:
        """三级降级：无真实数据 → 直说没有；AI 关闭/失败 → 模板拼接；AI 可用 → 模型摘要。

        三种情况都返回 success=True（除了真出异常），前端据 `fallback` / `reason`
        决定是否打「离线摘要」角标 —— 摘要是"锦上添花"，不该让按钮报错。
        """
        from service.ai_live import build_summary_template, generate_danmaku_summary

        stats = (summary or {}).get("stats") or {}
        scope = (summary or {}).get("scope") or {}
        # 没有真实弹幕就**不生成**摘要 —— 绝不拿虚拟弹幕凑一段好看的话
        if not stats.get("realCount"):
            return {
                "success": True,
                "summary": None,
                "reason": "no_real_data",
                "fallback": False,
                "message": "本场还没有真实观众弹幕，暂不能生成总结",
                "stats": stats,
                "scope": scope,
            }
        if getattr(self._data_manager.settings.aiReply, "useLLM", True):
            try:
                text = await generate_danmaku_summary(summary)
            except Exception as e:  # noqa: BLE001
                logger.warning("AI 弹幕总结异常，回退模板: %s", e)
                text = None
            if text:
                return {"success": True, "summary": text, "fallback": False,
                        "stats": stats, "scope": scope}
        return {"success": True, "summary": build_summary_template(summary),
                "fallback": True, "stats": stats, "scope": scope}

    async def handle_message(self, websocket: WebSocket, raw_data: str):
        """处理收到的WebSocket消息"""
        try:
            data = json.loads(raw_data)
            msg_type = data.get("type", "")
            msg_data = data.get("data", {})
            dm = self._data_manager

            # 扁平弹幕结构兼容：{"type":"danmaku","content":"..","sender":"..","role":"viewer"}
            # 归一到内部 send_danmaku 处理。注意 sender / role 只是展示字段 ——
            # 真实身份仍由握手凭据决定，客户端改这两个字段伪造不了身份。
            if msg_type == "danmaku":
                flat = {k: v for k, v in data.items() if k not in ("type", "data")}
                msg_data = {**flat, **msg_data}
                msg_type = "send_danmaku"

            # 角色闸门（fail-closed）：未列入观众白名单的消息类型一律要求 host。
            # 新增消息类型若忘记归类，默认是「观众不可调用」，而不是默认放开。
            if self._roles.get(websocket) != "host" and msg_type not in _VIEWER_ALLOWED:
                logger.warning("观众连接尝试调用主播专用消息 %s，已拒绝", msg_type)
                await self._send(websocket, "error", {
                    "code": "forbidden",
                    "message": f"无权调用: {msg_type}",
                })
                return

            if msg_type == "start_live":
                result = dm.start_live()
                await self._send(websocket, "start_live_result", result)

            elif msg_type == "pause_live":
                result = dm.pause_live()
                await self._send(websocket, "pause_live_result", result)

            elif msg_type == "resume_live":
                result = dm.resume_live()
                await self._send(websocket, "resume_live_result", result)

            elif msg_type == "end_live":
                result = dm.end_live()
                await self._send(websocket, "end_live_result", result)

            elif msg_type == "switch_stage":
                stage_key = msg_data.get("stage", "")
                result = dm.switch_stage(stage_key)
                await self._send(websocket, "switch_stage_result", result)

            elif msg_type == "set_product":
                product_id = msg_data.get("productId", "")
                product, reason = dm.set_current_product(product_id)
                await self._send(websocket, "set_product_result", {
                    "success": product is not None,
                    "product": product.model_dump() if product else None,
                    # 必须把原因带回去：「商品已下架，请先上架再讲解」和「商品不存在」
                    # 是两种完全不同的处置，笼统回一句「切换失败」等于没提示。
                    "message": reason,
                })

            elif msg_type == "add_product":
                product = ProductItem(**msg_data.get("product", {}))
                dm.add_product(product)
                await self._send(websocket, "add_product_result", {
                    "success": True,
                    "product": product.model_dump(),
                })

            elif msg_type == "save_product":
                product_data = dict(msg_data.get("product", {}))
                # 前端新建商品时 productId 传 null，而 ProductItem.productId 是必填 str，
                # 直接构造会抛 ValidationError → 归一成空串，交给下面的分支生成新 ID
                if product_data.get("productId") is None:
                    product_data["productId"] = ""
                product_id = product_data.get("productId", "")
                # 若指定了 productId 且该商品已存在 → 更新；否则（新增）一律走 add_product
                if product_id and dm.get_product(product_id):
                    dm.update_product(product_id, product_data)
                else:
                    product = ProductItem(**product_data)
                    # 生成不与现有商品冲突的新 ID
                    if not product.productId:
                        _n = 1
                        while f"P{_n:03d}" in dm.products:
                            _n += 1
                        product.productId = f"P{_n:03d}"
                    dm.add_product(product)
                await self._send(websocket, "save_product_result", {"success": True})

            elif msg_type == "delete_product":
                product_id = msg_data.get("productId", "")
                ok = dm.delete_product(product_id)
                await self._send(websocket, "delete_product_result", {"success": ok})

            elif msg_type == "on_sale_product":
                product_id = msg_data.get("productId", "")
                on_sale = bool(msg_data.get("onSale", False))
                product = dm.set_on_sale(product_id, on_sale)
                await self._send(websocket, "on_sale_product_result", {
                    "success": product is not None,
                    "product": product.model_dump() if product else None,
                })

            # ========== 主播端 · 直播间商品挂载 / 商品卡片推送 ==========
            elif msg_type == "mount_live_product":
                product_id = str(msg_data.get("productId", "") or "")
                product, reason = dm.mount_live_product(product_id)
                await self._send(websocket, "mount_live_product_result", {
                    "success": product is not None,
                    "product": product.model_dump() if product else None,
                    "message": "商品已挂载到直播间" if product else (reason or "挂载失败"),
                })

            elif msg_type == "unmount_live_product":
                product_id = str(msg_data.get("productId", "") or "")
                product, reason = dm.unmount_live_product(product_id)
                await self._send(websocket, "unmount_live_product_result", {
                    "success": product is not None,
                    "productId": product_id,
                    "message": "商品已从直播间撤下" if product else (reason or "下架失败"),
                })

            elif msg_type == "push_product_card":
                product_id = str(msg_data.get("productId", "") or "")
                now_ts = time.monotonic()
                if now_ts - self._last_card_push.get(websocket, 0.0) < _CARD_PUSH_MIN_INTERVAL:
                    await self._send(websocket, "push_product_card_result", {
                        "success": False,
                        "message": "推送太频繁，请稍后再试",
                    })
                    return
                card, reason = dm.push_product_card(product_id)
                if card is not None:
                    self._last_card_push[websocket] = now_ts
                await self._send(websocket, "push_product_card_result", {
                    "success": card is not None,
                    "card": card,
                    "message": "商品卡片已推送给所有在线观众" if card else (reason or "推送失败"),
                })

            elif msg_type == "generate_script":
                product_id = msg_data.get("productId", "")
                script_type = msg_data.get("scriptType", "product_intro")
                stage = msg_data.get("stage", "")
                product = dm.get_product(product_id) if product_id else dm.get_current_product()
                script = None
                try:
                    from service.ai_live import generate_script
                    script = await generate_script(product, script_type, stage)
                except Exception as e:
                    logger.warning("话术生成异常: %s", e)
                    script = None
                # AI 失败时给一段内置兜底话术，保证功能始终可用
                if not script:
                    script = _fallback_script(product, script_type)
                if product:
                    dm.record_script(product.productId, product.name, script_type, script)
                await self._send(websocket, "generate_script_result", {
                    "success": True,
                    "script": script,
                    "productId": product.productId if product else "",
                    "productName": product.name if product else "",
                    "scriptType": script_type,
                })

            elif msg_type == "get_scripts_history":
                history = dm.get_scripts_history()
                await self._send(websocket, "get_scripts_history_result", {"history": history})

            elif msg_type == "get_danmaku_history":
                # 新观众进入直播间拉取本场历史弹幕（最近 20 条）+ 当前置顶
                history = dm.get_danmaku_history(msg_data.get("limit", 20))
                await self._send(websocket, "danmaku_history", {
                    "history": [d.model_dump() for d in history],
                    "pinned": dm.get_pinned_danmaku(),
                })

            elif msg_type == "pin_danmaku":
                # 主播专用（不在 _VIEWER_ALLOWED，观众发这条会被角色闸门拦下）
                result = dm.pin_danmaku(msg_data.get("danmakuId", ""))
                await self._send(websocket, "pin_danmaku_result", result)

            elif msg_type == "unpin_danmaku":
                result = dm.unpin_danmaku()
                await self._send(websocket, "unpin_danmaku_result", result)

            elif msg_type == "send_danmaku":
                content = msg_data.get("content", "")
                # 身份一律由服务端确定：运营台用主播身份，观众必须是已登录账号。
                # 旧实现直接采信 msg_data 里的 userId/userName，任何人都能冒名发言。
                if self._roles.get(websocket) == "host":
                    hp = dm.get_host_profile()
                    user_id = HOST_DANMAKU_USER_ID
                    user_name = msg_data.get("userName") or hp.get("hostName") or "主播"
                    avatar = msg_data.get("avatar") or hp.get("hostAvatar") or ""
                else:
                    user_id = await self._require_viewer(websocket, "send_danmaku")
                    if not user_id:
                        return
                    acc = dm.get_viewer(user_id)
                    user_name = (acc.userName if acc else "") or "观众"
                    avatar = (acc.avatar if acc else "") or ""
                if content.strip():
                    is_question = is_question_like(content)
                    danmaku_item = DanmakuItem(
                        danmakuId=f"dm_ws_{int(time.time()*1000)}",
                        userId=user_id,
                        userName=user_name,
                        avatar=avatar,
                        content=content,
                        timestamp=datetime.now().isoformat(),
                        isVirtual=False,
                        isQuestion=is_question,
                        isReplied=False,
                    )
                    dm.add_danmaku(danmaku_item)
                    await self._send(websocket, "send_danmaku_result", {
                        "success": True,
                        "danmaku": danmaku_item.model_dump(),
                    })
                    # 观众端实时问答：仅问句触发 AI 回复，且必须走后台任务
                    # （收包循环是 inline await，一次 LLM 调用最长 20s 会卡死该连接）
                    if is_question:
                        self._spawn_danmaku_reply(danmaku_item.danmakuId, content)
                else:
                    await self._send(websocket, "send_danmaku_result", {
                        "success": False,
                        "message": "弹幕内容为空",
                    })

            elif msg_type == "send_danmaku_reply":
                danmaku_id = msg_data.get("danmakuId", "")
                content = msg_data.get("content", "")
                # 先确保弹幕在列表中（前端可能直接调用send_danmaku_reply）
                if not any(d.danmakuId == danmaku_id for d in dm.danmaku_list):
                    danmaku_item = DanmakuItem(
                        danmakuId=danmaku_id,
                        userId=msg_data.get("userId", "host_001"),
                        userName=msg_data.get("userName", "主播"),
                        avatar=msg_data.get("avatar", ""),
                        content=content,
                        timestamp=datetime.now().isoformat(),
                        isVirtual=False,
                        isQuestion=is_question_like(content),
                        isReplied=False,
                    )
                    dm.add_danmaku(danmaku_item)
                reply = await self._generate_danmaku_reply(danmaku_id, content)
                await self._send(websocket, "danmaku_reply_result", {
                    "success": bool(reply),
                    "reply": reply,
                })

            elif msg_type == "send_private_msg":
                msg = PrivateMessageItem(**msg_data.get("message", {}))
                dm.add_private_message(msg)
                await self._send(websocket, "send_private_msg_result", {"success": True})

            elif msg_type == "auto_reply_private":
                user_id = msg_data.get("userId", "")
                content = msg_data.get("content", "")
                reply = ""
                # 智能 AI 回复优先（失败回退规则）
                if getattr(dm.settings.aiReply, "useLLM", True):
                    try:
                        from service.ai_live import ai_reply
                        product = dm.get_current_product()
                        ai_text = await ai_reply(content, product)
                    except Exception as e:
                        logger.warning("AI 私信回复异常，回退规则话术: %s", e)
                        ai_text = None
                    if ai_text:
                        is_violation, _reason = dm.check_sensitive(ai_text)
                        if not is_violation:
                            reply = ai_text
                if not reply:
                    reply = dm.auto_reply_private(user_id, content)
                if reply:
                    # 创建AI回复消息
                    session = dm._get_or_create_session(user_id, msg_data.get("userName", "用户"))
                    reply_msg = PrivateMessageItem(
                        messageId=f"auto_{int(datetime.now().timestamp()*1000)}",
                        userId=user_id,
                        userName=session.userName,
                        avatar=session.avatar,
                        content=reply,
                        timestamp=datetime.now().isoformat(),
                        isFromUser=False,
                        replyType="ai",
                    )
                    dm.add_private_message(reply_msg)
                await self._send(websocket, "auto_reply_private_result", {"success": True, "reply": reply})

            elif msg_type == "switch_to_human":
                user_id = msg_data.get("userId", "")
                dm.update_session_status(user_id, "human_reception")
                await self._send(websocket, "switch_to_human_result", {"success": True})

            elif msg_type == "switch_to_ai":
                user_id = msg_data.get("userId", "")
                dm.update_session_status(user_id, "ai_reception")
                await self._send(websocket, "switch_to_ai_result", {"success": True})

            elif msg_type == "blacklist_user":
                user_id = msg_data.get("userId", "")
                user_name = msg_data.get("userName", "")
                reason = msg_data.get("reason", "")
                dm.add_to_blacklist(user_id, user_name, reason, "主播")
                await self._send(websocket, "blacklist_user_result", {"success": True})

            elif msg_type == "unblacklist_user":
                user_id = msg_data.get("userId", "")
                dm.remove_from_blacklist(user_id)
                await self._send(websocket, "unblacklist_user_result", {"success": True})

            elif msg_type == "mark_read":
                user_id = msg_data.get("userId", "")
                dm.mark_user_messages_read(user_id)
                await self._send(websocket, "mark_read_result", {"success": True})

            elif msg_type == "generate_report":
                report = dm.generate_report()
                await self._send(websocket, "generate_report_result", {"success": True, "report": report})

            elif msg_type == "update_settings":
                settings_data = msg_data.get("settings", {})
                dm.update_settings(settings_data)
                await self._send(websocket, "update_settings_result", {"success": True})

            elif msg_type == "add_preset":
                preset = PresetPhraseItem(**msg_data.get("preset", {}))
                dm.add_preset(preset)
                await self._send(websocket, "add_preset_result", {"success": True, "preset": preset.model_dump()})

            elif msg_type == "update_preset":
                preset_id = msg_data.get("presetId", "")
                update_data = msg_data.get("data", {})
                dm.update_preset(preset_id, update_data)
                await self._send(websocket, "update_preset_result", {"success": True})

            elif msg_type == "delete_preset":
                preset_id = msg_data.get("presetId", "")
                dm.delete_preset(preset_id)
                await self._send(websocket, "delete_preset_result", {"success": True})

            elif msg_type == "add_quick_reply":
                qr = QuickReplyItem(**msg_data.get("quickReply", {}))
                dm.add_quick_reply(qr)
                await self._send(websocket, "add_quick_reply_result", {"success": True})

            elif msg_type == "delete_quick_reply":
                qr_id = msg_data.get("quickReplyId", "")
                dm.delete_quick_reply(qr_id)
                await self._send(websocket, "delete_quick_reply_result", {"success": True})

            elif msg_type == "import_high_freq":
                questions = msg_data.get("questions", [])
                count = dm.import_to_pending_presets(questions)
                await self._send(websocket, "import_high_freq_result", {"success": True, "count": count})

            elif msg_type == "get_user_messages":
                user_id = msg_data.get("userId", "")
                msgs = dm.get_user_messages(user_id)
                await self._send(websocket, "get_user_messages_result", {
                    "userId": user_id,
                    "messages": [m.model_dump() for m in msgs],
                })

            elif msg_type == "get_inbox_threads":
                # 私信收件箱左列：只含真实观众/老账号的会话（模拟 u00x 已在 dm 层滤掉）。
                # 在线状态取自本管理器的连接表 —— 数据管理器看不到 WebSocket。
                threads = dm.get_inbox_threads(set(self._user_conns.keys()))
                await self._send(websocket, "get_inbox_threads_result", {
                    "threads": threads,
                    "unreadTotal": sum(t["unreadCount"] for t in threads),
                })

            elif msg_type == "get_products":
                # 观众只拿已上架的；运营台拿全量（商品管理页要能看见并操作已下架商品）
                is_host = self._roles.get(websocket, "viewer") == "host"
                await self._send(websocket, "get_products_result", {
                    "products": _products_payload(dm, on_sale_only=not is_host),
                })

            elif msg_type == "get_live_products":
                # 直播间挂载商品（= 已上架），主播与观众拿同一份公开数据
                products = _products_payload(dm, on_sale_only=True)
                await self._send(websocket, "get_live_products_result", {
                    "products": products,
                    "total": len(products),
                })

            elif msg_type == "get_reviews":
                sentiment = msg_data.get("sentiment", "")
                product_id = msg_data.get("productId", "")
                keyword = msg_data.get("keyword", "")
                source = msg_data.get("source", "")
                reviews = dm.get_reviews(sentiment, product_id, keyword, source)
                await self._send(websocket, "get_reviews_result", {
                    "reviews": [r.model_dump() for r in reviews],
                })

            elif msg_type == "get_high_freq":
                # 默认只回真实观众的问题；includeMock=True 时并入虚拟弹幕，每行带 isReal
                include_mock = bool(msg_data.get("includeMock", False))
                questions = dm.get_high_freq_questions(include_mock=include_mock)
                await self._send(websocket, "get_high_freq_result", {
                    "questions": questions,
                    "includeMock": include_mock,
                    "mockQuestionCount": len(dm.virtual_freq_questions),
                })

            elif msg_type == "get_danmaku_summary":
                # 弹幕总结的统计部分：毫秒级同步返回（host-only，未列入白名单）
                await self._send(websocket, "danmaku_summary_result",
                                 dm.get_danmaku_summary())

            elif msg_type == "get_review_summary":
                # 评价总结：纯统计、毫秒级同步返回（host-only，未列入白名单）。
                # 与弹幕总结不同，这里**没有** AI 摘要的配套消息 —— 按产品决策，
                # 评价总结只要统计图表，数字由 get_review_summary() 直接算好。
                await self._send(websocket, "review_summary_result",
                                 dm.get_review_summary())

            elif msg_type == "generate_danmaku_summary_ai":
                # AI 摘要最长 20s —— 必须走后台任务，否则这条连接的收包循环会被卡死
                self._spawn_danmaku_summary_ai(websocket)

            elif msg_type == "get_presets":
                category = msg_data.get("category", "")
                presets = list(dm.presets.values())
                if category:
                    presets = [p for p in presets if p.category == category]
                await self._send(websocket, "get_presets_result", {
                    "enabled": [p.model_dump() for p in presets if not p.isPending],
                    "pending": [p.model_dump() for p in presets if p.isPending],
                })

            elif msg_type == "get_blacklist":
                blacklist = dm.get_blacklist()
                await self._send(websocket, "get_blacklist_result", {
                    "blacklist": [b.model_dump() for b in blacklist],
                })

            elif msg_type == "get_stats":
                await self._send(websocket, "get_stats_result", {
                    "stats": dm.get_stats().model_dump(),
                    "liveStatus": dm.get_live_status(),
                })

            elif msg_type == "get_sessions":
                sessions = dm.get_all_sessions()
                await self._send(websocket, "get_sessions_result", {
                    "sessions": [s.model_dump() for s in sessions],
                })

            elif msg_type == "get_product_detail":
                product_id = msg_data.get("productId", "")
                product = dm.get_product(product_id)
                # 纵深防御，不是主闸：两个前端目前都没调这条消息（它只在 _VIEWER_ALLOWED
                # 里出现）。真正挡住观众的是列表过滤 + 下单/支付校验。
                if product is not None and self._roles.get(websocket) != "host" \
                        and not product.isOnSale:
                    product = None
                await self._send(websocket, "get_product_detail_result", {
                    "product": product.model_dump() if product else None,
                })

            elif msg_type == "export_data":
                export_type = msg_data.get("type", "")
                export_format = msg_data.get("format", "json")
                data = self._prepare_export_data(export_type)
                await self._send(websocket, "export_data_result", {
                    "success": True,
                    "data": data,
                    "format": export_format,
                })

            elif msg_type == "save_settings":
                settings_data = msg_data.get("settings", {})
                dm.update_settings(settings_data)
                await self._send(websocket, "save_settings_result", {"success": True})

            elif msg_type == "get_settings":
                await self._send(websocket, "get_settings_result", {
                    "settings": dm.get_settings().model_dump(),
                })

            # ── 大模型接入端口 ──────────────────────────────────────────
            # 这一组刻意**不进 _VIEWER_ALLOWED**：白名单 fail-closed，未列出的
            # 类型只有 host 能调，端口配置（含密钥掩码）不该给观众看。
            #
            # 每个动作都带回刷新后的完整列表：前端改完不用再补一次 get，也避免
            # 「保存成功但列表还是旧的」这种不一致。
            elif msg_type == "get_llm_endpoints":
                await self._send(websocket, "get_llm_endpoints_result",
                                 self._llm_endpoints_payload())

            elif msg_type == "save_llm_endpoint":
                try:
                    saved = llm_endpoint_store.upsert_endpoint(msg_data.get("endpoint") or {})
                except ValueError as e:
                    await self._send(websocket, "save_llm_endpoint_result", {
                        "success": False,
                        "message": str(e),
                    })
                else:
                    # 只记名字和地址，**绝不记 apiKey**
                    logger.info(
                        "运营台保存大模型端口: %s (%s @ %s)",
                        saved.get("name"), saved.get("model"), saved.get("baseUrl"),
                    )
                    payload = self._llm_endpoints_payload()
                    payload.update({"success": True, "id": saved["id"]})
                    await self._send(websocket, "save_llm_endpoint_result", payload)

            elif msg_type == "delete_llm_endpoint":
                endpoint_id = str(msg_data.get("id") or "")
                ok = llm_endpoint_store.delete_endpoint(endpoint_id)
                if ok:
                    logger.info("运营台删除大模型端口: %s", endpoint_id)
                payload = self._llm_endpoints_payload()
                payload.update({
                    "success": ok,
                    "message": "" if ok else "端口不存在（可能已被删除）",
                })
                await self._send(websocket, "delete_llm_endpoint_result", payload)

            elif msg_type == "set_active_llm_endpoint":
                # 空串 = 不使用，回退 .env 默认模型
                endpoint_id = str(msg_data.get("id") or "")
                ok = llm_endpoint_store.set_active(endpoint_id)
                if ok:
                    logger.info(
                        "运营台切换当前模型端口: %s",
                        endpoint_id or "(不使用，回退 .env 默认)",
                    )
                payload = self._llm_endpoints_payload()
                payload.update({
                    "success": ok,
                    "message": "" if ok else "端口不存在",
                })
                await self._send(websocket, "set_active_llm_endpoint_result", payload)

            elif msg_type == "test_llm_endpoint":
                result = await ai_endpoints.test_endpoint(msg_data.get("endpoint") or {})
                await self._send(websocket, "test_llm_endpoint_result", result)

            elif msg_type == "request_report":
                report = dm.generate_report()
                await self._send(websocket, "request_report_result", {
                    "success": True,
                    "report": report,
                })

            elif msg_type == "mark_review_handled":
                review_id = msg_data.get("reviewId", "")
                # 回真实结果：旧写法恒定 success=True，评价 ID 不存在时前端也显示"已处理"
                ok = dm.mark_review_handled(review_id)
                await self._send(websocket, "mark_review_handled_result", {
                    "success": ok,
                    "reviewId": review_id,
                    "message": "已标记为已处理" if ok else "评价不存在",
                })

            elif msg_type == "get_product_reviews":
                product_id = msg_data.get("productId", "")
                reviews = dm.get_product_reviews(product_id)
                await self._send(websocket, "get_product_reviews_result", {
                    "productId": product_id,
                    "reviews": [r.model_dump() for r in reviews],
                })

            elif msg_type == "get_orders":
                status = msg_data.get("status", "")
                limit = int(msg_data.get("limit", 50))
                orders = dm.get_orders(status, limit)
                await self._send(websocket, "get_orders_result", {
                    "orders": [o.model_dump() for o in orders],
                })

            elif msg_type == "get_session_orders":
                # 控制台的「本场订单」：本场 + 真实观众口径（默认 real_only=True）。
                # 列表与汇总一次带回 —— 前端那条 30 秒一次也没有的轮询就不必存在了，
                # 订单事件本身是广播的（order_new / order_updated）。
                # 刻意**不**进 _VIEWER_ALLOWED：白名单是 fail-closed 的，未列出的类型
                # 只有 host 可调，本场成交不该给观众看。
                limit = int(msg_data.get("limit", 50))
                orders = dm.get_session_orders(limit=limit)
                await self._send(websocket, "get_session_orders_result", {
                    "orders": [o.model_dump() for o in orders],
                    "stats": dm.get_session_order_stats(),
                })

            elif msg_type == "get_order_stats":
                # 这条消息不在观众白名单里（仅运营台可达），故直接取真实口径：
                # 运营台的成交面板只算真实观众订单，模拟单单独披露数量。
                await self._send(websocket, "get_order_stats_result", {
                    "stats": dm.get_order_stats(real_only=True),
                })

            elif msg_type == "get_live_room_stats":
                # 本场商品点击 / 下单数量 / GMV。**不进观众白名单**：
                # 点击明细与转化率属于主播侧经营数据。
                await self._send(websocket, "get_live_room_stats_result", {
                    "stats": dm.get_live_room_stats(),
                })

            # ⚠️ 这里曾有 simulate_orders / buy_product 两条「凭空造单」的消息：
            # 前者由运营台一颗按钮触发、一次生成 5 张随机成交单，后者给任意连接发一张
            # userId="" 的已付款假单。两者都会直接改商品 stock/soldCount 并广播 order_new，
            # 于是运营台的成交数据里混进了从未发生过的交易。
            # 现已整体移除（连同 data_manager 的 batch_simulate_orders / _simulate_initial_orders）。
            # 别再以任何形式接回来：成交必须来自真实注册观众的 place_order → pay_order。

            elif msg_type == "update_order_status":
                order_id = msg_data.get("orderId", "")
                status = msg_data.get("status", "")
                order = dm.update_order_status(order_id, status)
                await self._send(websocket, "update_order_status_result", {
                    "success": order is not None,
                    "message": "订单状态已更新" if order else "操作未生效（当前状态不允许该操作）",
                    "order": order.model_dump() if order else None,
                })

            # ========== 观众端 · 账户/购物/订单/评价 ==========
            elif msg_type == "viewer_register":
                nickname = msg_data.get("nickname") or ""
                password = msg_data.get("password") or ""
                avatar = msg_data.get("avatar") or ""
                try:
                    reg = await viewer_auth.register_viewer(nickname, password, avatar)
                except ValueError as e:
                    await self._send(websocket, "viewer_register_result", {
                        "success": False, "message": str(e),
                    })
                else:
                    new_user = reg["user"]
                    # 钱包（余额）仍存在 viewers_data.json，以 userId 关联，读写路径不变
                    acc = dm.get_or_create_viewer(new_user["userId"], new_user["userName"], avatar)
                    self._bind_user(websocket, new_user["userId"])
                    await self._send(websocket, "viewer_register_result", {
                        "success": True,
                        "token": reg["token"],
                        "user": {
                            "userId": new_user["userId"],
                            "userName": acc.userName if acc else new_user["userName"],
                            "avatar": (acc.avatar if acc else "") or avatar,
                            "balance": acc.balance if acc else 0.0,
                            "registerTime": acc.registerTime if acc else "",
                        },
                        "orders": [],
                        "messages": [],
                    })

            elif msg_type == "viewer_logout":
                self._unbind_user(websocket)
                await self._send(websocket, "viewer_logout_result", {"success": True})

            elif msg_type == "viewer_login":
                """凭据登录：`{token}` 续登 或 `{nickname, password}` 首次登录。

                不再隐式建号 —— 旧实现拿客户端自报的 userId 直接建档，身份可伪造。
                登录成功后把**本连接**绑定到该账号，后续所有操作从绑定身份取 userId。
                """
                token = (msg_data.get("token") or "").strip()
                nickname = msg_data.get("nickname") or ""
                password = msg_data.get("password") or ""
                try:
                    if token:
                        user_id = viewer_auth.verify_viewer_token(token)
                        if not user_id:
                            raise ValueError("登录已过期，请重新登录")
                        row = await viewer_auth.get_viewer_row(user_id)
                        if not row:
                            raise ValueError("账号不存在")
                        user = dict(row, balance=0.0)
                        token = viewer_auth.issue_token(user_id, row["userName"], row["avatar"])
                    else:
                        auth_res = await viewer_auth.authenticate_viewer(nickname, password)
                        user_id = auth_res["user"]["userId"]
                        user = dict(auth_res["user"], balance=0.0)
                        token = auth_res["token"]
                except ValueError as e:
                    await self._send(websocket, "viewer_login_result", {
                        "success": False, "message": str(e),
                    })
                    return

                acc = dm.get_or_create_viewer(user_id, user["userName"], user.get("avatar") or "")
                if acc:
                    user["balance"] = acc.balance
                    user["userName"] = acc.userName or user["userName"]
                    user["avatar"] = acc.avatar or user.get("avatar") or ""
                self._bind_user(websocket, user_id)
                await self._send(websocket, "viewer_login_result", {
                    "success": True,
                    "token": token,
                    "user": user,
                    "orders": [o.model_dump() for o in dm.get_user_orders(user_id)],
                    "messages": [m.model_dump() for m in dm.get_user_messages(user_id)],
                })

            elif msg_type == "update_viewer_profile":
                # 昵称即登录标识，改昵称要同步改 viewer_accounts 并保证唯一
                user_id = await self._require_viewer(websocket, "update_viewer_profile")
                if not user_id:
                    return
                user_name = (msg_data.get("userName") or "").strip()
                avatar = msg_data.get("avatar") or ""
                try:
                    if user_name:
                        await viewer_auth.rename_viewer(user_id, user_name)
                    await viewer_auth.set_viewer_avatar(user_id, avatar)
                except ValueError as e:
                    await self._send(websocket, "update_viewer_profile_result", {
                        "success": False, "message": str(e),
                    })
                    return
                acc = dm.update_viewer_profile(user_id, user_name, avatar)
                if acc is None:
                    await self._send(websocket, "update_viewer_profile_result", {
                        "success": False, "message": "账户不存在",
                    })
                else:
                    await self._send(websocket, "update_viewer_profile_result", {
                        "success": True,
                        "message": "资料已更新",
                        "user": {
                            "userId": acc.userId,
                            "userName": acc.userName,
                            "avatar": acc.avatar,
                            "balance": acc.balance,
                            "registerTime": acc.registerTime,
                        },
                    })

            elif msg_type == "update_host_profile":
                # 主播/运营台更新对外昵称头像：持久化到 settings 并广播给所有在线端
                host_name = (msg_data.get("hostName") or "").strip() or "主播"
                host_avatar = msg_data.get("hostAvatar") or ""
                dm.update_settings({"hostName": host_name[:20], "hostAvatar": host_avatar})
                hp = dm.get_host_profile()
                await self.broadcast("host_profile", hp)
                await self._send(websocket, "update_host_profile_result", {
                    "success": True,
                    "message": "主播资料已更新",
                    "hostProfile": hp,
                })

            elif msg_type == "recharge":
                user_id = await self._require_viewer(websocket, "recharge")
                if not user_id:
                    return
                try:
                    amount = float(msg_data.get("amount", 0) or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                if not (_RECHARGE_MIN <= amount <= _RECHARGE_MAX):
                    await self._send(websocket, "recharge_result", {
                        "success": False,
                        "message": f"单次充值金额需在 {_RECHARGE_MIN:.0f}~{_RECHARGE_MAX:.0f} 之间",
                        "balance": (dm.get_viewer(user_id).balance if dm.get_viewer(user_id) else 0.0),
                    })
                    return
                # 同一连接 1 秒内只允许一次，防前端连点重复入账
                now_ts = time.monotonic()
                if now_ts - self._last_recharge.get(websocket, 0.0) < _RECHARGE_MIN_INTERVAL:
                    await self._send(websocket, "recharge_result", {
                        "success": False,
                        "message": "操作过于频繁，请稍后再试",
                        "balance": (dm.get_viewer(user_id).balance if dm.get_viewer(user_id) else 0.0),
                    })
                    return
                self._last_recharge[websocket] = now_ts
                new_balance = dm.recharge_viewer(user_id, amount)
                await self._send(websocket, "recharge_result", {
                    "success": True,
                    "balance": new_balance,
                })

            elif msg_type == "click_live_product":
                # 观众端商品点击埋点：打开详情 / 点击主播推送的卡片时上报。
                # 只统计 viewer 角色连接 —— 运营台浏览商品不算观众点击。
                product_id = str(msg_data.get("productId", "") or "")
                role = self._roles.get(websocket, "viewer")
                if role != "viewer":
                    await self._send(websocket, "click_live_product_result", {
                        "success": False,
                        "counted": False,
                        "message": "仅观众端点击计入统计",
                    })
                    return
                now_ts = time.monotonic()
                if now_ts - self._last_product_click.get(websocket, 0.0) < _PRODUCT_CLICK_MIN_INTERVAL:
                    await self._send(websocket, "click_live_product_result", {
                        "success": True,
                        "counted": False,
                        "message": "点击太频繁，已忽略",
                    })
                    return
                self._last_product_click[websocket] = now_ts
                result = dm.record_product_click(
                    product_id, self._user_ids.get(websocket) or ""
                )
                await self._send(websocket, "click_live_product_result", result)

            elif msg_type == "place_order":
                # 观众下单：生成「待付款」订单（不扣钱包/库存），随后由 pay_order 付款
                user_id = await self._require_viewer(websocket, "place_order")
                if not user_id:
                    return
                product_id = msg_data.get("productId", "")
                try:
                    quantity = max(1, int(msg_data.get("quantity", 1) or 1))
                except (TypeError, ValueError):
                    quantity = 1
                acc = dm.get_viewer(user_id)
                user_name = (acc.userName if acc else "") or "观众"
                order, reason = dm.create_viewer_order(product_id, user_id, user_name, quantity)
                await self._send(websocket, "place_order_result", {
                    "success": order is not None,
                    # 观众端 onPlaceOrderResult 直接 toast 这个 message，所以具体原因
                    # （「商品已下架」/「库存不足」）能原样透到观众眼前。
                    "message": "下单成功，请尽快付款" if order else (reason or "下单失败"),
                    "order": order.model_dump() if order else None,
                })

            elif msg_type == "pay_order":
                user_id = await self._require_viewer(websocket, "pay_order")
                if not user_id:
                    return
                order_id = msg_data.get("orderId", "")
                order, reason = dm.pay_order(order_id, user_id)
                acc = dm.get_viewer(user_id)
                await self._send(websocket, "pay_order_result", {
                    "success": order is not None,
                    "message": "支付成功" if order else (reason or "支付失败"),
                    "order": order.model_dump() if order else None,
                    "balance": acc.balance if acc else 0.0,
                })

            elif msg_type == "confirm_receipt":
                user_id = await self._require_viewer(websocket, "confirm_receipt")
                if not user_id:
                    return
                order_id = msg_data.get("orderId", "")
                order, reason = dm.confirm_receipt(order_id, user_id)
                await self._send(websocket, "confirm_receipt_result", {
                    "success": order is not None,
                    "message": "已确认收货" if order else (reason or "操作失败"),
                    "order": order.model_dump() if order else None,
                })

            elif msg_type == "cancel_order":
                user_id = await self._require_viewer(websocket, "cancel_order")
                if not user_id:
                    return
                order_id = msg_data.get("orderId", "")
                order, reason = dm.cancel_viewer_order(order_id, user_id)
                await self._send(websocket, "cancel_order_result", {
                    "success": order is not None,
                    "message": "订单已取消" if order else (reason or "操作失败"),
                    "order": order.model_dump() if order else None,
                })

            elif msg_type == "submit_review":
                user_id = await self._require_viewer(websocket, "submit_review")
                if not user_id:
                    return
                product_id = msg_data.get("productId", "")
                order_id = msg_data.get("orderId", "") or ""
                acc = dm.get_viewer(user_id)
                user_name = (acc.userName if acc else "") or "观众"
                content = msg_data.get("content", "")
                try:
                    rating = max(1, min(5, int(msg_data.get("rating", 5) or 5)))
                except (TypeError, ValueError):
                    rating = 5
                review, reason = dm.submit_review(
                    product_id, user_id, user_name, content, rating, order_id
                )
                await self._send(websocket, "submit_review_result", {
                    "success": review is not None,
                    "message": "评价成功" if review else (reason or "评价失败"),
                    "review": review.model_dump() if review else None,
                })

            elif msg_type == "get_user_orders":
                user_id = await self._require_viewer(websocket, "get_user_orders")
                if not user_id:
                    return
                status = msg_data.get("status", "") or ""
                orders = dm.get_user_orders(user_id, status)
                await self._send(websocket, "get_user_orders_result", {
                    "orders": [o.model_dump() for o in orders],
                })

            elif msg_type == "get_my_messages":
                # 观众拉自己的私信列表
                user_id = await self._require_viewer(websocket, "get_my_messages")
                if not user_id:
                    return
                await self._send(websocket, "get_my_messages_result", {
                    "messages": [m.model_dump() for m in dm.get_user_messages(user_id)],
                })

            elif msg_type == "send_host_msg":
                # 观众 → 主播私信。**纯人工链路：本 handler 绝不调 ai_reply**，
                # 回复一律由主播在运营台收件箱亲自发出（send_viewer_msg）。
                # 与 get_my_messages 同属观众侧，但它是观众的主动发言，
                # 所以额外有长度与频率两道闸。
                # 身份只认本连接绑定的观众，绝不读 msg_data["userId"]：
                # 采信客户端自报的 id 就能冒充别人给主播留言。
                # 放在限流之前，未登录连接才会得到「请先登录」而不是「发送太频繁」，
                # 限流表也只对已鉴权观众生效。
                user_id = await self._require_viewer(websocket, "send_host_msg")
                if not user_id:
                    return
                now_ts = time.monotonic()
                if now_ts - self._last_pm.get(websocket, 0.0) < _PM_MIN_INTERVAL:
                    await self._send(websocket, "send_host_msg_result", {
                        "success": False, "message": "发送太频繁，请稍后再试",
                    })
                    return
                content = (msg_data.get("content") or "").strip()
                if not content:
                    await self._send(websocket, "send_host_msg_result", {
                        "success": False, "message": "消息内容为空",
                    })
                    return
                if len(content) > _PM_MAX_LEN:
                    await self._send(websocket, "send_host_msg_result", {
                        "success": False,
                        "message": f"消息过长（上限 {_PM_MAX_LEN} 字）",
                    })
                    return
                self._last_pm[websocket] = now_ts
                acc = dm.get_viewer(user_id)
                dm.add_private_message(PrivateMessageItem(
                    messageId=f"vm_{int(time.time()*1000)}",
                    userId=user_id,
                    userName=(acc.userName if acc else "") or "观众",
                    avatar=(acc.avatar if acc else "") or "",
                    content=content,
                    timestamp=datetime.now().isoformat(),
                    # isFromUser=True 有两个消费方，都不能搞反：
                    # 观众端据此渲染成「我」的右侧气泡、且不计入自己的未读红点；
                    # 运营台据此把它渲染在左侧并标注为观众。
                    isFromUser=True,
                    replyType="manual",
                ))
                await self._send(websocket, "send_host_msg_result", {"success": True})

            # ========== 运营台 · 观众列表与定向私信 ==========
            elif msg_type == "get_viewers":
                rows = dm.get_viewers_overview()
                live_ids = set(self._user_conns.keys())
                all_rows = [dict(r, isOnline=r["userId"] in live_ids,
                                 connections=len(self._user_conns.get(r["userId"], ())))
                            for r in rows]
                online = [r for r in all_rows if r["isOnline"]]
                breakdown = dm.get_online_breakdown()
                await self._send(websocket, "get_viewers_result", {
                    "online": online,
                    "all": all_rows,
                    "onlineCount": breakdown["onlineCount"],
                    "realCount": breakdown["realViewers"],
                    "base": breakdown["base"],
                })

            elif msg_type == "get_viewer_detail":
                target = msg_data.get("userId", "")
                row = next((r for r in dm.get_viewers_overview() if r["userId"] == target), None)
                if row is None:
                    await self._send(websocket, "get_viewer_detail_result", {
                        "success": False, "message": "观众不存在",
                    })
                else:
                    info = await viewer_auth.get_viewer_row(target)
                    await self._send(websocket, "get_viewer_detail_result", {
                        "success": True,
                        "viewer": dict(row,
                                       isOnline=target in self._user_conns,
                                       connections=len(self._user_conns.get(target, ())),
                                       hasAccount=info is not None,
                                       lastLoginAt=(info or {}).get("lastLoginAt")),
                        "orders": [o.model_dump() for o in dm.get_user_orders(target)],
                        "messages": [m.model_dump() for m in dm.get_user_messages(target)],
                    })

            elif msg_type == "send_viewer_msg":
                target = msg_data.get("userId", "")
                content = (msg_data.get("content") or "").strip()
                if not target or not content:
                    await self._send(websocket, "send_viewer_msg_result", {
                        "success": False, "message": "缺少观众或内容",
                    })
                elif dm.get_viewer(target) is None:
                    await self._send(websocket, "send_viewer_msg_result", {
                        "success": False, "message": "观众不存在",
                    })
                else:
                    acc = dm.get_viewer(target)
                    dm.add_private_message(PrivateMessageItem(
                        messageId=f"host_{int(datetime.now().timestamp()*1000)}",
                        userId=target,
                        userName=acc.userName or "观众",
                        avatar=acc.avatar or "",
                        content=content,
                        timestamp=datetime.now().isoformat(),
                        isFromUser=False,
                        replyType="manual",
                    ))
                    await self._send(websocket, "send_viewer_msg_result", {"success": True})

            elif msg_type == "pub_cam_frame":
                # 运营台画面帧 → 仅推给观众端连接（大帧不回灌运营台、不落库）
                await self.broadcast_to("viewer", "cam_frame", {"frame": msg_data.get("frame", "")})

            else:
                await self._send(websocket, "error", {"message": f"未知消息类型: {msg_type}"})

        except json.JSONDecodeError:
            await self._send(websocket, "error", {"message": "JSON解析失败"})
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {e}", exc_info=True)
            await self._send(websocket, "error", {"message": f"服务器内部错误: {str(e)[:100]}"})

    def _prepare_export_data(self, export_type: str) -> dict:
        """准备导出数据"""
        dm = self._data_manager
        if export_type == "reviews":
            return {"reviews": [r.model_dump() for r in dm.reviews]}
        elif export_type == "presets":
            return {"presets": [p.model_dump() for p in dm.presets.values()]}
        elif export_type == "blacklist":
            return {"blacklist": [b.model_dump() for b in dm.blacklist.values()]}
        elif export_type == "products":
            return {"products": [p.model_dump() for p in dm.products.values()]}
        elif export_type == "report":
            return dm.generate_report()
        return {}


# ============================================================
# 话术兜底（AI 不可用时的内置模板，保证生成功能始终可用）
# ============================================================
def _fallback_script(product: ProductItem | None, script_type: str) -> str:
    """根据话术类型生成一段内置带货口播（AI 失败时兜底）。"""
    if product is None:
        return (
            "哈喽直播间的家人们欢迎进来！今天这场直播福利超多，"
            "等下的商品保证让你们心动，先点点关注不迷路，我们马上开始～"
        )
    name = product.name
    price = product.price
    orig = product.originalPrice
    points = "、".join(product.sellingPoints[:3]) if product.sellingPoints else (product.description[:40] if product.description else "品质超好")
    base = {
        "opening": (
            f"欢迎刚进来的家人们！今天给大家带来的是【{name}】，"
            f"先剧透一下：直播间专属价只要 {price:.0f} 块，比日常省太多了！"
            "点点关注不迷路，把'想要'打在公屏上，人气到了我们马上开讲！"
        ),
        "product_intro": (
            f"来，咱重点说说这款【{name}】。它最大的亮点就是：{points}。"
            f"市面上同类动辄几百，今天直播间到手只要 {price:.0f} 元"
            f"（日常价 {orig:.0f}），品质一点不打折。"
            "担心效果的家人可以先加购，我待会一个个问题给大家解答清楚。"
        ),
        "promo": (
            f"最后一遍提醒！【{name}】直播间专属价 {price:.0f} 元，"
            f"对比日常价 {orig:.0f} 元直接省下一大截，而且库存有限、卖完就恢复原价。"
            "想入手的家人现在就把订单下起来，错过这波真的会拍大腿！"
        ),
        "interact": (
            f"家人们互动一下！有用过【{name}】或者想入手的，公屏扣1；"
            "还在纠结价钱的扣2，我看看有多少人需要我再给点福利。"
            "踊跃互动，说不定下一波抽奖就抽到你！"
        ),
        "objection": (
            "有家人说'还是有点贵''想再等等'——我特别理解！"
            f"但你看【{name}】这个品质：{points}。日常价 {orig:.0f}，"
            f"今天 {price:.0f} 已经是很实在的价了，而且我们有完善的售后，"
            "收到不满意随时联系我，放心拍就行。"
        ),
        "closing": (
            f"今天的爆款【{name}】就讲到这里，到手价 {price:.0f} 元还没上车的抓紧！"
            "感谢家人们一整场的陪伴，没点关注的记得点一下，"
            "下一场我会带来更多好物和更大的福利，我们不见不散！"
        ),
    }
    return base.get(script_type, base["product_intro"])


# ============================================================
# 全局WebSocket管理器单例
# ============================================================
_ws_manager: Optional[LiveCommerceWebSocketManager] = None


def get_ws_manager() -> LiveCommerceWebSocketManager:
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = LiveCommerceWebSocketManager()
    return _ws_manager


async def live_commerce_ws_handler(websocket: WebSocket):
    """
    WebSocket端点处理器
    路径: /ws/live-commerce
    """
    manager = get_ws_manager()
    if not await manager.connect(websocket):
        # 握手阶段已被拒绝（无效/过期 JWT 或自称 host 却无凭据）
        return
    try:
        while True:
            raw_data = await websocket.receive_text()
            await manager.handle_message(websocket, raw_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket处理异常: {e}")
        manager.disconnect(websocket)
