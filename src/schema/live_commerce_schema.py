"""
直播电商数据模型（Live Commerce Data Schema）
定义所有新增功能模块的数据结构，包括商品、弹幕、私信、用户会话、预设话术、黑名单、评价、设置等
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


# ============================================================
# 商品模型（扩展自 data/live_commerce/products.py 的 Product dataclass）
# ============================================================
class ProductItem(BaseModel):
    """商品数据模型（抖音式商品，含销量/评分/多图/卖点）"""
    productId: str = Field(description="商品唯一标识")
    name: str = Field(description="商品名称")
    originalPrice: float = Field(description="原价")
    price: float = Field(description="售卖价格")
    stock: int = Field(description="库存数量")
    category: str = Field(default="", description="品类")
    description: str = Field(default="", description="商品描述/卖点")
    specs: list[str] = Field(default_factory=list, description="商品规格列表，每项形如 '键: 值'")
    imageUrl: str = Field(default="", description="商品主图链接（可空，空则前端用品类 emoji 渐变主图）")
    tags: list[str] = Field(default_factory=list, description="商品标签")
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


# ============================================================
# 商单/订单模型
# ============================================================
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
# 观众账户（钱包）模型
# ============================================================
class ViewerAccount(BaseModel):
    """观众端账户（按浏览器持久 userId 存后端），余额用于真实下单支付"""
    userId: str = Field(description="观众唯一标识（viewer_xxx）")
    userName: str = Field(default="", description="观众昵称")
    avatar: str = Field(default="", description="观众头像（图片URL / emoji SVG dataURI）")
    balance: float = Field(default=2000.0, ge=0, description="账户余额（元）")
    registerTime: str = Field(default="", description="注册时间")


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
