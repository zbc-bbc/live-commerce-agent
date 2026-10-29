"""
直播电商 Agent 工具集合
包含：商品查询、优惠计算、库存查询、商品推荐、脚本生成、异议处理、复盘报告
"""

import contextvars
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from data.live_commerce import ALL_PRODUCTS, Product, get_product, search_products, list_categories

# ============================================================
# Per-session log isolation (contextvar keeps each user's logs separate)
# ============================================================
_session_key_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_key", default="default")
_live_session_logs: dict[str, list[dict[str, Any]]] = defaultdict(list)


def set_session_key(key: str) -> None:
    """Set the current session key (thread_id) for log isolation."""
    _session_key_var.set(key)


def _get_current_log() -> list[dict[str, Any]]:
    """Get the log list for the current session."""
    return _live_session_logs[_session_key_var.get()]


# ============================================================
# 商品查询工具
# ============================================================

@tool
def search_product_tool(query: str) -> str:
    """根据关键词搜索商品。返回匹配的商品列表及其详细资料。"""
    results = search_products(query)
    if not results:
        return f"未找到与「{query}」相关的商品。"
    return _format_products(results)


@tool
def get_product_detail_tool(product_id: str) -> str:
    """根据商品ID获取单个商品的完整资料（价格、规格、卖点等）。"""
    product = get_product(product_id)
    if not product:
        return f"未找到商品ID: {product_id}"
    return _format_product_detail(product)


@tool
def list_all_products_tool() -> str:
    """列出所有商品分类及每个分类下的商品概要。"""
    categories = list_categories()
    lines = ["📋 **全部商品分类**\n"]
    for cat, products in categories.items():
        lines.append(f"\n**{cat}**（{len(products)}件）")
        for p in products:
            lines.append(f"  - [{p.id}] {p.name} ¥{p.price:.0f} 库存:{p.stock}")
    return "\n".join(lines)


# ============================================================
# 优惠计算工具
# ============================================================

@tool
def calculate_promotion_tool(product_id: str, quantity: int = 1) -> str:
    """计算指定商品在给定数量下，应用所有可用优惠后的最终价格。

    Args:
        product_id: 商品ID
        quantity: 购买数量
    """
    product = get_product(product_id)
    if not product:
        return f"未找到商品ID: {product_id}"
    if quantity <= 0:
        return "购买数量必须大于0"
    if quantity > product.stock:
        return f"库存不足！{product.name}当前库存仅{product.stock}件。"

    # 计算原价
    original_total = product.price * quantity
    best_price = original_total
    best_promo_desc = "无可用优惠"

    for promo in product.promotions:
        calc_result = _evaluate_promo(promo, product.price, quantity)
        if calc_result is not None and calc_result < best_price:
            best_price = calc_result
            best_promo_desc = promo.description

    saved = original_total - best_price

    lines = [
        f"**{product.name}** 优惠计算结果：",
        f"  商品单价：¥{product.price:.2f}",
        f"  购买数量：{quantity}",
        f"  原价总计：¥{original_total:.2f}",
        f"  优惠后总价：¥{best_price:.2f}",
        f"  节省金额：¥{saved:.2f}",
        f"  适用优惠：{best_promo_desc}",
    ]
    return "\n".join(lines)


