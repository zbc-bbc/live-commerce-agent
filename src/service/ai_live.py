"""
直播电商 AI 话术服务（Live Commerce AI Service）

为 HTML 看板（WebSocket 后端）提供两类能力：
1. generate_script —— 按话术类型 + 商品信息，生成主播直播口播稿
2. ai_reply —— 针对弹幕/私信问题，结合商品信息生成自然回复

设计要点：
- 模型走 service.ai_endpoints.resolve_chat_model()：设置页配了「当前启用端口」就用它，
  没配则回退 core.llm.get_model（.env 里的 DeepSeek 默认）。全部 async，供 WS 层 await。
- 任何异常 / 超时 / key 不可用 → 返回 None，由调用方回退规则话术，绝不阻塞主播流程。
- 只依赖 schema 的商品模型与核心配置，不 import data_manager，避免环形依赖。
  （ai_endpoints 读的是独立的 llm_endpoint_store，同样不碰 data_manager。）
"""

import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from schema.live_commerce_schema import LIVE_SCRIPT_TYPES, ProductItem
from service.ai_endpoints import resolve_chat_model

logger = logging.getLogger(__name__)

# AI 生成超时上限（秒）——超时直接放弃走规则兜底
_LLM_TIMEOUT = 20

_ANCHOR_PERSONA = (
    "你是一位专业、热情、接地气的中国电商直播主播，名叫'小智'，正在抖音风格的直播间带货。"
    "你的口播自然口语化，会适度使用网络热词和 emoji 拉近距离，但不过度浮夸、不虚假承诺、不违反广告法。"
    "输出必须是可直接照着念的中文口播稿，通常 100~250 字，不要输出 markdown 标题或多余解释。"
)

_SCRIPT_PROMPT_HINTS = {t["key"]: t["prompt_hint"] for t in LIVE_SCRIPT_TYPES}


def _build_product_context(product: ProductItem | None) -> str:
    """把商品对象压缩成 LLM 可读的上下文文本。"""
    if product is None:
        return "（当前没有正在讲解的具体商品，可讲直播间的整体氛围与福利预告）"
    lines = [
        f"商品名称：{product.name}",
        f"品类：{product.category or '未分类'}",
        f"价格：¥{product.price:.2f}（原价 ¥{product.originalPrice:.2f}）",
        f"库存：{product.stock} 件 | 累计销量：{product.soldCount} 件 | 评分：{product.rating}",
    ]
    if product.sellingPoints:
        lines.append("核心卖点：" + "；".join(product.sellingPoints))
    elif product.tags:
        lines.append("卖点标签：" + "、".join(product.tags))
    if product.specs:
        lines.append("规格参数：" + "、".join(product.specs))
    if product.description:
        lines.append("详细介绍：" + product.description[:300])
    return "\n".join(lines)


async def _llm_chat(system: str, user: str) -> str | None:
    """统一调用大模型，异常/超时返回 None。"""
    try:
        model = resolve_chat_model()
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 模型初始化失败，回退规则话术: %s", e)
        return None
    try:
        resp = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)]),
            timeout=_LLM_TIMEOUT,
        )
        text = (resp.content or "").strip() if hasattr(resp, "content") else str(resp).strip()
        return text or None
    except asyncio.TimeoutError:
        logger.warning("AI 话术生成超时(%ss)，回退规则话术", _LLM_TIMEOUT)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 话术生成异常，回退规则话术: %s", e)
        return None


async def generate_script(
    product: ProductItem | None,
    script_type: str,
    current_stage: str = "",
) -> str | None:
    """生成主播口播话术。失败返回 None，由调用方兜底。"""
    hint = _SCRIPT_PROMPT_HINTS.get(script_type, _SCRIPT_PROMPT_HINTS.get("product_intro", ""))
    stage_line = f"当前直播阶段：{current_stage or '未指定'}"
    system = _ANCHOR_PERSONA + " 本次你的任务是：写一段" + hint + "。"
    user = (
        f"请根据下面的商品信息，写一段可以直接照着念的带货口播话术（{stage_line}）：\n\n"
        + _build_product_context(product)
        + "\n\n要求：口语化、有感染力、自然穿插引导（关注/点赞/下单），不要罗列编号。"
    )
    return await _llm_chat(system, user)


