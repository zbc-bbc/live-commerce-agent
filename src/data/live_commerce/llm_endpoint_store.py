"""
大模型接入端口存储（OpenAI 兼容端点列表）。

**为什么单独一个文件，而不是塞进 LiveCommerceSettings**：
`get_settings` / `init_state` 会把整个 settings 模型 `model_dump()` 下发给主播端
浏览器，密钥一旦进了那个 blob 就会跟着走，而且保存时还会原样回传。独立存放之后
「API Key 只出现在服务端」是结构上的保证，不必依赖以后每一处 dump 都记得脱敏。

与 `data_manager` 的两处有意不同：
1. 写盘用「临时文件 + os.replace」原子替换。`_save_json` 是先 "w" 截断再写，
   崩在半路会留下一个坏 JSON；这个文件很小，原子写几乎零成本，而它一旦损坏，
   丢的是**密钥配置**本身，比丢商品数据更难恢复。
2. 读坏了不静默当空 —— 会先把坏文件改名留档再以空列表启动，免得下一次保存
   把仅存的那份原始内容覆盖掉。
"""

import json
import os
import secrets
import threading
from datetime import datetime
from typing import Any, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
ENDPOINTS_FILE = os.path.join(DATA_DIR, "llm_endpoints.json")

# 单条字段的长度上限：防止前端塞进来一个几 MB 的字符串把配置文件和日志撑爆
_MAX_NAME = 60
_MAX_URL = 300
_MAX_MODEL = 120
_MAX_KEY = 400

_lock = threading.RLock()
_state: Optional[dict[str, Any]] = None


def _empty() -> dict[str, Any]:
    return {"activeId": "", "endpoints": []}


def _clean(value: Any, limit: int) -> str:
    """一律转成去首尾空白的字符串并截断，避免 None/数字混进配置里。"""
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _normalize(raw: dict[str, Any], eid: str) -> dict[str, Any]:
    """把外部传进来的字典整形成规范形态。不改 apiKey（由调用方决定是否保留）。"""
    return {
        "id": eid,
        "name": _clean(raw.get("name"), _MAX_NAME),
        "baseUrl": _clean(raw.get("baseUrl"), _MAX_URL).rstrip("/"),
        "model": _clean(raw.get("model"), _MAX_MODEL),
        "apiKey": _clean(raw.get("apiKey"), _MAX_KEY),
        "temperature": _normalize_temperature(raw.get("temperature")),
        "createdAt": _clean(raw.get("createdAt"), 40),
    }


def _normalize_temperature(value: Any) -> float:
    """温度容错到 [0, 2]：前端传空/非法值时不至于把请求打回 400。"""
    try:
        t = float(value)
    except (TypeError, ValueError):
        return 0.5
    return min(2.0, max(0.0, t))


def _load() -> dict[str, Any]:
    """读一次进内存，之后都走内存态（与 data_manager 一致）。"""
    global _state
    if _state is not None:
        return _state

    if not os.path.exists(ENDPOINTS_FILE):
        _state = _empty()
        return _state

    try:
        with open(ENDPOINTS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("顶层不是对象")
        endpoints = [e for e in (raw.get("endpoints") or []) if isinstance(e, dict)]
        _state = {
            "activeId": _clean(raw.get("activeId"), 40),
            "endpoints": [
                _normalize(e, _clean(e.get("id"), 40) or _new_id()) for e in endpoints
            ],
        }
    except Exception as e:  # noqa: BLE001
        # 关键：把坏文件改名留档，而不是直接覆盖。否则下一次保存就把用户
        # 唯一的一份配置（含密钥）永久冲掉了。
        backup = f"{ENDPOINTS_FILE}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
        try:
            os.replace(ENDPOINTS_FILE, backup)
        except OSError:
            backup = "(留档失败)"
        print(f"[LLMEndpoints] 配置文件读取失败，已留档到 {backup}，本次以空列表启动: {e}")
        _state = _empty()

    return _state


def _save(state: dict[str, Any]) -> None:
    """原子写：先写 .tmp 再 os.replace，避免半截文件。"""
    tmp = ENDPOINTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ENDPOINTS_FILE)