def _evaluate_promo(promo: Any, unit_price: float, quantity: int) -> float | None:
    """解析优惠规则逻辑并计算优惠后价格。返回 None 表示不适用。"""
    logic = promo.logic
    try:
        # 支持的规则模式
        if "买" in logic and "送" in logic and "总价=原价*" in logic and "送" not in logic.split("总价")[0]:
            # 买X送Y：只计费X件的价格
            # e.g. "买1件送1件，售价=原价，得2盒"
            return float(unit_price * quantity)

        if "直降" in logic:
            # "单件直降100元，售价=原价-100"
            discount = int(logic.split("直降")[1].split("元")[0].strip("元").strip())
            return float((unit_price - discount) * quantity)

        if "买" in logic and "减" in logic:
            # "买2件总价=原价*2-50"
            # "买2盒减30元，买3盒减60元"
            parts = logic.split("总价=")[1] if "总价=" in logic else logic
            # Simple eval for known patterns only
            if "买" in logic:
                # Find the discount threshold
                import re
                # Pattern: 买(N)件总价=原价*(N)-折扣
                threshold = None
                discount_amt = None
                for part in logic.split("，"):
                    if "买" in part and ("件" in part or "盒" in part or "袋" in part):
                        m = re.search(r"买(\d+)", part)
                        if m:
                            threshold = int(m.group(1))
                    if "减" in part:
                        m = re.search(r"减(\d+)", part)
                        if m:
                            discount_amt = float(m.group(1))
                if threshold and discount_amt is not None:
                    if quantity >= threshold:
                        # Apply the discount for that tier
                        # Find the right tier
                        max_tier = threshold
                        max_discount = discount_amt
                        # Check if there's a higher tier
                        for p in logic.split("，"):
                            if "买" in p and ("件" in p or "盒" in p or "袋" in p):
                                m = re.search(r"买(\d+)", p)
                                if m and int(m.group(1)) > max_tier:
                                    max_tier = int(m.group(1))
                            if "减" in p:
                                m = re.search(r"减(\d+)", p)
                                if m and float(m.group(1)) > max_discount:
                                    max_discount = float(m.group(1))
                        if quantity >= max_tier:
                            return float(unit_price * quantity - max_discount)
                        return float(unit_price * quantity - discount_amt)
                return None

        if "打" in logic and "折" in logic:
            # "两件9折，三件8折"
            import re
            matches = re.findall(r"(\d+)件(\d+(\.\d+)?)折", logic)
            best_discount = 1.0
            for num_str, disc_str, _ in matches:
                try:
                    num = int(num_str)
                    disc = float(disc_str) / 10.0
                    if quantity >= num:
                        best_discount = min(best_discount, disc)
                except (ValueError, TypeError):
                    continue
            if best_discount < 1.0:
                return float(unit_price * quantity * best_discount)
            return None

        if "特惠价" in logic or "总价=" in logic:
            # "买2件总价=399"
            eq = logic.split("总价=")[1] if "总价=" in logic else ""
            if "=" in logic:
                total = float(logic.split("=")[-1].strip("元").strip())
                return float(total)
            return None

        # Default: no applicable promotion
        return None
    except Exception:
        return None


# ============================================================
# 库存查询工具
# ============================================================

@tool
def check_stock_tool(product_id: str) -> str:
    """查询指定商品的当前库存状态。"""
    product = get_product(product_id)
    if not product:
        return f"未找到商品ID: {product_id}"
    status = "充足 ✅" if product.stock > 500 else "一般 ⚠️" if product.stock > 100 else "紧张 🔴"
    return (
        f"**{product.name}** 库存信息：\n"
        f"  当前库存：{product.stock} 件\n"
        f"  状态：{status}"
    )


# ============================================================
# 商品推荐工具
# ============================================================

@tool
def recommend_products_tool(category: str = "", budget: float = 0, scenario: str = "") -> str:
    """根据分类、预算或使用场景推荐商品。

    Args:
        category: 商品分类筛选（如"美妆护肤""数码3C""食品茶饮"等，为空则不限制）
        budget: 预算上限（0表示不限制）
        scenario: 使用场景描述（如"送礼""自用""办公室"）
    """
    candidates = ALL_PRODUCTS

    if category:
        candidates = [p for p in candidates if p.category == category]
    if budget > 0:
        candidates = [p for p in candidates if p.price <= budget]

    if scenario:
        scenario_lower = scenario.lower()
        keyword_map = {
            "送礼": ["礼盒", "礼盒包装", "精美"],
            "健身": ["低脂", "高蛋白", "健身"],
            "办公室": ["护腰", "靠垫", "台灯"],
            "宿舍": ["吹风机", "台灯", "耳机"],
            "旅行": ["翻译笔", "双肩包", "旅行"],
            "自用": [],
        }
        matched = []
        for kw_list in keyword_map.values():
            for p in candidates:
                if any(kw in p.name or kw in p.description for kw in kw_list):
                    if p not in matched:
                        matched.append(p)
        if matched:
            candidates = matched

    if not candidates:
        return "未找到符合条件的商品，请调整筛选条件。"

    return _format_products(candidates)


