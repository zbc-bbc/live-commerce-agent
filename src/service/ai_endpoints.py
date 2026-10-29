"""
大模型接入端口的服务层：掩码、构造模型对象、连通性测试。

为什么不让 `core.llm.get_model` 管这件事：
1. `get_model` 是 `@cache` 的，按枚举名缓存，配置改了也换不回来；
2. 它接受的模型名是闭集合枚举（`schema/models.py`），页面里随便填的
   "deepseek-chat" / "qwen-plus" 这类名字根本进不去。

所以这里另建一条「按配置构造 client」的路径，**完全不改 `get_model`**：
没配端口时仍然回退到它，现有行为一字不变。
"""

import asyncio
import logging
import threading
import time
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core import get_model, settings
from data.live_commerce import llm_endpoint_store as store

logger = logging.getLogger(__name__)

# 连通性测试的超时：比正式调用的 20s 短，操作者在页面上等着，不该等这么久
_TEST_TIMEOUT = 15.0
# 测试请求只让它吐几个 token，别为了探活真生成一段话
_TEST_MAX_TOKENS = 16

# 掩码保留的头/尾长度
_MASK_HEAD, _MASK_TAIL = 4, 4


# ============================================================
# 掩码 / 对外形态
# ============================================================

def mask_key(api_key: str) -> str:
    """`sk-1***cd12`。

    留头尾是为了让操作者能认出这是哪一把 key，中间一律三颗星 —— 不透露真实长度，
    也绝不可能被误当成真 key 存回去（`store.upsert_endpoint` 会识别 `*`）。
    """
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= _MASK_HEAD + _MASK_TAIL:
        return "*" * len(key)
    return key[:_MASK_HEAD] + "***" + key[-_MASK_TAIL:]


def public_view(endpoint: dict[str, Any]) -> dict[str, Any]:
    """给前端的形态：**没有明文 key**，只有掩码 + 一个「有没有配」的布尔。"""
    return {
        "id": endpoint.get("id", ""),
        "name": endpoint.get("name", ""),
        "baseUrl": endpoint.get("baseUrl", ""),
        "model": endpoint.get("model", ""),
        "temperature": endpoint.get("temperature", 0.5),
        "createdAt": endpoint.get("createdAt", ""),
        "apiKeyMasked": mask_key(endpoint.get("apiKey", "")),
        "hasApiKey": bool(endpoint.get("apiKey")),
    }


def list_public() -> list[dict[str, Any]]:
    """全部端口的对外形态。前端拿到的东西一律经过这里。"""
    return [public_view(e) for e in store.list_endpoints()]


# ============================================================
# 构造模型对象
# ============================================================

_client_lock = threading.Lock()
# 以配置本身为 key：改了 base_url / 模型 / 密钥就自然落到新对象上，
# 不需要任何「失效」逻辑，也没有 `get_model` 那种 @cache 换不掉的问题。
_client_cache: dict[tuple, ChatOpenAI] = {}


def build_chat_model(endpoint: dict[str, Any]) -> ChatOpenAI:
    """按端口配置构造一个 OpenAI 兼容的 chat 模型。配置不全时抛 ValueError。"""
    base_url = (endpoint.get("baseUrl") or "").strip().rstrip("/")
    model = (endpoint.get("model") or "").strip()
    api_key = (endpoint.get("apiKey") or "").strip()
    temperature = endpoint.get("temperature", 0.5)

    missing = [
        label
        for label, value in (("Base URL", base_url), ("模型名", model), ("API Key", api_key))
        if not value
    ]
    if missing:
        raise ValueError("、".join(missing) + " 未填写")

    cache_key = (base_url, model, api_key, float(temperature))
    with _client_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = ChatOpenAI(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=float(temperature),
                streaming=True,
            )
            _client_cache[cache_key] = client
    return client


