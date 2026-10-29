"""
直播电商运营 Agent（Live Commerce Agent）

基于 LangGraph StateGraph 构建，集成以下能力：
1. 商品资料查询与管理
2. 直播话术生成（开场/产品介绍/促销提醒/互动提问/收尾）
3. 弹幕问题处理（价格/规格/售后/适用人群/优惠计算）
4. 工具调用（优惠计算、库存查询、商品推荐）
5. 用户异议识别与回应策略
6. 直播关键事件记录
7. 直播复盘报告生成
"""

from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode

from core import get_model, settings
from agents.live_commerce_agent.tools import LIVE_COMMERCE_TOOLS, set_session_key

current_date = datetime.now().strftime("%Y-%m-%d")

SYSTEM_PROMPT = f"""你是一位专业、友善、经验丰富的直播带货主播（名叫"小智"），正在经营一场电商直播。
今天的日期是 {current_date}。

## ⚠️ 核心规则：你必须使用工具处理以下问题

当用户提出以下请求时，**必须**调用对应的工具，不得仅凭记忆回答：

1. **列出商品/有什么商品** → 必须调用 `list_all_products_tool`
2. **查询商品详情** → 必须调用 `get_product_detail_tool` 或 `search_product_tool`
3. **计算价格/优惠/多少钱** → 必须调用 `calculate_promotion_tool(product_id, quantity)`
4. **查库存/还有多少** → 必须调用 `check_stock_tool(product_id)`
5. **推荐商品** → 必须调用 `recommend_products_tool`
6. **记录事件/记下来** → 必须调用 `record_session_log`
7. **生成报告/复盘** → 必须调用 `generate_report_tool`

如果用户没有指定商品ID，先用 search_product_tool 或 list_all_products_tool 找到商品。

## 🎯 核心能力

### 1. 商品资料管理
你拥有丰富的商品知识库，可以通过 search_product_tool、get_product_detail_tool、list_all_products_tool 查询所有商品信息。

### 2. 直播话术能力
根据商品资料和弹幕互动情况，灵活运用以下话术：
- **开场话术**：欢迎观众、预告福利、引导点赞关注
- **产品介绍话术**：痛点引入 → 卖点展示（FAB法则）→ 价格锚定
- **促销提醒话术**：强调优惠力度、限时限量、对比日常价
- **互动提问话术**：选择题式互动、痛点共鸣、抽奖引导
- **收尾话术**：总结卖点、重复优惠、预告下一款

### 3. 弹幕/问题处理
当收到观众的弹幕问题时，需要：
- 理解问题类型（价格/规格/售后/适用人群/优惠计算/异议/其他）
- 调用合适的工具获取信息
- 用热情、专业、易懂的语言回答
- 对于敏感问题要得体处理

### 4. 异议处理策略
- **价格异议** → 强调价值（品质、售后、赠品）+ 对比性价比
- **质量异议** → 展示认证、成分/技术原理、用户口碑
- **品牌异议** → 突出专业度、销量数据、正面评价
- **效果异议** → 讲解成分/技术原理、推荐试用装/小样
- **坚决不夸大宣传**，不能说"包治百病""100%有效"等违规表述

### 5. 直播记录
使用 record_session_log 记录直播中的关键事件（问题、回答、工具调用、异议、订单）。
直播结束时可用 generate_report_tool 生成复盘报告。

## 📋 注意事项
- 始终使用中文与观众交流，语气亲切自然，像真人主播
- 恰当地使用 emoji 增强表达效果
- 回答弹幕问题时先理解用户真实需求，再给出针对性回答
- 不主动编造商品信息，不确定的内容要调用工具查询
- 不能做出虚假承诺（如保证效果、违规承诺退款等）
- 鼓励观众互动和下单，但不使用过度夸张的催促话术
- 如果遇到无法回答的技术问题，坦诚说明并建议联系客服

## 🔧 可用工具
你拥有以下工具可供调用：

1. search_product_tool(query) - 搜索商品
2. get_product_detail_tool(product_id) - 获取商品详情（需要商品ID，如P001）
3. list_all_products_tool() - 列出所有商品
4. calculate_promotion_tool(product_id, quantity) - 计算优惠价格
5. check_stock_tool(product_id) - 查询库存
6. recommend_products_tool(category, budget, scenario) - 推荐商品
7. record_session_log(event_type, content, product_id) - 记录直播事件
8. get_session_log_tool() - 查看记录
9. clear_session_log_tool() - 清除记录开始新直播
10. generate_report_tool(duration_minutes) - 生成复盘报告
"""


class LiveCommerceState(MessagesState):
    """直播电商 Agent 状态。"""
    remaining_steps: RemainingSteps


def wrap_model(model: BaseChatModel):
    """将模型绑定工具，并注入系统提示词。"""
    bound_model = model.bind_tools(LIVE_COMMERCE_TOOLS)
    preprocessor = RunnableLambda(
        lambda state: [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"],
        name="StateModifier",
    )
    return preprocessor | bound_model


async def acall_model(state: LiveCommerceState, config: RunnableConfig) -> LiveCommerceState:
    """调用 LLM 模型生成回复。"""
    # Set session key for per-session log isolation in tools
    thread_id = config["configurable"].get("thread_id", "default")
    set_session_key(thread_id)
    model = get_model(config["configurable"].get("model", settings.DEFAULT_MODEL))
    model_runnable = wrap_model(model)
    response = await model_runnable.ainvoke(state, config)

    if state["remaining_steps"] < 2 and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="抱歉，处理这个请求需要的步骤较多，请简化一下问题或分步提问 😊",
                )
            ]
        }
    return {"messages": [response]}


# 构建图
agent = StateGraph(LiveCommerceState)
agent.add_node("model", acall_model)
agent.add_node("tools", ToolNode(LIVE_COMMERCE_TOOLS))
agent.set_entry_point("model")

# 模型执行完后，如果有工具调用则走 tools，否则结束
def pending_tool_calls(state: LiveCommerceState) -> Literal["tools", "done"]:
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage):
        raise TypeError(f"Expected AIMessage, got {type(last_message)}")
    if last_message.tool_calls:
        return "tools"
    return "done"

agent.add_conditional_edges("model", pending_tool_calls, {"tools": "tools", "done": END})
agent.add_edge("tools", "model")

live_commerce_agent = agent.compile()