# ============================================================
# 话术生成工具
# ============================================================

LIVE_COMMERCE_KNOWLEDGE = """
## 直播带货标准化话术结构

### 1. 开场话术（前3分钟）
- 欢迎+问候，营造氛围
- 预告今天的福利商品和优惠力度
- 引导点赞关注（"点赞满1万抽奖"）
- 告知直播时长和节奏

### 2. 商品介绍话术
- 痛点引入："你有这个问题吗？"
- 产品亮点：3个核心卖点（FAB法则：特点-优势-利益）
- 价格锚定：先报原价/竞品价，再报直播间价
- 限时限量：制造紧迫感

### 3. 促销提醒话术
- 强调优惠力度和时限
- 对比日常价突出直播间优惠
- 引导下单："手慢无""最后XX件"

### 4. 互动提问话术
- 选择题式提问（"想要这款扣1"）
- 痛点共鸣（"久坐腰痛的扣2"）
- 抽奖互动（"评论抽3位送试用装"）

### 5. 收尾话术
- 总结商品卖点
- 重复优惠和下单方式
- 预告下一款商品
- 感谢+期待下次相遇

## 异议处理原则
- 价格异议：强调价值+品质+售后
- 质量异议：展示认证+检测报告+用户口碑
- 品牌异议：突出专业度+销量+好评率
- 效果异议：展示成分/技术原理+真实案例
"""


# ============================================================
# 日志工具
# ============================================================

@tool
def record_session_log(event_type: str, content: str, product_id: str = "") -> str:
    """记录直播过程中的关键事件。

    Args:
        event_type: 事件类型（question/answer/tool_call/objection/order）
        content: 事件内容描述
        product_id: 关联商品ID（可选）
    """
    log = _get_current_log()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "content": content,
        "product_id": product_id,
    }
    log.append(entry)
    return f"已记录: {event_type} - {content[:50]}..."


@tool
def get_session_log_tool() -> str:
    """获取当前直播会话的完整事件日志。"""
    log = _get_current_log()
    if not log:
        return "当前直播尚无事件记录。"
    lines = ["📋 **直播事件日志**\n"]
    for i, entry in enumerate(log, 1):
        lines.append(f"{i}. [{entry['type']}] {entry['content']}")
        if entry['product_id']:
            product = get_product(entry['product_id'])
            if product:
                lines[-1] += f" (商品: {product.name})"
    return "\n".join(lines)


@tool
def clear_session_log_tool() -> str:
    """清除当前直播会话的日志，开始新的一场直播记录。"""
    _get_current_log().clear()
    return "日志已清除，新的一场直播开始！"


# ============================================================
# 复盘报告生成工具
# ============================================================

