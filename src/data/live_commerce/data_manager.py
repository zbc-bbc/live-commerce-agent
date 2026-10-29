"""
直播电商数据管理与业务逻辑（Data Manager + Business Logic）
管理所有数据存储、业务逻辑、AI生成、实时分析等功能
"""

import json
import os
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from schema.live_commerce_schema import (
    LIVE_STAGES, ORDER_STATUS_LABELS, STAGE_STRATEGIES, BlacklistItem, DanmakuItem,
    LiveCommerceSettings, LiveSessionState, LiveStatsData, OrderItem, PresetPhraseItem,
    PrivateMessageItem, ProductItem, QuickReplyItem, ReviewItem, SmartAlert, UserSessionItem,
    ViewerAccount, VirtualDanmakuSettings, WSMessage,
)

# ============================================================
# 数据存储路径
# ============================================================
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

PRODUCTS_FILE = os.path.join(DATA_DIR, "products_data.json")
DANMAKU_FILE = os.path.join(DATA_DIR, "danmaku_data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings_data.json")
BLACKLIST_FILE = os.path.join(DATA_DIR, "blacklist_data.json")
PRESETS_FILE = os.path.join(DATA_DIR, "presets_data.json")
REVIEWS_FILE = os.path.join(DATA_DIR, "reviews_data.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions_data.json")
PRIVATE_MSGS_FILE = os.path.join(DATA_DIR, "private_msgs_data.json")
QUICK_REPLIES_FILE = os.path.join(DATA_DIR, "quick_replies_data.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders_data.json")
VIEWERS_FILE = os.path.join(DATA_DIR, "viewers_data.json")
# 场控提示历史。原先是纯内存，重启服务就清空；落盘后跨重启仍在，
# 但开播（start_live）会主动清空——提示属于「本场」的东西。
ALERTS_FILE = os.path.join(DATA_DIR, "alerts_data.json")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

# ── 场控提示的判定参数（集中放这里便于调；改这些不用动 _check_alerts 的逻辑）──
# 真实观众少于这个数就不判涨跌：只有 1~2 个人时，走掉一个就是「下滑 50%」，
# 报出来纯属噪声。
ALERT_MIN_REAL_VIEWERS = 3
# 真实在线观众数相对上一次检查变动超过 ±这个比例才提示
ALERT_SURGE_RATIO = 0.3
# 库存剩 ≤ 上架量的这个比例时提示。
# 用比例而不是固定件数：种子库存动辄几千，写死「≤20 件」要卖到只剩 20 才响，
# 演示和真实小场次里等于永远不触发。
ALERT_LOW_STOCK_RATIO = 0.2
# 保留的提示条数上限（内存与落盘一致）。前端只展示最近 20 条，
# 但原先 append 从不裁剪，进程活得越久内存里堆得越多。
ALERT_HISTORY_MAX = 50

# 直播间弹幕历史缓冲条数：中途进入的观众最多能拉到最近 20 条本场弹幕。
DANMAKU_HISTORY_LIMIT = 20
os.makedirs(REPORTS_DIR, exist_ok=True)


# ============================================================
# 敏感词库（内置基础敏感词）
# ============================================================
BUILTIN_SENSITIVE_WORDS = [
    "色情", "赌博", "毒品", "枪支", "违法", "暴力", "血腥", "反动",
    "诈骗", "传销", "洗钱", "假货", "走私", "偷税", "漏税",
]


# ============================================================
# 虚拟弹幕生成模板（按直播阶段和问题类型）
# ============================================================
VIRTUAL_DANMAKU_TEMPLATES = {
    "price": [
        "这个多少钱呀？", "现在买有优惠吗？", "能便宜点吗主播？",
        "什么价格？", "有折扣吗？", "怎么买最划算？",
        "直播间有专属价吗？", "现在下单多少钱？",
    ],
    "spec": [
        "这个什么规格的？", "一斤大概有几个？", "新鲜吗？",
        "产地是哪里？", "保质期多久？", "怎么保存？",
        "适合什么人用？", "多大尺寸？", "什么材质做的？",
    ],
    "usage": [
        "怎么做好吃？", "怎么用效果最好？", "使用方法复杂吗？",
        "需要搭配什么一起用吗？", "多久能看到效果？",
        "可以天天用吗？", "第一次用需要注意什么？",
    ],
    "stock": [
        "还有货吗？", "库存还多吗？", "会不会抢不到？",
        "什么时候发货？", "发什么快递？", "多久能到货？",
        "偏远地区包邮吗？",
    ],
    "compare": [
        "和超市买的有什么区别？", "比上次直播的好吗？",
        "和其他品牌比怎么样？", "这个是新款吗？",
        "升级了哪些地方？",
    ],
    "purchase_intent": [
        "想买了", "来一单！", "怎么下单？",
        "在哪拍？", "现在下单什么时候到？", "已经下单了",
        "下单后能改地址吗？", "可以买多份吗？",
    ],
}

# 各阶段虚拟弹幕类型权重
STAGE_DANMAKU_WEIGHTS = {
    "warmup": {"price": 15, "spec": 25, "usage": 20, "stock": 10, "compare": 20, "purchase_intent": 10},
    "flash_sale": {"price": 35, "spec": 10, "usage": 5, "stock": 25, "compare": 5, "purchase_intent": 20},
    "deep_dive": {"price": 10, "spec": 30, "usage": 30, "stock": 5, "compare": 15, "purchase_intent": 10},
    "interact": {"price": 15, "spec": 10, "usage": 10, "stock": 10, "compare": 5, "purchase_intent": 50},
    "closing": {"price": 10, "spec": 10, "usage": 5, "stock": 30, "compare": 5, "purchase_intent": 40},
}

# 内置昵称池（扩展版）
BUILTIN_NICKNAMES = [
    "爱吃水果", "晚风", "阿琪", "小夏", "果味少女", "阳光灿烂", "小鱼儿",
    "清风徐来", "草莓味", "奶茶控", "春天的风", "小确幸", "追梦人",
    "暖暖", "星辰大海", "快乐的鱼", "糖糖", "花间一壶酒", "吃货一枚",
    "小幸运", "浮生若梦", "浅笑安然", "静待花开", "人间烟火", "余生有你",
    "微微一笑", "盛夏光年", "牛奶咖啡", "棉花糖", "小丸子",
    "月下独酌", "风的季节", "落叶知秋", "晨曦", "暖阳",
    "星空物语", "心有灵犀", "转角遇到", "最初的梦想", "简单生活",
    "猫小姐", "狗先生", "兔兔那么可爱", "小熊软糖", "布丁奶茶",
    "彩虹糖", "泡泡糖", "棒棒糖", "甜甜圈", "冰淇淋",
    "阿花", "小明同学", "王大锤", "小李飞刀", "赵小刀",
]


# ============================================================
# 弹幕问句判定（观众端自动回复与高频问题统计共用的闸门）
# ============================================================
# 疑问标记：疑问词 / 疑问语气 / 请求讲解
_QUESTION_MARKERS = (
    "?", "？", "吗", "呢", "多少", "几", "怎么", "咋", "如何", "什么", "哪",
    "能不能", "可不可以", "有没有", "是不是", "多久", "介绍", "说说",
    "还是",  # 「A还是B」式选择问句
)

# 咨询主题词族：与 _generate_smart_reply 各分支的关键词保持一致，避免出现第二套口径
_QUESTION_TOPICS = (
    # 价格 / 优惠
    "多少钱", "价格", "价钱", "售价", "价位", "怎么卖", "什么价",
    "优惠", "打折", "折扣", "便宜", "划算", "降价",
    # 规格 / 适用人群
    "规格", "尺寸", "大小", "多重", "容量", "多少克", "适合", "能用",
    # 库存 / 物流
    "还有货", "库存", "发货", "快递", "物流", "什么时候到",
    # 产地 / 品质
    "产地", "哪里", "质量", "品质", "好不好", "怎么样", "口碑", "评价",
    # 用法 / 售后
    "怎么用", "如何使用", "用法", "怎么吃", "怎么操作",
    "退货", "退款", "保修", "维修", "售后", "坏了",
)


# ============================================================
# 下单顾虑分类（关键词表）
# ============================================================
# 与 _generate_smart_reply 的分支关键词同源：那边决定「怎么答」，这边决定
# 「复盘报告里各类顾虑各有多少条」。两处共用一份表，避免出现第二套口径。
# 顺序即匹配优先级（一条弹幕只归入最先命中的一类）。
_BARRIER_CATEGORIES = (
    {
        "type": "price_sensitive",
        "title": "价格敏感",
        "suggestion": "增加价格锚点对比，突出直播间专享价，设计阶梯优惠",
        "keywords": ("多少钱", "价格", "价钱", "售价", "价位", "怎么卖",
                     "什么价", "优惠", "打折", "折扣", "便宜", "划算", "降价", "贵"),
    },
    {
        "type": "trust_issue",
        "title": "信任不足",
        "suggestion": "展示品牌授权书和质检报告，安排已购用户分享",
        "keywords": ("质量", "品质", "正品", "靠谱", "真假", "口碑", "评价",
                     "退换", "退货", "退款", "保修", "维修", "售后", "坏了"),
    },
    {
        "type": "effect_doubt",
        "title": "效果疑虑",
        "suggestion": "增加成分科普环节，提供小样/试用装降低决策门槛",
        "keywords": ("效果", "有用吗", "管用", "有效", "真的能", "是不是骗人"),
    },
    {
        "type": "logistics_concern",
        "title": "物流顾虑",
        "suggestion": "明确发货时间和物流跟踪服务，偏远地区说明可发",
        "keywords": ("还有货", "库存", "现货", "发货", "快递", "物流",
                     "什么时候到", "多久能到", "包邮"),
    },
    {
        "type": "spec_confusion",
        "title": "规格不清",
        "suggestion": "在直播间多角度展示商品规格详情，对比尺寸",
        "keywords": ("规格", "尺寸", "大小", "多重", "容量", "多少克",
                     "适合", "能用", "什么材质", "产地"),
    },
)

# 情绪判定词表（纯规则：零依赖、零成本、可解释，且不受 LLM 可用性影响）
_POSITIVE_WORDS = (
    "好", "喜欢", "满意", "不错", "棒", "厉害", "划算", "实惠", "值", "赞",
    "支持", "推荐", "回购", "香", "爱了", "下单", "买了", "想要", "心动",
)
_NEGATIVE_WORDS = (
    "差", "贵", "失望", "不行", "退货", "退款", "骗", "坑", "垃圾", "假",
    "慢", "不好", "不值", "犹豫", "担心", "算了", "再看看",
)


def classify_barrier(content: str) -> str:
    """把一条弹幕归入某类下单顾虑；都不命中返回 "other"。"""
    t = (content or "").lower()
    for cat in _BARRIER_CATEGORIES:
        if any(k in t for k in cat["keywords"]):
            return cat["type"]
    return "other"


def classify_sentiment(content: str) -> str:
    """纯规则情绪判定 → positive / negative / neutral。"""
    t = (content or "").lower()
    neg = sum(1 for k in _NEGATIVE_WORDS if k in t)
    pos = sum(1 for k in _POSITIVE_WORDS if k in t)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def is_question_like(content: str) -> bool:
    """判断弹幕是否属于「值得主播回应」的问句。

    观众端自动回复与高频问题统计共用此闸门：只有命中的弹幕才会触发 AI 回复、
    才会计入 get_high_freq_questions() 的问题统计。闲聊、礼物文案等不命中。
    """
    t = (content or "").lower().strip()
    if not t:
        return False
    return any(k in t for k in _QUESTION_MARKERS) or any(k in t for k in _QUESTION_TOPICS)


# ============================================================
# 真实 / 模拟身份判别（运营台一切统计口径的唯一依据）
# ============================================================
# 为什么是「三值分类」而不是一个 boolean：
#   产品决策同时要求「统计只算真实用户」和「启动保留真实数据」，而这两件事的
#   集合并不相同 —— viewer_* 老账号**不**计入统计（协议层已不可登录），却**必须**
#   被保留（身上挂着真实余额与历史订单）。一个 boolean 无法同时表达，硬凑必然
#   在某一侧出错（要么老账号被当成真实用户永久虚高，要么被当成模拟数据删掉）。
#
# 各类 id 的来源（改前缀判定前务必逐一核对）：
#   v_<8hex>    服务端 viewer_auth._new_user_id 发的号 —— **唯一真实用户**
#   viewer_*    上一代观众端 id（以及 _normalize_viewer_ids 归一后的残留）
#   virtual_N   虚拟弹幕生成器的机器人 userId
#   u001..u010  generate_mock_data 造的模拟观众
#   host_001    主播本人在运营台发的弹幕/消息
#   ""          模拟订单（不绑定用户）
_VIEWER_PREFIX = "v_"
_LEGACY_PREFIX = "viewer_"
_VIRTUAL_PREFIX = "virtual_"

# 保留并落盘的 id 类型（generate_mock_data 的清理与 _save_danmaku 共用同一套口径，
# 避免「内存里留着、盘上没有」这种下次重启才暴露的错位）
_KEEP_KINDS = frozenset({"viewer", "legacy", "host"})


def classify_user_id(uid: Any) -> str:
    """把一个 userId 归到 viewer/legacy/virtual/mock/host/system/unknown 之一。

    ⚠️ **绝对不要把这个函数（或 is_real_user_id）用在 danmakuId 上**：
    `_generate_virtual_danmaku` 生成的 danmakuId 形如 `v_12_1757500000000`，
    **同样以 `v_` 开头**，与真实 userId 前缀完全撞车。对 danmakuId 调用会把
    虚拟弹幕判成真实用户。判定对象只能是 `userId`，不是 `danmakuId`。
    """
    t = (uid or "").strip()
    if not t:
        return "system"           # 模拟订单不绑定用户
    if t.startswith(_VIRTUAL_PREFIX):
        return "virtual"
    if t.startswith(_VIEWER_PREFIX):
        return "viewer"
    if t.startswith(_LEGACY_PREFIX):
        return "legacy"
    if t.startswith("host_"):
        return "host"
    if t[0] == "u" and t[1:].isdigit():
        return "mock"
    return "unknown"


def is_real_user_id(uid: Any) -> bool:
    """**统计口径**：只认服务端发号的 `v_` 真实观众。

    viewer_* 老账号返回 False —— 它们在 viewer_accounts 表里没有对应行
    （viewer_auth 只发 v_），拿不出密码、签不出 JWT、_require_viewer 永远拿不到
    这个 id，在协议层就不可达。计入「本场真实用户」会让复盘数字永久虚高且无法
    自然出清。它们仍会被 is_persistable_user_id 保留。
    """
    return classify_user_id(uid) == "viewer"


def is_persistable_user_id(uid: Any) -> bool:
    """**留盘口径**：真实观众 + 老账号 + 主播本人（即 _KEEP_KINDS）。

    模拟观众（u00x）与虚拟弹幕机器人（virtual_N）一律不落盘 —— 它们每次启动都会
    被重新生成，落盘只会让文件膨胀、并让下次启动把垃圾再读回来。
    """
    return classify_user_id(uid) in _KEEP_KINDS


def order_is_real(order: Any) -> bool:
    """订单是否属于真实用户。

    以 **userId 前缀**为准，而不是 `isVirtual` 字段 —— 判据必须与 pay_order /
    confirm_receipt / cancel_viewer_order / submit_review 里的归属校验同源，
    否则会出现「能付款却发不了货」或反之的撕裂。`isVirtual` 只作一致性校验，
    由 _audit_identity_consistency 在启动时对账并告警。
    """
    return classify_user_id(getattr(order, "userId", "")) == "viewer"