def _new_id() -> str:
    return "ep_" + secrets.token_hex(4)


# ============================================================
# 对外接口
# ============================================================

def list_endpoints() -> list[dict[str, Any]]:
    """全部端口（**含明文 apiKey**，仅服务端内部使用；给前端一律走 public_view）。"""
    with _lock:
        return [dict(e) for e in _load()["endpoints"]]


def get_endpoint(endpoint_id: str) -> Optional[dict[str, Any]]:
    if not endpoint_id:
        return None
    with _lock:
        for e in _load()["endpoints"]:
            if e["id"] == endpoint_id:
                return dict(e)
    return None


def get_active_endpoint() -> Optional[dict[str, Any]]:
    """当前启用的端口；没配或指向一个已删除的 id 时返回 None（回退 .env）。"""
    with _lock:
        state = _load()
        active_id = state["activeId"]
        if not active_id:
            return None
        for e in state["endpoints"]:
            if e["id"] == active_id:
                return dict(e)
    return None


def get_active_id() -> str:
    with _lock:
        return _load()["activeId"]


def upsert_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    """新增或更新一个端口，返回落盘后的那条（含明文 key）。

    密钥约定：`apiKey` 传空 = **保持原值不变**。前端拿不到明文，编辑时回传的
    是掩码串或空串，所以这里绝不能把空值直接写进去 —— 那会把真 key 抹掉。
    """
    if not isinstance(data, dict):
        raise ValueError("端口数据格式不对")

    incoming_key = _clean(data.get("apiKey"), _MAX_KEY)

    with _lock:
        state = _load()
        endpoint_id = _clean(data.get("id"), 40)
        existing = next((e for e in state["endpoints"] if e["id"] == endpoint_id), None)

        if existing is None:
            endpoint_id = _new_id()
            item = _normalize(data, endpoint_id)
            item["createdAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not incoming_key:
                raise ValueError("新增端口必须填 API Key")
        else:
            item = _normalize(data, endpoint_id)
            item["createdAt"] = existing.get("createdAt") or ""
            # 留空 或 传回来的恰好是掩码（含 * ）→ 保留原来的 key
            if not incoming_key or "*" in incoming_key:
                item["apiKey"] = existing.get("apiKey", "")

        if not item["baseUrl"]:
            raise ValueError("Base URL 不能为空")
        if not item["model"]:
            raise ValueError("模型名不能为空")
        if not item["apiKey"]:
            raise ValueError("API Key 不能为空")
        if not item["name"]:
            item["name"] = item["model"]

        if existing is None:
            state["endpoints"].append(item)
        else:
            state["endpoints"] = [
                item if e["id"] == endpoint_id else e for e in state["endpoints"]
            ]
        _save(state)
        return dict(item)


def delete_endpoint(endpoint_id: str) -> bool:
    """删除端口。若删的正是当前启用项，顺带把 activeId 清空（否则会指向幽灵 id）。"""
    with _lock:
        state = _load()
        before = len(state["endpoints"])
        state["endpoints"] = [e for e in state["endpoints"] if e["id"] != endpoint_id]
        if len(state["endpoints"]) == before:
            return False
        if state["activeId"] == endpoint_id:
            state["activeId"] = ""
        _save(state)
        return True


def set_active(endpoint_id: str) -> bool:
    """设置当前启用端口；传空串表示「不使用」，回退到 .env 里的默认模型。"""
    with _lock:
        state = _load()
        if not endpoint_id:
            state["activeId"] = ""
            _save(state)
            return True
        if not any(e["id"] == endpoint_id for e in state["endpoints"]):
            return False
        state["activeId"] = endpoint_id
        _save(state)
        return True