@tool
def generate_report_tool(duration_minutes: int = 60) -> str:
    """基于当前直播日志生成复盘报告。

    Args:
        duration_minutes: 直播时长（分钟）
    """
    log = _get_current_log()
    if not log:
        return "没有直播数据，无法生成报告。请先进行一次直播。"

    questions = [e for e in log if e["type"] == "question"]
    answers = [e for e in log if e["type"] == "answer"]
    objections = [e for e in log if e["type"] == "objection"]
    orders = [e for e in log if e["type"] == "order"]
    tool_calls = [e for e in log if e["type"] == "tool_call"]

    # 分析高频问题
    q_texts = [e["content"] for e in questions]
    from collections import Counter
    word_counter: Counter = Counter()
    keywords = ["价格", "优惠", "质量", "售后", "规格", "适用", "真假", "效果", "物流", "赠品"]
    for qt in q_texts:
        for kw in keywords:
            if kw in qt:
                word_counter[kw] += 1
    top_keywords = word_counter.most_common(5)

    # 转化阻碍分析
    barriers = []
    for obj in objections:
        content = obj["content"]
        if "贵" in content or "价格" in content:
            barriers.append("价格敏感 - 需强调性价比和分期方案")
        if "质量" in content or "杂牌" in content or "正品" in content:
            barriers.append("信任不足 - 需展示认证和用户好评")
        if "效果" in content or "智商税" in content:
            barriers.append("效果疑虑 - 需提供成分原理和使用案例")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "=" * 50,
        f"📊 **直播复盘报告**",
        f"生成时间：{now}",
        f"直播时长：约 {duration_minutes} 分钟",
        "=" * 50,
        "",
        "**【一、基础数据】**",
        f"  总提问数：{len(questions)}",
        f"  已回答数：{len(answers)}",
        f"  异议次数：{len(objections)}",
        f"  下单数：{len(orders)}",
        f"  工具调用：{len(tool_calls)}",
        "",
        "**【二、高频问题分析】**",
    ]
    if top_keywords:
        for kw, count in top_keywords:
            lines.append(f"  - 「{kw}」相关提问 {count} 次")
    else:
        lines.append("  - 暂无足够问题数据分析")

    lines += [
        "",
        "**【三、转化阻碍识别】**",
    ]
    if barriers:
        unique_barriers = list(set(barriers))
        for b in unique_barriers:
            lines.append(f"  - ⚠️ {b}")
    else:
        lines.append("  - 未发现明显转化阻碍 ✓")

    lines += [
        "",
        "**【四、优化建议】**",
    ]
    if "价格敏感" in str(barriers):
        lines.append("  1️⃣ 增加价格锚点对比，突出直播间专享价")
        lines.append("  2️⃣ 设计更多阶梯优惠，引导提高客单价")
    if "信任不足" in str(barriers):
        lines.append("  3️⃣ 在直播中展示品牌授权书和质检报告")
        lines.append("  4️⃣ 安排已购用户实时分享使用体验")
    if "效果疑虑" in str(barriers):
        lines.append("  5️⃣ 增加成分科普环节，用专业知识说服用户")
        lines.append("  6️⃣ 提供小样/试用装降低决策门槛")
    if not barriers:
        lines.append("  1️⃣ 保持现有直播节奏")
        lines.append("  2️⃣ 增加更多互动环节提升用户参与度")
        lines.append("  3️⃣ 考虑新增品类拓展观众群")

    lines += [
        "",
        "**【五、工具调用分析】**",
    ]
    if tool_calls:
        call_types = Counter(e["content"] for e in tool_calls)
        for ct, count in call_types.most_common():
            lines.append(f"  - {ct}：{count} 次")
    else:
        lines.append("  - 本场直播未使用工具")

    lines.append("")
    lines.append("=" * 50)
    lines.append("报告仅供参考，建议结合人工复盘优化。")

    return "\n".join(lines)


# ============================================================
# 工具列表
# ============================================================

LIVE_COMMERCE_TOOLS = [
    search_product_tool,
    get_product_detail_tool,
    list_all_products_tool,
    calculate_promotion_tool,
    check_stock_tool,
    recommend_products_tool,
    record_session_log,
    get_session_log_tool,
    clear_session_log_tool,
    generate_report_tool,
]


# ============================================================
# 格式化辅助
# ============================================================

def _format_products(products: list[Product]) -> str:
    lines = [f"找到 {len(products)} 件商品：\n"]
    for p in products:
        sells = " | ".join(p.selling_points[:2])
        promos = " | ".join(pr.description for pr in p.promotions[:2])
        lines.append(f"**[{p.id}] {p.name}**")
        lines.append(f"  价格：¥{p.price:.2f}  |  库存：{p.stock}")
        lines.append(f"  卖点：{sells}")
        if promos:
            lines.append(f"  优惠：{promos}")
        lines.append("")
    return "\n".join(lines)


def _format_product_detail(product: Product) -> str:
    lines = [
        f"**{product.name}**",
        f"  分类：{product.category}",
        f"  价格：¥{product.price:.2f}",
        f"  库存：{product.stock} 件",
        f"  描述：{product.description}",
        "",
        "**卖点：**",
    ]
    for sp in product.selling_points:
        lines.append(f"  ✅ {sp}")
    lines.append("")
    lines.append("**规格参数：**")
    for k, v in product.specs.items():
        lines.append(f"  - {k}: {v}")
    if product.promotions:
        lines.append("")
        lines.append("**优惠活动：**")
        for promo in product.promotions:
            lines.append(f"  🔥 {promo.description}")
    if product.notes:
        lines.append("")
        lines.append("**注意事项：**")
        for note in product.notes:
            lines.append(f"  ⚠️ {note}")
    return "\n".join(lines)
