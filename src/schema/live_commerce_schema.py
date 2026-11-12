"""
直播电商数据模型（Live Commerce Data Schema）
定义所有新增功能模块的数据结构，包括商品、弹幕、私信、用户会话、预设话术、黑名单、评价、设置等
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


# ============================================================
# SKU 规格模型
# ============================================================
class SkuItem(BaseModel):
    """商品 SKU：同一商品的不同规格，各自绑定价格、库存与成本。

    SKU 是本次商品/订单迭代的核心维度：订单行、库存流水、退款、渠道与毛利
    统计都以 skuId 为最小粒度。历史商品没有 SKU 时，DataManager 在加载/保存时
    自动补一个「默认规格」SKU，保证旧数据与旧前端继续可用。
    """
    skuId: str = Field(description="SKU 唯一标识（商品内唯一）")
    spec: str = Field(default="默认规格", description="规格描述，如「500g/箱」「红色 / M」")
    price: float = Field(default=0, ge=0, description="该规格直播价")
    originalPrice: float = Field(default=0, ge=0, description="该规格原价")
    stock: int = Field(default=0, ge=0, description="该规格可售库存（下单即锁）")
    costPrice: float = Field(default=0, ge=0, description="该规格进货成本，用于单品毛利")
    isEnabled: bool = Field(default=True, description="是否启用（停用后观众不可选）")


class ProductItem(BaseModel):
    """商品数据模型（抖音式商品，含销量/评分/多图/卖点）"""
    productId: str = Field(description="商品唯一标识")
    name: str = Field(description="商品名称")
    originalPrice: float = Field(description="原价")
    price: float = Field(description="售卖价格")
    stock: int = Field(description="库存数量（= 各 SKU 库存之和，由后端同步）")
    category: str = Field(default="", description="品类")
    description: str = Field(default="", description="商品描述/卖点")
    specs: list[str] = Field(default_factory=list, description="商品规格列表，每项形如 '键: 值'")
    imageUrl: str = Field(default="", description="商品主图链接（可空，空则前端用品类 emoji 渐变主图）")
    tags: list[str] = Field(default_factory=list, description="商品标签")
    tagGroup: str = Field(default="", description="商品标签分组（如「主推爆款」「清仓专区」）")
    # 默认改为 True：观众端只下发已上架商品，而运营台有两个新建商品入口
    # （「添加商品」显式传 isOnSale:false、「商品表单」干脆不带这个键），
    # 落到这个默认值上就等于「新建即对观众隐身」，且页面上没有任何提示。
    # 上架/下架是运营台的显式操作，默认应当是可见。
    isOnSale: bool = Field(default=True, description="是否当前上架中")
    addedTime: str = Field(default="", description="上架时间")
    isInLibrary: bool = Field(default=True, description="是否在备选商品库中")
    # ---- 以下为抖音式商品详情扩展字段（均带默认值，向后兼容旧 JSON）----
    soldCount: int = Field(default=0, description="累计销量（真实成交，来自订单聚合）")
    soldBase: int = Field(
        default=0,
        description=(
            "展示用销量基数：只叠加到观众端商品卡的「已售 N 件」，"
            "不参与任何经营统计、报告或运营台数字。"
            "存在的意义是模拟单停用后，观众端仍要有合理的『已售』观感。"
        ),
    )
    rating: float = Field(default=5.0, ge=0, le=5, description="平均评分 1-5")
    reviewCount: int = Field(default=0, description="评价数量")
    images: list[str] = Field(default_factory=list, description="多图 URL 列表（可空）")
    sellingPoints: list[str] = Field(default_factory=list, description="独立卖点列表（卡片高亮展示）")
    # ---- SKU / 成本（向后兼容：旧 JSON 没有这两个字段时由后端补默认 SKU）----
    skus: list[SkuItem] = Field(default_factory=list, description="SKU 规格列表；空列表时后端补默认规格")
    costPrice: float = Field(default=0, ge=0, description="商品默认进货成本（SKU 未设置成本时兜底）")


# ============================================================
# 收货地址模型
# ============================================================
class AddressItem(BaseModel):
    """观众收货地址（一个观众可保存多条，下单时自主选择）"""
    addressId: str = Field(description="地址唯一标识")
    receiverName: str = Field(description="收货人姓名")
    phone: str = Field(description="收货手机号")
    region: str = Field(default="", description="省 / 市 / 区（可空）")
    detail: str = Field(description="详细地址")
    isDefault: bool = Field(default=False, description="是否默认地址")
    createTime: str = Field(default="", description="创建时间")


# ============================================================
# 商单/订单模型
# ============================================================
class OrderLineItem(BaseModel):
    """订单行：一单可含多件商品/多个 SKU（购物车合并结算）。

    每个字段都是**下单瞬间的快照**：商品之后改名、改价、删 SKU 都不影响
    历史订单展示与退款金额计算。优惠按行金额比例分摊，行金额之和严格等于
    订单实付，避免四舍五入后对不上账。
    """
    productId: str = Field(description="商品ID")
    productName: str = Field(default="", description="商品名称快照")
    productImage: str = Field(default="", description="商品图快照")
    skuId: str = Field(default="", description="SKU ID 快照")
    skuSpec: str = Field(default="", description="规格描述快照")
    quantity: int = Field(default=1, ge=1, description="购买数量")
    unitPrice: float = Field(default=0, description="直播价单价")
    originalUnitPrice: float = Field(default=0, description="原价单价")
    amount: float = Field(default=0, description="行小计（优惠前，unitPrice×quantity）")
    originalAmount: float = Field(default=0, description="行原价小计（originalUnitPrice×quantity）")
    discountAmount: float = Field(default=0, description="该行分摊到的优惠金额")
    totalAmount: float = Field(default=0, description="该行实付（amount-discountAmount）")
    costPrice: float = Field(default=0, description="成本单价快照")
    costAmount: float = Field(default=0, description="成本小计（用于单品毛利）")
    channel: str = Field(default="live_cart", description="该行来源渠道：live_cart/replay/product_card/cart")


class OrderItem(BaseModel):
    """订单/商单数据模型（模拟成交为主，观众端真实下单为辅）"""
    orderId: str = Field(description="订单唯一标识")
    orderNo: str = Field(default="", description="订单编号（如 OMS20260904xxxx）")
    productId: str = Field(description="商品ID")
    productName: str = Field(default="", description="商品名称")
    productImage: str = Field(default="", description="下单时商品首图快照（便于订单/发货页展示，可空）")
    userId: str = Field(default="", description="买家ID（观众端真实单有值，模拟单为空）")
    userName: str = Field(default="", description="买家昵称")
    quantity: int = Field(default=1, ge=1, description="购买数量")
    unitPrice: float = Field(default=0, description="成交单价")
    totalAmount: float = Field(default=0, description="订单总金额")
    status: Literal["pending_pay", "paid", "shipped", "completed", "refunded", "canceled"] = Field(
        default="paid", description="订单状态"
    )
    payTime: str = Field(default="", description="支付时间")
    shipTime: str = Field(default="", description="发货时间")
    createTime: str = Field(description="下单时间")
    isVirtual: bool = Field(default=True, description="是否为模拟订单")
    # ---- 订单来源隔离：真实观众订单 / AI 模拟测试订单 ----
    source: Literal["real", "ai_test"] = Field(
        default="real",
        description="订单来源：real=真实观众订单；ai_test=AI 模拟测试订单（不参与统计，单独展示）",
    )
    # ---- 收货信息快照（下单时从观众地址簿复制，地址后续被改/删不影响历史订单）----
    addressId: str = Field(default="", description="下单时选择的收货地址ID")
    receiverName: str = Field(default="", description="收货人姓名快照")
    receiverPhone: str = Field(default="", description="收货手机号快照")
    receiverAddress: str = Field(default="", description="收货地址快照（地区+详细地址）")
    # ---- 退款申请：与 order.status 正交，避免影响既有成交统计口径 ----
    refundStatus: Literal["none", "pending", "approved", "rejected"] = Field(
        default="none", description="退款申请状态：none/pending/approved/rejected"
    )
    refundReason: str = Field(default="", description="观众提交的退款原因")
    refundApplyTime: str = Field(default="", description="退款申请时间")
    refundReviewTime: str = Field(default="", description="主播审核时间")
    refundRejectReason: str = Field(default="", description="拒绝退款时的说明")
    refundType: str = Field(default="", description="售后类型：refund=仅退款 / return=退货退款")
    # ---- 商品/订单迭代扩展字段（均带默认值，向后兼容旧 JSON）----
    items: list[OrderLineItem] = Field(
        default_factory=list,
        description="订单行（购物车多商品合并结算）；旧的单商品订单为空，读取时按顶层字段兜底",
    )
    skuId: str = Field(default="", description="首个商品 SKU ID（单商品订单兼容字段）")
    skuSpec: str = Field(default="", description="首个商品规格快照（兼容字段）")
    goodsAmount: float = Field(default=0, description="优惠前商品金额")
    originalAmount: float = Field(default=0, description="原价总额（用于展示优惠力度）")
    discountAmount: float = Field(default=0, description="优惠券/满减分摊总额")
    couponId: str = Field(default="", description="使用的优惠券ID")
    couponName: str = Field(default="", description="使用的优惠券名称")
    channel: str = Field(default="live_cart", description="订单主渠道：live_cart/replay/product_card/cart")
    costAmount: float = Field(default=0, description="订单总成本（用于毛利统计）")
    stockDeducted: bool = Field(
        default=False,
        description="该单库存是否已锁定/扣减；取消/退款仅回补已扣减的库存，避免旧单重复回补",
    )
    # ---- 订单溯源 / 阶段归因（迭代新增）----
    sourceDanmakuId: str = Field(
        default="", description="下单前该观众最近一条提问弹幕ID，用于「提问→下单」溯源")
    stage: str = Field(default="", description="下单时所属直播阶段（warmup/flash_sale/...）")
    sessionId: str = Field(default="", description="下单时所属直播场次ID")
    refundReasonCategory: str = Field(
        default="", description="退款原因分类（quality/size/logistics/price/no_need/other）")
    payRemindTime: str = Field(default="", description="最近一次 AI 私信催付时间（空=未催过）")


# 订单状态标签映射（供前端 badge / 后端统计复用）
ORDER_STATUS_LABELS = {
    "pending_pay": "待付款",
    "paid": "已付款",
    "shipped": "已发货",
    "completed": "已完成",
    "refunded": "已退款",
    "canceled": "已取消",
}


# ============================================================
# 订单来源渠道
# ============================================================
ORDER_CHANNELS = [
    {"key": "live_cart", "label": "直播间小黄车"},
    {"key": "replay", "label": "直播回放"},
    {"key": "product_card", "label": "商品卡"},
    {"key": "cart", "label": "购物车"},
    {"key": "legacy", "label": "历史订单"},
]
ORDER_CHANNEL_LABELS = {c["key"]: c["label"] for c in ORDER_CHANNELS}


# ============================================================
# 优惠券规则（演示用的平台券，下单时自动选最优一张）
# ============================================================
COUPON_RULES = [
    {"couponId": "LIVE10", "name": "直播专享满99减10", "threshold": 99.0, "amount": 10.0},
    {"couponId": "LIVE30", "name": "直播专享满299减30", "threshold": 299.0, "amount": 30.0},
    {"couponId": "LIVE80", "name": "直播专享满599减80", "threshold": 599.0, "amount": 80.0},
]


def best_coupon(amount: float) -> dict:
    """返回满足门槛且优惠金额最大的券；不满足任何门槛时返回空券。"""
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0.0
    eligible = [c for c in COUPON_RULES if amount >= c["threshold"]]
    if not eligible:
        return {"couponId": "", "name": "", "threshold": 0.0, "amount": 0.0}
    return max(eligible, key=lambda c: c["amount"])


# ============================================================
# 购物车 / 库存流水模型
# ============================================================
class CartLineItem(BaseModel):
    """购物车行（按观众 userId 持久化；productId + skuId 唯一）。"""
    cartItemId: str = Field(default="", description="购物车行ID")
    productId: str = Field(description="商品ID")
    skuId: str = Field(default="", description="SKU ID；空串=默认规格")
    quantity: int = Field(default=1, ge=1, description="数量")
    channel: str = Field(default="live_cart", description="加购来源渠道")
    addedTime: str = Field(default="", description="加购时间")


class StockLogItem(BaseModel):
    """库存变动流水：记录每一笔由订单/取消/退款引起的库存变更。"""
    logId: str = Field(description="流水ID")
    productId: str = Field(description="商品ID")
    productName: str = Field(default="", description="商品名称快照")
    skuId: str = Field(default="", description="SKU ID")
    skuSpec: str = Field(default="", description="规格快照")
    change: int = Field(description="变动件数：负数=扣减，正数=回补")
    beforeStock: int = Field(default=0, description="变更前库存")
    afterStock: int = Field(default=0, description="变更后库存")
    reason: str = Field(description="变动原因：order_create/order_cancel/refund/manual_update")
    orderId: str = Field(default="", description="关联订单ID")
    orderNo: str = Field(default="", description="关联订单号")
    operator: str = Field(default="system", description="操作人")
    timestamp: str = Field(description="变动时间")


# ============================================================
# 观众账户（钱包）模型
# ============================================================
class ViewerAccount(BaseModel):
    """观众端账户（按浏览器持久 userId 存后端），余额用于真实下单支付"""
    userId: str = Field(description="观众唯一标识（viewer_xxx）")
    userName: str = Field(default="", description="观众昵称")
    avatar: str = Field(default="", description="观众头像（图片URL / emoji SVG dataURI）")
    balance: float = Field(default=2000.0, ge=0, description="账户余额（元）")
    registerTime: str = Field(default="", description="注册时间")
    addresses: list[AddressItem] = Field(default_factory=list, description="收货地址列表（可保存多条）")


# ============================================================
# 弹幕模型
# ============================================================
class DanmakuItem(BaseModel):
    """弹幕数据模型"""
    danmakuId: str = Field(description="弹幕唯一标识")
    userId: str = Field(description="用户ID")
    userName: str = Field(description="用户昵称")
    avatar: str = Field(default="", description="用户头像链接")
    content: str = Field(description="弹幕内容")
    timestamp: str = Field(description="发送时间")
    isVirtual: bool = Field(default=False, description="是否为AI生成的虚拟弹幕（前端绝对不展示此字段）")
    isQuestion: bool = Field(default=False, description="是否被判定为需主播回应的问句（观众端自动回复与高频问题统计的闸门）")
    isReplied: bool = Field(default=False, description="是否已回复")
    replyContent: str = Field(default="", description="AI回复内容")
    replyTime: str = Field(default="", description="回复时间")
    source: str = Field(default="danmaku", description="来源（danmaku/private）")
    isViolation: bool = Field(default=False, description="是否违规弹幕")
    violationReason: str = Field(default="", description="违规原因")
    # ---- 场次绑定 / 复盘筛选扩展字段（均带默认值，向后兼容旧 JSON）----
    sessionId: str = Field(
        default="",
        description="所属直播场次ID（开播时生成，按场次隔离存储；未开播时的弹幕为空串）",
    )
    sentiment: str = Field(
        default="",
        description="观众情绪判定：positive / neutral / negative（纯规则词表，空串=尚未判定）",
    )
    stage: str = Field(default="", description="所属直播阶段（warmup/flash_sale/...）")
    isMarked: bool = Field(default=False, description="主播是否将该弹幕标记为重点")


# ============================================================
# 私信模型
# ============================================================
class PrivateMessageItem(BaseModel):
    """私信消息数据模型"""
    messageId: str = Field(description="消息唯一标识")
    userId: str = Field(description="用户ID")
    userName: str = Field(description="用户昵称")
    avatar: str = Field(default="", description="用户头像")
    content: str = Field(description="消息内容")
    timestamp: str = Field(description="发送时间")
    isFromUser: bool = Field(default=True, description="是否是用户发送的")
    replyType: str = Field(default="ai", description="回复类型（ai/human/system）")
    isPreset: bool = Field(default=False, description="是否使用了预设话术")
    presetId: str = Field(default="", description="使用的预设话术ID")


# ============================================================
# 用户会话模型
# ============================================================
class UserSessionItem(BaseModel):
    """用户会话数据模型"""
    sessionId: str = Field(description="会话唯一标识")
    userId: str = Field(description="用户ID")
    userName: str = Field(description="用户昵称")
    avatar: str = Field(default="", description="用户头像")
    status: Literal["ai_reception", "human_reception", "closed", "blacklisted"] = Field(
        default="ai_reception", description="会话状态"
    )
    userLevel: Literal["high_intent", "potential", "wait_and_see", "picky", "just_ask"] = Field(
        default="potential", description="用户分层标签"
    )
    lastMessage: str = Field(default="", description="最后一条消息摘要")
    lastMessageTime: str = Field(default="", description="最后消息时间")
    unreadCount: int = Field(default=0, description="未读消息数")
    productId: str = Field(default="", description="当前咨询的商品ID")
    lastHumanApplyTime: str = Field(default="", description="上次申请人工的时间")
    aiFailCount: int = Field(default=0, description="AI连续回答失败次数")
    historyProducts: list[str] = Field(default_factory=list, description="该用户咨询过的历史商品ID列表")


# ============================================================
# 预设话术模型
# ============================================================
class PresetPhraseItem(BaseModel):
    """预设话术数据模型"""
    presetId: str = Field(description="话术唯一标识")
    category: Literal["general", "price", "quality", "logistics", "aftersale", "other"] = Field(
        default="general", description="分类"
    )
    keywords: list[str] = Field(default_factory=list, description="匹配关键词")
    answer: str = Field(default="", description="标准回复内容")
    useCount: int = Field(default=0, description="使用次数")
    lastUsedTime: str = Field(default="", description="最后使用时间")
    createTime: str = Field(default="", description="创建时间")
    isEnabled: bool = Field(default=True, description="是否启用")
    isPending: bool = Field(default=False, description="是否待编辑")
    source: str = Field(default="manual", description="来源（manual/high_freq_import）")


# ============================================================
# 黑名单模型
# ============================================================
class BlacklistItem(BaseModel):
    """黑名单数据模型"""
    blacklistId: str = Field(description="唯一标识")
    userId: str = Field(description="用户ID")
    userName: str = Field(description="用户昵称")
    reason: str = Field(default="", description="拉黑原因")
    createTime: str = Field(description="创建时间")
    operator: str = Field(default="system", description="操作人")


# ============================================================
# 评价模型
# ============================================================
class ReviewItem(BaseModel):
    """评价数据模型"""
    reviewId: str = Field(description="评价唯一标识")
    userId: str = Field(description="用户ID")
    userName: str = Field(description="用户昵称")
    avatar: str = Field(default="", description="用户头像")
    content: str = Field(description="评价内容")
    rating: int = Field(default=5, ge=1, le=5, description="评分（1-5星）")
    sentiment: Literal["positive", "neutral", "negative", "question", "suggestion"] = Field(
        default="positive", description="情感分类"
    )
    tags: list[str] = Field(default_factory=list, description="关键词标签")
    productId: str = Field(default="", description="对应商品ID")
    productName: str = Field(default="", description="商品名称")
    source: Literal["danmaku", "private", "user"] = Field(default="danmaku", description="来源（user=观众端主动提交）")
    orderId: str = Field(default="", description="关联订单ID（观众提交评价时防重复）")
    timestamp: str = Field(description="发布时间")
    isHandled: bool = Field(default=False, description="是否已处理")
    images: list[str] = Field(default_factory=list, description="买家秀图片（可选）")
    # ---- 迭代新增：场次绑定 / 后台回复 / 差评预警 ----
    sessionId: str = Field(default="", description="评价所属直播场次ID（观众评价按下单场次绑定）")
    replyContent: str = Field(default="", description="主播后台回复内容")
    replyTime: str = Field(default="", description="主播后台回复时间")


# ============================================================
# 迭代新增：AI 知识库 / 敏感词日志 / 操作日志 / 价格历史 / 观众到场记录
# ============================================================
class KnowledgeBaseItem(BaseModel):
    """AI 问答知识库：主播可维护的「标准问题 → 标准答案」。"""
    kbId: str = Field(description="知识条目唯一标识")
    question: str = Field(description="标准问题（或归并后的高频问题）")
    answer: str = Field(description="标准答案")
    keywords: list[str] = Field(default_factory=list, description="命中关键词")
    source: str = Field(default="manual", description="来源：manual/reply_correction/high_freq")
    hitCount: int = Field(default=0, description="被 AI 命中次数")
    createTime: str = Field(default="", description="创建时间")
    updateTime: str = Field(default="", description="更新时间")
    operator: str = Field(default="host", description="最后修改人")


class SensitiveLogItem(BaseModel):
    """敏感词拦截日志：记录被拦截的弹幕/私信原文与命中词。"""
    logId: str = Field(description="日志唯一标识")
    userId: str = Field(default="", description="发送人ID")
    userName: str = Field(default="", description="发送人昵称")
    content: str = Field(description="被拦截内容")
    word: str = Field(default="", description="命中的敏感词")
    reason: str = Field(default="", description="拦截原因")
    scene: str = Field(default="danmaku", description="场景：danmaku/private/review")
    sessionId: str = Field(default="", description="所属直播场次ID")
    timestamp: str = Field(description="拦截时间")


class OperationLogItem(BaseModel):
    """运营台操作日志：谁在什么时候做了什么。"""
    logId: str = Field(description="日志唯一标识")
    operator: str = Field(default="host", description="操作人账号")
    role: str = Field(default="host", description="操作人角色：host/operator")
    action: str = Field(description="动作标识，如 batch_ship / save_settings")
    target: str = Field(default="", description="操作对象（订单号/商品ID/评价ID等）")
    detail: str = Field(default="", description="操作详情")
    timestamp: str = Field(description="操作时间")


class PriceHistoryItem(BaseModel):
    """商品/SKU 历史售价记录。"""
    historyId: str = Field(description="记录唯一标识")
    productId: str = Field(description="商品ID")
    productName: str = Field(default="", description="商品名称快照")
    skuId: str = Field(default="", description="SKU ID")
    skuSpec: str = Field(default="", description="规格快照")
    oldPrice: float = Field(default=0, description="变更前售价")
    newPrice: float = Field(default=0, description="变更后售价")
    oldOriginalPrice: float = Field(default=0, description="变更前原价")
    newOriginalPrice: float = Field(default=0, description="变更后原价")
    reason: str = Field(default="manual_update", description="变更原因")
    operator: str = Field(default="host", description="操作人")
    timestamp: str = Field(description="变更时间")


class ViewerVisitItem(BaseModel):
    """观众进场/离场记录：用于观看时长与观众详情页历史。"""
    visitId: str = Field(description="记录唯一标识")
    userId: str = Field(description="观众ID")
    userName: str = Field(default="", description="观众昵称快照")
    sessionId: str = Field(default="", description="直播场次ID（未开播为空）")
    enterTime: str = Field(description="进场时间")
    leaveTime: str = Field(default="", description="离场时间（空=仍在场）")
    durationSeconds: int = Field(default=0, description="本次观看时长（秒）")


# 退款原因分类（前端下拉 + 数据分析汇总共用）
REFUND_REASON_CATEGORIES = [
    {"key": "quality", "label": "商品质量问题", "keywords": ["质量", "破损", "坏了", "瑕疵", "假货", "变质"]},
    {"key": "size", "label": "规格/尺寸不符", "keywords": ["尺寸", "规格", "大小", "不合适", "太小", "太大"]},
    {"key": "logistics", "label": "物流/发货问题", "keywords": ["物流", "快递", "发货", "太慢", "没收到", "延迟"]},
    {"key": "price", "label": "价格/优惠问题", "keywords": ["价格", "贵", "优惠", "降价", "差价"]},
    {"key": "no_need", "label": "不想要了", "keywords": ["不想要", "买错", "多买", "后悔", "不需要"]},
    {"key": "other", "label": "其他原因", "keywords": []},
]


def classify_refund_reason(text: str) -> str:
    """把自由文本退款原因归入固定分类（规则匹配，未命中返回 other）。"""
    raw = str(text or "")
    for cat in REFUND_REASON_CATEGORIES:
        if any(k in raw for k in cat["keywords"]):
            return cat["key"]
    return "other"


# ============================================================
# 直播阶段与状态
# ============================================================
LIVE_STAGES = [
    {"key": "warmup", "label": "预热介绍阶段", "icon": "🔥"},
    {"key": "flash_sale", "label": "福利秒杀阶段", "icon": "⚡"},
    {"key": "deep_dive", "label": "单品深度讲解", "icon": "🎯"},
    {"key": "interact", "label": "互动促单阶段", "icon": "💰"},
    {"key": "closing", "label": "收尾下播阶段", "icon": "👋"},
]

LIVE_STATUS = Literal["idle", "live", "paused", "ended"]

# 各阶段的AI策略描述
STAGE_STRATEGIES = {
    "warmup": "AI策略：虚拟弹幕以好奇询问、期待类问题为主，AI回复侧重介绍商品亮点和直播福利预告",
    "flash_sale": "AI策略：虚拟弹幕以价格、库存、优惠、抢购类问题为主，AI回复强调限时优惠和库存紧张",
    "deep_dive": "AI策略：虚拟弹幕以品质、规格、产地、做法等深度问题为主，AI回复详细讲解商品细节",
    "interact": "AI策略：虚拟弹幕以购买意向、下单方式、优惠叠加类为主，AI回复侧重引导下单和互动",
    "closing": "AI策略：虚拟弹幕以售后、发货时间、复购、下次直播时间类为主，AI回复总结本场直播",
}

# 主播话术类型（供前端下拉与 AI 提示词复用）
LIVE_SCRIPT_TYPES = [
    {"key": "opening", "label": "🎤 开场暖场", "prompt_hint": "欢迎观众、预告今日福利、引导点赞关注，营造直播间热闹氛围"},
    {"key": "product_intro", "label": "📦 商品介绍", "prompt_hint": "用 FAB 法则：痛点引入→卖点亮点→价格锚定，把商品讲透并种草"},
    {"key": "promo", "label": "⚡ 促销逼单", "prompt_hint": "强调限时限量优惠、对比日常价、营造紧迫感促单"},
    {"key": "interact", "label": "💬 互动提问", "prompt_hint": "抛出选择题式互动、痛点共鸣问题，引导观众扣屏/评论"},
    {"key": "objection", "label": "🤝 异议安抚", "prompt_hint": "针对观众'太贵了''担心质量''想再等等'等异议给出化解话术"},
    {"key": "closing", "label": "👋 收尾下播", "prompt_hint": "总结本场卖点、重申优惠、预告下一场/下一款，引导关注不流失"},
]




# ============================================================
# 系统设置模型
# ============================================================
class VirtualDanmakuSettings(BaseModel):
    """虚拟弹幕设置"""
    enabled: bool = True
    frequency: Literal["low", "medium", "high"] = "medium"
    style: Literal["natural", "lively", "professional"] = "natural"
    autoBoostOnFlashSale: bool = True
    nicknames: list[str] = Field(default_factory=lambda: [
        "爱吃水果", "晚风", "阿琪", "小夏", "果味少女", "阳光灿烂", "小鱼儿",
        "清风徐来", "草莓味", "奶茶控", "春天的风", "小确幸", "追梦人",
        "暖暖", "星辰大海", "快乐的鱼", "糖糖", "花间一壶酒", "吃货一枚",
        "小幸运", "浮生若梦", "浅笑安然", "静待花开", "人间烟火", "余生有你"
    ])
    avatarUrls: list[str] = Field(default_factory=lambda: [
        "https://api.dicebear.com/7.x/bottts/svg?seed=" + str(i) for i in range(1, 31)
    ])
    contentBanEnabled: bool = True


class PrivateMessageSettings(BaseModel):
    """私信设置"""
    enabled: bool = True
    historyProductRecommend: bool = True
    recommendIntervalMinutes: int = 15
    humanTransferEnabled: bool = True
    autoTransferThreshold: int = 2
    humanTimeoutMinutes: int = 8
    userLevelReplyEnabled: bool = True
    welcomeMessage: str = "您好呀~欢迎来到直播间！有什么想了解的可以随时问我哦，我给您详细介绍😊"


class AIReplySettings(BaseModel):
    """AI回复设置"""
    style: Literal["friendly", "professional", "enthusiastic"] = "enthusiastic"
    length: Literal["short", "medium", "detailed"] = "medium"
    useLLM: bool = Field(default=True, description="是否使用大模型智能生成回复（关闭则回退规则话术）")
    emotionDetectionEnabled: bool = True
    autoSendProductInfo: bool = True
    sensitiveFilterEnabled: bool = True
    customSensitiveWords: list[str] = Field(default_factory=list)
    violationFilterEnabled: bool = True


class SystemSettings(BaseModel):
    """系统设置"""
    defaultCamera: str = ""
    danmakuFontSize: Literal["small", "medium", "large"] = "medium"
    notificationMethod: Literal["sound", "toast", "both", "none"] = "both"
    liveDurationReminderEnabled: bool = True
    liveDurationReminderInterval: int = 45
    lowStockThreshold: int = 20
    smartAlertEnabled: bool = True
    autoSaveReport: bool = True
    autoGenerateReportOnEnd: bool = True
    dataAutoBackup: bool = False
    onlineCountBase: int = Field(
        default=120,
        description="「X 人在看」的模拟热度基数，最终在线人数 = 本基数 + 真实在线观众数",
    )


class LiveCommerceSettings(BaseModel):
    """直播电商全局设置"""
    # 主播对外展示资料（昵称/头像；仅用于观众端展示与主播发言身份，不改 AI 人设名）
    hostName: str = Field(default="主播", description="主播对外昵称")
    hostAvatar: str = Field(default="", description="主播对外头像（图片URL / emoji SVG dataURI）")
    virtualDanmaku: VirtualDanmakuSettings = Field(default_factory=VirtualDanmakuSettings)
    privateMessage: PrivateMessageSettings = Field(default_factory=PrivateMessageSettings)
    aiReply: AIReplySettings = Field(default_factory=AIReplySettings)
    system: SystemSettings = Field(default_factory=SystemSettings)


# ============================================================
# 快捷回复模型
# ============================================================
class QuickReplyItem(BaseModel):
    """快捷回复数据模型"""
    quickReplyId: str = Field(description="唯一标识")
    content: str = Field(description="回复内容")
    category: str = Field(default="general", description="分类")


# ============================================================
# 直播状态模型
# ============================================================
class LiveSessionState(BaseModel):
    """直播会话状态"""
    status: LIVE_STATUS = "idle"
    # 本场直播的唯一场次ID（形如 live_20261109_161230_a1b2）。
    # 弹幕、复盘都按它隔离；开播时生成，结束后保留供复盘页回看。
    sessionId: str = Field(default="", description="当前直播场次唯一ID")
    currentProductId: str = Field(default="", description="当前上架商品ID")
    currentStage: str = Field(default="warmup", description="当前直播阶段")
    startTime: str = Field(default="", description="开播时间")
    pausedTime: str = Field(default="", description="暂停时间")
    liveDuration: int = Field(default=0, description="已直播时长（秒）")
    onlineCount: int = Field(default=0, description="实时在线人数")
    totalDanmaku: int = Field(default=0, description="累计弹幕总数")
    totalPrivateMessages: int = Field(default=0, description="累计私信咨询数")
    totalAIReplies: int = Field(default=0, description="AI自动回复总数")


# ============================================================
# 实时数据推送模型
# ============================================================
class LiveStatsData(BaseModel):
    """实时统计数据。

    **全部字段均由真实事件推导**（真实弹幕 / 真实私信 / 真实订单），不再有随机漂移。
    字段名保持稳定以免牵动前端渲染；含义见 data_manager._recompute_real_stats_locked
    的逐项注释。exposureCount 已删除 —— 曝光量没有埋点数据源，用「在线人数×系数」
    之类的代理指标估算只会把估算伪装成事实，比留空更糟。
    """
    onlineCount: int = 0
    totalDanmaku: int = 0
    totalPrivateMessages: int = 0
    totalAIReplies: int = 0
    aiReplySuccessRate: float = 0.0
    avgResponseTime: float = 0.0
    virtualDanmakuCount: int = 0
    humanTransferCount: int = 0
    presetUsedCount: int = 0
    mentionCount: int = 0
    inquiryCount: int = 0
    intentScore: float = 0.0
    highFreqQuestionCount: int = 0
    potentialOrderCount: int = 0


class SmartAlert(BaseModel):
    """智能提示"""
    alertId: str = Field(description="提示唯一标识")
    type: Literal["hot_question", "concern", "surge_up", "surge_down", "low_stock"] = Field(
        description="提示类型"
    )
    title: str = Field(description="提示标题")
    message: str = Field(description="提示内容")
    timestamp: str = Field(description="触发时间")
    autoCloseSeconds: int = Field(default=15, description="自动关闭时间（秒）")


# ============================================================
# WebSocket消息模型
# ============================================================
class WSMessage(BaseModel):
    """WebSocket消息格式"""
    type: str = Field(description="消息类型")
    data: dict[str, Any] = Field(default_factory=dict, description="消息数据")
    timestamp: str = Field(default="", description="时间戳")