def resolve_chat_model():
    """给 AI 文字功能用的模型：优先当前启用的端口，否则回退 `.env` 里的默认模型。

    端口存在但构造失败时**也回退**而不是抛出去 —— 直播间里宁可退化成 .env 的
    模型，也不要四个功能一起变哑巴。
    """
    endpoint = store.get_active_endpoint()
    if endpoint:
        try:
            model = build_chat_model(endpoint)
            logger.info(
                "AI 走端口「%s」：%s @ %s",
                endpoint.get("name") or endpoint.get("id"),
                endpoint.get("model"),
                endpoint.get("baseUrl"),
            )
            return model
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "端口「%s」构造失败(%s)，回退 .env 默认模型",
                endpoint.get("name") or endpoint.get("id"),
                e,
            )
    return get_model(settings.DEFAULT_MODEL)


# ============================================================
# 连通性测试
# ============================================================

def endpoint_for_test(data: dict[str, Any]) -> dict[str, Any]:
    """把「可能要测的那份配置」拼出来。

    页面里带 id 编辑时，API Key 一栏是空的（前端拿不到明文），这种情况要用
    存着的那把；如果操作者当场改了 key（不含 `*`），就以他填的为准。
    """
    data = data if isinstance(data, dict) else {}
    endpoint_id = str(data.get("id") or "").strip()
    merged: dict[str, Any] = dict(store.get_endpoint(endpoint_id) or {}) if endpoint_id else {}

    for field in ("name", "baseUrl", "model"):
        value = data.get(field)
        if value not in (None, ""):
            merged[field] = value
    if data.get("temperature") not in (None, ""):
        merged["temperature"] = data["temperature"]

    typed_key = str(data.get("apiKey") or "").strip()
    if typed_key and "*" not in typed_key:
        merged["apiKey"] = typed_key

    return merged


def _explain(err: Exception) -> str:
    """把底层异常翻译成操作者能照着改的提示。"""
    status = getattr(err, "status_code", None)
    if status is None:
        response = getattr(err, "response", None)
        status = getattr(response, "status_code", None)

    if status == 401:
        return "密钥无效或已过期（401）"
    if status == 403:
        return "这把密钥无权访问该模型（403）"
    if status == 404:
        return "接口地址或模型名不对（404）：Base URL 通常要带 /v1"
    if status == 429:
        return "被限流或额度不足（429）"
    if isinstance(status, int) and status >= 500:
        return f"对方服务异常（{status}）"

    text = str(err).lower()
    # 客户端带 streaming=True（与 core.llm.get_model 一致），所以对方必须回 SSE。
    # 只回一个普通 JSON 的"伪 OpenAI 接口"会抛这句，原文对操作者毫无信息量。
    if "no generations found in stream" in text:
        return "对方返回的不是流式响应：它可能不是标准 OpenAI 兼容接口（需支持 stream=true）"
    if any(
        hint in text
        for hint in (
            "connect", "connection", "timed out", "timeout",
            "getaddrinfo", "name or service", "ssl", "certificate", "proxy",
        )
    ):
        return "连不上这个地址：检查 Base URL、网络或代理"
    if isinstance(status, int):
        return f"请求失败（{status}）"
    return f"请求失败：{str(err)[:160]}"


async def test_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    """真打一次最小请求探连通性。返回 {ok, latencyMs, message}。"""
    endpoint = endpoint_for_test(data)

    try:
        model = build_chat_model(endpoint)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latencyMs": 0, "message": str(e)}

    started = time.monotonic()
    try:
        await asyncio.wait_for(
            model.bind(max_tokens=_TEST_MAX_TOKENS).ainvoke(
                [SystemMessage(content="你是连通性测试。"), HumanMessage(content="回复 OK")]
            ),
            timeout=_TEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "message": f"超时（超过 {_TEST_TIMEOUT:.0f} 秒没有响应）",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "message": _explain(e),
        }

    return {
        "ok": True,
        "latencyMs": int((time.monotonic() - started) * 1000),
        "message": f"连通正常 · {endpoint.get('model')}",
    }


def describe_active() -> str:
    """当前生效的模型描述，给前端文案用（「DeepSeek 官方」/「.env 默认」）。"""
    endpoint = store.get_active_endpoint()
    if not endpoint:
        return ".env 默认模型"
    return endpoint.get("name") or endpoint.get("model") or "已配置端口"