async def ai_reply(content: str, product: ProductItem | None) -> str | None:
    """针对一条弹幕/私信问题生成回复。失败返回 None。"""
    system = (
        _ANCHOR_PERSONA
        + " 现在是观众在直播间发了一条问题/弹幕，请以主播口吻当场回应。"
        " 回复要结合给出的商品真实信息，简洁热情、口语化，通常 20~60 字，可直接发送给观众。"
        " 如果问题与商品无关（如闲聊、打招呼），自然互动即可。"
    )
    user = "观众说：「" + content[:200] + "」\n\n" + _build_product_context(product)
    return await _llm_chat(system, user)


# ============================================================
# 弹幕总结
# ============================================================
def build_summary_template(summary: dict) -> str:
    """AI 不可用时把**已算好的统计**拼成一段摘要。

    这是降级路径，不是"备用随机文案"：出现的每一个数字都来自入参，
    没有的就不提。宁可摘要短，也不编造。
    """
    stats = (summary or {}).get("stats") or {}
    n = stats.get("realCount") or 0
    if not n:
        return ""
    users = stats.get("realUserCount") or 0
    questions = stats.get("questionCount") or 0
    replied = stats.get("repliedCount") or 0
    latency = stats.get("replyLatencyAvg") or 0
    per_min = stats.get("avgPerMinute") or 0
    sentiment = stats.get("sentiment") or {}
    parts = [
        f"本场共收到真实观众弹幕 {n} 条，来自 {users} 位观众，平均每分钟 {per_min} 条。",
        f"其中提问 {questions} 条，AI 已回复 {replied} 条"
        + (f"，平均响应 {latency} 秒。" if latency else "。"),
    ]
    pos = sentiment.get("positive") or 0
    neg = sentiment.get("negative") or 0
    neu = sentiment.get("neutral") or 0
    if pos or neg or neu:
        parts.append(f"情绪分布：正面 {pos} 条、中性 {neu} 条、负面 {neg} 条。")
    kws = [k.get("keyword") for k in (stats.get("topKeywords") or [])[:5] if k.get("keyword")]
    if kws:
        parts.append("弹幕中出现最多的词是：" + "、".join(kws) + "。")
    qs = [q.get("question") for q in (stats.get("topQuestions") or [])[:3] if q.get("question")]
    if qs:
        parts.append("观众最关心的问题依次是：" + "；".join(qs) + "。")
    buckets = stats.get("timeBuckets") or []
    if buckets:
        peak = max(buckets, key=lambda b: b.get("count") or 0)
        parts.append(f"互动最密集的时段是 {peak.get('label')}（{peak.get('count')} 条）。")
    return "".join(parts)


async def generate_danmaku_summary(summary: dict) -> str | None:
    """把**后端已经算好的**弹幕统计交给模型组织成一段复盘摘要。失败返回 None。

    关键约束：数字先算好再喂进来。让模型自己去数弹幕，它一定会编 —— 这里 system
    里写死了"只准引用给定数字"，user 里给的是 JSON 而不是弹幕原文合集。
    """
    system = (
        _ANCHOR_PERSONA
        + " 现在你的任务是把本场直播**真实观众**的弹幕统计数据写成一段复盘摘要，"
        "供主播复盘使用。"
        " 严格要求：只引用下面 JSON 里给出的数字与词语，"
        "**不得编造任何未给出的数据**（不要提 GMV、不要提观众总数、不要虚构具体弹幕内容、"
        "不要添加 JSON 里没有的评价性结论）。"
        " 输出 150~250 字的一段中文，自然口语化，不要分点、不要 markdown、不要标题。"
    )
    user = (
        "本场真实观众弹幕统计（JSON）：\n"
        + json.dumps(summary, ensure_ascii=False)
        + "\n\n请据此写一段复盘摘要。"
    )
    return await _llm_chat(system, user)