class LiveCommerceDataManager:
    """
    直播电商数据管理器
    管理所有数据的增删改查和持久化，线程安全
    """

    def __init__(self):
        # 使用可重入锁：data_manager 内存在"持锁期间再次调用同样加锁的读取方法"的路径
        # （如 start_live 在 with self._lock 内调用 get_live_status），普通 Lock 会造成自死锁。
        self._lock = threading.RLock()
        # 商品数据
        self.products: dict[str, ProductItem] = {}
        self.current_product_id: str = ""
        # 弹幕数据
        self.danmaku_list: list[DanmakuItem] = []
        self._danmaku_counter = 0
        # 直播间弹幕历史缓冲：开播时清空，之后滚动保留最近 DANMAKU_HISTORY_LIMIT 条，
        # 供中途进入的观众拉取（get_danmaku_history / danmaku_history）。
        self.live_danmaku_history: list[DanmakuItem] = []
        # 当前置顶弹幕 id（空串表示无置顶）。只在本场直播内有效，开播时清空。
        self.pinned_danmaku_id: str = ""
        # 私信数据
        self.private_messages: dict[str, list[PrivateMessageItem]] = {}
        self._msg_counter = 0
        # 用户会话
        self.user_sessions: dict[str, UserSessionItem] = {}
        self._session_counter = 0
        # 话术数据
        self.presets: dict[str, PresetPhraseItem] = {}
        self._preset_counter = 0
        # 黑名单
        self.blacklist: dict[str, BlacklistItem] = {}
        self._blacklist_counter = 0
        # 评价
        self.reviews: list[ReviewItem] = []
        self._review_counter = 0
        # 快捷回复
        self.quick_replies: dict[str, QuickReplyItem] = {}
        self._qr_counter = 0
        # 商单/订单
        self.orders: dict[str, OrderItem] = {}
        self._order_counter = 0
        # 观众账户（钱包）
        self.viewers: dict[str, ViewerAccount] = {}
        # AI 话术生成历史（内存，不入盘）
        self.scripts_history: list[dict] = []
        # 直播状态
        self.live_state: LiveSessionState = LiveSessionState()
        # 统计数据
        self.stats: LiveStatsData = LiveStatsData()
        # 高频问题统计（**真实观众**口径：运营台榜单、话术导入都读这个）
        self.high_freq_questions: dict[str, int] = {}
        # 虚拟弹幕的问题计数（平行字典）。刻意不合成嵌套 dict —— 那要连带动
        # get_high_freq_questions / _recompute_real_stats_locked / WS 两个下发点
        # 以及两个前端渲染函数，收益却只是省一个字段。
        self.virtual_freq_questions: dict[str, int] = {}
        # 智能提示列表
        self.alerts: list[SmartAlert] = []
        self._alert_counter = 0
        # 设置
        self.settings: LiveCommerceSettings = LiveCommerceSettings()
        # 敏感词列表
        self.sensitive_words: list[str] = list(BUILTIN_SENSITIVE_WORDS)
        # 违规弹幕历史
        self.violation_log: list[dict] = []
        # ── 在线人数：真实在线观众数 + 可配置模拟热度基数 ──
        # 对外数字只由这两项相加得出（见 _refresh_online_locked），不再有随机游走，
        # 因此数字随真实观众进出确定性变化。基数在 _load_all() 后按设置重读。
        self._online_base: int = 120
        self._real_online: int = 0
        # ── 场控提示（SmartAlert）的判定状态 ──
        # 上一次检查时的真实在线观众数，做涨跌比较的基准；None = 还没建立基准
        # （首轮只记录不判定，否则开播后第一条必然误报）。
        self._last_alert_real_online: Optional[int] = None
        # 库存不足是否已经报过。库存告急是**持续状态**，不做边沿判定的话
        # 每 30 秒会重复推一条一模一样的提示。
        self._low_stock_alerted: bool = False
        # ── 本场真实信号（复盘报告与统计的唯一来源，全部只由真实事件驱动）──
        # 本场登录过的真实观众（去重 userId）。WebSocket 层 _bind_user 时记入，
        # 是「本场真实观众数」唯一可靠的来源 —— 不能拿 userSessions 去数，
        # 那里混着老账号与（清理前的）模拟会话。
        self._session_real_users: set[str] = set()
        # 在线人数峰值 / 按秒累加（均值用），只在直播进行中累计
        self._online_peak: int = 0
        # 真实观众峰值（不含 _online_base 模拟基数）——复盘报告的头条数字。
        # 单独记而不是用 (peak - base) 反推：base 是设置项，直播中途被改一次反推值就错了。
        self._online_real_peak: int = 0
        self._online_sum: int = 0
        self._online_samples: int = 0
        self._online_tracking_active: bool = False
        # WebSocket回调
        self.ws_callbacks: list[callable] = []
        # 定时器引用
        self._live_timer: Optional[threading.Thread] = None
        self._danmaku_timer: Optional[threading.Thread] = None
        self._stats_timer: Optional[threading.Thread] = None
        self._alert_timer: Optional[threading.Thread] = None
        self._live_timer_running = False
        self._danmaku_running = False
        self._stats_running = False
        self._alert_running = False
        # 加载数据
        self._load_all()
        # 设置已从文件加载完毕，据此确定模拟热度基数并算出在线人数初值
        self._online_base = self._online_base_from_settings()
        self._refresh_online_locked()

    def register_ws_callback(self, callback: callable):
        """注册WebSocket推送回调"""
        self.ws_callbacks.append(callback)

    def _broadcast(self, msg_type: str, data: dict, to_user: Optional[str] = None,
                   host_only: bool = False):
        """通过WebSocket广播消息。

        路由：默认公开（所有连接可见）。
          to_user=<viewer userId> → 只发给该观众的全部连接 + 运营台
          host_only=True         → 只发给运营台
        路由标记以 "_toUser"/"_hostOnly" 附在 msg 外层，由 WS 层的 _on_data_event
        读取并剥离（剥离后才会下发，客户端看不到标记）。此处**不修改 msg**，
        因为同一份 msg 会交给所有回调，就地 pop 会影响后续回调。
        """
        msg = {
            "type": msg_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        if to_user is not None:
            msg["_toUser"] = to_user
        if host_only:
            msg["_hostOnly"] = True
        for cb in self.ws_callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def _broadcast_product_sales(self, product_id: str) -> None:
        """公开广播某商品的库存/销量变化（订单定向化之后，别的观众下单不会再触发
        本端的 order_new，商品横条的「已售 N 件」「库存」会僵住；用这条不含任何
        用户信息的公共事件补上刷新信号）。"""
        with self._lock:
            product = self.products.get(product_id)
            if product is None:
                return
            payload = {"productId": product_id, "stock": product.stock,
                       "soldCount": product.soldCount}
        self._broadcast("product_sales_changed", payload)

    def _broadcast_order(self, msg_type: str, order: OrderItem) -> None:
        """订单事件按归属定向下发：归属某观众 → 只发该观众 + 运营台；
        模拟单（userId 为空，运营台批量造的数据）→ 只发运营台。
        在此之前订单是全广播的，甲下单乙能看到甲的金额与订单号。"""
        if order.userId:
            self._broadcast(msg_type, order.model_dump(), to_user=order.userId)
        else:
            self._broadcast(msg_type, order.model_dump(), host_only=True)

    # ============================================================
    # 数据持久化
    # ============================================================
    def _load_all(self):
        """加载所有持久化数据"""
        self._load_products()
        self._load_settings()
        self._load_blacklist()
        self._load_presets()
        self._load_reviews()
        self._load_sessions()
        self._load_private_messages()
        self._load_quick_replies()
        self._load_danmaku()
        self._load_orders()
        self._load_viewers()
        self._load_alerts()
        self._normalize_viewer_ids()
        # 必须排在 _normalize_viewer_ids 之后：归一化会重指 userId，
        # 先审计的话会看到一批「未知前缀」的旧 id 而误报。
        self._audit_identity_consistency()

    def _save_json(self, filepath: str, data: Any):
        """保存JSON文件"""
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[LiveCommerce] 保存文件失败 {filepath}: {e}")

    def _load_json(self, filepath: str, default: Any = None) -> Any:
        """加载JSON文件"""
        try:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[LiveCommerce] 加载文件失败 {filepath}: {e}")
        return default if default is not None else {}

    # ---- 商品持久化 ----
    def _load_products(self):
        """加载商品数据"""
        data = self._load_json(PRODUCTS_FILE)
        if data:
            for item in data:
                product = ProductItem(**item)
                self.products[product.productId] = product
        # 从现有产品数据导入初始商品
        if not self.products:
            self._init_default_products()

    def _init_default_products(self):
        """初始化默认商品列表（从现有数据库导入）"""
        try:
            from data.live_commerce.products import ALL_PRODUCTS
            for p in ALL_PRODUCTS:
                product = ProductItem(
                    productId=p.id,
                    name=p.name,
                    originalPrice=p.price * 1.3 if p.promotions else p.price,
                    price=p.price,
                    stock=p.stock,
                    category=p.category,
                    description=p.description,
                    specs=[f"{k}: {v}" for k, v in p.specs.items()],
                    imageUrl=p.image_url or "",
                    tags=[s[:10] for s in p.selling_points[:3]],
                    # 默认上架：观众端只下发 isOnSale=True 的商品，这里若默认 False，
                    # 数据目录一旦重建（src/data/ 不进 git），13 件商品会全部对观众不可见，
                    # 叠加「未上架不能讲解」的规则就直接开不了播。
                    isOnSale=True,
                    addedTime=datetime.now().isoformat(),
                    isInLibrary=True,
                    soldBase=p.sold_base,
                )
                self.products[product.productId] = product
            self._save_products()
        except ImportError:
            pass

    def _save_products(self):
        self._save_json(PRODUCTS_FILE, [p.model_dump() for p in self.products.values()])

    # ---- 设置持久化 ----
    def _load_settings(self):
        data = self._load_json(SETTINGS_FILE)
        if data:
            self.settings = LiveCommerceSettings(**data)

    def _save_settings(self):
        self._save_json(SETTINGS_FILE, self.settings.model_dump())

    # ---- 其他数据持久化 ----
    def _load_blacklist(self):
        data = self._load_json(BLACKLIST_FILE, [])
        for item in data:
            b = BlacklistItem(**item)
            self.blacklist[b.userId] = b

    def _save_blacklist(self):
        self._save_json(BLACKLIST_FILE, [b.model_dump() for b in self.blacklist.values()])

    def _load_presets(self):
        data = self._load_json(PRESETS_FILE, [])
        for item in data:
            p = PresetPhraseItem(**item)
            self.presets[p.presetId] = p

    def _save_presets(self):
        self._save_json(PRESETS_FILE, [p.model_dump() for p in self.presets.values()])

    def _load_reviews(self):
        data = self._load_json(REVIEWS_FILE, [])
        self.reviews = [ReviewItem(**r) for r in data]
        if self.reviews:
            # 恢复计数器，避免 reviewId 重号（观众评价的 id 里带 _review_counter + 1）
            self._review_counter = max(len(self.reviews), self._review_counter)

    def _save_reviews(self):
        self._save_json(REVIEWS_FILE, [r.model_dump() for r in self.reviews])

    def _load_sessions(self):
        data = self._load_json(SESSIONS_FILE, [])
        for item in data:
            s = UserSessionItem(**item)
            self.user_sessions[s.userId] = s
        # 恢复计数器，避免新建会话的 sessionId 与历史撞车（对齐 _load_private_messages
        # 里 _msg_counter 的做法）。不恢复的话重启后第一个新会话又叫 session_1，
        # 和盘上已有的 session_1 重号。
        used = [int(s.sessionId.split("_")[-1]) for s in self.user_sessions.values()
                if s.sessionId.startswith("session_") and s.sessionId.split("_")[-1].isdigit()]
        if used:
            self._session_counter = max(max(used), self._session_counter)

    def _save_sessions(self):
        """只落盘真实/老账号/主播的会话（口径同 `_save_private_messages`）。

        u001~u010 模拟观众的会话每次启动都由 generate_mock_data 重建，落盘只是垃圾，
        还会在下次启动时被 `_load_sessions` 读回来、与重建的那份并存。
        """
        self._save_json(SESSIONS_FILE, [
            s.model_dump() for uid, s in self.user_sessions.items()
            if is_persistable_user_id(uid)])

    def _load_private_messages(self):
        data = self._load_json(PRIVATE_MSGS_FILE, {})
        for uid, msgs in data.items():
            self.private_messages[uid] = [PrivateMessageItem(**m) for m in msgs]
        # 恢复计数器，避免 messageId 与历史的撞车
        total = sum(len(v) for v in self.private_messages.values())
        if total:
            self._msg_counter = max(total, self._msg_counter)
        # 私信与会话是两份文件，可能对不上。会话原先只在 mark_user_messages_read
        # 里落盘，观众发来的消息虽然落了盘、它的会话却可能还停在内存里没写出去 ——
        # 重启后收件箱左列（由 user_sessions 生成）就没有这一行，主播点不进去，
        # 盘上的私信等于失联。这里以私信为准，把缺的会话补回来，让
        # 「有私信必有会话」成为结构上的不变量，而不是靠每个写入口记得落盘。
        for uid, msgs in self.private_messages.items():
            if uid in self.user_sessions or not msgs:
                continue
            self._get_or_create_session(uid, msgs[-1].userName, msgs[-1].avatar)

    def _save_private_messages(self):
        """只落盘真实/老账号/主播的私信（模拟会话每次启动都会重建，落盘只是垃圾）。"""
        data = {
            uid: [m.model_dump() for m in msgs]
            for uid, msgs in self.private_messages.items()
            if is_persistable_user_id(uid) and msgs
        }
        self._save_json(PRIVATE_MSGS_FILE, data)

    def _load_quick_replies(self):
        data = self._load_json(QUICK_REPLIES_FILE, [])
        self.quick_replies = {q["quickReplyId"]: QuickReplyItem(**q) for q in data}

    def _save_quick_replies(self):
        self._save_json(QUICK_REPLIES_FILE, [q.model_dump() for q in self.quick_replies.values()])

    def _load_danmaku(self):
        data = self._load_json(DANMAKU_FILE, [])
        self.danmaku_list = [DanmakuItem(**d) for d in data]
        if self.danmaku_list:
            # 恢复计数器，避免 danmakuId 重号（模拟弹幕的 id 里带 _danmaku_counter）
            self._danmaku_counter = max(len(self.danmaku_list), self._danmaku_counter)

    def _save_danmaku(self):
        """只落盘真实观众 / 老账号 / 主播的弹幕，并只保留最近 1000 条。

        过滤掉模拟与虚拟弹幕有两层原因：① 它们每次启动都会被重新生成，落盘只会让
        文件膨胀、并在下次启动时把垃圾再读回内存；② 反复序列化会带来无谓的写盘。
        """
        recent = [d for d in self.danmaku_list if is_persistable_user_id(d.userId)][-1000:]
        self._save_json(DANMAKU_FILE, [d.model_dump() for d in recent])

    # ---- 场控提示持久化 ----
    def _load_alerts(self):
        data = self._load_json(ALERTS_FILE, [])
        try:
            loaded = [SmartAlert(**a) for a in data]
        except Exception as e:  # noqa: BLE001
            # 单个条目损坏不该让整份历史丢失，也不该拦住启动
            print(f"[LiveCommerce] 场控提示历史解析失败，已忽略: {e}")
            loaded = []
        self.alerts = loaded[-ALERT_HISTORY_MAX:]

    def _save_alerts(self):
        self._save_json(ALERTS_FILE, [a.model_dump() for a in self.alerts[-ALERT_HISTORY_MAX:]])

    def _load_orders(self):
        data = self._load_json(ORDERS_FILE, [])
        self.orders = {o["orderId"]: OrderItem(**o) for o in data}
        if self.orders:
            # 恢复计数器，避免单号/ID 冲突
            self._order_counter = max(len(self.orders), self._order_counter)

    def _save_orders(self):
        self._save_json(ORDERS_FILE, [o.model_dump() for o in self.orders.values()])

    # ---- 观众账户（钱包）持久化 ----
    def _load_viewers(self):
        data = self._load_json(VIEWERS_FILE, [])
        self.viewers = {}
        if isinstance(data, list):
            for item in data:
                try:
                    a = ViewerAccount(**item)
                    self.viewers[a.userId] = a
                except Exception:
                    continue

    def _save_viewers(self):
        self._save_json(VIEWERS_FILE, [a.model_dump() for a in self.viewers.values()])

    def _normalize_viewer_ids(self):
        """归并观众 userId 的历史脏数据（一次性、幂等）。

        早期观众端把 localStorage 里**不带前缀**的 token 拼上 'viewer_' 之后，又把
        **整串**写回了 localStorage，于是从第二次加载起 id 变成 `viewer_viewer_xxx`，
        首次加载建立的 `viewer_xxx` 被孤立。实测 viewers_data.json 里
        `viewer_mts145mw842` 与 `viewer_viewer_mts145mw842` 并存即此 bug 的产物。

        归并规则：
        - 反复剥离 'viewer_' 前缀得到规范 id；
        - 撞键时保留**双前缀那份**——它才是从第二次加载起一直被使用、余额和订单真正
          挂着的账户；registerTime 取两者较早的，余额取**较大值**而非相加（两份都可能
          是「新观众默认 2000」，相加会凭空翻倍）；
        - orders / reviews / danmaku / blacklist / private_messages / user_sessions 上的
          旧 userId 一并重指到规范 id，否则历史订单会变成查不到的孤儿。
        """
        with self._lock:
            groups: dict[str, list[str]] = {}
            for uid in list(self.viewers.keys()):
                canon = uid
                while canon.startswith("viewer_viewer_"):
                    canon = canon[len("viewer_"):]
                groups.setdefault(canon, []).append(uid)

            remap: dict[str, str] = {}
            merged = 0
            for canon, olds in groups.items():
                for old in olds:
                    if old != canon:
                        remap[old] = canon
                if len(olds) == 1 and olds[0] == canon:
                    continue  # 已是规范 id，无脏数据
                accounts = [self.viewers[o] for o in olds]
                # 优先双前缀那份（活跃账户），否则退化为唯一那条
                survivor = next((self.viewers[o] for o in olds if o != canon), accounts[0])
                survivor.userId = canon
                survivor.balance = max(a.balance for a in accounts)
                regs = [a.registerTime for a in accounts if a.registerTime]
                if regs:
                    survivor.registerTime = min(regs)
                survivor.userName = next(
                    (a.userName for a in accounts if a.userName and a.userName != "观众"),
                    survivor.userName,
                )
                survivor.avatar = next((a.avatar for a in accounts if a.avatar), survivor.avatar)
                self.viewers[canon] = survivor
                for old in olds:
                    if old != canon:
                        self.viewers.pop(old, None)
                merged += 1

            if not remap:
                return

            # 重指所有引用旧 id 的记录
            for coll in (self.orders.values(), self.danmaku_list, self.reviews,
                         self.blacklist.values()):
                for obj in coll:
                    old = getattr(obj, "userId", None)
                    if old in remap:
                        obj.userId = remap[old]

            for uid in list(self.private_messages.keys()):
                target = remap.get(uid)
                if not target:
                    continue
                msgs = self.private_messages.pop(uid)
                for m in msgs:
                    m.userId = target
                self.private_messages.setdefault(target, []).extend(msgs)

            for uid in list(self.user_sessions.keys()):
                target = remap.get(uid)
                if not target:
                    continue
                sess = self.user_sessions.pop(uid)
                sess.userId = target
                self.user_sessions.setdefault(target, sess)

            # 落盘，让磁盘上的脏数据也一并消失（幂等，下次启动不会再触发）
            self._save_viewers()
            self._save_orders()
            self._save_sessions()
            print(
                f"[LiveCommerce] 观众 userId 归一化完成：合并 {merged} 组，"
                f"重指 {len(remap)} 个旧 id（"
                + ", ".join(f"{o}->{n}" for o, n in remap.items()) + "）"
            )

    def _audit_identity_consistency(self) -> dict:
        """启动时对账「每个集合里有多少真实 / 多少模拟」，并纠正 isVirtual 矛盾。

        这条日志本身就是验收标准：任何一次统计口径改动若跑偏，都会在这里露馅
        （例如真实订单数突然变成 237）。打印之外只做**内存内**纠正，不回写盘 ——
        启动即写盘会让「看一眼日志」变成一个有副作用的操作。
        """
        counts: dict[str, dict[str, int]] = {}
        inconsistent: list[str] = []

        for name, coll in (("danmaku", list(self.danmaku_list)),
                           ("reviews", list(self.reviews)),
                           ("sessions", list(self.user_sessions.values()))):
            bucket: dict[str, int] = {}
            for obj in coll:
                kind = classify_user_id(getattr(obj, "userId", ""))
                bucket[kind] = bucket.get(kind, 0) + 1
            counts[name] = bucket

        # 订单单独按 order_is_real 二分。不用 classify_user_id 的桶：模拟单的 userId 是空的，
        # 落在 "system" 桶里，日志会显示成 system=133 而不是 mock=133 —— 那正是这个审计
        # 最该一眼看清的量。订单口径本来就只有「真实 / 非真实」两种。
        real_orders = sum(1 for o in self.orders.values() if order_is_real(o))
        counts["orders"] = {"real": real_orders, "mock": len(self.orders) - real_orders}

        # 一致性：order_is_real 与 isVirtual 应当互为反面，矛盾时以 userId 前缀为准
        for o in self.orders.values():
            real = order_is_real(o)
            if real == bool(o.isVirtual):
                if len(inconsistent) < 5:
                    inconsistent.append(
                        f"{o.orderId}(userId={o.userId!r} isVirtual={o.isVirtual})"
                    )
                o.isVirtual = not real

        def _fmt(name: str) -> str:
            b = counts.get(name) or {}
            if not b:
                return f"{name} (空)"
            return f"{name} " + " ".join(f"{k}={v}" for k, v in sorted(b.items()))

        summary = {
            "orders": counts.get("orders", {}),
            "danmaku": counts.get("danmaku", {}),
            "reviews": counts.get("reviews", {}),
            "sessions": counts.get("sessions", {}),
            "inconsistent": len(inconsistent),
        }
        # flush=True：这条日志是验收标准，而 stdout 被重定向到文件时是块缓冲的 ——
        # 不 flush 就会卡在缓冲区里，让"看一眼日志"变成"什么都看不到"。
        print(
            "[LiveCommerce] 身份审计: "
            + _fmt("orders") + " | " + _fmt("danmaku") + " | " + _fmt("reviews")
            + f" | inconsistent={len(inconsistent)}",
            flush=True,
        )
        if inconsistent:
            print("[LiveCommerce] 身份审计 · isVirtual 矛盾样本（已按 userId 前缀纠正）: "
                  + ", ".join(inconsistent), flush=True)
        return summary

    def get_identity_audit(self) -> dict:
        """只统计、不纠正不打印的身份快照（供自检探针反复读取，无副作用）。"""
        def bucket(coll, attr="userId"):
            out: dict[str, int] = {}
            for obj in coll:
                k = classify_user_id(getattr(obj, attr, ""))
                out[k] = out.get(k, 0) + 1
            return out
        return {
            "orders": bucket(self.orders.values()),
            "danmaku": bucket(self.danmaku_list),
            "reviews": bucket(self.reviews),
            "sessions": bucket(self.user_sessions.values()),
            "messages": bucket(
                [m for msgs in self.private_messages.values() for m in msgs]
            ),
        }

    # ============================================================
    # 会话边界（本场 vs 历史）
    # ============================================================
    def _in_session(self, timestamp: str) -> bool:
        """时间戳是否落在当前这一场直播内。

        弹幕/私信一旦落盘，内存里就会混着上一场的真实数据；没有这道边界，
        「本场统计」会随重启不断累积，复盘报告变成历史总和而不是本场成绩。
        未开播（没有 startTime）、或时间戳无法解析时一律返回 False。
        """
        start = self.live_state.startTime
        if not start or not timestamp:
            return False
        try:
            return datetime.fromisoformat(timestamp) >= datetime.fromisoformat(start)
        except (TypeError, ValueError):
            return False

    def note_session_viewer(self, user_id: str) -> None:
        """记录一个本场登录过的真实观众（WebSocket 层 _bind_user 时调用）。"""
        if is_real_user_id(user_id):
            with self._lock:
                self._session_real_users.add(user_id)

    # ============================================================
    # 商品管理
    # ============================================================
    def next_product_id(self) -> str:
        """生成一个不与现有商品冲突的商品ID（P001、P002 …）。"""
        with self._lock:
            n = 1
            while f"P{n:03d}" in self.products:
                n += 1
            return f"P{n:03d}"

    def add_product(self, product: ProductItem) -> ProductItem:
        """新增商品。

        必须广播：HTTP / WebSocket 新建的商品若对在线观众不发信号，观众端
        要等到下一次刷新才能看到。事件只带 id 与上架状态，观众端收到后会
        重新拉取「已上架」列表，未上架商品的内容不会外泄。
        """
        with self._lock:
            self.products[product.productId] = product
            self._save_products()
        self._broadcast("product_updated", {
            "productId": product.productId,
            "isOnSale": product.isOnSale,
        })
        return product

    def update_product(self, product_id: str, data: dict) -> Optional[ProductItem]:
        """更新商品信息"""
        with self._lock:
            if product_id not in self.products:
                return None
            p = self.products[product_id]
            for k, v in data.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            self._save_products()
            payload = {"productId": product_id, "isOnSale": p.isOnSale}
        # 广播放在锁外：回调只做入队，但避免持锁期间触碰别的模块
        self._broadcast("product_updated", payload)
        return p

    def set_current_product(self, product_id: str) -> tuple[Optional[ProductItem], str]:
        """设置当前讲解商品。返回 (product, error)，成功时 error 为 ""。

        **与「上架」彻底解耦**：这里只改 current_product_id，一根手指都不碰 isOnSale。
        旧实现在这里 `for p in products: p.isOnSale = False` 再把目标置 True —— 于是
        「讲解」变成了「把其它商品全部下架」，运营台点几次讲解就偷偷下架了一批商品，
        观众端也就永远看不到「上下架」按钮的效果（两者抢同一个字段，慢的那个白干）。
        「同一时刻只有一个当前讲解商品」这个不变量的载体是 current_product_id，
        从来不是 isOnSale。

        返回 tuple 而不是单纯 Optional：调用方要能区分「商品不存在」和「未上架」，
        否则运营台只能弹一句没信息量的「切换失败」。
        """
        with self._lock:
            if product_id not in self.products:
                return None, "商品不存在"
            product = self.products[product_id]
            # 必须在改 current_product_id **之前**返回，否则会出现「拒绝了但状态已改」
            if not product.isOnSale:
                return None, "商品已下架，请先上架再讲解"
            if self.current_product_id == product_id:
                return product, ""  # 幂等：重复点「讲解」不再重复广播
            self.current_product_id = product_id
            # 刻意不 _save_products()：原来那行只是为了把 isOnSale 落盘，而 current_product_id
            # 全项目没有任何落盘路径（_save_products 只写商品数组）。留着等于每次点「讲解」
            # 白写一遍整表。
            self._broadcast("product_on_sale", {
                "productId": product_id,
                "name": product.name,
                "price": product.price,
            })
            return product, ""

    def get_current_product(self) -> Optional[ProductItem]:
        """获取当前上架商品"""
        if self.current_product_id and self.current_product_id in self.products:
            return self.products[self.current_product_id]
        return None

    def get_product(self, product_id: str) -> Optional[ProductItem]:
        return self.products.get(product_id)

    def get_all_products(self) -> list[ProductItem]:
        return list(self.products.values())

    def set_on_sale(self, product_id: str, on_sale: bool) -> Optional[ProductItem]:
        """独立上架/下架某个商品（不影响其它商品的状态与当前讲解商品）。

        上下架变动通过 ``product_updated`` 实时广播给直播间所有在线连接：
        观众端收到后重新拉取商品列表（只含已上架商品），因此下架商品会立即
        从观众端消失，上架商品会立即出现。事件本身只带 id 与状态，不带商品
        详情，避免把未上架商品的信息推给观众。
        """
        with self._lock:
            if product_id not in self.products:
                return None
            p = self.products[product_id]
            p.isOnSale = on_sale
            # 若下架的是当前讲解商品，则取消 current_product_id
            if not on_sale and self.current_product_id == product_id:
                self.current_product_id = ""
            self._save_products()
        self._broadcast("product_updated", {
            "productId": product_id,
            "isOnSale": on_sale,
        })
        return p

    def delete_product(self, product_id: str) -> bool:
        with self._lock:
            if product_id not in self.products:
                return False
            del self.products[product_id]
            # 若删除的是当前讲解商品，清空 current_product_id 避免指向不存在的商品
            if self.current_product_id == product_id:
                self.current_product_id = ""
            self._save_products()
            # 必须广播：原先这里啥也不发，观众端收不到任何信号，会一直挂着一件
            # 已经不存在的「幽灵商品」，直到下次别的事件顺带触发一次刷新。删除和
            # 上下架在观众端是同一件事（不在列表里了），所以复用 product_updated。
            self._broadcast("product_updated", {"productId": product_id})
            return True

    # ============================================================
    # 弹幕管理
    # ============================================================
    def add_danmaku(self, danmaku: DanmakuItem) -> DanmakuItem:
        """添加弹幕。

        totalDanmaku 只统计**真实观众**的弹幕（判据是 userId 前缀，不是 isVirtual ——
        模拟弹幕的 isVirtual 是 False，旧写法把它们全算进了真实口径）。
        """
        with self._lock:
            self.danmaku_list.append(danmaku)
            self._remember_live_danmaku_locked(danmaku)
            self._danmaku_counter += 1
            if is_real_user_id(danmaku.userId) and not danmaku.isViolation:
                self.live_state.totalDanmaku += 1
            # 只有会被写进盘的那部分才触发落盘：虚拟弹幕每秒都可能生成，不能让它带写。
            if is_persistable_user_id(danmaku.userId):
                self._save_danmaku()
            self._broadcast("new_danmaku", danmaku.model_dump())
            return danmaku

    def get_danmaku_list(self, count: int = 200) -> list[DanmakuItem]:
        """获取最近N条弹幕"""
        with self._lock:
            return list(self.danmaku_list[-count:])

    # ── 本场弹幕历史缓冲（新观众中途进入时拉取）──────────────────────────
    def _remember_live_danmaku_locked(self, danmaku: DanmakuItem) -> None:
        """把一条弹幕记入本场历史缓冲（调用方需已持锁）。

        只保留最近 DANMAKU_HISTORY_LIMIT 条：这是「新进直播间能看到什么」，
        不是审计日志，没必要留全量。
        """
        self.live_danmaku_history.append(danmaku)
        if len(self.live_danmaku_history) > DANMAKU_HISTORY_LIMIT:
            del self.live_danmaku_history[:-DANMAKU_HISTORY_LIMIT]

    def get_danmaku_history(self, limit: int = DANMAKU_HISTORY_LIMIT) -> list[DanmakuItem]:
        """拉取本场最近弹幕（默认 20 条，上限也是 20 条）。"""
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = DANMAKU_HISTORY_LIMIT
        n = max(1, min(n, DANMAKU_HISTORY_LIMIT))
        with self._lock:
            return list(self.live_danmaku_history[-n:])

    # ── 弹幕置顶（主播操作）──────────────────────────────────────────────
    def get_pinned_danmaku(self) -> Optional[dict]:
        """当前置顶弹幕；没有置顶或置顶弹幕已不存在时返回 None。"""
        with self._lock:
            if not self.pinned_danmaku_id:
                return None
            for d in self.danmaku_list:
                if d.danmakuId == self.pinned_danmaku_id:
                    return d.model_dump()
            return None

    def pin_danmaku(self, danmaku_id: str) -> dict:
        """置顶一条弹幕并广播给直播间所有在线用户。"""
        with self._lock:
            item = next((d for d in self.danmaku_list if d.danmakuId == danmaku_id), None)
            if item is None:
                return {"success": False, "message": "弹幕不存在"}
            self.pinned_danmaku_id = danmaku_id
            payload = {"pinned": True, "danmakuId": danmaku_id, "danmaku": item.model_dump()}
        # 广播放在锁外：回调只做入队，但避免持锁期间触碰别的模块
        self._broadcast("danmaku_pinned", payload)
        return {"success": True, **payload}

    def unpin_danmaku(self) -> dict:
        """取消置顶并广播。"""
        with self._lock:
            prev = self.pinned_danmaku_id
            self.pinned_danmaku_id = ""
        payload = {"pinned": False, "danmakuId": prev, "danmaku": None}
        self._broadcast("danmaku_pinned", payload)
        return {"success": True, **payload}

    def danmaku_is_real(self, danmaku_id: str) -> bool:
        """这条弹幕是否来自真实观众（找不到该弹幕时返回 False）。

        供回复路径判定高频问题的归属：回复入口只拿得到 danmakuId，
        而真伪只能看它的 userId。
        """
        with self._lock:
            for d in self.danmaku_list:
                if d.danmakuId == danmaku_id:
                    return is_real_user_id(d.userId)
            return False

    def reply_danmaku(self, danmaku_id: str, reply_content: str) -> bool:
        """回复弹幕。回复计数同样只认真实观众（虚拟弹幕的自动回复不计入运营口径）。"""
        with self._lock:
            for d in self.danmaku_list:
                if d.danmakuId == danmaku_id:
                    d.isReplied = True
                    d.replyContent = reply_content
                    d.replyTime = datetime.now().isoformat()
                    if is_real_user_id(d.userId):
                        self.live_state.totalAIReplies += 1
                        self.stats.totalAIReplies += 1
                    if is_persistable_user_id(d.userId):
                        # 回复内容也要落盘，否则重启后「已回复」字段丢失
                        self._save_danmaku()
                    self._broadcast("danmaku_reply", {
                        "danmakuId": danmaku_id,
                        "replyContent": reply_content,
                        "replyTime": d.replyTime,
                    })
                    return True
            return False

    # ============================================================
    # 私信管理
    # ============================================================
    def add_private_message(self, msg: PrivateMessageItem,
                            track_unread: bool = True) -> PrivateMessageItem:
        """添加私信消息。

        track_unread=False → 只入库/广播，不累加会话未读数，也不计入真实咨询数。
        generate_mock_data 重灌模拟私信时必须传 False：那些 u00x 模拟会话本来就不该
        在运营台的未读红点上出现（决策：运营台只呈现真实用户）。
        totalPrivateMessages 只统计真实观众（判据同 add_danmaku）。
        """
        with self._lock:
            uid = msg.userId
            if uid not in self.private_messages:
                self.private_messages[uid] = []
            self.private_messages[uid].append(msg)
            self._msg_counter += 1
            if is_persistable_user_id(uid):
                self._save_private_messages()
            if msg.isFromUser and track_unread and is_real_user_id(uid):
                self.live_state.totalPrivateMessages += 1
            # 更新会话
            session = self._get_or_create_session(uid, msg.userName, msg.avatar)
            session.lastMessage = msg.content[:50]
            session.lastMessageTime = msg.timestamp
            if msg.isFromUser and track_unread:
                session.unreadCount += 1
            # 会话也要落盘：收件箱左列是按 user_sessions 生成的，会话没写出去，
            # 重启后这个观众就从左列消失（私信还在盘上，但主播点不进去）。
            # 原先只有 mark_user_messages_read 会落盘，等于把「看不看得见这条会话」
            # 押在了主播先点开过它上面。
            if is_persistable_user_id(uid):
                self._save_sessions()
            # 私信按会话归属定向：只发给该观众本人 + 运营台。
            # 之前是全广播，任何观众都能读到别人的私信内容与会话。
            self._broadcast("new_private_msg", {
                "messageId": msg.messageId,
                "userId": uid,
                "userName": msg.userName,
                "content": msg.content,
                "timestamp": msg.timestamp,
                "isFromUser": msg.isFromUser,
            }, to_user=uid)
            return msg

    def get_user_messages(self, user_id: str) -> list[PrivateMessageItem]:
        """获取某个用户的私信消息"""
        with self._lock:
            return list(self.private_messages.get(user_id, []))

    def mark_user_messages_read(self, user_id: str):
        """标记用户私信已读（并落盘 —— 旧实现只改内存，重启后未读红点会复活）"""
        with self._lock:
            if user_id in self.user_sessions:
                self.user_sessions[user_id].unreadCount = 0
                self._save_sessions()

    # ============================================================
    # 用户会话管理
    # ============================================================
    def _get_or_create_session(self, user_id: str, user_name: str, avatar: str = "") -> UserSessionItem:
        """获取或创建用户会话"""
        if user_id in self.user_sessions:
            return self.user_sessions[user_id]
        self._session_counter += 1
        session = UserSessionItem(
            sessionId=f"session_{self._session_counter}",
            userId=user_id,
            userName=user_name,
            avatar=avatar or f"https://api.dicebear.com/7.x/bottts/svg?seed={user_id}",
        )
        self.user_sessions[user_id] = session
        return session

    def get_all_sessions(self) -> list[UserSessionItem]:
        """获取所有用户会话"""
        with self._lock:
            return sorted(
                list(self.user_sessions.values()),
                key=lambda s: s.lastMessageTime,
                reverse=True,
            )

    def get_inbox_threads(self, online_user_ids: Optional[set] = None) -> list[dict]:
        """运营台「私信收件箱」左列的会话列表（决策 #4）。

        只列出**有私信往来的真实观众 / 老账号**会话：
          · `is_persistable_user_id` 过滤掉 u001~u010 等模拟观众 —— 那些是氛围数据，
            出现在运营台工作台上会让主播去回复一个根本不存在的人
          · 没有私信往来的会话（只在弹幕里出现过）不进收件箱
        订单数与累计消费一次性从 get_viewers_overview() 建 map，避免逐线程 N+1。
        `online_user_ids` 由 WS 层传入（在线连接表在 WS 管理器里，不在本类）。
        """
        online = online_user_ids or set()
        overview = {r["userId"]: r for r in self.get_viewers_overview()}
        with self._lock:
            sessions = list(self.user_sessions.values())
            msg_map = {uid: list(msgs) for uid, msgs in self.private_messages.items()}
        rows: list[dict] = []
        for s in sessions:
            uid = s.userId
            if not is_persistable_user_id(uid):
                continue
            msgs = msg_map.get(uid) or []
            if not msgs:
                continue
            last = msgs[-1]
            acc = overview.get(uid) or {}
            rows.append({
                "userId": uid,
                "userName": s.userName or last.userName or "观众",
                "avatar": s.avatar or last.avatar or "",
                "unreadCount": s.unreadCount,
                "lastMessage": last.content,
                "lastMessageTime": last.timestamp,
                "lastFromUser": last.isFromUser,
                "messageCount": len(msgs),
                "isOnline": uid in online,
                "isLegacy": not uid.startswith("v_"),
                "orderCount": acc.get("orderCount", 0),
                "totalSpend": acc.get("totalSpend", 0.0),
                "balance": acc.get("balance", 0.0),
                "sessionStatus": s.status,
            })
        rows.sort(key=lambda r: r["lastMessageTime"] or "", reverse=True)
        return rows

    def update_session_status(self, user_id: str, status: str) -> bool:
        """更新会话状态"""
        with self._lock:
            if user_id in self.user_sessions:
                self.user_sessions[user_id].status = status
                # 会话状态是运营台的工作台信息，观众端不消费
                self._broadcast("session_status_changed", {
                    "userId": user_id,
                    "status": status,
                }, host_only=True)
                return True
            return False

    def update_session_level(self, user_id: str, level: str) -> bool:
        """更新用户分层标签"""
        with self._lock:
            if user_id in self.user_sessions:
                self.user_sessions[user_id].userLevel = level
                return True
            return False

    # ============================================================
    # 预设话术管理
    # ============================================================
    def add_preset(self, preset: PresetPhraseItem) -> PresetPhraseItem:
        with self._lock:
            self.presets[preset.presetId] = preset
            self._preset_counter += 1
            self._save_presets()
            return preset

    def update_preset(self, preset_id: str, data: dict) -> Optional[PresetPhraseItem]:
        with self._lock:
            if preset_id not in self.presets:
                return None
            p = self.presets[preset_id]
            for k, v in data.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            self._save_presets()
            return p

    def delete_preset(self, preset_id: str) -> bool:
        with self._lock:
            if preset_id in self.presets:
                del self.presets[preset_id]
                self._save_presets()
                return True
            return False

    def get_enabled_presets(self) -> list[PresetPhraseItem]:
        """获取已启用的话术"""
        return [p for p in self.presets.values() if p.isEnabled]

    def get_pending_presets(self) -> list[PresetPhraseItem]:
        """获取待编辑的话术"""
        return [p for p in self.presets.values() if p.isPending]

    def find_matching_preset(self, content: str) -> Optional[PresetPhraseItem]:
        """根据内容查找匹配的预设话术"""
        for p in self.presets.values():
            if p.isEnabled and not p.isPending:
                for kw in p.keywords:
                    if kw.lower() in content.lower():
                        p.useCount += 1
                        p.lastUsedTime = datetime.now().isoformat()
                        return p
        return None

    # ============================================================
    # 黑名单管理
    # ============================================================
    def add_to_blacklist(self, user_id: str, user_name: str, reason: str = "", operator: str = "system") -> BlacklistItem:
        """拉黑用户"""
        with self._lock:
            if user_id in self.blacklist:
                return self.blacklist[user_id]
            self._blacklist_counter += 1
            item = BlacklistItem(
                blacklistId=f"bl_{self._blacklist_counter}",
                userId=user_id,
                userName=user_name,
                reason=reason,
                createTime=datetime.now().isoformat(),
                operator=operator,
            )
            self.blacklist[user_id] = item
            self._save_blacklist()
            # 关闭该用户会话
            if user_id in self.user_sessions:
                self.user_sessions[user_id].status = "blacklisted"
            self._broadcast("user_blacklisted", {"userId": user_id, "userName": user_name},
                            host_only=True)
            return item

    def remove_from_blacklist(self, user_id: str) -> bool:
        """解除拉黑"""
        with self._lock:
            if user_id in self.blacklist:
                del self.blacklist[user_id]
                self._save_blacklist()
                if user_id in self.user_sessions:
                    self.user_sessions[user_id].status = "ai_reception"
                return True
            return False

    def is_blacklisted(self, user_id: str) -> bool:
        return user_id in self.blacklist

    def get_blacklist(self) -> list[BlacklistItem]:
        return list(self.blacklist.values())

    # ============================================================
    # 评价管理
    # ============================================================
    def add_review(self, review: ReviewItem) -> ReviewItem:
        with self._lock:
            self.reviews.append(review)
            self._review_counter += 1
            self._save_reviews()
            self._broadcast("new_review", review.model_dump())
            return review

    def get_reviews(self, sentiment: str = "", product_id: str = "", keyword: str = "", source: str = "") -> list[ReviewItem]:
        """获取评价（支持筛选）"""
        with self._lock:
            results = list(self.reviews)
            if sentiment and sentiment != "all":
                results = [r for r in results if r.sentiment == sentiment]
            if product_id:
                results = [r for r in results if r.productId == product_id]
            if source:
                results = [r for r in results if r.source == source]
            if keyword:
                results = [r for r in results if keyword.lower() in r.content.lower()]
            return sorted(results, key=lambda r: r.timestamp, reverse=True)

    def mark_review_handled(self, review_id: str) -> bool:
        with self._lock:
            for r in self.reviews:
                if r.reviewId == review_id:
                    r.isHandled = True
                    self._save_reviews()
                    return True
            return False

    def get_product_reviews(self, product_id: str) -> list[ReviewItem]:
        """获取某商品的评价（按时间倒序）"""
        with self._lock:
            return sorted(
                (r for r in self.reviews if r.productId == product_id),
                key=lambda r: r.timestamp,
                reverse=True,
            )

    def submit_review(self, product_id: str, user_id: str, user_name: str,
                      content: str, rating: int = 5, order_id: str = "") -> tuple[Optional[ReviewItem], str]:
        """观众端主动提交评价（**必须**挂在自己已确认收货的订单上，一单一商品限一次）。
        返回 (review, "") 或 (None, 原因)。同时重算商品 rating/reviewCount。

        `order_id` 为必填：它既是归属凭据（userId 严格相等），也是「确实买过」的凭据
        （order.status == "completed"）。省掉它不再被接受。
        """
        content = (content or "").strip()
        if not product_id:
            return None, "商品不存在"
        if not content:
            return None, "评价内容不能为空"
        try:
            rating = max(1, min(5, int(rating or 5)))
        except (TypeError, ValueError):
            rating = 5
        with self._lock:
            product = self.products.get(product_id)
            if not product:
                return None, "商品不存在"
            # 评价必须挂在一张**自己的、已确认收货**的订单上，orderId 不可省。
            # 旧实现在 orderId 为空时只查「该商品是否评价过」，等于放行：任何登录观众
            # 都能凭空评价一件从未买过的商品（WS 消息里不传 orderId 即可绕过）。
            # 既然成交只能来自真实注册观众，评价可信度的来源就是那张真实订单，不设
            # 「无订单评价」的兼容分支。
            if not order_id:
                return None, "请从「我的订单」里对已收货的订单发起评价"
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 归属判定必须是严格相等：旧写法 `order.userId and ...` 对 userId 为空的
            # 订单直接放行，任何人都能评价别人的单
            if order.userId != user_id:
                return None, "无权评价该订单"
            if order.productId != product_id:
                return None, "评价商品与订单不符"
            if order.status != "completed":
                return None, "确认收货后才能评价"
            # 防重复评价
            for r in self.reviews:
                if r.orderId == order_id and r.productId == product_id:
                    return None, "该订单已评价过了"
            sentiment = "positive" if rating >= 4 else ("neutral" if rating == 3 else "negative")
            review = ReviewItem(
                reviewId=f"rv_user_{int(time.time()*1000)}_{self._review_counter + 1}",
                userId=user_id or "",
                userName=user_name or "观众",
                avatar="",
                content=content,
                rating=rating,
                sentiment=sentiment,
                tags=[],
                productId=product_id,
                productName=product.name,
                source="user",
                orderId=order_id,
                timestamp=datetime.now().isoformat(),
                isHandled=False,
                images=[],
            )
            self.add_review(review)  # 内部加锁+落库+广播 new_review
            # 重算商品评分与评价数（与 generate_mock_data 共用同一套口径，
            # 避免「启动时按全部评价算、提交时按单商品手写算」两条平行逻辑漂移）
            self._recompute_product_aggregates()
            return review, ""

    # ============================================================
    # 商单/订单管理
    # ============================================================
    _BUYER_NAMES = [
        "爱吃水果的喵", "南山南", "小圆子", "追风少年", "奶茶三分糖", "山谷里的风",
        "甜甜的梨", "阿泽", "江南烟雨", "一颗小橙子", "晚睡冠军", "清风与鹿",
        "胖虎爱购物", "桃桃乌龙", "林深见鹿", "等风来", "半糖主义", "十里桃林",
    ]

    def _next_order_no(self) -> str:
        from datetime import datetime
        return "OMS" + datetime.now().strftime("%Y%m%d") + f"{self._order_counter + 1:04d}"

    def create_order(
        self,
        product_id: str,
        quantity: int = 1,
        status: str = "paid",
        user_name: str | None = None,
    ) -> Optional[OrderItem]:
        """创建一笔**模拟**订单（`userId=""` / `isVirtual=True`）。状态为 paid/completed/shipped 时累计到商品销量。

        ⚠️ 仅供内部/测试构造使用，**绝对不得再接回任何 WS handler**。
        它造出的订单在 `order_is_real()` 口径下永远不是真实单：不可发货、不进运营台统计、
        并且会在下次启动时被 `_purge_mock_collections()` 清掉。历史上它由 `simulate_orders`
        与 `buy_product` 两条消息对外暴露过，运营台因此凭空多出大量假成交，现已移除。
        真实成交只能走 `create_viewer_order` → `pay_order`。
        """
        with self._lock:
            product = self.products.get(product_id)
            if not product:
                return None
            if product.stock < quantity:
                return None
            product.stock -= quantity
            # 只有成交/履约类状态才累计销量
            if status in ("paid", "completed", "shipped"):
                product.soldCount += quantity
            product.isInLibrary = True
            product.addedTime = product.addedTime or datetime.now().isoformat()
            self._order_counter += 1
            import random as _r
            buyer_name = user_name or _r.choice(self._BUYER_NAMES)
            now = datetime.now().isoformat()
            order = OrderItem(
                orderId=f"order_{int(datetime.now().timestamp()*1000)}_{self._order_counter}",
                orderNo=self._next_order_no(),
                productId=product_id,
                productName=product.name,
                userId="",  # 模拟订单不绑定真实用户
                userName=buyer_name,
                quantity=quantity,
                unitPrice=product.price,
                totalAmount=round(product.price * quantity, 2),
                status=status,
                payTime=now if status not in ("pending_pay", "canceled") else "",
                createTime=now,
                isVirtual=True,
            )
            self.orders[order.orderId] = order
            self._save_orders()
            self._save_products()
            self._broadcast_order("order_new", order)
            self._broadcast_product_sales(order.productId)
            return order

    def get_orders(self, status: str = "", limit: int = 50) -> list[OrderItem]:
        """获取订单列表（按时间倒序，可按状态筛选）"""
        with self._lock:
            results = list(self.orders.values())
            if status:
                results = [o for o in results if o.status == status]
            results.sort(key=lambda o: o.createTime, reverse=True)
            return results[:limit]

    def get_session_orders(self, limit: int = 50, real_only: bool = True) -> list[OrderItem]:
        """**本场**订单列表（createTime 倒序）。

        与 get_orders 的差别只在口径：这里只要当前这场直播内下的单（边界是
        live_state.startTime，见 _in_session），默认还只要真实注册观众的。
        未开播时 startTime 为空 → 返回空列表，这是正确结果，不是异常。
        """
        with self._lock:
            results = self._session_orders_locked(real_only=real_only)
            results.sort(key=lambda o: o.createTime, reverse=True)
            return results[:limit]

    def get_session_order_stats(self, real_only: bool = True) -> dict:
        """**本场**订单汇总（主播在控制台盯的那几个数）。

        字段名刻意不用 get_order_stats 的 today*/total* —— 后者是「日历日 / 累计」
        口径，两者混在一起会让主播看到对不上的数字。

        汇总在切 limit **之前**全量算：先截列表再统计的话，本场单子一多数字就偏小。
        """
        with self._lock:
            orders = self._session_orders_locked(real_only=real_only)
            all_session = self._session_orders_locked(real_only=False)
            paid_statuses = ("paid", "shipped", "completed")
            paid = [o for o in orders if o.status in paid_statuses]
            return {
                "startTime": self.live_state.startTime or "",
                "orderCount": len(orders),
                "paidCount": len(paid),
                "gmv": round(sum(o.totalAmount for o in paid), 2),
                "qty": sum(o.quantity for o in paid),
                # 主播真正要动手的两个数：待发货（要他去点发货）、待付款（要去催）
                "pendingShip": sum(1 for o in orders if o.status == "paid"),
                "pendingPay": sum(1 for o in orders if o.status == "pending_pay"),
                "refundCount": sum(1 for o in orders if o.status == "refunded"),
                "buyerCount": len({o.userId for o in paid}),
                # 本场被 real_only 挡掉的笔数，显式披露 —— 否则主播看到数字比预期小，
                # 会怀疑丢单，而不是知道「那几笔是模拟单」。
                "excludedOrders": len(all_session) - len(orders),
            }

    def update_order_status(self, order_id: str, status: str) -> Optional[OrderItem]:
        """运营台手动推进订单状态：发货 paid->shipped / 退款 paid|shipped|completed->refunded / 取消 pending_pay->canceled。
        退款时回补库存销量，若为观众钱包单则把金额返充回该观众账户。非法转移返回 None（不改动、不广播）。"""
        if status not in ORDER_STATUS_LABELS:
            return None
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None
            old = order.status
            now = datetime.now().isoformat()
            # 只允许合法流转
            legal = {
                "shipped": old == "paid",                       # 发货
                "canceled": old == "pending_pay",               # 取消待付款
                "refunded": old in ("paid", "shipped", "completed"),  # 退款
            }
            if not legal.get(status):
                return None
            # 发货闸（决策：只有真实用户订单可发货）。前端隐藏按钮只是体验，
            # WS 消息可以直接构造，真正的闸门必须在这里。退款/取消不受此限 ——
            # _credit_viewer 对空 userId 已有兜底，模拟单退款不会出错，不顺手扩大范围。
            if status == "shipped" and not order_is_real(order):
                return None
            order.status = status
            if status == "shipped":
                order.shipTime = order.shipTime or now
            elif status == "refunded":
                # 退款：回补库存与销量
                product = self.products.get(order.productId)
                if product:
                    product.stock += order.quantity
                    product.soldCount = max(0, product.soldCount - order.quantity)
                    self._save_products()
                # 钱包单返充金额
                self._credit_viewer(order.userId, order.totalAmount)
            order.payTime = order.payTime or now
            self._save_orders()
            self._broadcast_order("order_updated", order)
            self._broadcast_product_sales(order.productId)
            return order

    # ============================================================
    # 观众账户（钱包）
    # ============================================================
    def _get_or_create_viewer_locked(self, user_id: str, user_name: str = "", avatar: str = "") -> Optional[ViewerAccount]:
        """锁内取或建观众账户（返回账户）；user_id 为空返回 None"""
        if not user_id:
            return None
        acc = self.viewers.get(user_id)
        if acc is None:
            acc = ViewerAccount(
                userId=user_id,
                userName=user_name or "观众",
                avatar=avatar or "",
                balance=2000.0,  # 新观众初始余额
                registerTime=datetime.now().isoformat(),
            )
            self.viewers[user_id] = acc
            self._save_viewers()
        else:
            changed = False
            if user_name and acc.userName != user_name:
                acc.userName = user_name
                changed = True
            if avatar and acc.avatar != avatar:
                acc.avatar = avatar
                changed = True
            if changed:
                self._save_viewers()
        return acc

    def get_or_create_viewer(self, user_id: str, user_name: str = "", avatar: str = "") -> Optional[ViewerAccount]:
        """登录/建档：返回观众账户（不存在则创建，初始余额 2000）"""
        with self._lock:
            return self._get_or_create_viewer_locked(user_id, user_name, avatar)

    def update_viewer_profile(self, user_id: str, user_name: str = "", avatar: str = "") -> Optional[ViewerAccount]:
        """观众主动更新资料（昵称/头像），非空即覆盖；返回最新账户"""
        if not user_id:
            return None
        with self._lock:
            acc = self._get_or_create_viewer_locked(user_id, user_name, avatar)
            if acc is None:
                return None
            changed = False
            if user_name and acc.userName != user_name:
                acc.userName = user_name
                changed = True
            if avatar and acc.avatar != avatar:
                acc.avatar = avatar
                changed = True
            if changed:
                self._save_viewers()
            return acc

    def get_viewer(self, user_id: str) -> Optional[ViewerAccount]:
        with self._lock:
            return self.viewers.get(user_id)

    def recharge_viewer(self, user_id: str, amount: float) -> float:
        """充值（模拟），返回最新余额"""
        with self._lock:
            acc = self._get_or_create_viewer_locked(user_id)
            if acc is None:
                return 0.0
            amount = round(float(amount), 2)
            if amount > 0:
                acc.balance = round(acc.balance + amount, 2)
                self._save_viewers()
            return acc.balance

    def _credit_viewer(self, user_id: str, amount: float):
        """退款/返充到观众钱包（锁内调用）"""
        if not user_id or amount <= 0:
            return
        acc = self.viewers.get(user_id)
        if acc is not None:
            acc.balance = round(acc.balance + float(amount), 2)
            self._save_viewers()

    def get_viewers_overview(self) -> list[dict]:
        """运营台「观众列表」用：每个观众账户一行，附订单数。

        「是否历史账户」由 id 前缀推导（新账号体系的服务端 id 一律以 v_ 开头，
        老的自增/时间戳 id 不带）——刻意不加 schema 字段，避免与真实前缀失同步。
        """
        with self._lock:
            order_count: dict[str, int] = {}
            spend: dict[str, float] = {}
            for o in self.orders.values():
                if not o.userId:
                    continue
                order_count[o.userId] = order_count.get(o.userId, 0) + 1
                if o.status in ("paid", "shipped", "completed"):
                    spend[o.userId] = spend.get(o.userId, 0.0) + o.totalAmount
            rows = []
            for uid, acc in self.viewers.items():
                rows.append({
                    "userId": uid,
                    "userName": acc.userName,
                    "avatar": acc.avatar or "",
                    "balance": acc.balance,
                    "registerTime": acc.registerTime,
                    "orderCount": order_count.get(uid, 0),
                    "totalSpend": round(spend.get(uid, 0.0), 2),
                    "isLegacy": not uid.startswith("v_"),
                })
            rows.sort(key=lambda r: r["registerTime"] or "", reverse=True)
            return rows

    # ============================================================
    # 观众端下单（两步：下单 pending_pay → 支付 paid）
    # ============================================================
    def create_viewer_order(self, product_id: str, user_id: str, user_name: str = "",
                            quantity: int = 1) -> tuple[Optional[OrderItem], str]:
        """观众下单：生成「待付款」订单，快照商品首图，不扣库存/销量。返回 (order, 错误原因)。"""
        with self._lock:
            product = self.products.get(product_id)
            if not product:
                return None, "商品不存在"
            # 下架商品观众端已经看不到，正常点不到这里；兜住的是「详情弹层还开着时
            # 商品被下架」这类陈旧视图，以及直接构造的请求。
            if not product.isOnSale:
                return None, "商品已下架"
            qty = max(1, int(quantity or 1))
            if product.stock < qty:
                return None, "库存不足"
            now = datetime.now().isoformat()
            self._order_counter += 1
            img = (product.images[0] if product.images else product.imageUrl) or ""
            order = OrderItem(
                orderId=f"order_{int(datetime.now().timestamp()*1000)}_{self._order_counter}",
                orderNo=self._next_order_no(),
                productId=product_id,
                productName=product.name,
                productImage=img,
                userId=user_id or "",
                userName=user_name or "",
                quantity=qty,
                unitPrice=product.price,
                totalAmount=round(product.price * qty, 2),
                status="pending_pay",
                payTime="",
                shipTime="",
                createTime=now,
                isVirtual=False,
            )
            self.orders[order.orderId] = order
            self._save_orders()
            self._broadcast_order("order_new", order)
            return order, ""

    def pay_order(self, order_id: str, user_id: str) -> tuple[Optional[OrderItem], str]:
        """观众支付：pending_pay → paid；扣钱包余额 + 扣库存 + 累销量。返回 (order, 错误原因)。"""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 严格归属：空 userId 的模拟单不再对任何观众开放（运营台走 update_order_status）
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status != "pending_pay":
                return None, "订单状态不允许支付"
            product = self.products.get(order.productId)
            if not product:
                return None, "商品不存在"
            # 下架后不允许支付「待付款」的旧单 —— 但位置很关键：必须排在
            # status != "pending_pay" 那关**之后**，这样已付款/已发货的订单在上一关
            # 就被放行了，永远不会被「已下架」拦住（下架不影响已经成交的生意）。
            if not product.isOnSale:
                return None, "商品已下架，无法支付"
            if product.stock < order.quantity:
                return None, "库存不足，无法支付"
            acc = self.viewers.get(user_id)
            if acc is None:
                return None, "请先登录观众账户"
            if acc.balance < order.totalAmount:
                return None, "钱包余额不足"
            acc.balance = round(acc.balance - order.totalAmount, 2)
            product.stock -= order.quantity
            product.soldCount += order.quantity
            now = datetime.now().isoformat()
            order.status = "paid"
            order.payTime = order.payTime or now
            self._save_viewers()
            self._save_products()
            self._save_orders()
            self._broadcast_order("order_updated", order)
            self._broadcast_product_sales(order.productId)
            return order, ""

    def confirm_receipt(self, order_id: str, user_id: str) -> tuple[Optional[OrderItem], str]:
        """观众确认收货：shipped → completed"""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 严格归属：空 userId 的模拟单不再对任何观众开放
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status != "shipped":
                return None, "仅已发货订单可确认收货"
            order.status = "completed"
            self._save_orders()
            self._broadcast_order("order_updated", order)
            return order, ""

    def cancel_viewer_order(self, order_id: str, user_id: str) -> tuple[Optional[OrderItem], str]:
        """观众取消待付款订单：pending_pay → canceled"""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 严格归属：空 userId 的模拟单不再对任何观众开放
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status != "pending_pay":
                return None, "仅待付款订单可取消"
            order.status = "canceled"
            self._save_orders()
            self._broadcast_order("order_updated", order)
            return order, ""

    def get_user_orders(self, user_id: str, status: str = "") -> list[OrderItem]:
        """某观众的全部订单（时间倒序），可按状态过滤"""
        with self._lock:
            results = [o for o in self.orders.values() if o.userId == user_id]
            if status:
                results = [o for o in results if o.status == status]
            results.sort(key=lambda o: o.createTime, reverse=True)
            return results

    def get_order_stats(self, real_only: bool = False) -> dict:
        """商单成交统计。

        real_only=True → 只统计真实观众订单（运营台口径，见决策「统计只算真实用户」）；
        real_only=False → 全部订单（观众端与旧调用点保持原行为）。
        返回值里额外带 realOrders / mockOrdersExcluded，让调用方一眼看出被排除了多少，
        避免看到 GMV 骤降时误判成 bug。
        """
        with self._lock:
            orders = list(self.orders.values())
            total_all = len(orders)
            if real_only:
                orders = [o for o in orders if order_is_real(o)]
            mock_excluded = total_all - len(orders)
            from datetime import datetime, timedelta
            today = datetime.now().strftime("%Y-%m-%d")
            paid_statuses = ("paid", "shipped", "completed")
            effective = [o for o in orders if o.status in paid_statuses]
            total_orders = len(effective)
            total_sales = round(sum(o.totalAmount for o in effective), 2)
            total_qty = sum(o.quantity for o in effective)
            refund_orders = sum(1 for o in orders if o.status == "refunded")
            today_orders = sum(1 for o in effective if o.createTime[:10] == today)
            today_sales = round(sum(o.totalAmount for o in effective if o.createTime[:10] == today), 2)
            # 按商品聚合销量
            by_product: dict[str, dict] = {}
            for o in effective:
                b = by_product.setdefault(o.productId, {"productId": o.productId, "name": o.productName, "qty": 0, "amount": 0.0})
                b["qty"] += o.quantity
                b["amount"] = round(b["amount"] + o.totalAmount, 2)
            by_product_list = sorted(by_product.values(), key=lambda x: x["qty"], reverse=True)
            return {
                "totalOrders": total_orders,
                "totalSales": total_sales,
                "totalQty": total_qty,
                "todayOrders": today_orders,
                "todaySales": today_sales,
                "refundCount": refund_orders,
                "byProduct": by_product_list,
                # 口径披露：被排除的模拟订单数（real_only=False 时为 0）
                "mockOrdersExcluded": mock_excluded,
                "realOnly": real_only,
            }

    def get_product_sales_map(self) -> dict:
        """每个商品的成交销售情况（基于有效订单实时聚合，不落库）。

        返回 {productId: {"qty": 累计成交件数, "amount": 累计成交额, "todayQty": 今日成交件数}}，
        只统计已付款/已发货/已完成等有效订单。
        """
        from datetime import datetime
        paid_statuses = ("paid", "shipped", "completed")
        today = datetime.now().strftime("%Y-%m-%d")
        sales: dict[str, dict] = {}
        with self._lock:
            for o in self.orders.values():
                if o.status not in paid_statuses:
                    continue
                s = sales.setdefault(o.productId, {"qty": 0, "amount": 0.0, "todayQty": 0})
                s["qty"] += o.quantity
                s["amount"] = round(s["amount"] + o.totalAmount, 2)
                if o.createTime[:10] == today:
                    s["todayQty"] += o.quantity
        return sales

    def get_real_product_sales_map(self) -> dict:
        """与 get_product_sales_map 同构，但**只统计真实用户订单**。

        口径分离的原因（决策 #7）：观众端商品卡要显示「已售 N 件」的热闹数字，
        它包含模拟单；运营台的数据分析要看的却是真实成交。同一份 JSON 里两个键
        并存 —— `salesQty`（含模拟）给观众端，`realSalesQty`（纯真实）给运营台。
        返回结构与 get_product_sales_map 完全一致。
        """
        from datetime import datetime
        paid_statuses = ("paid", "shipped", "completed")
        today = datetime.now().strftime("%Y-%m-%d")
        sales: dict[str, dict] = {}
        with self._lock:
            for o in self.orders.values():
                if o.status not in paid_statuses or not order_is_real(o):
                    continue
                s = sales.setdefault(o.productId, {"qty": 0, "amount": 0.0, "todayQty": 0})
                s["qty"] += o.quantity
                s["amount"] = round(s["amount"] + o.totalAmount, 2)
                if o.createTime[:10] == today:
                    s["todayQty"] += o.quantity
        return sales

    # ============================================================
    # AI 话术历史（内存）
    # ============================================================
    def record_script(self, product_id: str, product_name: str, script_type: str, content: str):
        """记录一次 AI 生成的话术到历史"""
        if not content:
            return
        self.scripts_history.insert(0, {
            "productId": product_id,
            "productName": product_name,
            "type": script_type,
            "content": content,
            "time": datetime.now().isoformat(),
        })
        del self.scripts_history[30:]  # 仅保留最近 30 条

    def get_scripts_history(self, limit: int = 8) -> list[dict]:
        return self.scripts_history[:limit]

    # ============================================================
    # 快捷回复管理
    # ============================================================
    def add_quick_reply(self, qr: QuickReplyItem) -> QuickReplyItem:
        with self._lock:
            self.quick_replies[qr.quickReplyId] = qr
            self._save_quick_replies()
            return qr

    def delete_quick_reply(self, qr_id: str) -> bool:
        with self._lock:
            if qr_id in self.quick_replies:
                del self.quick_replies[qr_id]
                self._save_quick_replies()
                return True
            return False

    def get_quick_replies(self) -> list[QuickReplyItem]:
        return list(self.quick_replies.values())

    # ============================================================
    # 设置管理
    # ============================================================
    def update_settings(self, data: dict) -> LiveCommerceSettings:
        """更新设置"""
        with self._lock:
            for section, values in data.items():
                if hasattr(self.settings, section):
                    section_obj = getattr(self.settings, section)
                    if isinstance(values, dict):
                        for k, v in values.items():
                            if hasattr(section_obj, k):
                                setattr(section_obj, k, v)
                    else:
                        setattr(self.settings, section, values)
            self._save_settings()
            # 在线人数基数可能刚被改动：重读并立即重算（无变化则不广播）
            prev_base = self._online_base
            self._online_base = self._online_base_from_settings()
            if self._online_base != prev_base and self._refresh_online_locked():
                self._broadcast("status_update", self.get_live_status())
            # 同步敏感词
            if hasattr(self.settings, 'aiReply'):
                custom_words = self.settings.aiReply.customSensitiveWords
                base_words = list(BUILTIN_SENSITIVE_WORDS)
                self.sensitive_words = list(set(base_words + custom_words))
            return self.settings

    def get_settings(self) -> LiveCommerceSettings:
        return self.settings

    def get_host_profile(self) -> dict:
        """主播对外展示资料（昵称/头像）"""
        return {
            "hostName": getattr(self.settings, "hostName", "") or "主播",
            "hostAvatar": getattr(self.settings, "hostAvatar", "") or "",
        }

    # ============================================================
    # 敏感词过滤
    # ============================================================
    def check_sensitive(self, content: str) -> tuple[bool, str]:
        """检查内容是否包含敏感词，返回（是否违规, 违规原因）"""
        for word in self.sensitive_words:
            if word in content:
                return True, f"包含敏感词: {word}"
        return False, ""

    # ============================================================
    # 直播状态管理
    # ============================================================
    def start_live(self) -> dict:
        """开始直播"""
        with self._lock:
            if not self.current_product_id:
                # 「上架」和「选讲解商品」现在是两件事，文案得跟着改，否则主播会去
                # 商品管理里点「上架」然后困惑为什么还开不了播。
                return {"success": False, "message": "请先选择讲解商品"}
            self.live_state.status = "live"
            self.live_state.startTime = datetime.now().isoformat()
            # 在线人数 = 配置的模拟热度基数 + 真实在线观众数（不再随机取数）
            self._online_base = self._online_base_from_settings()
            self._refresh_online_locked()
            self.live_state.totalDanmaku = 0
            self.live_state.totalPrivateMessages = 0
            self.live_state.totalAIReplies = 0
            self.live_state.liveDuration = 0
            self.stats = LiveStatsData()
            # 这里**不清空任何集合**：开播只划会话边界。
            # 「本场 vs 历史」由 _in_session(startTime) 判定，真实数据跨场留存；
            # 模拟数据（弹幕/私信/评价/订单）按决策 #1 是直播氛围的一部分，也一并留着 ——
            # 它们本来就不进任何统计（统计口径按 userId 前缀过滤），清掉只丢氛围不换准确度。
            # 曾经在这里调 _purge_mock_collections()：后果是一开播 186 条模拟单当场消失，
            # 运营台订单页空掉、报告里的 mockOrdersExcluded 变成 0，重启才复活。
            self.alerts = []
            self._alert_counter = 0
            # 场控提示的判定基准也要跟着清：_last_alert_real_online 留着上一场的
            # 人数，本场第一个检查点就会拿它去算涨跌，凭空报一条假提示。
            self._last_alert_real_online = None
            self._low_stock_alerted = False
            self._save_alerts()
            self.high_freq_questions = {}
            self.virtual_freq_questions = {}
            self.violation_log.clear()
            # 弹幕历史缓冲与置顶属于「本场」：开播即新会话边界
            self.live_danmaku_history.clear()
            self.pinned_danmaku_id = ""
            # 重置本场真实信号
            self._session_real_users = set()
            self._online_peak = 0
            self._online_real_peak = 0
            self._online_sum = 0
            self._online_samples = 0
            self._online_tracking_active = True
            # 启动后台定时任务
            self._start_timers()
            self._broadcast("live_started", {
                "startTime": self.live_state.startTime,
                "liveStatus": self.get_live_status(),
            })
            return {"success": True, "message": "直播已开始"}

    def pause_live(self) -> dict:
        """暂停直播"""
        with self._lock:
            if self.live_state.status != "live":
                return {"success": False, "message": "当前不在直播中"}
            self.live_state.status = "paused"
            self.live_state.pausedTime = datetime.now().isoformat()
            self._stop_timers()
            self._broadcast("live_paused", {"pausedTime": self.live_state.pausedTime})
            return {"success": True, "message": "直播已暂停"}

    def resume_live(self) -> dict:
        """恢复直播"""
        with self._lock:
            if self.live_state.status != "paused":
                return {"success": False, "message": "当前不在暂停状态"}
            self.live_state.status = "live"
            self._start_timers()
            self._broadcast("live_resumed", {})
            return {"success": True, "message": "直播已恢复"}

    def end_live(self) -> dict:
        """结束直播"""
        with self._lock:
            if self.live_state.status not in ("live", "paused"):
                return {"success": False, "message": "当前不在直播中"}
            self.live_state.status = "ended"
            self._online_tracking_active = False
            now = datetime.now().isoformat()
            if self.live_state.startTime:
                try:
                    start = datetime.fromisoformat(self.live_state.startTime)
                    self.live_state.liveDuration = int((datetime.now() - start).total_seconds())
                except Exception:
                    self.live_state.liveDuration = 0
            self._stop_timers()
        # 释放锁后生成报告（避免死锁）
        report = self.generate_report()
        self._broadcast("live_ended", {
            "endTime": now,
            "duration": self.live_state.liveDuration,
            "report": report,
        })
        return {"success": True, "message": "直播已结束", "report": report}

    def switch_stage(self, stage_key: str) -> dict:
        """切换直播阶段"""
        valid_keys = [s["key"] for s in LIVE_STAGES]
        if stage_key not in valid_keys:
            return {"success": False, "message": "无效的直播阶段"}
        with self._lock:
            self.live_state.currentStage = stage_key
            strategy = STAGE_STRATEGIES.get(stage_key, "")
            self._broadcast("stage_changed", {
                "stage": stage_key,
                "strategy": strategy,
            })
        return {"success": True, "message": f"已切换至{stage_key}阶段", "strategy": strategy}

    # ============================================================
    # 在线人数：真实在线观众数 + 模拟热度基数
    # ============================================================
    def _online_base_from_settings(self) -> int:
        """读取配置的模拟热度基数。

        update_settings 用的是通用 setattr 且模型未开 validate_assignment，存进来的
        可能是字符串甚至 None，因此这里一律 int() 兜底，避免脏值参与算术。
        语义上区分三档：缺失/None/无法解析 → 默认 120；显式 0 → 0（表示不要模拟基数，
        只显示真实观众）；负数 → 夹到 0。
        """
        try:
            return max(0, int(getattr(self.settings.system, "onlineCountBase", 120)))
        except (TypeError, ValueError):
            return 120

    def _refresh_online_locked(self) -> bool:
        """按「基数 + 真实在线观众数」重算 onlineCount（锁内调用）。返回是否发生变化。

        live_state.onlineCount 是唯一对外数字，get_live_status / 复盘报告 / Streamlit
        都读它，所以只改这一个字段即可全端生效。
        """
        n = max(0, self._online_base) + max(0, self._real_online)
        changed = self.live_state.onlineCount != n
        self.live_state.onlineCount = n
        # 顺带消掉 stats.onlineCount 恒为 0 的矛盾（该字段此前从未被赋值）
        self.stats.onlineCount = n
        # 峰值只在直播进行中累计：_online_tracking_active 由 start_live 打开，
        # 这样开播瞬间初始化的那个基数不会被当成「峰值」。既有算法一行未动，只追加计数。
        if self._online_tracking_active:
            self._online_peak = max(self._online_peak, n)
            self._online_real_peak = max(self._online_real_peak, max(0, self._real_online))
        return changed

    def set_online_viewers(self, n: int) -> None:
        """由 WebSocket 层在观众登录/断开时告知真实在线观众数（去重 userId 数）。

        数字有变化才广播，避免刷屏；观众端与运营台都靠这条 status_update 实时跳动。
        """
        try:
            n = max(0, int(n))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._real_online = n
            if self._refresh_online_locked():
                self._broadcast("status_update", self.get_live_status())

    def get_online_breakdown(self) -> dict:
        """在线人数构成，供运营台展示「真实观众 / 模拟基数」明细。"""
        with self._lock:
            return {
                "onlineCount": self.live_state.onlineCount,
                "realViewers": self._real_online,
                "base": self._online_base,
            }

    def get_live_status(self) -> dict:
        """获取直播状态"""
        with self._lock:
            s = self.live_state
            return {
                "status": s.status,
                "currentStage": s.currentStage,
                "startTime": s.startTime,
                "liveDuration": s.liveDuration,
                "onlineCount": s.onlineCount,
                "totalDanmaku": s.totalDanmaku,
                "totalPrivateMessages": s.totalPrivateMessages,
                "totalAIReplies": s.totalAIReplies,
            }

    # ============================================================
    # 后台定时任务
    # ============================================================
    def _start_timers(self):
        """启动所有后台定时任务"""
        if not self._live_timer_running:
            self._live_timer_running = True
            self._live_timer = threading.Thread(target=self._live_timer_loop, daemon=True)
            self._live_timer.start()
        if not self._danmaku_running:
            self._danmaku_running = True
            self._danmaku_timer = threading.Thread(target=self._danmaku_gen_loop, daemon=True)
            self._danmaku_timer.start()
        if not self._stats_running:
            self._stats_running = True
            self._stats_timer = threading.Thread(target=self._stats_update_loop, daemon=True)
            self._stats_timer.start()
        if not self._alert_running:
            self._alert_running = True
            self._alert_timer = threading.Thread(target=self._alert_check_loop, daemon=True)
            self._alert_timer.start()

    def _stop_timers(self):
        """停止所有后台定时任务"""
        self._live_timer_running = False
        self._danmaku_running = False
        self._stats_running = False
        self._alert_running = False

    def _is_live_active(self) -> bool:
        """检查直播是否活跃（未暂停且未结束）"""
        s = self.live_state.status
        return s == "live"

    def _live_timer_loop(self):
        """直播计时循环（每秒推进直播时长，每 10 秒广播一次状态）"""
        while self._live_timer_running:
            time.sleep(1)
            if not self._is_live_active():
                continue
            try:
                with self._lock:
                    # 在线人数不再随机游走：只随真实观众进出变化（见 set_online_viewers），
                    # 这里仅推进直播时长。基数保持为配置值，保证数字可预期、可断言。
                    if self.live_state.startTime:
                        start = datetime.fromisoformat(self.live_state.startTime)
                        self.live_state.liveDuration = int((datetime.now() - start).total_seconds())
                    # 在线人数按秒采样，供复盘报告算「本场平均在线」。放在这里是因为
                    # _live_timer_loop 是唯一有稳定时间轴、且已经处理了 paused 的循环。
                    if self._online_tracking_active:
                        self._online_sum += self.live_state.onlineCount
                        self._online_samples += 1
                # 每10秒推送一次状态更新
                if int(time.time()) % 10 < 2:
                    self._broadcast("status_update", self.get_live_status())
            except Exception:
                pass

    def _danmaku_gen_loop(self):
        """虚拟弹幕生成循环"""
        while self._danmaku_running:
            # 根据频率档位计算等待时间
            freq = self.settings.virtualDanmaku.frequency
            wait_range = {"low": (10, 60), "medium": (12, 30), "high": (7, 15)}
            wmin, wmax = wait_range.get(freq, (12, 30))
            # 秒杀阶段自动提频
            if self.settings.virtualDanmaku.autoBoostOnFlashSale and self.live_state.currentStage == "flash_sale":
                wmin = max(5, wmin - 3)
                wmax = max(10, wmax - 5)
            wait_time = random.randint(wmin, wmax)
            time.sleep(wait_time)
            if not self._is_live_active():
                continue
            try:
                self._generate_virtual_danmaku()
            except Exception:
                pass

    def _generate_virtual_danmaku(self):
        """生成一条虚拟弹幕并触发AI自动回复"""
        current_product = self.get_current_product()
        if not current_product:
            return
        # 按阶段权重选择问题类型
        stage = self.live_state.currentStage
        weights = STAGE_DANMAKU_WEIGHTS.get(stage, STAGE_DANMAKU_WEIGHTS["warmup"])
        question_type = random.choices(
            list(weights.keys()),
            weights=list(weights.values()),
            k=1
        )[0]
        # 根据商品信息个性化弹幕内容
        templates = VIRTUAL_DANMAKU_TEMPLATES.get(question_type, VIRTUAL_DANMAKU_TEMPLATES["spec"])
        content = random.choice(templates)
        # 昵称池
        nicknames = self.settings.virtualDanmaku.nicknames or BUILTIN_NICKNAMES
        avatar_urls = self.settings.virtualDanmaku.avatarUrls or []
        danmaku_id = None
        with self._lock:
            self._danmaku_counter += 1
            nickname = random.choice(nicknames)
            avatar = random.choice(avatar_urls) if avatar_urls else ""
            danmaku_id = f"v_{self._danmaku_counter}_{int(time.time()*1000)}"
            danmaku = DanmakuItem(
                danmakuId=danmaku_id,
                userId=f"virtual_{self._danmaku_counter}",
                userName=nickname,
                avatar=avatar,
                content=content,
                timestamp=datetime.now().isoformat(),
                isVirtual=True,
                isQuestion=is_question_like(content),
                isReplied=False,
            )
            self.danmaku_list.append(danmaku)
            # 虚拟弹幕同样会广播给在线观众，因此也计入本场历史缓冲，
            # 否则中途进来的观众看到的「历史」会缺掉屏幕上刚刷过的那几条。
            self._remember_live_danmaku_locked(danmaku)
            # 不再累加 totalDanmaku / stats.virtualDanmakuCount：
            # 前者是「真实弹幕数」口径，后者由 _recompute_real_stats_locked
            # 从列表直接数出来，两处都改由单一来源产出，避免重复计数。
            self._broadcast("new_danmaku", danmaku.model_dump())
        # 释放锁后自动AI回复（避免死锁）
        if danmaku_id:
            self.auto_reply_danmaku(danmaku_id, content, is_real=False)

    def _session_real_danmaku(self) -> list[DanmakuItem]:
        """本场真实观众弹幕（锁内调用）。"""
        return [d for d in self.danmaku_list
                if is_real_user_id(d.userId) and self._in_session(d.timestamp)]

    def _session_orders_locked(self, real_only: bool = True) -> list[OrderItem]:
        """本场订单：createTime 落在当前直播会话内；real_only 时只留真实注册观众（锁内调用）。

        口径的**唯一出处** —— generate_report / _recompute_real_stats_locked /
        get_session_orders 都走这里，别再各写一遍
        `order_is_real(o) and self._in_session(o.createTime)`。这件事原先已经有两份
        副本，加第三份的话，将来改口径必漏，而漏掉的那处会安静地算出另一套数字。
        """
        return [o for o in self.orders.values()
                if (not real_only or order_is_real(o)) and self._in_session(o.createTime)]

    def _recompute_real_stats_locked(self) -> None:
        """在锁内按真实事件重算统计面板的全部指标（**没有任何随机量**）。

        旧实现在这里写 random 漂移值（exposureCount/mentionCount/intentScore），面板上
        的数字与直播间的真实情况无关。现在每一项都能追溯到一组真实事件：

          totalDanmaku / totalAIReplies / totalPrivateMessages —— 本场真实计数（live_state 持有）
          aiReplySuccessRate  —— 本场真实弹幕中已被回复的比例
                                 （严格说这是「覆盖率」，字段名为兼容前端沿用）
          avgResponseTime     —— 本场真实弹幕 replyTime - timestamp 的均值（秒）
          virtualDanmakuCount —— 列表里虚拟弹幕条数（含上一场，仅供对照氛围量级）
          mentionCount        —— 本场真实弹幕提到当前商品名/卖点/标签的条数
          inquiryCount        —— 本场真实观众发来的私信条数
          intentScore         —— 100 × (0.4×人均弹幕率 + 0.3×私信观众占比 + 0.3×下单观众占比)，
                                 三个分量各自先按「真实观众数」归一并截顶到 1；没有人时恒为 0
          potentialOrderCount —— 真实观众的待付款订单数（加购未付款）
          humanTransferCount  —— 真实会话中已转人工的个数
        """
        real_dm = self._session_real_danmaku()
        n_dm = len(real_dm)
        replied = [d for d in real_dm if d.isReplied]

        self.stats.totalDanmaku = self.live_state.totalDanmaku
        self.stats.totalAIReplies = self.live_state.totalAIReplies
        self.stats.totalPrivateMessages = self.live_state.totalPrivateMessages
        self.stats.aiReplySuccessRate = round(len(replied) / n_dm * 100, 1) if n_dm else 0.0
        self.stats.virtualDanmakuCount = sum(
            1 for d in self.danmaku_list if classify_user_id(d.userId) == "virtual")
        self.stats.highFreqQuestionCount = len(self.high_freq_questions)

        # 平均响应耗时：只统计有 replyTime 的真实弹幕
        latencies: list[float] = []
        for d in replied:
            try:
                latencies.append((
                    datetime.fromisoformat(d.replyTime) - datetime.fromisoformat(d.timestamp)
                ).total_seconds())
            except (TypeError, ValueError):
                continue
        self.stats.avgResponseTime = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

        # 商品提及：真实弹幕里提到当前讲解商品的名称/卖点/标签的条数
        product = self.get_current_product()
        if product is None:
            self.stats.mentionCount = 0
        else:
            hints = [product.name] + list(product.sellingPoints or []) + list(product.tags or [])
            self.stats.mentionCount = sum(
                1 for d in real_dm if any(h and h in d.content for h in hints))

        # 真实私信（本场 + 真实观众）
        real_msgs = [m for uid, msgs in self.private_messages.items()
                     if is_real_user_id(uid)
                     for m in msgs if self._in_session(m.timestamp)]
        self.stats.inquiryCount = len(real_msgs)

        # 意向度：三个分量按真实观众数归一，再截顶到 1
        viewers = max(len(self._session_real_users), 1)
        dm_per_viewer = min(1.0, n_dm / viewers / 3.0)   # 人均 3 条弹幕视为满格
        senders = {uid for uid, msgs in self.private_messages.items()
                   if is_real_user_id(uid)
                   and any(self._in_session(m.timestamp) for m in msgs)}
        private_ratio = min(1.0, len(senders) / viewers)
        # 必须和上面两个分量同口径（本场）。只按 order_is_real 数全量订单的话，
        # 上一场的一个已付款单会让「今天刚开播、什么都没发生」的意向分直接跳到 30 ——
        # 分子是历史、分母是本场，这个比值没有意义。
        buyers = {o.userId for o in self._session_orders_locked()
                  if o.status in ("paid", "shipped", "completed")}
        buyer_ratio = min(1.0, len(buyers) / viewers)
        self.stats.intentScore = round(
            100 * (0.4 * dm_per_viewer + 0.3 * private_ratio + 0.3 * buyer_ratio), 1)

        self.stats.potentialOrderCount = sum(
            1 for o in self.orders.values()
            if order_is_real(o) and o.status == "pending_pay")
        self.stats.humanTransferCount = sum(
            1 for uid, s in self.user_sessions.items()
            if is_real_user_id(uid) and s.status == "human_reception")
        self.stats.presetUsedCount = sum(1 for p in self.presets.values() if p.useCount > 0)

    def _stats_update_loop(self):
        """统计数据更新循环（每5秒按真实事件重算一次）"""
        while self._stats_running:
            time.sleep(5)
            if not self._is_live_active():
                continue
            try:
                with self._lock:
                    self._recompute_real_stats_locked()
                # 运营台数据面板专用：观众端只消费 status_update，5 秒一次的统计包没必要发
                self._broadcast("stats_update", {
                    "stats": self.stats.model_dump(),
                    "liveStatus": self.get_live_status(),
                }, host_only=True)
            except Exception:
                pass

    def _alert_check_loop(self):
        """智能提示检查循环（每30秒检查一次）"""
        while self._alert_running:
            time.sleep(30)
            if not self._is_live_active():
                continue
            # 设置里的「智能提示」开关。原先这个字段只被前端存取和回显，
            # 后端压根没读 —— 关掉开关也照跑，属于开关失效。
            if not getattr(self.settings.system, "smartAlertEnabled", True):
                # 关掉 = 停止跟踪，而不是「照常跟踪只是不显示」：判定基准
                # 会随时间变旧，重新打开时若拿几分钟前的旧值比，会平白报一条
                # 假涨跌。清成未知，等于重新开跑，第一次只建基准。
                self._last_alert_real_online = None
                self._low_stock_alerted = False
                continue
            try:
                alerts = self._check_alerts()
                for alert in alerts:
                    with self._lock:
                        self.alerts.append(alert)
                        # 裁剪 + 落盘放在锁内：_save_alerts 读的是 self.alerts，
                        # 出了锁再存可能与其它写者交错、存出半新半旧的列表。
                        del self.alerts[:-ALERT_HISTORY_MAX]
                        self._save_alerts()
                    self._broadcast("new_alert", alert.model_dump(), host_only=True)
            except Exception as e:  # noqa: BLE001
                # 必须留痕：这里一旦失败，场控面板就静默失灵 —— 前端只会看到
                # 「什么都没有」，而原先的 `except Exception: pass` 连一行日志都没有，
                # 排查时完全无从下手（本次调试就被它吞掉过一次真实异常）。
                print(f"[LiveCommerce] 场控提示检查失败: {e!r}")

    def _check_alerts(self) -> list[SmartAlert]:
        """检查是否需要触发智能提示。

        涨跌比较的是**真实在线观众数**（``_real_online``），不是对外的 onlineCount。
        后者 = 模拟热度基数 + 真实观众，基数不变时它只随真实观众动，而旧实现拿它
        和固定基数比，直接导致故障：

          * 「下滑」条件是 ``online < base * 0.7``，而 ``online >= base`` 恒成立（基数非负），
            基数 >0 时数学上不可能触发 —— 这条提示是死代码；
          * 「上涨」条件是 ``online > base * 1.15``，等于要求真实观众 > 0.15×基数，
            基数 120 时需 18 个真实观众，演示里同样见不到。
        """
        alerts = []
        now = datetime.now().isoformat()
        real = max(0, self._real_online)

        # ── 在线人数涨跌：和上一次检查比 ──
        prev = self._last_alert_real_online
        # 首轮（prev is None）只建立基准不判定；人数太少时也不判，避免 1 人进出就报「下滑 50%」
        if prev is not None and prev >= ALERT_MIN_REAL_VIEWERS and real >= ALERT_MIN_REAL_VIEWERS:
            change = (real - prev) / prev
            if change >= ALERT_SURGE_RATIO:
                alerts.append(SmartAlert(
                    alertId=f"alert_{int(time.time())}_surge",
                    type="surge_up",
                    title="在线人数快速上涨",
                    message=f"真实在线观众从{prev}人涨到{real}人，建议抓住节奏重点讲解",
                    timestamp=now,
                ))
            elif change <= -ALERT_SURGE_RATIO:
                alerts.append(SmartAlert(
                    alertId=f"alert_{int(time.time())}_drop",
                    type="surge_down",
                    title="在线人数下滑",
                    message=f"真实在线观众从{prev}人掉到{real}人，建议调整内容或发福利留人",
                    timestamp=now,
                ))
        self._last_alert_real_online = real

        # ── 库存：按上架量的比例 ──
        # 上架量 = 现存 + 已售（守恒量，见商品数据不变量）。soldBase 是纯展示基数，
        # schema 明确它不参与任何经营统计，这里也不能算进去。
        product = self.get_current_product()
        if product:
            listed = max(0, product.stock) + max(0, product.soldCount)
            threshold = int(listed * ALERT_LOW_STOCK_RATIO)
            low = product.stock > 0 and product.stock <= threshold
            if low and not self._low_stock_alerted:
                # 只在「进入告急」时报一次；库存补回来后再告急才会再报
                self._low_stock_alerted = True
                alerts.append(SmartAlert(
                    alertId=f"alert_{int(time.time())}_stock",
                    type="low_stock",
                    title="库存不足提醒",
                    message=(
                        f"当前商品「{product.name}」库存仅剩{product.stock}件"
                        f"（上架{listed}件，剩不足{int(ALERT_LOW_STOCK_RATIO * 100)}%），"
                        f"建议控单或准备换下一款商品"
                    ),
                    timestamp=now,
                ))
            elif not low:
                self._low_stock_alerted = False
        return alerts

    # ============================================================
    # 统计分析
    # ============================================================
    def get_stats(self) -> LiveStatsData:
        """获取实时统计数据"""
        return self.stats

    def get_high_freq_questions(self, top_n: int = 10, include_mock: bool = False) -> list[dict]:
        """获取高频问题TOP N。

        默认只返回**真实观众**提的问题（决策：榜单只算真实用户）。include_mock=True
        时并入虚拟弹幕的问题合计，并在每行标注 isReal —— 供运营台「仅真实 / 含模拟」
        切换查看，避免把模拟氛围误读成真实需求。
        """
        with self._lock:
            if include_mock:
                merged = dict(self.virtual_freq_questions)
                for q, c in self.high_freq_questions.items():
                    merged[q] = merged.get(q, 0) + c
                items = [(q, c, q in self.high_freq_questions) for q, c in merged.items()]
            else:
                items = [(q, c, True) for q, c in self.high_freq_questions.items()]
            sorted_q = sorted(items, key=lambda x: -x[1])
            results = []
            for question, count, is_real in sorted_q[:top_n]:
                # 检查是否已有预设话术
                has_preset = False
                for p in self.presets.values():
                    if p.isEnabled and any(kw.lower() in question.lower() for kw in p.keywords):
                        has_preset = True
                        break
                results.append({
                    "question": question,
                    "count": count,
                    "hasPreset": has_preset,
                    "isReal": is_real,
                })
            return results

    def get_danmaku_summary(self) -> dict:
        """弹幕总结的**统计部分**：毫秒级、纯聚合、零 LLM、零随机。

        只统计真实观众弹幕（决策 #1）。被排除的模拟/虚拟弹幕数量显式写在 scope 里 ——
        让运营台一眼看出「图表上的数字为什么比弹幕区里看到的少」。
        情绪判定用纯规则词表（classify_sentiment），不走 LLM：逐条弹幕过 20 秒链路
        且成本随弹幕数线性增长，不适合放在同步消息里。
        """
        with self._lock:
            real = self._session_real_danmaku()
            all_dm = list(self.danmaku_list)
            excluded_virtual = sum(
                1 for d in all_dm if classify_user_id(d.userId) == "virtual")
            excluded_mock = sum(
                1 for d in all_dm if classify_user_id(d.userId) == "mock")
            start = self.live_state.startTime or ""
            duration = self.live_state.liveDuration

        n = len(real)
        replied = [d for d in real if d.isReplied]
        speakers = {d.userId for d in real}

        # 时间桶：以本场开播时刻为 0 点，每 5 分钟一桶
        buckets: dict[int, int] = {}
        t0 = None
        if start:
            try:
                t0 = datetime.fromisoformat(start)
            except (TypeError, ValueError):
                t0 = None
        for d in real:
            if t0 is None:
                break
            try:
                offset = (datetime.fromisoformat(d.timestamp) - t0).total_seconds()
            except (TypeError, ValueError):
                continue
            if offset >= 0:
                buckets[int(offset // 300)] = buckets.get(int(offset // 300), 0) + 1
        time_buckets = [{"label": f"{i * 5}-{i * 5 + 5}分钟", "count": c}
                        for i, c in sorted(buckets.items())]

        # 每分钟弹幕数：优先用直播时长；还没开播/刚开播则用「首末条弹幕跨度」，下限 1 分钟
        if duration > 0:
            minutes = duration / 60.0
        elif real:
            try:
                span = (datetime.fromisoformat(real[-1].timestamp)
                        - datetime.fromisoformat(real[0].timestamp)).total_seconds() / 60.0
            except (TypeError, ValueError):
                span = 0.0
            minutes = max(1.0, span)
        else:
            minutes = 1.0

        # 回复耗时：只有带 replyTime 的真实弹幕能算
        latencies: list[float] = []
        for d in replied:
            try:
                latencies.append((
                    datetime.fromisoformat(d.replyTime)
                    - datetime.fromisoformat(d.timestamp)).total_seconds())
            except (TypeError, ValueError):
                continue

        sentiment = {"positive": 0, "neutral": 0, "negative": 0}
        for d in real:
            sentiment[classify_sentiment(d.content)] += 1

        # 关键词：统计「有多少条弹幕提到了它」，词表与顾虑分类/情绪判定同源
        vocab = [k for cat in _BARRIER_CATEGORIES for k in cat["keywords"]]
        vocab += list(_POSITIVE_WORDS) + list(_NEGATIVE_WORDS)
        kw_counts: dict[str, int] = {}
        for d in real:
            text = d.content.lower()
            for k in vocab:
                if k in text:
                    kw_counts[k] = kw_counts.get(k, 0) + 1
        top_keywords = [{"keyword": k, "count": c} for k, c in sorted(
            kw_counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))[:10]]

        return {
            "scope": {
                "caliber": "只统计真实注册观众（v_* 账号）在本场内的弹幕",
                "realCount": n,
                "excludedVirtual": excluded_virtual,
                "excludedMock": excluded_mock,
                "sessionStart": start,
            },
            "stats": {
                "realCount": n,
                "realUserCount": len(speakers),
                "sessionRealViewers": len(self._session_real_users),
                "questionCount": sum(1 for d in real if d.isQuestion),
                "repliedCount": len(replied),
                "avgPerMinute": round(n / minutes, 1) if minutes else 0.0,
                "replyLatencyAvg": (round(sum(latencies) / len(latencies), 1)
                                    if latencies else 0.0),
                "timeBuckets": time_buckets,
                "sentiment": sentiment,
                "topKeywords": top_keywords,
                "topQuestions": self.get_high_freq_questions(10),
            },
        }

    def record_question(self, content: str, is_real: bool = False):
        """记录一个问题到高频统计。

        **is_real 必须由调用方显式给出。** auto_reply_danmaku 同时服务「虚拟弹幕
        生成器」和「真实观众弹幕兜底」两条路径，内部无从分辨——这正是旧实现里
        高频问题榜被虚拟弹幕灌水的根因。默认 False：漏传时宁可少算真实，也不能让
        虚拟弹幕混进真实榜单。
        """
        with self._lock:
            # 简化处理：取前30个字作为问题键
            key = content[:30]
            if is_real:
                self.high_freq_questions[key] = self.high_freq_questions.get(key, 0) + 1
            else:
                self.virtual_freq_questions[key] = self.virtual_freq_questions.get(key, 0) + 1

    # ============================================================
    # AI自动回复逻辑
    # ============================================================
    def auto_reply_danmaku(self, danmaku_id: str, content: str, is_real: bool = False) -> str:
        """
        根据弹幕内容自动生成回复
        优先匹配预设话术，没有预设时根据商品信息生成

        is_real 决定这条问题计入「真实高频问题」还是「虚拟问题」两个平行统计，
        见 record_question 的说明。
        """
        # 检查是否违规
        is_violation, reason = self.check_sensitive(content)
        if is_violation:
            return ""

        # 记录到高频统计
        self.record_question(content, is_real=is_real)

        # 先匹配预设话术
        preset = self.find_matching_preset(content)
        if preset:
            self.reply_danmaku(danmaku_id, preset.answer)
            return preset.answer

        # 没有预设话术，使用商品信息生成回复
        product = self.get_current_product()
        if not product:
            return ""

        # 基于关键词匹配生成简单回复
        reply = self._generate_smart_reply(content, product)
        if reply:
            self.reply_danmaku(danmaku_id, reply)
        return reply or ""

    def _generate_smart_reply(self, content: str, product: ProductItem) -> str:
        """智能生成回复（基于规则匹配）"""
        content_lower = content.lower()

        # 价格相关
        if any(kw in content_lower for kw in ["多少钱", "价格", "价钱", "售价", "价位", "怎么卖", "什么价"]):
            return f"这款{product.name}现在直播间专享价仅需{product.price:.0f}元！"
        # 优惠相关
        if any(kw in content_lower for kw in ["优惠", "打折", "折扣", "便宜", "划算", "降价"]):
            return f"现在下单超级划算！{product.name}原价{product.originalPrice:.0f}元，直播间到手仅{product.price:.0f}元，抓紧机会哟~"
        # 规格相关
        if any(kw in content_lower for kw in ["规格", "尺寸", "大小", "多重", "容量", "多少克"]):
            specs_str = "、".join(product.specs) if product.specs else "详情请看商品介绍"
            return f"这款{product.name}的规格是：{specs_str}，亲有什么想了解的可以继续问我哦~"
        # 库存/发货
        if any(kw in content_lower for kw in ["还有货", "库存", "发货", "快递", "物流", "什么时候到"]):
            if product.stock > 100:
                return f"库存充足！目前还有{product.stock}件，下单后48小时内发货，预计3-5天送达~"
            else:
                return f"库存仅剩{product.stock}件，手慢无哦！下单后48小时发货~"
        # 适用人群
        if any(kw in content_lower for kw in ["适合", "能用", "可以用", "适合什么人", "谁用"]):
            return f"{product.name}适合大多数人群使用，具体可以看看商品详情页的介绍，有什么特殊情况可以私信我详细了解~"
        # 产地
        if any(kw in content_lower for kw in ["产地", "哪里出产", "哪里生产", "哪里产", "哪里的"]):
            for spec in product.specs:
                if "产地" in spec:
                    return f"这款{product.name}的{spec}，品质有保障哦~"
            return f"我们这款{product.name}精选优质原料，品质口碑非常好~"
        # 品质/质量
        if any(kw in content_lower for kw in ["质量", "品质", "好不好", "怎么样", "口碑", "评价"]):
            return f"亲放心，这款{product.name}的品质非常好，很多老客户都在回购，好评率很高！"
        # 使用/用法
        if any(kw in content_lower for kw in ["怎么用", "如何使用", "用法", "怎么吃", "怎么操作"]):
            if product.description:
                return f"使用很简单哦~{product.description[:60]}如果还有不清楚的可以私信问我！"
            return f"这款{product.name}使用很简单，按照说明书操作即可，有任何问题随时私信我~"
        # 售后
        if any(kw in content_lower for kw in ["退货", "退款", "保修", "维修", "售后", "坏了"]):
            preset = self.find_matching_preset("售后")
            if preset:
                return preset.answer
            return f"亲，关于售后问题您可以私信我，我会给您详细介绍售后政策，请放心购买~"
        # 一般意向
        if any(kw in content_lower for kw in ["买", "下单", "拍下", "来一单", "想要", "心动了"]):
            return f"点击下方小黄车即可下单！现在购买还能享受直播间专属优惠，不要错过哦~"

        # 通用兜底回复
        return f"亲，这款{product.name}现在直播间专享价{product.price:.0f}元，品质非常好，有什么想了解的可以继续问我哦~"

    def auto_reply_private(self, user_id: str, content: str) -> str:
        """私信自动回复"""
        # 检查是否黑名单
        if self.is_blacklisted(user_id):
            return ""

        # 检查违规
        is_violation, reason = self.check_sensitive(content)
        if is_violation:
            return ""

        session = self._get_or_create_session(user_id, "用户" + user_id[-4:])
        product = self.get_current_product()

        # 匹配预设话术
        preset = self.find_matching_preset(content)
        if preset:
            return preset.answer

        # 生成回复
        if product:
            reply = self._generate_smart_reply(content, product)
            if reply:
                return reply

        # 兜底
        return f"亲，有什么可以帮到您的？欢迎随时咨询~"

    def import_to_pending_presets(self, questions: list[str]) -> int:
        """将高频问题导入为待编辑话术"""
        count = 0
        with self._lock:
            for q in questions:
                if not q or not q.strip():
                    continue
                self._preset_counter += 1
                preset = PresetPhraseItem(
                    presetId=f"preset_{int(time.time()*1000)}_{self._preset_counter}",
                    keywords=[q.strip()[:10]],
                    answer="",
                    createTime=datetime.now().isoformat(),
                    isEnabled=False,
                    isPending=True,
                    source="high_freq_import",
                )
                self.presets[preset.presetId] = preset
                count += 1
            self._save_presets()
        return count

    # ============================================================
    # 复盘报告生成
    # ============================================================
    def generate_report(self, save: bool = True) -> dict:
        """生成直播复盘报告（**真实用户口径**，线程安全，快速返回）。

        这份报告里的每一个数字都来自真实观众的真实行为：本场真实弹幕、真实私信、
        真实订单、真实评价。模拟数据（u001~u010 / virtual_* / userId 为空的订单）
        只用于直播间营造氛围，一条都不进报告 —— 被排除的量在 `scope.excluded*`
        与 `chapter8_conversion.mockOrdersExcluded` 里**显式披露**，让看报告的人
        立刻明白「为什么 GMV 只有几十块」，而不是当成 bug。

        旧实现的问题（全部删除）：
          · online_peak / totalViewers / chapter2.score / peakHeat 直接 random
          · chapter3.realCount 取 totalDanmaku —— 而虚拟弹幕生成器绕过 add_danmaku
            直接累加，所以那个"真实弹幕数"里混着氛围弹幕
          · 没有真实数据时用 too_short / actual_duration=3600 兜底，**凭空造出一场
            一小时的直播**。现在没有真实数据就如实显示空态。
          · 完全不读 orders（成交数据明明已经在 get_order_stats 里）

        save=False 供自检探针使用：报告会写入 data/reports/，探针不该留下垃圾文件。
        """
        now = datetime.now()
        with self._lock:
            # ---- 本场真实数据快照（一次取齐，避免报告内部各章节口径漂移）----
            real_dm = self._session_real_danmaku()
            real_reviews = [r for r in self.reviews if is_real_user_id(r.userId)]
            real_msgs = [m for uid, msgs in self.private_messages.items()
                         if is_real_user_id(uid)
                         for m in msgs if self._in_session(m.timestamp)]
            real_replies = [d for d in real_dm if d.isReplied]
            virtual_count = sum(1 for d in self.danmaku_list
                                if classify_user_id(d.userId) == "virtual")
            mock_dm_excluded = sum(
                1 for d in self.danmaku_list
                if classify_user_id(d.userId) in ("virtual", "mock")
                and self._in_session(d.timestamp))
            unique_real = len(self._session_real_users)
            base = self._online_base
            online_peak = self._online_peak
            online_avg = (round(self._online_sum / self._online_samples)
                          if self._online_samples else 0)
            real_peak = self._online_real_peak
            # 减去模拟热度基数才是"真实观众数"；没有采样时平均值无意义，报 0
            avg_real = max(0, online_avg - base) if self._online_samples else 0
            duration = self.live_state.liveDuration
            start_time = self.live_state.startTime or ""
            product = self.get_current_product()
            product_name = product.name if product else "未上架商品"
            preset_used = sum(1 for p in self.presets.values() if p.useCount > 0)
            human_transfer = sum(
                1 for uid, s in self.user_sessions.items()
                if is_real_user_id(uid) and s.status == "human_reception")
            top_questions = self.get_high_freq_questions(10)
            stats_snapshot = {
                "mentionCount": self.stats.mentionCount,
                "intentScore": self.stats.intentScore,
                "aiReplySuccessRate": self.stats.aiReplySuccessRate,
                "avgResponseTime": self.stats.avgResponseTime,
                "humanTransferCount": human_transfer,
                "presetUsedCount": preset_used,
                "virtualDanmakuCount": virtual_count,
                "potentialOrderCount": self.stats.potentialOrderCount,
            }

            # ---- 成交（本场真实观众订单）----
            real_orders = self._session_orders_locked()
            mock_orders_excluded = sum(1 for o in self.orders.values() if not order_is_real(o))
            paid_statuses = ("paid", "shipped", "completed")
            paid_orders = [o for o in real_orders if o.status in paid_statuses]
            gmv = round(sum(o.totalAmount for o in paid_orders), 2)
            sold_qty = sum(o.quantity for o in paid_orders)
            refund_count = sum(1 for o in real_orders if o.status == "refunded")
            by_product: dict[str, dict] = {}
            for o in paid_orders:
                b = by_product.setdefault(o.productId, {
                    "productId": o.productId, "name": o.productName,
                    "qty": 0, "amount": 0.0})
                b["qty"] += o.quantity
                b["amount"] = round(b["amount"] + o.totalAmount, 2)

            # ---- 转化阻碍：对**真实弹幕**做关键词分类计数（旧实现是 5 个魔数）----
            barrier_counts: dict[str, int] = {}
            for d in real_dm:
                k = classify_barrier(d.content)
                if k != "other":
                    barrier_counts[k] = barrier_counts.get(k, 0) + 1

            # ---- 优缺点：全部来自真实评价，没有就留空（前端隐藏该行）----
            positive_tags: dict[str, int] = {}
            concern_tags: dict[str, int] = {}
            for r in real_reviews:
                bucket = positive_tags if r.sentiment == "positive" else concern_tags
                if r.sentiment not in ("positive", "negative"):
                    continue
                if r.tags:
                    for t in r.tags:
                        bucket[t] = bucket.get(t, 0) + 1
                else:
                    # 观众提交的评价没有 tags，退化成"用户原话里命中的情绪词"
                    for w in (_POSITIVE_WORDS if r.sentiment == "positive" else _NEGATIVE_WORDS):
                        if w in r.content:
                            bucket[w] = bucket.get(w, 0) + 1
            positive_points = [k for k, _ in sorted(
                positive_tags.items(), key=lambda kv: -kv[1])[:3]]
            concern_points = [k for k, _ in sorted(
                concern_tags.items(), key=lambda kv: -kv[1])[:3]]

            # ---- 评价聚合（只算真实评价）----
            total_reviews = len(real_reviews)
            positive = sum(1 for r in real_reviews if r.sentiment == "positive")
            negative = sum(1 for r in real_reviews if r.sentiment == "negative")
            positive_rate = (positive / total_reviews * 100) if total_reviews else 0.0
            real_rating = (round(sum(r.rating for r in real_reviews) / total_reviews, 1)
                           if total_reviews else None)

            # ---- 本场真实弹幕回复覆盖率与平均响应耗时 ----
            reply_rate = (len(real_replies) / len(real_dm) * 100) if real_dm else 0.0
            latencies: list[float] = []
            for d in real_replies:
                try:
                    latencies.append((
                        datetime.fromisoformat(d.replyTime)
                        - datetime.fromisoformat(d.timestamp)).total_seconds())
                except (TypeError, ValueError):
                    continue
            avg_response = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

        duration_min = duration // 60
        grade = self._calculate_grade(positive_rate, len(real_replies), len(real_dm))

        live_data = {
            "status": self.live_state.status,
            "liveDuration": duration,
            "startTime": start_time,
            "endTime": now.isoformat(),
            "onlineCount": online_peak,
            "totalDanmaku": len(real_dm),
            "totalAIReplies": len(real_replies),
            "totalPrivateMessages": len(real_msgs),
            "currentProductId": self.current_product_id,
            "currentProductName": product_name,
        }

        # 小结文字：只陈述本场真实发生的事，没有真实数据就直说没有
        highlights: list[str] = []
        if real_dm:
            highlights.append(
                f"本场收到真实观众弹幕 {len(real_dm)} 条，来自 {unique_real} 位真实观众")
        else:
            highlights.append("本场没有真实观众发言（统计只算真实用户，不含模拟氛围弹幕）")
        if real_dm:
            highlights.append(
                f"AI 自动回复 {len(real_replies)} 条，回复覆盖率 {reply_rate:.1f}%")
        if paid_orders:
            highlights.append(f"真实成交 {len(paid_orders)} 单，GMV ¥{gmv}")
        else:
            highlights.append("本场没有真实用户下单")
        if total_reviews:
            highlights.append(
                f"收到 {total_reviews} 条真实用户评价，好评率 {positive_rate:.0f}%"
                f"（{positive}/{total_reviews}）")
        else:
            highlights.append("本场没有真实用户评价")

        report = {
            # schemaVersion 2：章节编号与字段口径都在本版改过。
            # 前端遇到没有该字段的历史报告会提示「历史报告」，不去猜旧格式的语义。
            "schemaVersion": 2,
            "reportId": f"report_{int(time.time())}",
            "title": f"{now.strftime('%Y年%m月%d日')} 直播复盘报告",
            "generateTime": now.isoformat(),
            "liveDuration": duration,
            "liveDurationStr": f"{duration_min // 60}小时{duration_min % 60}分钟",
            "grade": grade,
            # 等级阈值（_calculate_grade）是按"一场有几百条弹幕"标定的，真实演示
            # 场景下弹幕只有几十条，等级会偏低。这是产品标定问题，本版不擅自改数字，
            # 而是在报告里把口径讲清楚。
            "gradeNote": "等级阈值按单场 50/200/500 条弹幕标定；真实场景弹幕量级更小时等级偏低属正常",
            "productName": product_name,
            "scope": {
                "caliber": "本报告只统计真实注册观众（服务端发号的 v_* 账号）",
                "excludedMockUsers": 10,
                "excludedVirtualDanmaku": virtual_count,
                "excludedMockDanmaku": mock_dm_excluded,
                "excludedMockOrders": mock_orders_excluded,
                "excludedMockReviews": sum(
                    1 for r in self.reviews if not is_real_user_id(r.userId)),
            },
            "summary": {
                "overallGrade": grade,
                "topProduct": product_name,
                "topQuestions": [q["question"] for q in top_questions[:3]],
                "highlights": highlights,
                "improvements": [
                    "建议补充常见问题的话术库，提高AI回复精准度",
                    "适当增加与观众的互动频次",
                ],
            },
            "chapter1_overview": {
                "basic": {
                    "date": now.strftime("%Y-%m-%d"),
                    "duration": duration,
                    "startTime": start_time,
                    "endTime": now.isoformat(),
                },
                "audience": {
                    # 头条是真实口径
                    "peakRealViewers": real_peak,
                    "avgRealViewers": avg_real,
                    "uniqueRealViewers": unique_real,
                    # 对外展示的含基数数字，标注清楚，不算进上面的分析
                    "peakOnline": online_peak,
                    "avgOnline": online_avg,
                    "baseNote": {
                        "label": "模拟热度基数（不计入分析）",
                        "value": base,
                    },
                },
                "interaction": {
                    "totalDanmaku": len(real_dm),
                    "avgDanmakuPerUser": (round(len(real_dm) / unique_real, 1)
                                          if unique_real else 0.0),
                    "totalPrivateMessages": len(real_msgs),
                    "totalAIReplies": len(real_replies),
                },
                "conversion": {
                    "inquiryUsers": len({m.userId for m in real_msgs}),
                    "intentUsers": int(stats_snapshot["intentScore"]),
                    "potentialOrders": stats_snapshot["potentialOrderCount"],
                    "humanTransferCount": human_transfer,
                },
            },
            "chapter2_product": {
                "productName": product_name,
                # None = 没有真实评价。前端显示「—」，**不要**退回 70 分这种默认值
                "score": real_rating,
                "realReviewCount": total_reviews,
                "intentTrend": stats_snapshot["intentScore"],
                "mentionCount": stats_snapshot["mentionCount"],
                "topQuestions": top_questions,
                "positivePoints": positive_points,
                "concernPoints": concern_points,
            },
            "chapter3_danmaku": {
                "realCount": len(real_dm),
                "virtualCount": virtual_count,
                "violationCount": sum(1 for d in real_dm if d.isViolation),
                "virtualRealRatio": f"{virtual_count}:{len(real_dm)}",
            },
            "chapter4_reviews": {
                "totalReviews": total_reviews,
                "positiveRate": round(positive_rate, 1),
                "positiveCount": positive,
                "negativeCount": negative,
                "source": "真实用户评价",
            },
            "chapter5_ai": {
                "successRate": round(reply_rate, 1),
                "repliedCount": len(real_replies),
                "humanTransferCount": human_transfer,
                "avgResponseTime": avg_response,
                "presetUsedCount": preset_used,
                "failedQuestions": self._get_failed_questions(real_dm),
            },
            # 编号修正：旧版 chapter5_ai 与 chapter5_barriers 重号，顺延重排
            "chapter6_barriers": [
                {
                    "type": cat["type"],
                    "title": cat["title"],
                    "count": barrier_counts.get(cat["type"], 0),
                    "suggestion": cat["suggestion"],
                }
                for cat in _BARRIER_CATEGORIES
            ],
            "chapter7_highlights": self._generate_highlights(real_dm, start_time),
            # 新增：旧报告完全不读订单，成交数据明明已经聚合好了却没人用
            "chapter8_conversion": {
                "realOrders": len(paid_orders),
                "realPaidOrders": sum(1 for o in real_orders if o.status == "paid"),
                "realGMV": gmv,
                "realQty": sold_qty,
                "refundCount": refund_count,
                "buyerCount": len({o.userId for o in paid_orders}),
                "conversionRate": (round(len({o.userId for o in paid_orders}) / unique_real * 100, 1)
                                   if unique_real else 0.0),
                "byProduct": sorted(by_product.values(), key=lambda x: -x["qty"]),
                # 刻意披露：让看报告的人立刻理解 GMV 为什么这么小
                "mockOrdersExcluded": mock_orders_excluded,
            },
            "chapter9_improvements": {
                "product": ["补充更多商品详情信息，增加规格参数展示", "优化卖点描述，突出差异化优势"],
                "presets": ["常见问题建议配置标准话术，减少重复回答", "售后话术需要完善，提高用户满意度"],
                "operation": ["建议增加互动频次，每10分钟发起一次投票/抽奖", "优化直播节奏，核心商品讲解控制在5-8分钟"],
                "content": ["增加商品对比环节，帮助用户做决策", "追加用户评价展示，增强社交证明"],
            },
        }
        if save:
            # 保存报告（不持有锁）
            report_file = os.path.join(REPORTS_DIR, f"report_{int(time.time())}.json")
            self._save_json(report_file, report)
        return report

    def _calculate_grade(self, positive_rate: float, ai_replies: int, total_danmaku: int) -> str:
        """计算直播等级"""
        score = 0
        if positive_rate >= 90: score += 30
        elif positive_rate >= 70: score += 20
        elif positive_rate >= 50: score += 10
        if ai_replies >= 200: score += 30
        elif ai_replies >= 100: score += 20
        elif ai_replies >= 50: score += 10
        if total_danmaku >= 500: score += 40
        elif total_danmaku >= 200: score += 25
        elif total_danmaku >= 50: score += 10
        if score >= 90: return "S"
        elif score >= 70: return "A"
        elif score >= 50: return "B"
        elif score >= 30: return "C"
        return "D"

    def _get_failed_questions(self, real_danmaku: Optional[list] = None) -> list[dict]:
        """真实观众提了、但 AI 没能答上的问题（仅本场）。

        旧写法用 `not d.isVirtual` 当判据 —— 而模拟观众 u001~u010 的 isVirtual 也是
        False，它们只是恰好都被自动回复过才没暴露。改用身份前缀判定（唯一的真伪依据）。
        """
        items = self._session_real_danmaku() if real_danmaku is None else real_danmaku
        failed = [
            {
                "userId": d.userId,
                "userName": d.userName,
                "content": d.content,
                "timestamp": d.timestamp,
            }
            for d in items if d.isQuestion and not d.isReplied
        ]
        return failed[:20]

    def _generate_highlights(self, real_danmaku: Optional[list] = None,
                             start_time: str = "") -> list[dict]:
        """按真实弹幕的时间分布，取最热的 3 个 5 分钟时段。

        旧实现是写死的 3 个时段 + `random.randint(200, 500)` 的热度，还带一句
        「观众参与度高」的评语 —— 无论直播间里实际发生过什么，报告都长一个样。
        现在每个时段的「热度」就是该时段真实观众弹幕的条数。
        """
        items = self._session_real_danmaku() if real_danmaku is None else real_danmaku
        if not items or not start_time:
            return []
        try:
            t0 = datetime.fromisoformat(start_time)
        except (TypeError, ValueError):
            return []
        buckets: dict[int, list] = {}
        for d in items:
            try:
                offset = (datetime.fromisoformat(d.timestamp) - t0).total_seconds()
            except (TypeError, ValueError):
                continue
            if offset < 0:
                continue
            buckets.setdefault(int(offset // 300), []).append(d)
        if not buckets:
            return []
        # 先按热度挑出前 3 个桶，再按时间先后排列 —— 热度只用于筛选，不用于展示顺序
        top = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
        top.sort(key=lambda kv: kv[0])
        out = []
        for idx, bucket in top:
            topic_counts: dict[str, int] = {}
            for d in bucket:
                k = classify_barrier(d.content)
                if k != "other":
                    topic_counts[k] = topic_counts.get(k, 0) + 1
            topic = "观众互动"
            if topic_counts:
                key = max(topic_counts.items(), key=lambda kv: kv[1])[0]
                topic = next((c["title"] for c in _BARRIER_CATEGORIES
                              if c["type"] == key), "观众互动")
            sample = max(bucket, key=lambda d: len(d.content))
            summary = f"该时段收到 {len(bucket)} 条真实观众弹幕"
            if topic != "观众互动":
                summary += f"，主要集中在「{topic}」"
            out.append({
                "timeSlot": f"{idx * 5}-{idx * 5 + 5}分钟",
                "peakHeat": len(bucket),
                "topic": topic,
                "summary": summary,
                "sampleDanmaku": sample.content,
            })
        return out

    # ============================================================
    # 临时数据生成（用于前端开发测试）
    # ============================================================
    def generate_mock_data(self):
        """生成充实的模拟数据：弹幕、问答、评价、会话、私信，让前端各页面看起来真实"""
        import uuid
        now = datetime.now()
        base_time = now.timestamp() - 3600  # 模拟从1小时前开始

        # 商品列表（用于引用）
        products_list = list(self.products.values()) if self.products else []
        if not products_list:
            return

        # ① 先撤销上一轮模拟订单对库存/销量的扣减（幂等），否则重建会让库存净减
        self._rollback_mock_side_effects()
        # ② 只清模拟氛围，**保留真实观众的一切**（弹幕/私信/评价/订单/会话）
        self._purge_mock_collections()
        self.blacklist.clear()
        self.alerts.clear()
        self._last_alert_real_online = None
        self._low_stock_alerted = False
        self._save_alerts()
        self.high_freq_questions = {}
        self.virtual_freq_questions = {}
        self.stats = LiveStatsData()
        # ⚠️ 计数器一律**不复位**：真实订单/弹幕/私信/评价会跨重启存活，
        # 复位会让新生成的 id 与存活记录撞号（orderNo 尤其明显）。
        # 重置直播状态为「未开播」。
        # 旧实现这里是 status="ended" + liveDuration=3600 + startTime=一小时前，
        # 凭空造出一场完整直播供复盘报告消费 —— 报告里的时长、峰值、观众数因此
        # 全部有据可依地「看起来像真的」，实际一个真实用户都没来过。
        # 未开播时 live_state.startTime 为空，_in_session() 恒 False，本场统计自然全 0。
        self.live_state = LiveSessionState(
            status="idle", onlineCount=self._online_base_from_settings()
        )
        self._online_tracking_active = False
        self._online_peak = 0
        self._online_real_peak = 0
        self._online_sum = 0
        self._online_samples = 0
        self._session_real_users = set()
        # 商品评分/评价数由下面的评价重灌之后统一重算，此处不预设

        def ts(offset_minutes: int) -> str:
            """生成相对 now 的过去时间戳"""
            return datetime.fromtimestamp(base_time + offset_minutes * 60).isoformat()

        # ============================================================
        # 1. 生成用户会话（8个真实感用户）
        # ============================================================
        mock_users = [
            {"id": "u001", "name": "小甜甜", "level": "high_intent", "avatar": ""},
            {"id": "u002", "name": "追风的少年", "level": "potential", "avatar": ""},
            {"id": "u003", "name": "花开富贵", "level": "wait_and_see", "avatar": ""},
            {"id": "u004", "name": "淡定哥", "level": "picky", "avatar": ""},
            {"id": "u005", "name": "随便逛逛", "level": "just_ask", "avatar": ""},
            {"id": "u006", "name": "果粉一枚", "level": "high_intent", "avatar": ""},
            {"id": "u007", "name": "喵星人", "level": "potential", "avatar": ""},
            {"id": "u008", "name": "大头儿子", "level": "wait_and_see", "avatar": ""},
            {"id": "u009", "name": "晴天娃娃", "level": "high_intent", "avatar": ""},
            {"id": "u010", "name": "夜空中最亮的星", "level": "potential", "avatar": ""},
        ]
        for u in mock_users:
            self._get_or_create_session(u["id"], u["name"], u["avatar"])
            self.update_session_level(u["id"], u["level"])

        # ============================================================
        # 2. 生成 30+ 条弹幕（根据当前商品列表中实际存在的商品生成，含 AI 回复）
        # ============================================================
        # 先选第一个商品作为"上架商品"
        default_product = products_list[0]
        self.current_product_id = default_product.productId
        default_product.isOnSale = True

        # 弹幕模板（只针对当前上架的商品，弹幕和回复都引用商品名）
        p = default_product
        danmaku_data = [
            ("u001", f"这个{p.name}多少钱呀？", f"这款{p.name}现在直播间专享价仅需{p.price:.0f}元！"),
            ("u002", "有优惠吗现在？", f"现在下单超级划算！原价{p.originalPrice:.0f}元，直播间到手仅{p.price:.0f}元，抓紧机会哟~"),
            ("u003", "保质期多久？", f"这款{p.name}保质期充足，生产日期都是最新的，放心购买哦~"),
            ("u004", "质量怎么样？靠谱吗", f"亲放心，这款{p.name}品质非常好，很多老客户都在回购，好评率很高！"),
            ("u005", f"看看{p.name}怎么样", f"这款{p.name}现在卖得特别好，品质口碑都很棒👍"),
            ("u006", "这个什么规格的？", f"规格详情可以看商品介绍，有什么想了解的可以继续问我哦~"),
            ("u007", "这个适合什么人用", f"当然适合！这款{p.name}就是为{p.category}场景设计的，非常合适~"),
            ("u008", "能不能再便宜点", f"亲，这个价格已经是最优惠了，品质绝对对得起价格，放心入手吧！"),
            ("u001", "下单后多久发货", "下单后48小时内发货，预计3-5天送达，感谢支持~"),
            ("u009", "这个送人合适吗", f"这款{p.name}非常体面，送人很合适，包装精美品质好~"),
            ("u002", "和别家比有什么优势", f"我们这款{p.name}品质有保障，价格还实惠，售后服务也很到位~"),
            ("u010", "怎么使用效果最好", f"亲，按照说明使用效果最好哦，这款{p.name}用法很简单~"),
            ("u003", "库存还有多少", f"目前库存还有{p.stock}件，卖完就没了，喜欢的抓紧入手~"),
            ("u005", "能退换吗", "支持7天无理由退换，有质量问题随时联系客服处理~"),
            ("u004", "我感觉有点贵啊", f"亲，这款{p.name}的品质真的值得这个价格，现在直播间还有优惠，性价比超高！"),
            ("u006", "有什么优点？", f"卖点太多了！{p.name}品质好、口碑棒、价格实惠，样样都出色~"),
            ("u007", "还有其他款式吗", f"目前这款就是直播间展示的款式，经典百搭，很受欢迎哦~"),
            ("u001", "下单了！", f"感谢亲的支持！{p.name}品质绝对让您满意，有任何问题随时找我们~"),
            ("u008", f"{p.name}和之前直播的比怎么样", f"这款{p.name}是最新款，比之前的品质更好了，价格还更实惠呢！"),
            ("u009", "主播推荐一下呗", f"强烈推荐这款{p.name}！品质超棒，直播间专属价很划算~"),
            ("u010", "买了买了", f"恭喜亲入手！{p.name}绝对不会让您失望的~"),
            ("u001", f"{p.name}好用吗", f"这款{p.name}口碑非常好，很多老客户都在回购，放心入手~"),
            ("u002", "现在买有什么赠品", f"现在下单购买{p.name}，直播间还有专属赠品，千万不要错过！"),
            ("u003", f"{p.name}和别家比怎么样", f"我们这款{p.name}性价比非常高，品质有保障，价格还实惠！"),
            ("u004", f"{p.name}效果怎么样", f"亲，{p.name}的效果反馈非常好，很多客户用了都说好~"),
            ("u005", "什么时候有活动", f"现在就是活动价！{p.name}直播间专享价仅{p.price:.0f}元，比日常优惠很多~"),
            ("u006", f"{p.name}包邮吗", f"这款{p.name}全国包邮（偏远地区除外），下单后48小时发货~"),
            ("u007", "可以试用吗", "亲，支持7天无理由退换，可以放心购买试用~"),
            ("u008", f"{p.name}有现货吗", f"目前{p.name}库存还有{p.stock}件，现货充足，拍下即发~"),
            ("u009", "主播用的是什么", f"我现在用的就是这款{p.name}，品质真的很好，推荐给大家！"),
            ("u010", f"{p.name}怎么买最划算", f"现在直播间就是最低价！{p.name}仅{p.price:.0f}元，还可以叠加优惠券~"),
        ]

        offset = 2
        for uid, question, reply in danmaku_data:
            danmaku_id = f"mock_dm_{uuid.uuid4().hex[:8]}"
            user_name = next((u["name"] for u in mock_users if u["id"] == uid), uid)
            d = DanmakuItem(
                danmakuId=danmaku_id,
                userId=uid,
                userName=user_name,
                content=question,
                timestamp=ts(offset),
                isVirtual=False,
                isReplied=True,
                replyContent=reply,
                replyTime=ts(offset + 0.5),
            )
            self.danmaku_list.append(d)
            self._danmaku_counter += 1
            # 刻意不累加 live_state.totalDanmaku / totalAIReplies：
            # 这两个是**真实观众**口径，模拟弹幕只负责氛围，不进任何统计。
            offset += 3

        # ============================================================
        # 3. 生成私信对话（每个用户2-3条）
        # ============================================================
        private_msg_data = [
            ("u001", "你好，我想咨询一下精华液", True, 3),
            ("u001", "这款精华液和XX大牌比效果怎么样？", True, 5),
            ("", "亲，我们的精华液采用三重玻尿酸分子，深层补水锁水，很多客户反馈比大牌性价比更高哦~", False, 6),
            ("u001", "好的，那我来一瓶试试", True, 8),
            ("", "感谢您！已经帮您记录了，点击下方小黄车即可下单~", False, 9),
            ("u002", "请问这个保质期多久？上次买过很好用", True, 12),
            ("", "保质期3年，日期都是最新的，感谢您再次光临~", False, 13),
            ("u003", "有什么优惠活动吗？", True, 15),
            ("", "现在直播间下单享专属优惠，买2瓶立减50元，买3瓶送旅行装~", False, 16),
            ("u004", "质量到底行不行啊，之前买到过不好的", True, 20),
            ("", "亲放心，我们的商品都有品质保障，支持7天无理由退换，您可以先买一瓶试试~", False, 21),
            ("u005", "随便看看，不用管我", True, 25),
            ("u006", "耳机能连苹果手机吗", True, 30),
            ("", "完全兼容！蓝牙5.3，支持iPhone/iPad，连接稳定延迟低~", False, 31),
            ("u007", "有没有适合办公室用的", True, 35),
            ("", "推荐护腰靠垫和静音台灯，办公室久坐必备神器！", False, 36),
            ("u008", "买3盒益生菌发什么快递", True, 40),
            ("", "默认发中通快递，3-5天送达，益生菌常温保存即可，不用冷藏哦~", False, 41),
            ("u009", "想问问送人有什么推荐", True, 45),
            ("", "推荐有机乌龙茶礼盒和花胶羹礼盒，包装精美又实用，送礼很有面子~", False, 46),
            ("u010", "这个吹风机保修多久", True, 50),
            ("", "保修1年，注册延保半年，有问题随时联系我们客服~", False, 51),
        ]

        for uid, content, is_from, offset in private_msg_data:
            if not uid:
                uid = "u001"
            user_name = next((u["name"] for u in mock_users if u["id"] == uid), "用户")
            msg = PrivateMessageItem(
                messageId=f"msg_{uuid.uuid4().hex[:8]}",
                userId=uid,
                userName=user_name,
                content=content,
                timestamp=ts(offset),
                isFromUser=is_from,
            )
            # track_unread=False：模拟会话不该在运营台的未读红点上出现
            self.add_private_message(msg, track_unread=False)

        # ============================================================
        # 4. 生成 15 条评价（各类情感混合）
        # ============================================================
        review_data = [
            ("u001", "东西很好，品质不错！用了一周皮肤明显变好了", 5, "positive", ["品质好", "效果好"], "P001"),
            ("u002", "发货很快，包装也很好，五星好评", 4, "positive", ["物流快", "包装好"], "P001"),
            ("u003", "价格合适，性价比高，已经推荐给朋友了", 4, "positive", ["价格实惠", "推荐"], "P001"),
            ("u004", "感觉没有描述的那么好，一般般吧", 3, "neutral", [], "P004"),
            ("u005", "质量有点失望，不值这个价", 2, "negative", ["不新鲜"], "P008"),
            ("u006", "耳机降噪效果很棒，续航也长，值得购买", 5, "positive", ["降噪好", "续航长"], "P005"),
            ("u007", "吹风机风力大，吹完头发很顺滑不毛躁", 4, "positive", ["风力大", "护发"], "P002"),
            ("u008", "买给父母的，他们说茶叶味道很好", 5, "positive", ["品质好", "送礼"], "P003"),
            ("u009", "靠垫用了两周，腰确实舒服多了", 4, "positive", ["效果好", "舒适"], "P004"),
            ("u010", "益生菌吃了两周感觉肠胃好多了", 4, "positive", ["效果好"], "P006"),
            ("u003", "翻译笔识别率很高，出国旅游太方便了", 5, "positive", ["识别率高", "实用"], "P009"),
            ("u007", "乳胶枕头有点高，睡了几天适应了", 3, "neutral", [], "P010"),
            ("u004", "鸡胸肉肠味道一般，但是健身吃很方便", 3, "neutral", [], "P011"),
            ("u001", "台灯设计很漂亮，亮度可调很好用", 5, "positive", ["设计好", "实用"], "P012"),
            ("u005", "双肩包做工不错，就是有点小", 4, "positive", ["做工好"], "P007"),
            ("u002", "花胶羹挺好吃的，回购第二次了", 5, "positive", ["好吃", "回购"], "P008"),
            ("u006", "性价比一般，同价位有更好的选择", 2, "negative", ["价格高"], "P002"),
        ]
        for uid, content, rating, sentiment, tags, pid in review_data:
            user_name = next((u["name"] for u in mock_users if u["id"] == uid), "匿名")
            product_name = next((p.name for p in self.products.values() if p.productId == pid), "商品")
            review = ReviewItem(
                reviewId=f"review_{uuid.uuid4().hex[:8]}",
                userId=uid,
                userName=user_name,
                content=content,
                rating=rating,
                sentiment=sentiment,
                tags=tags,
                productId=pid,
                productName=product_name,
                source="danmaku" if random.random() > 0.3 else "private",
                timestamp=ts(random.randint(30, 80)),
                isHandled=random.random() > 0.3,
            )
            self.reviews.append(review)
            self._review_counter += 1

        # ============================================================
        # 5. 生成预设话术
        # ============================================================
        if len(self.presets) < 5:
            default_presets = [
                ("price", ["多少钱", "价格", "价位", "售价"], "现在直播间专属价仅需XX元，比日常优惠很多哦~"),
                ("general", ["优惠", "折扣", "打折", "有活动"], "现在下单享直播间专属优惠，千万不要错过！"),
                ("logistics", ["什么时候发", "发货时间", "快递", "物流"], "下单后48小时内发货，预计3-5天送达~"),
                ("aftersale", ["退货", "退款", "坏了", "售后"], "亲，如有任何质量问题，请联系客服处理，我们会给您满意的解决方案~"),
                ("quality", ["质量", "品质", "正品", "靠谱"], "亲放心，我们所有商品都是正品保障，有品质保证，不满意支持退换~"),
            ]
            for cat, keywords, answer in default_presets:
                self._preset_counter += 1
                preset = PresetPhraseItem(
                    presetId=f"preset_{self._preset_counter}",
                    category=cat,
                    keywords=keywords,
                    answer=answer,
                    createTime=now.isoformat(),
                )
                self.presets[preset.presetId] = preset
            self._save_presets()

        # ============================================================
        # 6. 统计数据
        # ============================================================
        # ⚠️ 这里**不再预置任何"看起来像样"的数字**。旧实现在此硬写
        # aiReplySuccessRate=100.0 / intentScore=76.5 / mentionCount=245 /
        # potentialOrderCount=18 / exposureCount=1286 等常数，运营台一开机就显示
        # 一份漂亮的假经营数据。模拟弹幕/私信已灌进内存供直播间营造氛围，但它们
        # 不属于真实经营情况，一律不进统计（决策 #1）。
        #
        # 唯一的例外 virtualDanmakuCount：它**就是**虚拟弹幕的计数，语义即真实，
        # 运营台靠它把「真实弹幕 vs 氛围弹幕」分开看。
        #
        # 其余真实指标由 _recompute_real_stats_locked() 在本函数末尾统一推导。
        self.stats = LiveStatsData(
            onlineCount=self._online_base_from_settings(),
            virtualDanmakuCount=sum(
                1 for d in self.danmaku_list if classify_user_id(d.userId) == "virtual"),
        )

        # ============================================================
        # 7. 生成高频问题统计（**虚拟弹幕口径**）
        # ============================================================
        # ⚠️ 灌进 virtual_freq_questions，**绝不能**进 high_freq_questions：
        # 后者是运营台「高频问题排行」的数据源，决策 #1 要求它只反映真实观众提问。
        # 这两个 dict 是平行的，high_freq_questions 只由 record_question(is_real=True) 填。
        high_freq = [
            ("这个多少钱", 15),
            ("有优惠吗", 12),
            ("保质期多久", 8),
            ("什么时候发货", 7),
            ("适合什么人用", 6),
            ("质量怎么样", 5),
            ("怎么下单", 5),
            ("什么规格", 4),
            ("能退换吗", 4),
            ("库存还有吗", 3),
            ("可以开发票吗", 2),
            ("支持花呗吗", 2),
        ]
        self.virtual_freq_questions = dict(high_freq)

        self._save_presets()

        # ============================================================
        # 8. （已移除）模拟商单成交
        # ============================================================
        # 这里曾调用 _simulate_initial_orders() 在每次启动时给每件商品批量生成
        # randint(5,30) 张 userId="" / isVirtual=True 的假订单，并直接扣 stock、加 soldCount。
        # 后果是 orders_data.json 里 238 张模拟单淹没了 13 张真实单（94.8%），观众端
        # 「已售 N 件」也全靠这些假单撑；配合历史上「purge 不回滚」的 bug，还让商品
        # 库存每开播一次净减一轮（实测累计蒸发 9,960 件）。
        # 现已彻底移除：成交只能来自真实注册观众的 place_order → pay_order。
        # 观众端仍要有热闹的「已售」数字 —— 那由商品自身的 soldBase 展示基数提供，
        # 与订单无关，也绝不会进入任何经营统计（见 _products_payload）。
        #
        # 上面第 0 步保留的 _rollback_mock_side_effects() + _purge_mock_collections()
        # 仍会执行，用于清理历史上遗留在盘里的那批模拟单（幂等，清完即为空转）。

        # ============================================================
        # 9. 重算商品聚合口径 + 真实统计
        # ============================================================
        # 商品 rating/reviewCount 按**全部**评价（真实+模拟）重算 —— 这是观众端
        # 商品卡的展示口径；运营台要的「只算真实评价」走 get_real_product_stats()。
        # 旧实现在 _simulate_initial_orders 里直接 random.uniform(4.2, 5.0) 赋分，
        # 每次重启商品评分都变，本身就是决策 #6 要消灭的随机量。
        self._recompute_product_aggregates()
        # 真实统计（本场内、真实观众）——正常应是全 0：没开播就没有本场
        with self._lock:
            self._recompute_real_stats_locked()

    _SOLD_STATUSES = ("paid", "shipped", "completed")

    def _rollback_mock_side_effects(self) -> None:
        """撤销「上一轮模拟订单」对商品库存/销量的影响（幂等）。

        为什么这一步必须有：`create_order`（以及已删除的 `_simulate_initial_orders`）
        都是直接 `prod.stock -= qty`，而 stock 是**当前值、无法从订单反推**。若只是
        「删掉模拟单再重新生成一批」，库存会**每重启一次净减一轮** —— 多演示几天商品
        就"卖光"了。历史事故正是如此（累计净减 9,960 件，靠重建种子才修得回来）。

        现在已无任何代码会新造模拟单，所以本方法对**新数据**恒为空转；它保留是为了
        清理历史上遗留在 orders_data.json 里的那批模拟单 —— 清干净之后便再无事可做。
        因此**不要**因为「看起来用不上了」就删掉它，否则那些遗留单的库存扣减会永久固化。

        必须按状态区分，否则会二次归还：
          refunded               —— update_order_status 的退款分支已经归还过，跳过
          paid/shipped/completed —— 扣了库存也计了销量，两者都还
          pending_pay/canceled   —— create_order 扣了库存但没计销量，只还库存
        """
        with self._lock:
            per_product: dict[str, list[tuple[int, int]]] = {}
            for o in self.orders.values():
                if order_is_real(o) or o.status == "refunded":
                    continue
                sold = o.quantity if o.status in self._SOLD_STATUSES else 0
                per_product.setdefault(o.productId, []).append((o.quantity, sold))
            for pid, entries in per_product.items():
                prod = self.products.get(pid)
                if prod is None:
                    continue
                prod.stock += sum(q for q, _ in entries)
                prod.soldCount = max(0, prod.soldCount - sum(s for _, s in entries))

    def _purge_mock_collections(self) -> None:
        """只清「模拟 / 虚拟」数据，保留真实观众、老账号与主播本人的一切。

        ⚠️ 必须是**按分类过滤**，不能实现成「跳过清空」：reviews_data.json 里就躺着
        17 条模拟评价，若 _load_reviews 装进来后这里不过滤、而重灌逻辑又 append 一批，
        每重启一次评价数就翻一倍。

        reviews 的判据用 userId 前缀（与全局身份层同源），而不是 source 字段 ——
        source 只是个来源标签，userId 才是身份。

        ⚠️ 调用顺序不可颠倒：**必须先 `_rollback_mock_side_effects()` 再调本方法**。
        本方法删掉模拟单之后就再也查不到它们的 quantity，那笔库存/销量扣减会**永久**
        留在商品上。旧代码曾在 `start_live()` 里单独调本方法（没有前置回滚），后果正是
        「每开播一次，商品库存净少一轮模拟单的量」，而且因为 `_save_orders` 不按真伪
        过滤、模拟单本来会跨重启存续，这个净减是**不可逆**的。现已移除该调用，别无脑加回。
        """
        with self._lock:
            self.danmaku_list = [d for d in self.danmaku_list
                                 if is_persistable_user_id(d.userId)]
            self.private_messages = {
                uid: msgs for uid, msgs in self.private_messages.items()
                if is_persistable_user_id(uid)
            }
            self.reviews = [r for r in self.reviews if is_real_user_id(r.userId)]
            self.user_sessions = {
                uid: s for uid, s in self.user_sessions.items()
                if is_persistable_user_id(uid)
            }
            for oid in [oid for oid, o in self.orders.items() if not order_is_real(o)]:
                del self.orders[oid]
            # 内存已与磁盘对齐，顺手落盘（幂等：第二次启动时这些文件里已经没有模拟数据）
            self._save_orders()
            self._save_danmaku()
            self._save_private_messages()
            self._save_reviews()
            self._save_sessions()

    def get_review_summary(self) -> dict:
        """运营台「用户评价管理」的统计总结（纯统计，**不调 LLM**）。

        口径：只统计真实注册观众主动提交的评价（`source == "user"`）。模拟评价
        （`u001`~`u010`，`source` 为 danmaku/private）只用于观众端商品卡的评分氛围，
        不进这里 —— 被排除的条数在 `scope.excludedMock` 里显式披露，看图的人能一眼
        看出「为什么只有几条」。

        ⚠️ **不做会话边界**：`_in_session` 只适用于弹幕那种「本场」事件；评价可以跨
        场次、跨天补交，硬套会话边界会把历史好评整段过滤掉。默认范围就是「全部真实
        评价」，时间维度由 `trend`（最近 7 天）表达。

        `tags` 依赖：`submit_review` 目前从不写 tags（恒为 `[]`），所以关键词走与
        `generate_report` 同一套兜底 —— 在用户原话里匹配 `_POSITIVE_WORDS` /
        `_NEGATIVE_WORDS`。两处共用同一份词表，不要另造。
        """
        with self._lock:
            real: list[ReviewItem] = []
            excluded_mock = 0
            for r in self.reviews:
                if r.source == "user":
                    real.append(r)
                else:
                    excluded_mock += 1

            total = len(real)
            rating_avg = (round(sum(r.rating for r in real) / total, 1)
                          if total else None)
            rating_dist = {str(n): 0 for n in range(1, 6)}
            sentiment = {"positive": 0, "neutral": 0, "negative": 0}
            for r in real:
                key = str(max(1, min(5, int(r.rating or 5))))
                rating_dist[key] = rating_dist.get(key, 0) + 1
                if r.sentiment in sentiment:
                    sentiment[r.sentiment] += 1

            handled = sum(1 for r in real if r.isHandled)
            images = sum(1 for r in real if r.images)
            with_order = sum(1 for r in real if r.orderId)

            # 按商品聚合
            per_product: dict[str, dict] = {}
            for r in real:
                pid = r.productId or ""
                row = per_product.setdefault(
                    pid, {"productId": pid, "productName": r.productName or "",
                          "count": 0, "_sum": 0})
                row["count"] += 1
                row["_sum"] += int(r.rating or 5)
            by_product = sorted(per_product.values(), key=lambda x: -x["count"])[:10]
            for row in by_product:
                row["avgRating"] = round(row.pop("_sum") / row["count"], 1)

            # 关键词：tags 优先，为空则回退到原话里的情绪词（与报告同源）
            keyword_counts: dict[str, int] = {}
            for r in real:
                if r.tags:
                    for t in r.tags:
                        keyword_counts[t] = keyword_counts.get(t, 0) + 1
                    continue
                words = (_POSITIVE_WORDS if r.sentiment == "positive"
                         else _NEGATIVE_WORDS if r.sentiment == "negative" else ())
                for w in words:
                    if w in (r.content or ""):
                        keyword_counts[w] = keyword_counts.get(w, 0) + 1
            top_tags = [{"tag": k, "count": c} for k, c in
                        sorted(keyword_counts.items(), key=lambda kv: -kv[1])[:10]]

            # 最近 7 天趋势（含 0 的天，条形图才连得起来）
            today = datetime.now().date()
            days = [today - timedelta(days=i) for i in range(6, -1, -1)]
            buckets: dict[str, list[int]] = {d.isoformat(): [] for d in days}
            for r in real:
                try:
                    key = datetime.fromisoformat(r.timestamp).date().isoformat()
                except (TypeError, ValueError):
                    continue
                if key in buckets:
                    buckets[key].append(int(r.rating or 5))
            trend = [{"label": d.strftime("%m-%d"),
                      "count": len(buckets[d.isoformat()]),
                      "avgRating": (round(sum(buckets[d.isoformat()])
                                          / len(buckets[d.isoformat()]), 1)
                                    if buckets[d.isoformat()] else None)}
                     for d in days]

            return {
                "scope": {
                    "caliber": "仅统计真实注册观众（source=user）主动提交的评价；"
                               "模拟评价只用于观众端商品卡的评分展示，不计入本页",
                    "realCount": total,
                    "excludedMock": excluded_mock,
                },
                "stats": {
                    "total": total,
                    "ratingAvg": rating_avg,
                    "ratingDist": rating_dist,
                    "sentiment": sentiment,
                    "handledCount": handled,
                    "handledRate": round(handled / total * 100, 1) if total else 0.0,
                    "imageCount": images,
                    "withOrderCount": with_order,
                    "productCount": len(per_product),
                    "byProduct": by_product,
                    "topTags": top_tags,
                    "trend": trend,
                },
            }

    def _recompute_product_aggregates(self) -> None:
        """按**全部**评价（真实 + 模拟）重算商品的 rating / reviewCount。

        这是给观众端商品卡用的展示口径（决策：观众端保留含模拟数据的显示）。
        运营台要的「只算真实评价」走 get_real_product_stats()，两者互不干扰。
        """
        for prod in self.products.values():
            rs = [r for r in self.reviews if r.productId == prod.productId]
            if rs:
                prod.rating = round(sum(r.rating for r in rs) / len(rs), 1)
            prod.reviewCount = len(rs)
        self._save_products()

    def get_real_product_stats(self, product_id: str) -> dict:
        """某商品的**真实用户**评价聚合（运营台商品卡与复盘报告用）。"""
        with self._lock:
            rs = [r for r in self.reviews
                  if r.productId == product_id and is_real_user_id(r.userId)]
            return {
                "realReviewCount": len(rs),
                "realRating": round(sum(r.rating for r in rs) / len(rs), 1) if rs else None,
            }


# ============================================================
# 全局单例
# ============================================================
_data_manager: Optional[LiveCommerceDataManager] = None


def get_data_manager() -> LiveCommerceDataManager:
    """获取全局数据管理器单例"""
    global _data_manager
    if _data_manager is None:
        _data_manager = LiveCommerceDataManager()
    return _data_manager
