"""
直播电商数据管理与业务逻辑（Data Manager + Business Logic）
管理所有数据存储、业务逻辑、AI生成、实时分析等功能
"""

import csv
import io
import json
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from schema.live_commerce_schema import (
    COUPON_RULES, LIVE_STAGES, ORDER_CHANNELS, ORDER_CHANNEL_LABELS, ORDER_STATUS_LABELS,
    REFUND_REASON_CATEGORIES, STAGE_STRATEGIES, AddressItem, BlacklistItem, CartLineItem,
    DanmakuItem, KnowledgeBaseItem, LiveCommerceSettings, LiveSessionState, LiveStatsData,
    OrderItem, OrderLineItem, PresetPhraseItem, PriceHistoryItem,
    PrivateMessageItem, ProductItem, ReviewItem, SensitiveLogItem, SkuItem,
    SmartAlert, StockLogItem, UserSessionItem, ViewerAccount, ViewerVisitItem,
    VirtualDanmakuSettings, WSMessage, best_coupon, classify_refund_reason,
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
ORDERS_FILE = os.path.join(DATA_DIR, "orders_data.json")
VIEWERS_FILE = os.path.join(DATA_DIR, "viewers_data.json")
# 演示脚本（scripts/seed_demo_viewers.py）登记的账号 ID。这些账号走的是真实注册
# 链路（同样是 v_ 前缀），但订单/退款是脚本造的，运营台的订单工作台需要把它们
# 和真人订单区分开，见 is_demo_viewer / order_is_human。
DEMO_IDS_FILE = os.path.join(DATA_DIR, "demo_viewer_ids.json")
# 购物车 / 库存流水 / 加购事件（商品订单迭代新增；均在 data/ 目录下，已被 .gitignore 忽略）
CARTS_FILE = os.path.join(DATA_DIR, "carts_data.json")
STOCK_LOGS_FILE = os.path.join(DATA_DIR, "stock_logs_data.json")
CART_EVENTS_FILE = os.path.join(DATA_DIR, "cart_events_data.json")
STOCK_LOGS_MAX = 2000
CART_EVENTS_MAX = 5000
ORDER_PAY_TIMEOUT_MINUTES = 30
# ── 全模块迭代新增的独立数据文件 ──
KNOWLEDGE_FILE = os.path.join(DATA_DIR, "knowledge_base.json")
SENSITIVE_LOGS_FILE = os.path.join(DATA_DIR, "sensitive_logs.json")
PRICE_HISTORY_FILE = os.path.join(DATA_DIR, "price_history.json")
VIEWER_VISITS_FILE = os.path.join(DATA_DIR, "viewer_visits.json")
KNOWLEDGE_MAX = 500
SENSITIVE_LOGS_MAX = 1000
PRICE_HISTORY_MAX = 2000
VIEWER_VISITS_MAX = 5000
STAGE_LABELS = {s["key"]: s["label"] for s in LIVE_STAGES}
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

# ── 场次弹幕归档 ──
# 每场直播结束时，把本场**全部**弹幕固化到 danmaku_sessions/<sessionId>.json，
# 索引 index.json 保存场次列表与统计。场次之间物理隔离，复盘页按 sessionId 读取，
# 不再从跨场混存的滚动列表里反推。
# 目录按 DANMAKU_FILE 动态推导（而不是导入时算死），这样测试把 DANMAKU_FILE
# 重定向到 tmp_path 时，归档目录也会跟着走。
DANMAKU_SESSIONS_DIRNAME = "danmaku_sessions"
DANMAKU_SESSION_INDEX = "index.json"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ── 复盘报告独立存储 ──
# 每份报告落盘为 reports/<reportId>.json，index.json 只保存摘要。
# reportId 含时间戳 + 随机串，重新生成会新增一份，**不覆盖**历史报告。
REPORTS_INDEX = "index.json"
_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_QUESTION_PUNCT_RE = re.compile(r"[\s？?！!。，,、；;：:~～…\.]+")


def _question_key(text: str) -> str:
    """高频问题归并键：去空白 / 标点后截前 30 字。"""
    return _QUESTION_PUNCT_RE.sub("", (text or ""))[:30]


def _parse_dt(value: Any) -> Optional[datetime]:
    """宽松解析 ISO 时间戳；失败返回 None。"""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _timestamp_in_window(ts: Any, start: Any, end: Any) -> bool:
    """时间戳是否落在 [start, end] 内；end 缺失时只校验下界。"""
    t, a = _parse_dt(ts), _parse_dt(start)
    if t is None or a is None:
        return False
    b = _parse_dt(end)
    return t >= a and (b is None or t <= b)


def danmaku_sessions_dir() -> str:
    """返回场次弹幕归档目录（不存在则创建）。"""
    path = os.path.join(os.path.dirname(DANMAKU_FILE), DANMAKU_SESSIONS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path

# ── 收货地址校验 ──
# 手机号按中国大陆手机号校验；地址条数上限防止单个账号无限堆积。
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_ADDRESS_MAX = 10


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


# 演示账号登记表的 mtime 缓存：(mtime, ids)。seed_demo_viewers.py 会在服务运行
# 期间登记新账号，所以不能只在进程启动时读一次；用一个 stat 判断是否要重读。
_demo_ids_cache: Optional[tuple[float, frozenset[str]]] = None


def load_demo_viewer_ids() -> frozenset[str]:
    """读取「演示脚本账号」登记表（demo_viewer_ids.json）。"""
    global _demo_ids_cache
    try:
        mtime = os.path.getmtime(DEMO_IDS_FILE)
    except OSError:
        _demo_ids_cache = None
        return frozenset()
    if _demo_ids_cache is not None and _demo_ids_cache[0] == mtime:
        return _demo_ids_cache[1]
    try:
        with open(DEMO_IDS_FILE, encoding="utf-8") as f:
            raw = json.load(f) or []
    except (OSError, json.JSONDecodeError, TypeError):
        return frozenset()
    ids = frozenset(str(x).strip() for x in raw if str(x).strip())
    _demo_ids_cache = (mtime, ids)
    return ids


def is_demo_viewer(uid: Any) -> bool:
    """是否由 scripts/seed_demo_viewers.py 批量注册的演示账号。

    这些账号走的是真实注册链路（同样是 v_ 前缀），订单也能正常流转；但订单和
    退款申请是脚本批量造的，不属于主播要处理的工作量，运营台的订单列表 /
    退款审核 / 退款原因汇总要按真人口径把它们排除。
    """
    return str(uid or "").strip() in load_demo_viewer_ids()


def is_human_user_id(uid: Any) -> bool:
    """真人观众：服务端发号的真实注册账号，且不是 seed 脚本的演示账号。

    运营台的「本场数据」口径统一用它：演示账号走的是真实链路，但行为是脚本
    批量造的，混进漏斗 / 总览会让主播看到对不上自己操作的数字。
    """
    return is_real_user_id(uid) and not is_demo_viewer(uid)


def order_is_human(order: Any) -> bool:
    """运营台订单工作台口径：真人观众订单（排除演示脚本账号）。"""
    return is_human_user_id(getattr(order, "userId", ""))


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
        # 本场**全程**弹幕（不截断）。开播清空，直播结束整场固化到 danmaku_sessions/。
        # 与上面 20 条缓冲的区别：那个只负责「中途进来的观众能看到什么」，
        # 这个负责复盘时「本场到底聊了什么」，因此一条都不能丢。
        self.current_session_danmaku: list[DanmakuItem] = []
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
        self._qr_counter = 0
        # 商单/订单
        self.orders: dict[str, OrderItem] = {}
        self._order_counter = 0
        # 观众账户（钱包）
        self.viewers: dict[str, ViewerAccount] = {}
        # 购物车：userId -> [CartLineItem]
        self.carts: dict[str, list[CartLineItem]] = {}
        # 库存变动流水（上限 STOCK_LOGS_MAX，超出丢弃最旧）
        self.stock_logs: list[StockLogItem] = []
        # 加购事件：用于「加购人数 / 加购转下单转化率」统计
        self.cart_events: list[dict] = []
        # 低库存已提醒的键（productId / productId:skuId），补货后移除以便再次提醒
        self._low_stock_alerted_keys: set[str] = set()
        # ── 直播间商品挂载 / 本场点击埋点 ──
        # 挂载状态本身与商品的 isOnSale 同步（isOnSale=True 即挂载到直播间），
        # 这样观众端沿用既有「只下发已上架商品」的过滤规则，不会出现两套口径。
        # _mounted_at 只负责记录挂载顺序，开播/重启后按 isOnSale 重建，不单独落盘。
        self._mounted_at: dict[str, str] = {}
        # 本场商品点击：productId -> 点击次数。开播时清空，属于会话级统计。
        self._product_clicks: dict[str, int] = {}
        # 按商品去重的点击观众（漏斗按商品筛选时用；全局 _click_viewers 无法区分商品）
        self._product_click_viewers: dict[str, set[str]] = {}
        # 本场点击过商品的真实观众（去重）；游客没有 userId，不计入「点击人数」。
        self._click_viewers: set[str] = set()
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
        # ── 全模块迭代新增的独立集合 ──
        # AI 问答知识库（主播维护的标准问答，AI 回复优先命中）
        self.knowledge_base: list[KnowledgeBaseItem] = []
        # 敏感词拦截日志 / 商品历史售价 / 观众进场离场记录
        self.sensitive_logs: list[SensitiveLogItem] = []
        self.price_history: list[PriceHistoryItem] = []
        self.viewer_visits: list[ViewerVisitItem] = []
        # 观众当前在线连接的进场时间（断开时结算为一条 ViewerVisitItem）
        self._viewer_enter_at: dict[str, str] = {}
        # 观众当前未结算的进场记录：userId -> visitId
        self._viewer_open_visit: dict[str, str] = {}
        # 高频问题 → 各直播阶段的出现次数（TOP10 每行标记所属阶段）
        self.high_freq_stages: dict[str, dict[str, int]] = {}
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
        # 挂载状态没有独立落盘：重启后以「已上架」的商品为准重建挂载顺序，
        # 用商品自带的 addedTime 作为排序依据（旧商品在前）。
        self._mounted_at = {
            p.productId: (p.addedTime or "")
            for p in self.products.values() if p.isOnSale
        }
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
            payload = {
                "productId": product_id,
                "stock": product.stock,
                "soldCount": product.soldCount,
                "skus": [{"skuId": s.skuId, "spec": s.spec, "price": s.price,
                          "originalPrice": s.originalPrice,
                          "stock": s.stock, "isEnabled": s.isEnabled}
                         for s in product.skus],
            }
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
        self._load_danmaku()
        self._load_orders()
        self._load_viewers()
        self._load_carts()
        self._load_stock_logs()
        self._load_cart_events()
        self._load_alerts()
        self._load_knowledge()
        self._load_sensitive_logs()
        self._load_price_history()
        self._load_viewer_visits()
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
                self._normalize_product_skus(product)
                self.products[product.productId] = product
        # 从现有产品数据导入初始商品
        if not self.products:
            self._init_default_products()

    def _normalize_product_skus(self, product: ProductItem) -> ProductItem:
        """保证每个商品至少有一个 SKU，并让 product.stock 与 SKU 库存之和一致。

        旧数据没有 skus 字段：按商品现有价格/库存补一个「默认规格」，这样
        SKU 订单、库存流水、SKU 统计从第一天起就有稳定维度，不需要前端判断
        「这个商品有没有 SKU」。
        """
        if not product.skus:
            spec = " / ".join(product.specs) if product.specs else "默认规格"
            product.skus = [SkuItem(
                skuId=f"{product.productId}-S1",
                spec=spec or "默认规格",
                price=max(0.0, float(product.price or 0)),
                originalPrice=max(0.0, float(product.originalPrice or product.price or 0)),
                stock=max(0, int(product.stock or 0)),
                costPrice=max(0.0, float(product.costPrice or 0)),
                isEnabled=True,
            )]
        seen: set[str] = set()
        for idx, sku in enumerate(product.skus):
            if not sku.skuId or sku.skuId in seen:
                sku.skuId = f"{product.productId}-S{idx + 1}"
            seen.add(sku.skuId)
            if sku.originalPrice <= 0:
                sku.originalPrice = sku.price
            if sku.costPrice <= 0:
                sku.costPrice = product.costPrice
        product.stock = sum(max(0, s.stock) for s in product.skus if s.isEnabled)
        enabled_prices = [s.price for s in product.skus if s.isEnabled and s.price > 0]
        if enabled_prices and product.price <= 0:
            product.price = min(enabled_prices)
        return product

    def _default_sku(self, product: ProductItem) -> Optional[SkuItem]:
        """取商品默认 SKU（第一个启用规格；没有则第一个规格）。"""
        self._normalize_product_skus(product)
        enabled = [s for s in product.skus if s.isEnabled]
        return enabled[0] if enabled else (product.skus[0] if product.skus else None)

    def _find_sku(self, product: ProductItem, sku_id: str = "") -> Optional[SkuItem]:
        """按 skuId 查找规格；skuId 为空时返回默认规格。"""
        self._normalize_product_skus(product)
        if sku_id:
            return next((s for s in product.skus if s.skuId == sku_id), None)
        return self._default_sku(product)

    # ---- 商品/规格历史售价 ----
    def _record_price_changes_locked(self, product: ProductItem,
                                     old_prices: dict[str, tuple[float, float]],
                                     reason: str = "manual_update",
                                     operator: str = "host") -> None:
        """锁内比对 SKU 售价，发生变化就追加一条历史记录。

        old_prices 为空表示新建商品：为每个 SKU 记一条初始售价（oldPrice=0），
        这样商品详情页的「历史售价」从第一天起就有基线。
        """
        now = datetime.now().isoformat()
        changed = False
        for sku in product.skus:
            before = old_prices.get(sku.skuId)
            old_price = float(before[0]) if before else 0.0
            old_original = float(before[1]) if before else 0.0
            if before is not None and (old_price == sku.price
                                       and old_original == sku.originalPrice):
                continue
            self.price_history.append(PriceHistoryItem(
                historyId=f"price_{int(time.time() * 1000)}_{len(self.price_history) + 1}",
                productId=product.productId,
                productName=product.name,
                skuId=sku.skuId,
                skuSpec=sku.spec,
                oldPrice=round(old_price, 2),
                newPrice=round(float(sku.price or 0), 2),
                oldOriginalPrice=round(old_original, 2),
                newOriginalPrice=round(float(sku.originalPrice or 0), 2),
                reason=reason,
                operator=operator,
                timestamp=now,
            ))
            changed = True
        if changed:
            if len(self.price_history) > PRICE_HISTORY_MAX:
                self.price_history = self.price_history[-PRICE_HISTORY_MAX:]
            self._save_price_history()

    def get_price_history(self, product_id: str = "", limit: int = 200) -> list[dict]:
        """历史售价记录（时间倒序），可按商品过滤。"""
        with self._lock:
            rows = [x for x in self.price_history
                    if not product_id or x.productId == product_id]
            rows.sort(key=lambda x: x.timestamp, reverse=True)
            return [x.model_dump() for x in rows[:max(1, int(limit or 200))]]

    def get_tag_groups(self) -> list[dict]:
        """商品标签分组汇总：组名 → 标签 → 商品数。

        tagGroup 为空的历史商品归入「未分组」，标签取自商品 tags。
        前端据此渲染分组筛选与标签管理面板。
        """
        groups: dict[str, dict] = {}
        with self._lock:
            for p in self.products.values():
                group_name = (p.tagGroup or "").strip() or "未分组"
                g = groups.setdefault(group_name, {
                    "group": group_name, "products": 0, "tags": {}, "productIds": []})
                g["products"] += 1
                g["productIds"].append(p.productId)
                for tag in p.tags or []:
                    g["tags"][tag] = g["tags"].get(tag, 0) + 1
            rows = []
            for g in groups.values():
                tags = [{"tag": k, "count": v}
                        for k, v in sorted(g.pop("tags").items(), key=lambda kv: -kv[1])]
                rows.append({**g, "tags": tags})
            rows.sort(key=lambda r: (-r["products"], r["group"]))
            return rows

    # ---- SKU 库存联动 / 流水 ----
    def _append_stock_log_locked(self, product: ProductItem, sku: Optional[SkuItem],
                                 change: int, before: int, after: int, reason: str,
                                 order: Optional[OrderItem] = None,
                                 operator: str = "system") -> None:
        """锁内追加一条库存流水（调用方负责落盘商品与流水文件）。"""
        self.stock_logs.append(StockLogItem(
            logId=f"stock_{int(time.time() * 1000)}_{len(self.stock_logs) + 1}",
            productId=product.productId,
            productName=product.name,
            skuId=sku.skuId if sku else "",
            skuSpec=sku.spec if sku else "默认规格",
            change=change,
            beforeStock=before,
            afterStock=after,
            reason=reason,
            orderId=order.orderId if order else "",
            orderNo=order.orderNo if order else "",
            operator=operator,
            timestamp=datetime.now().isoformat(),
        ))
        if len(self.stock_logs) > STOCK_LOGS_MAX:
            self.stock_logs = self.stock_logs[-STOCK_LOGS_MAX:]
        self._save_stock_logs()

    def _change_sku_stock_locked(self, product: ProductItem, sku: Optional[SkuItem],
                                 delta: int, reason: str, order: Optional[OrderItem] = None,
                                 operator: str = "system") -> int:
        """锁内变更某 SKU 库存（负数为扣减），同步商品总库存并写流水。

        返回变更后的 SKU 库存。调用方必须先校验扣减不超库存；这里再加一道
        max(0) 兜底，避免任何一条路径把库存写成负数。
        """
        if sku is not None:
            before = max(0, int(sku.stock))
            after = max(0, before + int(delta))
            sku.stock = after
        else:
            before = max(0, int(product.stock))
            after = max(0, before + int(delta))
            product.stock = after
        # 商品总库存 = 各启用 SKU 库存之和（有 SKU 时）
        if product.skus:
            product.stock = sum(max(0, s.stock) for s in product.skus if s.isEnabled)
        self._append_stock_log_locked(
            product, sku, after - before, before, after, reason, order, operator,
        )
        return after

    @staticmethod
    def _order_lines(order: OrderItem) -> list[OrderLineItem]:
        """读取订单行；旧单没有 items 时用顶层单商品字段合成一行。"""
        if order.items:
            return order.items
        return [OrderLineItem(
            productId=order.productId,
            productName=order.productName,
            productImage=order.productImage,
            skuId=order.skuId,
            skuSpec=order.skuSpec,
            quantity=order.quantity,
            unitPrice=order.unitPrice,
            originalUnitPrice=order.unitPrice,
            amount=round(order.unitPrice * order.quantity, 2),
            originalAmount=round(order.unitPrice * order.quantity, 2),
            discountAmount=0,
            totalAmount=order.totalAmount,
            channel=order.channel or "legacy",
        )]

    def _product_has_order(self, order: OrderItem, product_id: str) -> bool:
        return any(line.productId == product_id for line in self._order_lines(order))

    def get_stock_logs(self, product_id: str = "", limit: int = 100) -> list[StockLogItem]:
        """库存流水（时间倒序），可按商品过滤。仅运营台可读。"""
        with self._lock:
            rows = [x for x in self.stock_logs if not product_id or x.productId == product_id]
            rows.sort(key=lambda x: x.timestamp, reverse=True)
            return rows[:max(1, int(limit or 100))]

    def get_coupon_rules(self) -> list[dict]:
        return [dict(c) for c in COUPON_RULES]

    def get_order_channels(self) -> list[dict]:
        return [dict(c) for c in ORDER_CHANNELS]

    # ============================================================
    # 观众购物车
    # ============================================================
    @staticmethod
    def _cart_key(product_id: str, sku_id: str) -> str:
        return f"{product_id}|{sku_id or ''}"

    def _cart_payload_locked(self, user_id: str) -> dict:
        """锁内把购物车转成前端可渲染的实时快照（价格/库存以当前商品为准）。"""
        rows = self.carts.get(user_id, [])
        items: list[dict] = []
        goods = 0.0
        total_qty = 0
        for row in rows:
            product = self.products.get(row.productId)
            sku = self._find_sku(product, row.skuId) if product else None
            available = bool(
                product and product.isOnSale and sku and sku.isEnabled
                and sku.stock >= row.quantity
            )
            price = sku.price if sku else 0.0
            line_amount = round(price * row.quantity, 2)
            if available:
                goods = round(goods + line_amount, 2)
                total_qty += row.quantity
            items.append({
                "cartItemId": row.cartItemId or self._cart_key(row.productId, row.skuId),
                "productId": row.productId,
                "name": product.name if product else "商品已删除",
                "image": ((product.images[0] if product.images else product.imageUrl) or "")
                if product else "",
                "skuId": row.skuId,
                "skuSpec": sku.spec if sku else "规格已失效",
                "price": price,
                "originalPrice": (sku.originalPrice if sku else 0) or 0,
                "quantity": row.quantity,
                "stock": max(0, sku.stock) if sku else 0,
                "channel": row.channel,
                "available": available,
                "addTime": row.addedTime,
            })
        coupon = best_coupon(goods)
        discount = round(min(coupon["amount"], goods), 2)
        return {
            "items": items,
            "lineCount": len(items),
            "totalQty": total_qty,
            "goodsAmount": goods,
            "discountAmount": discount,
            "couponId": coupon["couponId"],
            "couponName": coupon["name"],
            "totalAmount": round(goods - discount, 2),
            "couponRules": self.get_coupon_rules(),
        }

    def get_cart(self, user_id: str) -> dict:
        with self._lock:
            return self._cart_payload_locked(user_id)

    def add_cart_item(self, user_id: str, product_id: str, sku_id: str = "",
                      quantity: int = 1, channel: str = "live_cart") -> tuple[dict, str]:
        """加入购物车（同商品同 SKU 累加数量），并记录加购事件。"""
        if not user_id:
            return {}, "请先登录"
        with self._lock:
            product = self.products.get(product_id)
            if not product:
                return self._cart_payload_locked(user_id), "商品不存在"
            if not product.isOnSale:
                return self._cart_payload_locked(user_id), "商品已下架"
            sku = self._find_sku(product, sku_id)
            if sku is None or not sku.isEnabled:
                return self._cart_payload_locked(user_id), "请选择有效的商品规格"
            try:
                qty = int(quantity or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty < 1:
                qty = 1
            rows = self.carts.setdefault(user_id, [])
            if len(rows) >= 50:
                return self._cart_payload_locked(user_id), "购物车最多放 50 种商品"
            row = next((r for r in rows
                        if r.productId == product_id and (r.skuId or "") == (sku.skuId or "")),
                       None)
            new_qty = min(99, (row.quantity if row else 0) + qty, max(0, sku.stock))
            if new_qty <= 0:
                return self._cart_payload_locked(user_id), "该规格库存不足"
            if row is None:
                row = CartLineItem(
                    cartItemId=f"cart_{int(time.time() * 1000)}_{len(rows) + 1}",
                    productId=product_id,
                    skuId=sku.skuId,
                    quantity=new_qty,
                    channel=channel if channel in ORDER_CHANNEL_LABELS else "live_cart",
                    addedTime=datetime.now().isoformat(),
                )
                rows.append(row)
            else:
                row.quantity = new_qty
            self.cart_events.append({
                "userId": user_id,
                "productId": product_id,
                "skuId": sku.skuId,
                "channel": row.channel,
                "timestamp": datetime.now().isoformat(),
            })
            if len(self.cart_events) > CART_EVENTS_MAX:
                self.cart_events = self.cart_events[-CART_EVENTS_MAX:]
            self._save_carts()
            self._save_cart_events()
            cap_msg = "（已达库存上限）" if new_qty >= sku.stock else ""
            return self._cart_payload_locked(user_id), f"已加入购物车{cap_msg}"

    def update_cart_item(self, user_id: str, product_id: str, sku_id: str = "",
                         quantity: int = 1) -> tuple[dict, str]:
        with self._lock:
            rows = self.carts.get(user_id, [])
            row = next((r for r in rows
                        if r.productId == product_id and (r.skuId or "") == (sku_id or "")),
                       None)
            if row is None:
                return self._cart_payload_locked(user_id), "购物车中没有该商品"
            product = self.products.get(product_id)
            sku = self._find_sku(product, sku_id) if product else None
            if product is None or sku is None or not product.isOnSale:
                return self._cart_payload_locked(user_id), "商品已失效"
            try:
                qty = int(quantity or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty < 1:
                return self.remove_cart_item(user_id, product_id, sku_id)
            row.quantity = min(qty, 99, max(0, sku.stock))
            if row.quantity <= 0:
                rows.remove(row)
            if not rows:
                self.carts.pop(user_id, None)
            self._save_carts()
            return self._cart_payload_locked(user_id), ""

    def remove_cart_item(self, user_id: str, product_id: str,
                         sku_id: str = "") -> tuple[dict, str]:
        with self._lock:
            rows = self.carts.get(user_id, [])
            keep = [r for r in rows
                    if not (r.productId == product_id and (r.skuId or "") == (sku_id or ""))]
            if keep:
                self.carts[user_id] = keep
            else:
                self.carts.pop(user_id, None)
            self._save_carts()
            return self._cart_payload_locked(user_id), ""

    def clear_cart_items(self, user_id: str, keys: list[str]) -> dict:
        """下单成功后移除已结算的行；keys 形如 productId|skuId。"""
        with self._lock:
            target = set(keys or [])
            rows = self.carts.get(user_id, [])
            keep = [r for r in rows if self._cart_key(r.productId, r.skuId) not in target]
            if keep:
                self.carts[user_id] = keep
            else:
                self.carts.pop(user_id, None)
            self._save_carts()
            return self._cart_payload_locked(user_id)

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
                self._normalize_product_skus(product)
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

    # ---- 全模块迭代：知识库 / 敏感词日志 / 价格历史 / 观众到场 ----
    @staticmethod
    def _load_models(path: str, model, limit: int = 0) -> list:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = [model(**x) for x in data if isinstance(x, dict)]
            return rows[-limit:] if limit else rows
        except Exception as e:  # noqa: BLE001
            print(f"[LiveCommerce] 加载 {os.path.basename(path)} 失败: {e}")
            return []

    def _load_knowledge(self):
        self.knowledge_base = self._load_models(KNOWLEDGE_FILE, KnowledgeBaseItem, KNOWLEDGE_MAX)

    def _save_knowledge(self):
        self._save_json(KNOWLEDGE_FILE,
                        [x.model_dump() for x in self.knowledge_base[-KNOWLEDGE_MAX:]])

    def _load_sensitive_logs(self):
        self.sensitive_logs = self._load_models(
            SENSITIVE_LOGS_FILE, SensitiveLogItem, SENSITIVE_LOGS_MAX)

    def _save_sensitive_logs(self):
        self._save_json(
            SENSITIVE_LOGS_FILE,
            [x.model_dump() for x in self.sensitive_logs[-SENSITIVE_LOGS_MAX:]])

    def _load_price_history(self):
        self.price_history = self._load_models(
            PRICE_HISTORY_FILE, PriceHistoryItem, PRICE_HISTORY_MAX)

    def _save_price_history(self):
        self._save_json(
            PRICE_HISTORY_FILE,
            [x.model_dump() for x in self.price_history[-PRICE_HISTORY_MAX:]])

    def _load_viewer_visits(self):
        self.viewer_visits = self._load_models(
            VIEWER_VISITS_FILE, ViewerVisitItem, VIEWER_VISITS_MAX)

    def _save_viewer_visits(self):
        self._save_json(
            VIEWER_VISITS_FILE,
            [x.model_dump() for x in self.viewer_visits[-VIEWER_VISITS_MAX:]])

    def _load_orders(self):
        data = self._load_json(ORDERS_FILE, [])
        self.orders = {o["orderId"]: OrderItem(**o) for o in data}
        if self.orders:
            # 恢复计数器，避免单号/ID 冲突
            self._order_counter = max(len(self.orders), self._order_counter)
        # 旧订单在支付时才扣库存，且没有 stockDeducted 标记。这里把已成交/已退款的
        # 历史单标成「库存已扣」，退款回补逻辑才不会漏；待付款旧单保持 False，
        # 取消时也不会凭空加库存。新订单在创建时就会写入 True。
        for order in self.orders.values():
            if not order.stockDeducted and order.status in ("paid", "shipped", "completed", "refunded"):
                order.stockDeducted = True
            if not order.channel:
                order.channel = "legacy"
            if not order.items and order.goodsAmount <= 0:
                order.goodsAmount = order.totalAmount
                order.originalAmount = order.originalAmount or round(order.unitPrice * order.quantity, 2)

    def _save_orders(self):
        self._save_json(ORDERS_FILE, [o.model_dump() for o in self.orders.values()])

    # ---- 购物车 / 库存流水 / 加购事件持久化 ----
    def _load_carts(self):
        data = self._load_json(CARTS_FILE, {})
        self.carts = {}
        if isinstance(data, dict):
            for uid, rows in data.items():
                try:
                    self.carts[uid] = [CartLineItem(**r) for r in rows]
                except Exception:
                    continue

    def _save_carts(self):
        self._save_json(CARTS_FILE, {
            uid: [r.model_dump() for r in rows] for uid, rows in self.carts.items() if rows
        })

    def _load_stock_logs(self):
        data = self._load_json(STOCK_LOGS_FILE, [])
        try:
            self.stock_logs = [StockLogItem(**x) for x in data][-STOCK_LOGS_MAX:]
        except Exception:
            self.stock_logs = []

    def _save_stock_logs(self):
        self._save_json(STOCK_LOGS_FILE,
                        [x.model_dump() for x in self.stock_logs[-STOCK_LOGS_MAX:]])

    def _load_cart_events(self):
        data = self._load_json(CART_EVENTS_FILE, [])
        self.cart_events = data[-CART_EVENTS_MAX:] if isinstance(data, list) else []

    def _save_cart_events(self):
        self._save_json(CART_EVENTS_FILE, self.cart_events[-CART_EVENTS_MAX:])

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
        """记录一个本场登录过的真人观众（WebSocket 层 _bind_user 时调用）。

        演示脚本账号不计入：本场漏斗 / 总览里的「观看直播」只反映真人。
        """
        if is_human_user_id(user_id):
            with self._lock:
                self._session_real_users.add(user_id)

    # ============================================================
    # 场次弹幕：绑定 / 归档 / 复盘查询
    # ============================================================
    @staticmethod
    def _new_live_session_id() -> str:
        """生成唯一场次ID：live_年月日_时分秒_4位随机串。"""
        return f"live_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    def _ensure_live_session_id_locked(self) -> str:
        """确保直播场次有唯一ID（调用方需持锁）。

        正常路径由 start_live 生成；这里兜住「直接改 live_state 的测试/旧调用」，
        避免弹幕因为 sessionId 为空而无法归入任何场次。
        """
        if not self.live_state.sessionId:
            self.live_state.sessionId = self._new_live_session_id()
        return self.live_state.sessionId

    def _bind_danmaku_session_locked(self, danmaku: DanmakuItem) -> None:
        """判定情绪并把弹幕绑定到当前场次（调用方需持锁）。

        只有直播中/暂停中才采集：未开播时发的弹幕 sessionId 保持空串，
        仍进入全局滚动列表，但不属于任何一场直播。
        """
        if not danmaku.sentiment:
            danmaku.sentiment = classify_sentiment(danmaku.content)
        if self.live_state.status in ("live", "paused"):
            sid = self._ensure_live_session_id_locked()
            if not danmaku.sessionId:
                danmaku.sessionId = sid
            # 记录弹幕发生时的直播阶段（高频问题 TOP10 按阶段归属）
            if not danmaku.stage:
                danmaku.stage = self.live_state.currentStage or ""
            if danmaku.sessionId == sid:
                self.current_session_danmaku.append(danmaku)

    @staticmethod
    def _count_session_danmaku(items: list[DanmakuItem]) -> dict:
        """场次弹幕统计：总数、真实观众数、提问数、情绪分布。

        `human` 排除 seed 脚本的演示账号，运营台「场次弹幕复盘」用它做展示口径。
        """
        counts = {
            "total": len(items), "real": 0, "human": 0, "question": 0,
            "emotion": 0, "positive": 0, "negative": 0, "neutral": 0,
        }
        for d in items:
            if is_real_user_id(d.userId):
                counts["real"] += 1
            if is_human_user_id(d.userId) and not d.isViolation:
                counts["human"] += 1
            if d.isQuestion:
                counts["question"] += 1
            sentiment = d.sentiment or classify_sentiment(d.content)
            if sentiment in ("positive", "negative"):
                counts[sentiment] += 1
                counts["emotion"] += 1
            else:
                counts["neutral"] += 1
        return counts

    def _archive_current_session_locked(self, end_time: str) -> Optional[dict]:
        """把本场全部弹幕固化到独立场次文件，并更新索引（调用方需持锁）。

        归档是幂等的：同一 sessionId 再次归档会覆盖旧文件、索引里只保留一条。
        返回场次摘要（供 live_ended 广播）；没有可归档内容时返回 None。
        """
        items = list(self.current_session_danmaku)
        if not self.live_state.sessionId:
            if not items:
                return None
            self._ensure_live_session_id_locked()
        sid = self.live_state.sessionId
        product = self.get_current_product()
        product_name = product.name if product else "未上架商品"
        start_time = self.live_state.startTime or ""
        start_label = start_time[:16].replace("T", " ") if start_time else end_time[:16].replace("T", " ")
        counts = self._count_session_danmaku(items)
        # 复盘重新生成时场次内存快照已不存在，在线人数 / 真实观众 / 话术使用等
        # 指标必须随场次一起固化，否则历史报告只能退回 0。
        avg_online = (round(self._online_sum / self._online_samples)
                      if self._online_samples else 0)
        avg_real = (max(0, avg_online - self._online_base)
                    if self._online_samples else 0)
        meta = {
            "sessionId": sid,
            "title": f"直播场次 · {start_label}" if start_label else "直播场次",
            "productId": product.productId if product else "",
            "productName": product_name,
            "startTime": start_time,
            "endTime": end_time,
            "duration": self.live_state.liveDuration,
            **counts,
            "realUsers": len(self._session_real_users),
            "peakOnline": self._online_peak,
            "avgOnline": avg_online,
            "peakRealViewers": self._online_real_peak,
            "avgRealViewers": avg_real,
            "onlineBase": self._online_base,
            "presetUsedCount": sum(1 for p in self.presets.values() if p.useCount > 0),
            "humanTransferCount": sum(
                1 for uid, s in self.user_sessions.items()
                if is_real_user_id(uid) and s.status == "human_reception"),
            "archivedAt": end_time,
        }
        session_dir = danmaku_sessions_dir()
        # 1) 本场独立文件：场次之间物理隔离，复盘只读这里
        self._save_json(
            os.path.join(session_dir, f"{sid}.json"),
            {"session": meta, "danmaku": [d.model_dump() for d in items]},
        )
        # 2) 索引：只存摘要，不存弹幕正文，便于复盘页快速列出所有场次
        index_path = os.path.join(session_dir, DANMAKU_SESSION_INDEX)
        index = self._load_json(index_path, [])
        if not isinstance(index, list):
            index = []
        index = [m for m in index
                 if isinstance(m, dict) and m.get("sessionId") != sid]
        index.append(meta)
        index.sort(key=lambda m: str(m.get("startTime", "")), reverse=True)
        self._save_json(index_path, index)
        self.current_session_danmaku = []
        return meta

    def get_danmaku_sessions(self) -> list[dict]:
        """所有已归档场次摘要（按开播时间倒序）。

        旧归档的索引里没有 `human`（排除 seed 演示账号后的真人弹幕数），但复盘页
        的下拉必须和列表口径一致，否则会出现「下拉显示 24 条、点进去 0 条」。
        这里对缺字段的历史场次读一次场次文件补齐，新归档已在 meta 里直接带 human。
        """
        with self._lock:
            index = self._load_json(
                os.path.join(danmaku_sessions_dir(), DANMAKU_SESSION_INDEX), [])
        if not isinstance(index, list):
            return []
        sessions = [m for m in index if isinstance(m, dict) and m.get("sessionId")]
        session_dir = danmaku_sessions_dir()
        for meta in sessions:
            if "human" in meta:
                continue
            try:
                data = self._load_json(
                    os.path.join(session_dir, f"{meta['sessionId']}.json"), {})
                raw = data.get("danmaku", []) if isinstance(data, dict) else []
                meta["human"] = sum(
                    1 for d in raw
                    if isinstance(d, dict)
                    and is_human_user_id(d.get("userId"))
                    and not d.get("isViolation"))
            except (OSError, ValueError, TypeError):
                meta["human"] = int(meta.get("real") or 0)
        sessions.sort(key=lambda m: str(m.get("startTime", "")), reverse=True)
        return sessions

    def get_session_danmaku(self, session_id: str, filter_type: str = "all",
                            keyword: str = "") -> dict:
        """读取某一场已归档弹幕，并按提问 / 情绪 / 关键词筛选。

        filter_type: all | question | emotion（正面或负面） | positive | negative | neutral
        场次ID 来自客户端，先做白名单校验再拼路径，避免路径穿越。
        """
        sid = (session_id or "").strip()
        empty = {"success": False, "session": None, "danmaku": [], "counts": {}}
        if not _SESSION_ID_RE.fullmatch(sid):
            return {**empty, "message": "场次ID不合法"}
        meta = next((m for m in self.get_danmaku_sessions()
                     if m.get("sessionId") == sid), None)
        if meta is None:
            return {**empty, "message": "场次不存在或尚未归档"}
        with self._lock:
            data = self._load_json(
                os.path.join(danmaku_sessions_dir(), f"{sid}.json"), {})
        raw = data.get("danmaku", []) if isinstance(data, dict) else []
        items: list[DanmakuItem] = []
        for d in raw:
            try:
                item = DanmakuItem(**d)
            except Exception:
                continue
            # 复盘页只回看真人观众的弹幕：AI 虚拟弹幕、u001 模拟观众、主播本人
            # 以及 seed 脚本的演示账号都不进列表，避免主播对着假数据复盘。
            # 命中敏感词被拦截的消息也排除 —— 它从未公开显示，不能算进本场互动。
            if is_human_user_id(item.userId) and not item.isViolation:
                items.append(item)
        counts = self._count_session_danmaku(items)

        flt = (filter_type or "all").strip().lower()
        kw = (keyword or "").strip().lower()

        def match(item: DanmakuItem) -> bool:
            if kw and kw not in (item.content or "").lower():
                return False
            sentiment = item.sentiment or classify_sentiment(item.content)
            if flt == "question":
                return item.isQuestion
            if flt == "emotion":
                return sentiment in ("positive", "negative")
            if flt in ("positive", "negative", "neutral"):
                return sentiment == flt
            return True

        result = []
        for item in items:
            if not item.sentiment:
                # 兼容引入 sentiment 字段之前写入的归档
                item.sentiment = classify_sentiment(item.content)
            if match(item):
                result.append(item.model_dump())
        return {"success": True, "session": meta, "danmaku": result,
                "counts": counts, "filter": flt, "keyword": kw}

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
            self._normalize_product_skus(product)
            if not product.addedTime:
                product.addedTime = datetime.now().isoformat()
            self.products[product.productId] = product
            self._record_price_changes_locked(product, {})
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
            old_sku_stock = {s.skuId: s.stock for s in p.skus}
            old_sku_prices = {s.skuId: (s.price, s.originalPrice) for s in p.skus}
            # 用「完整字段 + 更新字典」重新校验，而不是 setattr 直接塞值：
            # 前端传来的 skus 是 dict 列表，setattr 会绕过 Pydantic 校验，
            # 后续访问 sku.stock 就会炸。
            merged = p.model_dump()
            merged.update({k: v for k, v in data.items() if k in ProductItem.model_fields})
            merged["productId"] = product_id
            try:
                updated = ProductItem(**merged)
            except Exception:
                return None
            self._normalize_product_skus(updated)
            # 兼容只传 price/originalPrice/stock 的旧调用（HTTP 单字段更新）：
            # SKU 是价格与库存的真源，不把这几个字段同步到单规格商品的话，
            # 接口返回「修改成功」但下单仍按旧价/旧库存走。
            # 多规格商品的价格/库存无法从单个字段无歧义地拆分，必须显式传 skus。
            if "skus" not in data and len(updated.skus) == 1:
                only_sku = updated.skus[0]
                if data.get("price") is not None:
                    only_sku.price = max(0.0, float(data["price"]))
                if data.get("originalPrice") is not None:
                    only_sku.originalPrice = max(0.0, float(data["originalPrice"]))
                if data.get("stock") is not None:
                    only_sku.stock = max(0, int(data["stock"]))
                self._normalize_product_skus(updated)
            self.products[product_id] = updated
            # 手工改库存也留流水，和订单引起的变更放在同一本账上。
            for sku in updated.skus:
                before = old_sku_stock.get(sku.skuId, sku.stock)
                if before != sku.stock:
                    self._append_stock_log_locked(
                        updated, sku, sku.stock - before, before, sku.stock,
                        reason="manual_update", operator="host",
                    )
            self._record_price_changes_locked(updated, old_sku_prices)
            self._save_products()
            p = updated
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
            # 挂载状态跟随 isOnSale：上架即挂载，下架即从直播间撤下。
            if on_sale:
                self._mounted_at.setdefault(product_id, datetime.now().isoformat())
            else:
                self._mounted_at.pop(product_id, None)
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
    # 直播间商品挂载 / 商品卡片推送
    # ============================================================
    def get_mounted_products(self) -> list[ProductItem]:
        """当前直播间已挂载的商品（按挂载时间排序，旧挂载在前）。

        挂载状态 = 商品已上架。观众端商品列表、舞台当前商品、下单校验用的都是
        同一个 isOnSale 字段，因此这里不需要再维护一份独立的白名单 —— 两套集合
        迟早会不同步，而下单/支付服务端只认 isOnSale。
        """
        with self._lock:
            items = [p for p in self.products.values() if p.isOnSale]
            items.sort(key=lambda p: (
                self._mounted_at.get(p.productId, ""),
                p.addedTime or "",
                p.productId,
            ))
            return items

    def mount_live_product(self, product_id: str) -> tuple[Optional[ProductItem], str]:
        """把商品挂载到直播间（幂等）。返回 (product, 错误原因)。

        挂载 = 上架：成功后观众端立刻能通过 get_products 拿到该商品，并收到
        ``live_product_mounted`` 事件（含商品快照，可直接渲染）。
        """
        with self._lock:
            product = self.products.get(product_id)
            if product is None:
                return None, "商品不存在"
            already = product.isOnSale
            product.isOnSale = True
            mounted_at = self._mounted_at.setdefault(
                product_id, datetime.now().isoformat()
            )
            if not already:
                self._save_products()
            snapshot = product.model_dump()
        # 上下架事件是老前端（商品管理页）认的刷新信号，保留；挂载事件带完整
        # 商品快照，供直播间控制台与观众端即时插入列表，不必等一次往返。
        self._broadcast("product_updated", {
            "productId": product_id,
            "isOnSale": True,
        })
        self._broadcast("live_product_mounted", {
            "product": snapshot,
            "mountedAt": mounted_at,
            "alreadyMounted": already,
        })
        return product, ""

    def unmount_live_product(self, product_id: str) -> tuple[Optional[ProductItem], str]:
        """把商品从直播间撤下（幂等）。返回 (product, 错误原因)。

        撤下 = 下架：观众端列表立即过滤掉该商品；若它正是当前讲解商品，
        同时清空 current_product_id，避免舞台继续展示一个买不到的商品。
        """
        with self._lock:
            product = self.products.get(product_id)
            if product is None:
                return None, "商品不存在"
            already = not product.isOnSale
            product.isOnSale = False
            self._mounted_at.pop(product_id, None)
            cleared_current = self.current_product_id == product_id
            if cleared_current:
                self.current_product_id = ""
            if not already:
                self._save_products()
            name = product.name
        self._broadcast("product_updated", {
            "productId": product_id,
            "isOnSale": False,
        })
        self._broadcast("live_product_unmounted", {
            "productId": product_id,
            "name": name,
            "clearedCurrent": cleared_current,
            "alreadyUnmounted": already,
        })
        return product, ""

    def build_product_card(self, product_id: str) -> tuple[Optional[dict], str]:
        """构造要推送给观众的商品卡片快照（只读，不广播）。"""
        with self._lock:
            product = self.products.get(product_id)
            if product is None:
                return None, "商品不存在"
            if not product.isOnSale:
                return None, "商品未挂载到直播间，请先挂载"
            card = product.model_dump()
            sales = self.get_product_sales_map().get(product_id) or {}
            # 与观众端商品卡同口径：展示销量 = 真实成交 + 展示基数
            card["salesQty"] = sales.get("qty", 0) + (product.soldBase or 0)
            card["salesAmount"] = sales.get("amount", 0.0)
            card["pushedAt"] = datetime.now().isoformat()
            card["pushId"] = f"push_{int(time.time() * 1000)}"
            return card, ""

    def push_product_card(self, product_id: str) -> tuple[Optional[dict], str]:
        """主播向所有在线观众推送一张商品卡片（弹窗）。

        只允许推送已挂载商品：未上架商品的内容不应借弹窗泄露给观众。
        """
        card, reason = self.build_product_card(product_id)
        if card is None:
            return None, reason
        self._broadcast("product_card_push", card)
        return card, ""

    # ============================================================
    # 本场商品点击 / 直播数据基础统计
    # ============================================================
    def record_product_click(self, product_id: str, user_id: str = "") -> dict:
        """记录一次观众端商品点击（打开详情 / 点击推送卡片）。

        只有直播进行中（live/paused）才计入本场统计；未开播时的浏览不计入，
        否则主播看到的「本场点击数」会混进开播前的闲逛。

        **只统计真人观众**（v_ 前缀，且排除 seed 脚本的演示账号）：分析页的
        商品点击 / 转化 / 漏斗全部按真人口径，游客、历史账号、演示账号的点击
        不计数，避免「点击数」虚高而「点击人数」对不上。
        """
        with self._lock:
            product = self.products.get(product_id)
            if product is None:
                return {"success": False, "counted": False, "message": "商品不存在"}
            if not product.isOnSale:
                return {"success": False, "counted": False, "message": "商品未挂载到直播间"}
            if not is_human_user_id(user_id):
                return {
                    "success": True,
                    "counted": False,
                    "productId": product_id,
                    "message": "仅统计真人观众的点击",
                }
            counting = (
                self.live_state.status in ("live", "paused")
                and bool(self.live_state.startTime)
            )
            if not counting:
                return {
                    "success": True,
                    "counted": False,
                    "productId": product_id,
                    "message": "当前未开播，本次点击不计入本场统计",
                }
            self._product_clicks[product_id] = (
                self._product_clicks.get(product_id, 0) + 1
            )
            self._click_viewers.add(user_id)
            self._product_click_viewers.setdefault(product_id, set()).add(user_id)
            count = self._product_clicks[product_id]
        stats = self.get_live_room_stats()
        # 只广播给运营台：点击明细属于主播侧数据，不能回灌观众端。
        self._broadcast("live_product_clicked", {
            "productId": product_id,
            "productClicks": count,
            "stats": stats,
        }, host_only=True)
        return {
            "success": True,
            "counted": True,
            "productId": product_id,
            "productClicks": count,
        }

    def get_live_room_stats(self) -> dict:
        """本场直播数据基础统计：商品点击、下单数量、成交金额与单品转化。

        口径与运营台「本场订单」一致（``_session_orders_locked(real_only=True)``）：
        只统计**本场真人观众**（排除 seed 脚本的演示账号）且落在当前 startTime
        之后的订单。未开播时订单维度全部为 0，但已挂载商品仍会列出（方便主播
        开播前检查挂载情况）。
        """
        with self._lock:
            mounted = [p for p in self.products.values() if p.isOnSale]
            orders = self._session_orders_locked(real_only=True)
            paid_statuses = ("paid", "shipped", "completed")
            paid = [o for o in orders if o.status in paid_statuses]

            # 单品行：已挂载商品全部列出（哪怕 0 点击 0 单），再补上「有历史
            # 点击/订单但已被撤下」的商品 —— 撤下不应让本场数据凭空消失。
            rows: dict[str, dict] = {}

            def _row(product_id: str, name: str = "", price: float = 0.0) -> dict:
                return rows.setdefault(product_id, {
                    "productId": product_id,
                    "name": name,
                    "price": price,
                    "mounted": False,
                    "clicks": 0,
                    "orderCount": 0,
                    "paidCount": 0,
                    "qty": 0,
                    "gmv": 0.0,
                })

            for p in mounted:
                r = _row(p.productId, p.name, p.price)
                r["mounted"] = True
                r["stock"] = p.stock
            for product_id, clicks in self._product_clicks.items():
                p = self.products.get(product_id)
                r = _row(product_id, p.name if p else "", p.price if p else 0.0)
                r["clicks"] = clicks
                if p is not None:
                    r["stock"] = p.stock
            for o in orders:
                # 合并单按商品聚合：同一单里的多件商品各自计入自己的行，
                # 同一商品多个 SKU 也只给这个商品记一次订单数。
                per_product: dict[str, dict] = {}
                for line in self._order_lines(o):
                    agg = per_product.setdefault(line.productId, {
                        "name": line.productName, "price": line.unitPrice,
                        "qty": 0, "amount": 0.0,
                    })
                    agg["qty"] += line.quantity
                    agg["amount"] = round(agg["amount"] + line.totalAmount, 2)
                for pid, agg in per_product.items():
                    r = _row(pid, agg["name"], agg["price"])
                    r["orderCount"] += 1
                    if o.status in paid_statuses:
                        r["paidCount"] += 1
                        r["qty"] += agg["qty"]
                        r["gmv"] = round(r["gmv"] + agg["amount"], 2)

            product_stats = sorted(
                rows.values(),
                key=lambda r: (-r["clicks"], -r["orderCount"], r["productId"]),
            )
            click_count = sum(self._product_clicks.values())
            order_count = len(orders)
            paid_count = len(paid)
            gmv = round(sum(o.totalAmount for o in paid), 2)
            return {
                "startTime": self.live_state.startTime or "",
                "status": self.live_state.status,
                "liveDuration": self.live_state.liveDuration,
                "onlineCount": self.live_state.onlineCount,
                "mountedCount": len(mounted),
                "clickCount": click_count,
                "clickViewerCount": len({
                    u for u in self._click_viewers if not is_demo_viewer(u)
                }),
                "orderCount": order_count,
                "paidOrderCount": paid_count,
                "pendingPayCount": sum(1 for o in orders if o.status == "pending_pay"),
                "paidQty": sum(o.quantity for o in paid),
                "gmv": gmv,
                # 点击→下单转化率：下单数 / 点击数。没有点击时恒为 0，不虚构成 100%。
                "clickToOrderRate": (
                    round(order_count / click_count * 100, 1) if click_count else 0.0
                ),
                "productStats": product_stats,
            }

    # ============================================================
    # 数据分析：阶段筛选 / 转化漏斗 / 多商品对比
    # ============================================================
    def get_stage_analysis(self, session_id: str = "") -> list[dict]:
        """按直播阶段汇总本场互动与成交（分析页阶段筛选 / 复盘分阶段对比）。"""
        sid = session_id or self.live_state.sessionId or ""
        with self._lock:
            items = [d for d in self.current_session_danmaku
                     if not sid or d.sessionId == sid]
            orders = [o for o in self._session_orders_locked(real_only=True)
                      if not sid or o.sessionId == sid]
            paid_statuses = ("paid", "shipped", "completed")
            rows = []
            for stage in LIVE_STAGES:
                key = stage["key"]
                stage_dm = [d for d in items if d.stage == key]
                stage_real = [d for d in stage_dm if is_real_user_id(d.userId)]
                stage_orders = [o for o in orders if o.stage == key]
                paid = [o for o in stage_orders if o.status in paid_statuses]
                rows.append({
                    "stage": key,
                    "label": stage["label"],
                    "icon": stage["icon"],
                    "danmaku": len(stage_dm),
                    "realDanmaku": len(stage_real),
                    "questions": sum(1 for d in stage_dm if d.isQuestion),
                    # 分析页按真实用户口径：弹幕/提问都只取 v_ 观众，
                    # 虚拟弹幕和模拟观众不参与阶段对比。
                    "realQuestions": sum(1 for d in stage_real if d.isQuestion),
                    "uniqueViewers": len({d.userId for d in stage_real}),
                    "positive": sum(1 for d in stage_dm
                                    if (d.sentiment or classify_sentiment(d.content))
                                    == "positive"),
                    "negative": sum(1 for d in stage_dm
                                    if (d.sentiment or classify_sentiment(d.content))
                                    == "negative"),
                    "orderCount": len(stage_orders),
                    "paidCount": len(paid),
                    "qty": sum(o.quantity for o in paid),
                    "gmv": round(sum(o.totalAmount for o in paid), 2),
                })
            # 未标注阶段的历史弹幕/订单单独一行，避免数字对不上
            other_dm = [d for d in items if d.stage not in STAGE_LABELS]
            other_orders = [o for o in orders if o.stage not in STAGE_LABELS]
            if other_dm or other_orders:
                paid = [o for o in other_orders if o.status in paid_statuses]
                other_real = [d for d in other_dm if is_real_user_id(d.userId)]
                rows.append({
                    "stage": "unassigned", "label": "未分阶段", "icon": "❓",
                    "danmaku": len(other_dm),
                    "realDanmaku": len(other_real),
                    "questions": sum(1 for d in other_dm if d.isQuestion),
                    "realQuestions": sum(1 for d in other_real if d.isQuestion),
                    "uniqueViewers": len({d.userId for d in other_real}),
                    "positive": 0, "negative": 0,
                    "orderCount": len(other_orders), "paidCount": len(paid),
                    "qty": sum(o.quantity for o in paid),
                    "gmv": round(sum(o.totalAmount for o in paid), 2),
                })
            return rows

    def get_conversion_funnel(self, product_id: str = "") -> dict:
        """电商转化漏斗：观看 → 点击/加购 → 下单 → 支付 → 确认收货。

        只统计**本场直播的真人观众**（排除 seed 脚本的演示账号），漏斗各级
        都从同一批人里取，避免「观看 43 人、下单 10 单」却对不上主播的操作。

        加购和直接下单不是互斥关系：把点击、加购、下单用户合并为「点击/加购」
        兴趣步骤，保证漏斗逐级递减，避免出现「提交订单 > 加购」的倒挂。
        """
        with self._lock:
            start = self.live_state.startTime or ""
            orders = self._session_orders_locked(real_only=True)
            if product_id:
                orders = [o for o in orders if self._product_has_order(o, product_id)]
            paid_statuses = ("paid", "shipped", "completed")
            viewers = {u for u in self._session_real_users if not is_demo_viewer(u)}
            if not viewers:
                viewers = {d.userId for d in self.current_session_danmaku
                           if is_human_user_id(d.userId)}
            if product_id:
                viewers = {o.userId for o in orders} | viewers
                clickers = {u for u in self._product_click_viewers.get(product_id, set())
                            if not is_demo_viewer(u)}
            else:
                clickers = {u for u in self._click_viewers if not is_demo_viewer(u)}
            cart_users = {
                str(e.get("userId") or "")
                for e in self.cart_events
                if e.get("userId") and not is_demo_viewer(e.get("userId"))
                and (not product_id or e.get("productId") == product_id)
                and (not start or str(e.get("timestamp") or "") >= start)
            }
            order_users = {o.userId for o in orders if o.userId}
            paid_users = {o.userId for o in orders
                          if o.userId and o.status in paid_statuses}
            completed_users = {o.userId for o in orders
                               if o.userId and o.status == "completed"}
            interest_users = clickers | cart_users | order_users
            # 漏斗起点 = 本场连接过的真人观众 ∪ 有真实行为的观众。
            # 只依赖连接集合时，服务重启 / 观众先连后开播都会让 _session_real_users
            # 为空，后置步骤明明有数却因为「观看 0 人」画不出来（前端会判为口径不完整）。
            viewers = viewers | interest_users
            steps = [
                {"key": "view", "label": "观看直播", "count": len(viewers)},
                {"key": "interest", "label": "点击/加购商品", "count": len(interest_users)},
                {"key": "order", "label": "提交订单", "count": len(order_users)},
                {"key": "pay", "label": "完成支付", "count": len(paid_users)},
                {"key": "complete", "label": "确认收货", "count": len(completed_users)},
            ]
            for idx, step in enumerate(steps):
                if idx == 0:
                    step["rate"] = 100.0 if step["count"] else 0.0
                else:
                    prev = steps[idx - 1]["count"]
                    step["rate"] = round(step["count"] / prev * 100, 1) if prev else 0.0
            return {"productId": product_id or "", "steps": steps,
                    "sessionId": self.live_state.sessionId or ""}

    def compare_products(self, product_ids: list[str]) -> list[dict]:
        """多商品数据对比：销量/GMV/点击/退款/评分/加购/库存。"""
        ids = [str(x) for x in (product_ids or []) if str(x)]
        with self._lock:
            orders = [o for o in self.orders.values()
                      if order_is_real(o) and o.status in ("paid", "shipped", "completed")]
            refunded = {o.orderId for o in self.orders.values()
                        if order_is_real(o) and o.status == "refunded"}
            rows = []
            for pid in ids:
                p = self.products.get(pid)
                if p is None:
                    continue
                qty = 0
                gmv = 0.0
                order_count = 0
                refund_count = 0
                for o in orders:
                    for line in self._order_lines(o):
                        if line.productId != pid:
                            continue
                        qty += line.quantity
                        gmv = round(gmv + line.totalAmount, 2)
                        order_count += 1
                refund_count = sum(
                    1 for o in self.orders.values()
                    if o.orderId in refunded and self._product_has_order(o, pid))
                clicks = int(self._product_clicks.get(pid, 0))
                cart_users = len({str(e.get("userId") or "") for e in self.cart_events
                                  if e.get("productId") == pid and e.get("userId")})
                reviews = [r for r in self.reviews if r.productId == pid]
                rows.append({
                    "productId": pid,
                    "name": p.name,
                    "price": p.price,
                    "stock": p.stock,
                    "soldCount": p.soldCount,
                    "qty": qty,
                    "gmv": gmv,
                    "orderCount": order_count,
                    "refundCount": refund_count,
                    "refundRate": round(refund_count / order_count * 100, 1)
                    if order_count else 0.0,
                    "clicks": clicks,
                    "cartUsers": cart_users,
                    "clickToOrderRate": round(order_count / clicks * 100, 1)
                    if clicks else 0.0,
                    "avgRating": round(sum(r.rating for r in reviews) / len(reviews), 1)
                    if reviews else p.rating,
                    "reviewCount": len(reviews),
                })
            return rows

    # ============================================================
    # 弹幕管理
    # ============================================================
    def add_danmaku(self, danmaku: DanmakuItem) -> DanmakuItem:
        """添加弹幕（含敏感词拦截）。

        totalDanmaku 只统计**真人观众**的弹幕（v_ 真实账号且排除演示脚本账号，
        不是 isVirtual —— 模拟弹幕的 isVirtual 是 False，旧写法把它们全算进了真实口径）。
        命中敏感词时：标记 isViolation、写拦截日志、只推给运营台，不再公开广播。
        """
        blocked = False
        reason = ""
        if not danmaku.isViolation and danmaku.content:
            blocked, reason = self.check_sensitive(danmaku.content)
            if blocked:
                danmaku.isViolation = True
                danmaku.violationReason = reason
        with self._lock:
            # 绑定场次 + 判定情绪：直播中的每条弹幕都归入当前 sessionId，
            # 并进入本场全程缓冲（直播结束时整场固化）。
            self._bind_danmaku_session_locked(danmaku)
            self.danmaku_list.append(danmaku)
            self._remember_live_danmaku_locked(danmaku)
            self._danmaku_counter += 1
            if is_human_user_id(danmaku.userId) and not danmaku.isViolation:
                self.live_state.totalDanmaku += 1
            # 只有会被写进盘的那部分才触发落盘：虚拟弹幕每秒都可能生成，不能让它带写。
            if is_persistable_user_id(danmaku.userId):
                self._save_danmaku()
        if blocked:
            self.log_sensitive_blocked(
                danmaku.content, reason, danmaku.userId, danmaku.userName, "danmaku")
            self._broadcast("danmaku_blocked", danmaku.model_dump(), host_only=True)
        else:
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
                    if is_human_user_id(d.userId):
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
        totalPrivateMessages 只统计真人观众（判据同 add_danmaku，演示账号不计）。
        """
        with self._lock:
            uid = msg.userId
            if uid not in self.private_messages:
                self.private_messages[uid] = []
            self.private_messages[uid].append(msg)
            self._msg_counter += 1
            if is_persistable_user_id(uid):
                self._save_private_messages()
            if msg.isFromUser and track_unread and is_human_user_id(uid):
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

    def get_unread_summary(self) -> dict:
        """私信未读汇总：总数 + 有未读的会话列表（用于导航红点）。"""
        with self._lock:
            threads = []
            total = 0
            for uid, s in self.user_sessions.items():
                if s.unreadCount > 0:
                    total += s.unreadCount
                    threads.append({"userId": uid, "userName": s.userName,
                                    "unread": s.unreadCount})
            threads.sort(key=lambda x: -x["unread"])
            return {"total": total, "threads": threads}

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
        """获取所有用户会话（运营台口径：排除 seed 脚本的演示账号）。"""
        with self._lock:
            return sorted(
                [s for s in self.user_sessions.values()
                 if not is_demo_viewer(s.userId)],
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
            if not review.sessionId:
                # 观众主动评价按订单所属场次绑定；其余（模拟/弹幕评价）挂当前场次
                order = self.orders.get(review.orderId) if review.orderId else None
                review.sessionId = ((order.sessionId if order else "")
                                    or self.live_state.sessionId or "")
            self.reviews.append(review)
            self._review_counter += 1
            self._save_reviews()
        payload = review.model_dump()
        # 观众端仍要收到（商品卡评分氛围），但运营台必须能识别演示账号并在
        # 实时推送里直接丢弃，否则 seed 脚本一跑就会把假评价塞进主播列表。
        payload["isDemoViewer"] = is_demo_viewer(review.userId)
        self._broadcast("new_review", payload)
        # 差评实时预警：只推运营台，且只对真人评价告警（演示账号不打扰主播）。
        if review.sentiment == "negative" and is_human_user_id(review.userId):
            self._broadcast("negative_review_alert", {
                "review": review.model_dump(),
                "message": f"收到差评 {review.rating} 星：{review.content[:40]}",
            }, host_only=True)
        return review

    def reply_review(self, review_id: str, content: str,
                     operator: str = "host") -> tuple[Optional[ReviewItem], str]:
        """主播后台回复评价（观众端可看到）。"""
        content = (content or "").strip()
        if not content:
            return None, "回复内容不能为空"
        content = content[:500]
        with self._lock:
            review = next((r for r in self.reviews if r.reviewId == review_id), None)
            if review is None:
                return None, "评价不存在"
            review.replyContent = content
            review.replyTime = datetime.now().isoformat()
            review.isHandled = True
            self._save_reviews()
        self._broadcast("review_updated", review.model_dump())
        return review, ""

    def get_review_pain_points(self) -> dict:
        """AI 差评痛点汇总（规则聚合：关键词 → 痛点 → 建议）。

        逐条差评提取 _NEGATIVE_WORDS 命中词与商品信息，归并为痛点条目；
        不依赖外部 LLM，保证后台一打开就能看到结果。
        """
        with self._lock:
            # 只聚合真人观众的评价：u001~u010 与 seed 演示账号的差评只用于观众端
            # 评分氛围，不能出现在主播的「AI 差评痛点」里，否则会对着不存在的人做整改。
            negatives = [r for r in self.reviews
                         if is_human_user_id(r.userId)
                         and (r.sentiment == "negative" or int(r.rating or 5) <= 2)]
            groups: dict[str, dict] = {}
            for r in negatives:
                hits = [w for w in _NEGATIVE_WORDS if w in (r.content or "")]
                if r.tags:
                    hits += [t for t in r.tags if t not in hits]
                if not hits:
                    hits = ["综合体验不佳"]
                for hit in hits[:3]:
                    g = groups.setdefault(hit, {
                        "point": hit, "count": 0, "examples": [],
                        "products": {}, "suggestion": "",
                    })
                    g["count"] += 1
                    if len(g["examples"]) < 3:
                        g["examples"].append((r.content or "")[:80])
                    if r.productName:
                        g["products"][r.productName] = (
                            g["products"].get(r.productName, 0) + 1)
            suggestions = {
                "质量": "加强出库质检，并在直播中主动展示质检报告与实拍细节",
                "物流": "与快递方确认时效，直播间明确发货时间预期",
                "价格": "讲解时说明优惠构成与保价规则，避免价格预期落差",
                "尺寸": "增加尺码/规格对照表，主播口播时强调选规格方法",
                "包装": "优化易碎品包装，发货前增加外观检查环节",
                "客服": "安排场控及时响应私信，缩短售后响应时长",
                "色差": "直播灯光下补充自然光实拍图，减少色差预期",
            }
            rows = []
            for g in sorted(groups.values(), key=lambda x: -x["count"]):
                top_product = (max(g["products"].items(), key=lambda kv: kv[1])[0]
                               if g["products"] else "")
                suggestion = next(
                    (v for k, v in suggestions.items() if k in g["point"]),
                    "针对该痛点补充标准答疑话术，并在下一场直播中主动回应")
                rows.append({
                    "point": g["point"], "count": g["count"],
                    "examples": g["examples"], "topProduct": top_product,
                    "suggestion": suggestion,
                })
            total = len(negatives)
            summary = []
            if rows:
                summary.append(
                    f"共 {total} 条差评，最集中的痛点是「{rows[0]['point']}」"
                    f"（{rows[0]['count']} 条，占差评 {rows[0]['count'] / total * 100:.0f}%）。")
                for row in rows[:3]:
                    summary.append(f"· {row['point']}：{row['suggestion']}")
            else:
                summary.append("暂无差评，整体评价体验良好。")
            return {
                "totalNegative": total,
                "painPoints": rows,
                "summary": summary,
                "suggestions": [r["suggestion"] for r in rows[:5]],
            }

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
                # source=user 是运营台「用户评价管理」的口径：再加一道真人账号
                # 校验，保证页面只展示真人观众的评价（seed 演示账号也不进列表）。
                if source == "user":
                    results = [r for r in results if is_human_user_id(r.userId)]
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

    def get_order_review(self, order_id: str, user_id: str = "") -> Optional[ReviewItem]:
        """查某订单的观众评价：用于订单卡片展示「已评价」与评价内容。"""
        if not order_id:
            return None
        with self._lock:
            for r in self.reviews:
                if r.orderId == order_id and (not user_id or r.userId == user_id):
                    return r
        return None

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
                sessionId=order.sessionId or "",
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
            sku = self._default_sku(product)
            if sku is None:
                return None
            if sku.stock < quantity:
                return None
            # 只有成交/履约类状态才累计销量
            if status in ("paid", "completed", "shipped"):
                product.soldCount += quantity
            product.isInLibrary = True
            product.addedTime = product.addedTime or datetime.now().isoformat()
            self._order_counter += 1
            import random as _r
            buyer_name = user_name or _r.choice(self._BUYER_NAMES)
            now = datetime.now().isoformat()
            amount = round(sku.price * quantity, 2)
            line = OrderLineItem(
                productId=product_id, productName=product.name,
                productImage=(product.images[0] if product.images else product.imageUrl) or "",
                skuId=sku.skuId, skuSpec=sku.spec, quantity=quantity,
                unitPrice=sku.price, originalUnitPrice=sku.originalPrice or sku.price,
                amount=amount, originalAmount=round((sku.originalPrice or sku.price) * quantity, 2),
                discountAmount=0, totalAmount=amount,
                costPrice=sku.costPrice or product.costPrice or 0,
                costAmount=round((sku.costPrice or product.costPrice or 0) * quantity, 2),
                channel="legacy",
            )
            order = OrderItem(
                orderId=f"order_{int(datetime.now().timestamp()*1000)}_{self._order_counter}",
                orderNo=self._next_order_no(),
                productId=product_id,
                productName=product.name,
                productImage=line.productImage,
                skuId=sku.skuId,
                skuSpec=sku.spec,
                userId="",  # 模拟订单不绑定真实用户
                userName=buyer_name,
                quantity=quantity,
                unitPrice=sku.price,
                items=[line],
                goodsAmount=amount,
                originalAmount=line.originalAmount,
                totalAmount=amount,
                costAmount=line.costAmount,
                channel="legacy",
                status=status,
                payTime=now if status not in ("pending_pay", "canceled") else "",
                createTime=now,
                isVirtual=True,
                stockDeducted=True,
            )
            self._change_sku_stock_locked(product, sku, -quantity, "order_create", order,
                                          operator="system")
            self.orders[order.orderId] = order
            self._save_orders()
            self._save_products()
            self._broadcast_order("order_new", order)
            self._broadcast_product_sales(order.productId)
            return order

    def get_orders(self, status: str = "", limit: int = 50,
                   product_id: str = "") -> list[OrderItem]:
        """获取真人订单列表（按时间倒序，可按状态/商品筛选）。

        只保留真人账号的订单：模拟订单（u00x / 无 userId）本来就进不来，演示脚本
        批量注册的账号（demo_viewer_ids.json）也一并排除 —— 订单管理工作台是主播
        处理真实售后/退款的地方，演示数据留在数据分析里撑图表即可。
        """
        with self._lock:
            results = [o for o in self.orders.values() if order_is_human(o)]
            if status:
                results = [o for o in results if o.status == status]
            if product_id:
                results = [o for o in results if self._product_has_order(o, product_id)]
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
                # 主播直接退款：回补库存/销量 + 钱包返充，并把退款申请标记为已通过
                self._apply_refund_locked(order, now)
            elif status == "canceled" and order.stockDeducted:
                # 主播取消待付款单：同样要释放下单时锁定的库存
                self._restock_order_locked(order, "order_cancel", "host")
            order.payTime = order.payTime or now
            self._save_orders()
            self._broadcast_order("order_updated", order)
            for pid in {x.productId for x in self._order_lines(order)}:
                self._broadcast_product_sales(pid)
            return order

    def _restock_order_locked(self, order: OrderItem, reason: str,
                              operator: str = "system") -> None:
        """锁内按订单行回补库存并写流水，然后清除库存已扣标记。"""
        for line in self._order_lines(order):
            product = self.products.get(line.productId)
            if product is None:
                continue
            sku = self._find_sku(product, line.skuId)
            self._change_sku_stock_locked(
                product, sku, line.quantity, reason, order, operator=operator,
            )
        order.stockDeducted = False
        self._save_products()

    def _apply_refund_locked(self, order: OrderItem, now: str) -> None:
        """锁内执行退款落地：回补库存/销量、钱包返充、标记退款申请已通过。

        只应由 request_refund 的审核通过与 update_order_status 的直接退款分支调用。
        只有 stockDeducted=True 的订单才回补库存：新单在下单时锁、旧单在支付时扣；
        如果某条旧数据既没锁也没扣，退款再补就会凭空多出库存。
        """
        if order.stockDeducted:
            self._restock_order_locked(order, "refund", "host")
        for line in self._order_lines(order):
            product = self.products.get(line.productId)
            if product:
                product.soldCount = max(0, product.soldCount - line.quantity)
        self._save_products()
        self._credit_viewer(order.userId, order.totalAmount)
        order.status = "refunded"
        order.refundStatus = "approved"
        order.refundReviewTime = now

    def request_refund(self, order_id: str, user_id: str, reason: str = "",
                       refund_type: str = "refund", return_address: str = "",
                       return_method: str = "") -> tuple[Optional[OrderItem], str]:
        """观众提交退款/退货申请：只登记申请，不改 order.status、不动库存/余额。

        真正退款要等主播在后台「同意」，由 review_refund 完成。申请状态单独放在
        refundStatus 上，这样待审核期间不会提前影响既有成交/GMV 统计口径。
        退货退款还必须提供寄件地址与寄件方式，方便主播安排取件/收货。
        """
        reason = (reason or "").strip()
        if not reason:
            return None, "请填写退款原因"
        if len(reason) > 200:
            reason = reason[:200]
        if refund_type not in ("refund", "return"):
            refund_type = "refund"
        return_address = (return_address or "").strip()[:200]
        return_method = (return_method or "").strip()[:40]
        if refund_type == "return":
            if not return_address:
                return None, "请填写退货寄件地址"
            if not return_method:
                return None, "请选择退货寄件方式"
        else:
            # 仅退款不涉及寄回，清空旧值，避免历史退货信息残留到新申请
            return_address = ""
            return_method = ""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status not in ("paid", "shipped", "completed"):
                return None, "当前订单状态不支持退款"
            if order.refundStatus == "pending":
                return None, "退款申请正在审核中，请勿重复提交"
            if order.refundStatus == "approved" or order.status == "refunded":
                return None, "该订单已退款"
            order.refundStatus = "pending"
            order.refundReason = reason
            order.refundType = refund_type
            order.returnAddress = return_address
            order.returnMethod = return_method
            order.refundReasonCategory = classify_refund_reason(reason)
            order.refundApplyTime = datetime.now().isoformat()
            order.refundRejectReason = ""
            self._save_orders()
            self._broadcast_order("order_updated", order)
            return order, ""

    def review_refund(self, order_id: str, approve: bool,
                      reason: str = "") -> tuple[Optional[OrderItem], str]:
        """主播审核退款申请（仅 host 可达）。

        approve=True  → 执行退款：状态 refunded、回补库存、钱包返充；
        approve=False → 只把申请标记为 rejected，订单保持原状态，并记录拒绝理由。
        """
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            if order.refundStatus != "pending":
                return None, "该订单没有待审核的退款申请"
            now = datetime.now().isoformat()
            if approve:
                self._apply_refund_locked(order, now)
            else:
                order.refundStatus = "rejected"
                order.refundReviewTime = now
                order.refundRejectReason = (reason or "").strip()[:200]
            self._save_orders()
            self._broadcast_order("order_updated", order)
            self._broadcast_product_sales(order.productId)
            return order, ""

    # ============================================================
    # 订单批量操作 / 导出 / 溯源 / 退款原因 / 超时催付
    # ============================================================
    def _find_trace_danmaku_locked(self, user_id: str, minutes: int = 30) -> str:
        """锁内找该观众下单前最近的提问弹幕（用于「提问→下单」溯源）。

        只看本场、只看问句、只认本人；超出 minutes 分钟的不算 —— 半小时前随口
        问一句、半小时后买别的商品，不该被硬串成转化链路。
        """
        if not user_id:
            return ""
        now = datetime.now()
        for d in reversed(self.current_session_danmaku):
            if d.userId != user_id or not d.isQuestion:
                continue
            t = _parse_dt(d.timestamp)
            if t is None:
                continue
            if (now - t).total_seconds() <= minutes * 60:
                return d.danmakuId
            break
        return ""

    def get_order_trace(self, order_id: str) -> dict:
        """订单溯源：该观众提问 → AI/主播回复 → 加购/点击 → 下单 → 支付链路。"""
        with self._lock:
            order = self.orders.get(order_id)
            if order is None:
                return {"success": False, "message": "订单不存在"}
            question = None
            if order.sourceDanmakuId:
                question = next((d for d in self.danmaku_list
                                 if d.danmakuId == order.sourceDanmakuId), None)
            # 该观众在本场内、下单之前的全部提问（倒序），便于主播看完整上下文
            user_questions = [
                d for d in self.current_session_danmaku
                if order.userId and d.userId == order.userId and d.isQuestion
                and d.timestamp <= order.createTime
            ][-10:]
            clicks = sum(1 for e in self.cart_events
                         if order.userId and e.get("userId") == order.userId
                         and e.get("timestamp", "") <= order.createTime)
            order_data = order.model_dump()
        chain = []
        if question is not None:
            chain.append({
                "type": "question", "time": question.timestamp,
                "content": question.content, "danmakuId": question.danmakuId,
            })
            if question.replyContent:
                chain.append({
                    "type": "reply", "time": question.replyTime,
                    "content": question.replyContent,
                })
        chain.append({
            "type": "order", "time": order.createTime,
            "content": f"下单 {order.orderNo or order.orderId}，实付 ¥{order.totalAmount:.2f}",
        })
        if order.payTime:
            chain.append({
                "type": "pay", "time": order.payTime,
                "content": f"支付成功 ¥{order.totalAmount:.2f}",
            })
        if order.shipTime:
            chain.append({"type": "ship", "time": order.shipTime, "content": "已发货"})
        if order.status == "refunded":
            chain.append({
                "type": "refund", "time": order.refundReviewTime or order.refundApplyTime,
                "content": f"已退款（原因：{order.refundReason or order.refundReasonCategory or '未填写'}）",
            })
        return {
            "success": True,
            "order": order_data,
            "question": question.model_dump() if question else None,
            "userQuestions": [d.model_dump() for d in user_questions],
            "guestInteractions": clicks,
            "chain": chain,
        }

    def batch_ship(self, order_ids: list[str], operator: str = "host") -> dict:
        """批量发货：逐单走 update_order_status 的合法流转与库存/广播逻辑。"""
        success, skipped, messages = 0, 0, []
        for oid in dict.fromkeys(order_ids or []):
            order = self.update_order_status(str(oid), "shipped")
            if order is None:
                skipped += 1
                messages.append(f"{oid}: 当前状态不允许发货")
            else:
                success += 1
        return {"success": success > 0, "shipped": success, "skipped": skipped,
                "messages": messages}

    def batch_refund(self, order_ids: list[str], reason: str = "",
                     category: str = "", operator: str = "host") -> dict:
        """批量退款：逐单执行退款并记录原因分类（汇总进数据分析）。"""
        reason = (reason or "主播批量退款").strip()[:200]
        cat = category or classify_refund_reason(reason)
        success, skipped, messages = 0, 0, []
        for oid in dict.fromkeys(order_ids or []):
            with self._lock:
                order = self.orders.get(str(oid))
                if order is not None:
                    order.refundReason = reason
                    order.refundReasonCategory = cat
            result = self.update_order_status(str(oid), "refunded")
            if result is None:
                skipped += 1
                messages.append(f"{oid}: 当前状态不允许退款")
            else:
                success += 1
        return {"success": success > 0, "refunded": success, "skipped": skipped,
                "messages": messages, "category": cat}

    def export_orders_csv(self, status: str = "") -> str:
        """导出真人订单为 CSV 文本（UTF-8 BOM，Excel 直接打开不乱码）。"""
        orders = self.get_orders(status=status, limit=100000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "订单号", "下单时间", "买家", "商品", "规格", "数量", "实付金额",
            "订单状态", "退款状态", "退款原因", "退款分类", "直播阶段",
            "收货人", "手机号", "收货地址", "支付时间", "发货时间",
        ])
        cat_labels = {c["key"]: c["label"] for c in REFUND_REASON_CATEGORIES}
        for o in orders:
            writer.writerow([
                o.orderNo or o.orderId,
                o.createTime,
                o.userName,
                o.productName,
                o.skuSpec,
                o.quantity,
                f"{o.totalAmount:.2f}",
                ORDER_STATUS_LABELS.get(o.status, o.status),
                o.refundStatus,
                o.refundReason,
                cat_labels.get(o.refundReasonCategory, o.refundReasonCategory),
                STAGE_LABELS.get(o.stage, o.stage or "-"),
                o.receiverName,
                o.receiverPhone,
                o.receiverAddress,
                o.payTime,
                o.shipTime,
            ])
        return "\ufeff" + buf.getvalue()

    def get_refund_reason_summary(self) -> dict:
        """退款原因汇总（数据分析用）：分类计数、金额、占比与最近原因列表。"""
        labels = {c["key"]: c["label"] for c in REFUND_REASON_CATEGORIES}
        with self._lock:
            # 与订单列表同一口径：只汇总真人观众的退款，模拟订单、演示脚本账号
            # 的退款都不进主播的售后汇总，否则会出现「列表里没有、汇总里多出几单」。
            refunded = [o for o in self.orders.values()
                        if order_is_human(o)
                        and (o.status == "refunded"
                             or o.refundStatus in ("approved", "pending"))]
            total = len(refunded)
            groups: dict[str, dict] = {}
            recent = []
            for o in refunded:
                cat = o.refundReasonCategory or classify_refund_reason(o.refundReason)
                g = groups.setdefault(cat, {
                    "category": cat, "label": labels.get(cat, "其他原因"),
                    "count": 0, "amount": 0.0,
                })
                g["count"] += 1
                g["amount"] = round(g["amount"] + float(o.totalAmount or 0), 2)
                if o.refundReason:
                    recent.append({
                        "orderNo": o.orderNo or o.orderId,
                        "reason": o.refundReason,
                        "category": g["label"],
                        "amount": o.totalAmount,
                        "time": o.refundApplyTime or o.refundReviewTime,
                    })
            rows = sorted(groups.values(), key=lambda x: -x["count"])
            for row in rows:
                row["ratio"] = round(row["count"] / total * 100, 1) if total else 0.0
            recent.sort(key=lambda x: x["time"] or "", reverse=True)
            return {"total": total,
                    "amount": round(sum(x["amount"] for x in rows), 2),
                    "categories": rows,
                    "recent": recent[:30]}

    def remind_pending_pay(self, timeout_minutes: int = 0,
                           operator: str = "AI", order_id: str = "") -> list[dict]:
        """向待付款订单发送 AI 催付私信。

        默认 timeout_minutes=0：主播手动触发时立即催付全部待付款真实订单；
        传 >0 时只催超过该时长的订单。传 order_id 可只催指定订单。
        """
        now = datetime.now()
        targets = []
        with self._lock:
            for o in self.orders.values():
                if (o.status != "pending_pay" or o.refundStatus != "none"
                        or not o.userId or not order_is_real(o)):
                    continue
                if order_id and o.orderId != order_id:
                    continue
                created = _parse_dt(o.createTime)
                if created is None:
                    continue
                if timeout_minutes and timeout_minutes > 0:
                    if (now - created).total_seconds() < timeout_minutes * 60:
                        continue
                o.payRemindTime = now.isoformat()
                targets.append(o.model_dump())
            if targets:
                self._save_orders()
        for order_data in targets:
            user_id = order_data.get("userId") or ""
            acc = self.get_viewer(user_id)
            age_text = (f"已超时 {timeout_minutes} 分钟未支付，"
                        if timeout_minutes and timeout_minutes > 0 else "还未支付，")
            content = (
                f"亲爱的{acc.userName if acc else '观众'}，您有一笔待付款订单"
                f"「{order_data.get('productName') or '商品'}」（¥{order_data.get('totalAmount', 0):.2f}）"
                f"{age_text}库存为您保留中，"
                f"点击「我的订单」即可继续付款，有疑问随时私信我~"
            )
            self.add_private_message(PrivateMessageItem(
                messageId=f"payremind_{int(time.time()*1000)}_{order_data.get('orderId','')[-6:]}",
                userId=user_id,
                userName=(acc.userName if acc else "观众"),
                avatar=(acc.avatar if acc else ""),
                content=content,
                timestamp=datetime.now().isoformat(),
                isFromUser=False,
                replyType="ai",
            ))
        if targets:
            self._broadcast("pay_remind_sent", {
                "count": len(targets),
                "orders": [{"orderId": o.get("orderId"), "orderNo": o.get("orderNo"),
                            "userId": o.get("userId")} for o in targets],
            }, host_only=True)
        return targets

    def cancel_expired_pending_orders(self, timeout_minutes: int = ORDER_PAY_TIMEOUT_MINUTES) -> list[OrderItem]:
        """自动取消超过 timeout_minutes 仍未支付的真实待付款订单。

        复用 update_order_status 的合法流转与库存回补逻辑，逐笔广播 order_updated，
        观众端与运营台都会立刻看到「已取消」。
        """
        minutes = int(timeout_minutes) if timeout_minutes and int(timeout_minutes) > 0 \
            else ORDER_PAY_TIMEOUT_MINUTES
        now = datetime.now()
        expired: list[str] = []
        with self._lock:
            for o in self.orders.values():
                if o.status != "pending_pay" or not order_is_real(o):
                    continue
                created = _parse_dt(o.createTime)
                if created is None:
                    continue
                if (now - created).total_seconds() >= minutes * 60:
                    expired.append(o.orderId)
        canceled: list[OrderItem] = []
        for oid in expired:
            order = self.update_order_status(oid, "canceled")
            if order is not None:
                canceled.append(order)
        return canceled

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
            question_count: dict[str, int] = {}
            for d in self.danmaku_list:
                if d.userId and d.isQuestion:
                    question_count[d.userId] = question_count.get(d.userId, 0) + 1
            visit_seconds: dict[str, int] = {}
            for v in self.viewer_visits:
                visit_seconds[v.userId] = (visit_seconds.get(v.userId, 0)
                                           + int(v.durationSeconds or 0))
            rows = []
            for uid, acc in self.viewers.items():
                row = {
                    "userId": uid,
                    "userName": acc.userName,
                    "avatar": acc.avatar or "",
                    "balance": acc.balance,
                    "registerTime": acc.registerTime,
                    "orderCount": order_count.get(uid, 0),
                    "totalSpend": round(spend.get(uid, 0.0), 2),
                    "isLegacy": not uid.startswith("v_"),
                    "questionCount": question_count.get(uid, 0),
                    "watchSeconds": visit_seconds.get(uid, 0),
                }
                row["tags"] = self._compute_viewer_tags_locked(
                    uid, row["orderCount"], row["totalSpend"],
                    row["questionCount"], row["watchSeconds"])
                rows.append(row)
            rows.sort(key=lambda r: r["registerTime"] or "", reverse=True)
            return rows

    # ============================================================
    # 观众自动标签 / 进场离场 / 详情历史
    # ============================================================
    @staticmethod
    def _compute_viewer_tags_locked(user_id: str, order_count: int, spend: float,
                                    question_count: int, watch_seconds: int) -> list[str]:
        """自动给观众打标签（规则透明可解释，前端展示同一套文案）。"""
        tags: list[str] = []
        if spend >= 500:
            tags.append("高价值")
        if order_count >= 3:
            tags.append("复购客")
        elif order_count == 0 and (question_count > 0 or watch_seconds >= 60):
            tags.append("只看不买")
        if question_count >= 3:
            tags.append("高意向")
        if watch_seconds >= 600:
            tags.append("深度观看")
        if not tags:
            tags.append("新客")
        return tags

    def note_viewer_enter(self, user_id: str, user_name: str = "") -> None:
        """观众连接建立：开始计时并生成一条未结算的到场记录。"""
        if not user_id:
            return
        with self._lock:
            now = datetime.now().isoformat()
            self._viewer_enter_at[user_id] = now
            if user_id in self._viewer_open_visit:
                return  # 同一账号多标签页：只记一次进场
            visit = ViewerVisitItem(
                visitId=f"visit_{int(time.time() * 1000)}_{len(self.viewer_visits) + 1}",
                userId=user_id,
                userName=user_name or "",
                sessionId=self.live_state.sessionId or "",
                enterTime=now,
            )
            self.viewer_visits.append(visit)
            if len(self.viewer_visits) > VIEWER_VISITS_MAX:
                self.viewer_visits = self.viewer_visits[-VIEWER_VISITS_MAX:]
            self._viewer_open_visit[user_id] = visit.visitId
            self._save_viewer_visits()

    def note_viewer_leave(self, user_id: str) -> None:
        """观众全部连接断开：结算观看时长。"""
        if not user_id:
            return
        with self._lock:
            visit_id = self._viewer_open_visit.pop(user_id, "")
            self._viewer_enter_at.pop(user_id, None)
            if not visit_id:
                return
            visit = next((v for v in self.viewer_visits if v.visitId == visit_id), None)
            if visit is None:
                return
            now = datetime.now()
            visit.leaveTime = now.isoformat()
            enter = _parse_dt(visit.enterTime)
            visit.durationSeconds = (max(0, int((now - enter).total_seconds()))
                                     if enter else 0)
            self._save_viewer_visits()

    def get_viewer_visits(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = [v for v in self.viewer_visits if v.userId == user_id]
            rows.sort(key=lambda v: v.enterTime, reverse=True)
            return [v.model_dump() for v in rows[:max(1, int(limit or 50))]]

    def get_viewer_detail(self, user_id: str) -> Optional[dict]:
        """观众详情：观看/下单/提问/私信历史 + 自动标签 + 累计时长。"""
        with self._lock:
            acc = self.viewers.get(user_id)
            if acc is None:
                return None
            orders = [o for o in self.orders.values()
                      if o.userId == user_id and order_is_real(o)]
            orders.sort(key=lambda o: o.createTime, reverse=True)
            questions = [d for d in self.danmaku_list
                         if d.userId == user_id and d.isQuestion]
            questions.sort(key=lambda d: d.timestamp, reverse=True)
            messages = list(self.private_messages.get(user_id, []))
            messages.sort(key=lambda m: m.timestamp, reverse=True)
            visits = self.get_viewer_visits(user_id, 50)
            spend = round(sum(o.totalAmount for o in orders
                              if o.status in ("paid", "shipped", "completed")), 2)
            watch = sum(int(v.get("durationSeconds") or 0) for v in visits)
            tags = self._compute_viewer_tags_locked(
                user_id, len(orders), spend, len(questions), watch)
            return {
                "viewer": {
                    "userId": user_id, "userName": acc.userName,
                    "avatar": acc.avatar or "", "balance": acc.balance,
                    "registerTime": acc.registerTime,
                    "orderCount": len(orders), "totalSpend": spend,
                    "questionCount": len(questions),
                    "watchSeconds": watch,
                    "watchMinutes": round(watch / 60, 1),
                    "tags": tags,
                    "region": self._simulated_region(user_id),
                },
                "visits": visits,
                "orders": [o.model_dump() for o in orders[:50]],
                "questions": [d.model_dump() for d in questions[:50]],
                "messages": [m.model_dump() for m in messages[:50]],
            }

    @staticmethod
    def _simulated_region(user_id: str) -> str:
        """按 userId 稳定映射到模拟地域（同一观众永远同一地区，便于演示画像）。

        这是**展示用模拟数据**：没有真实定位来源，分析页标题会明确标注
        「模拟数据」，与「本场商品 Top」等真实口径区分开。
        """
        regions = ["广东", "浙江", "江苏", "上海", "北京", "四川", "山东",
                   "河南", "湖北", "福建", "湖南", "陕西", "重庆", "安徽"]
        if not user_id:
            return "其他"
        return regions[sum(ord(c) for c in user_id) % len(regions)]

    def get_audience_profile(self) -> dict:
        """观众画像：地域分布 + 自动标签分布（分析页图表，均标注为模拟数据）。

        这两块是演示用的模拟画像：人口取真实注册的 v_ 账号（含 seed 脚本的
        演示账号），地域按 userId 稳定映射，保证图表始终有完整的分布、不会
        出现「未填写」这类空档。真实的成交/商品口径见本场商品 Top 等图表。
        """
        with self._lock:
            viewers = [(uid, acc) for uid, acc in self.viewers.items()
                       if is_real_user_id(uid)]
            orders_by_user: dict[str, list[OrderItem]] = {}
            for o in self.orders.values():
                if order_is_real(o):
                    orders_by_user.setdefault(o.userId, []).append(o)
            question_count: dict[str, int] = {}
            for d in self.danmaku_list:
                if d.userId and d.isQuestion:
                    question_count[d.userId] = question_count.get(d.userId, 0) + 1
            visit_seconds: dict[str, int] = {}
            for v in self.viewer_visits:
                visit_seconds[v.userId] = (visit_seconds.get(v.userId, 0)
                                           + int(v.durationSeconds or 0))
            regions: dict[str, int] = {}
            tag_counts: dict[str, int] = {}
            for uid, acc in viewers:
                region = self._simulated_region(uid)
                regions[region] = regions.get(region, 0) + 1
                user_orders = orders_by_user.get(uid, [])
                spend = sum(o.totalAmount for o in user_orders
                            if o.status in ("paid", "shipped", "completed"))
                for tag in self._compute_viewer_tags_locked(
                        uid, len(user_orders), spend,
                        question_count.get(uid, 0), visit_seconds.get(uid, 0)):
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            return {
                "totalViewers": len(viewers),
                "regions": [{"region": k, "count": v}
                            for k, v in sorted(regions.items(), key=lambda kv: -kv[1])],
                "tags": [{"tag": k, "count": v}
                         for k, v in sorted(tag_counts.items(), key=lambda kv: -kv[1])],
            }

    # ============================================================
    # 观众收货地址（多条保存 + 默认地址 + 下单自选）
    # ============================================================
    def get_addresses(self, user_id: str) -> list[AddressItem]:
        """某观众的收货地址列表（默认地址排最前，其余按创建时间倒序）。"""
        if not user_id:
            return []
        with self._lock:
            acc = self.viewers.get(user_id)
            if acc is None:
                return []
            return sorted(
                list(acc.addresses),
                key=lambda a: (not a.isDefault, a.createTime or ""),
                reverse=False,
            )

    def save_address(
        self,
        user_id: str,
        receiver_name: str,
        phone: str,
        detail: str,
        region: str = "",
        is_default: bool = False,
        address_id: str = "",
    ) -> tuple[Optional[AddressItem], str]:
        """新增或编辑收货地址。address_id 非空即编辑，空则新增。

        返回 (地址, 错误原因)。同一条地址编辑不改 createTime；首次新增自动设为默认。
        """
        if not user_id:
            return None, "请先登录"
        name = (receiver_name or "").strip()
        phone = (phone or "").strip().replace(" ", "").replace("-", "")
        detail = (detail or "").strip()
        region = (region or "").strip()
        if not name:
            return None, "请填写收货人姓名"
        if len(name) > 20:
            return None, "收货人姓名过长"
        if not _PHONE_RE.match(phone):
            return None, "请填写正确的 11 位手机号"
        if not detail:
            return None, "请填写详细地址"
        if len(region) > 60 or len(detail) > 120:
            return None, "地址内容过长"
        with self._lock:
            acc = self._get_or_create_viewer_locked(user_id)
            if acc is None:
                return None, "观众账户不存在"
            target = None
            if address_id:
                target = next((a for a in acc.addresses if a.addressId == address_id), None)
                if target is None:
                    return None, "收货地址不存在"
            if target is None and len(acc.addresses) >= _ADDRESS_MAX:
                return None, f"最多保存 {_ADDRESS_MAX} 条收货地址"
            if target is None:
                target = AddressItem(
                    addressId=f"addr_{int(time.time() * 1000)}_{len(acc.addresses)}",
                    receiverName=name,
                    phone=phone,
                    region=region,
                    detail=detail,
                    createTime=datetime.now().isoformat(),
                )
                # 第一条地址自动成为默认地址
                target.isDefault = bool(is_default) or not acc.addresses
                acc.addresses.append(target)
            else:
                target.receiverName = name
                target.phone = phone
                target.region = region
                target.detail = detail
                target.isDefault = bool(is_default) or target.isDefault and not any(
                    a is not target and a.isDefault for a in acc.addresses
                )
            if target.isDefault:
                for a in acc.addresses:
                    if a is not target:
                        a.isDefault = False
            # 兜底：列表中始终有且仅有一个默认地址（避免编辑时取消默认后无默认）
            if not any(a.isDefault for a in acc.addresses):
                acc.addresses[0].isDefault = True
            self._save_viewers()
            return target, ""

    def delete_address(self, user_id: str, address_id: str) -> tuple[bool, str]:
        """删除一条收货地址；若删除的是默认地址，自动把剩余第一条设为默认。"""
        if not user_id:
            return False, "请先登录"
        with self._lock:
            acc = self._get_or_create_viewer_locked(user_id)
            if acc is None:
                return False, "观众账户不存在"
            target = next((a for a in acc.addresses if a.addressId == address_id), None)
            if target is None:
                return False, "收货地址不存在"
            acc.addresses = [a for a in acc.addresses if a.addressId != address_id]
            if acc.addresses and not any(a.isDefault for a in acc.addresses):
                acc.addresses[0].isDefault = True
            self._save_viewers()
            return True, ""

    def set_default_address(self, user_id: str, address_id: str) -> tuple[bool, str]:
        """把指定地址设为默认地址。"""
        if not user_id:
            return False, "请先登录"
        with self._lock:
            acc = self._get_or_create_viewer_locked(user_id)
            if acc is None:
                return False, "观众账户不存在"
            target = next((a for a in acc.addresses if a.addressId == address_id), None)
            if target is None:
                return False, "收货地址不存在"
            for a in acc.addresses:
                a.isDefault = a is target
            self._save_viewers()
            return True, ""

    # ============================================================
    # 观众端下单（两步：下单 pending_pay → 支付 paid）
    # ============================================================
    def create_viewer_order(self, product_id: str, user_id: str, user_name: str = "",
                            quantity: int = 1,
                            address_id: str = "",
                            sku_id: str = "",
                            channel: str = "live_cart") -> tuple[Optional[OrderItem], str]:
        """观众单商品下单（兼容旧调用）：转成一条购物车行交给统一结算。"""
        return self.create_viewer_cart_order(
            [{"productId": product_id, "skuId": sku_id,
              "quantity": quantity, "channel": channel}],
            user_id, user_name, address_id=address_id,
        )

    def _build_order_lines_locked(
        self, items: list[dict], channel: str = "live_cart",
    ) -> tuple[list[dict], str]:
        """校验并合并下单行。返回 (lines, 错误原因)，此时还不改库存。"""
        merged: dict[tuple[str, str], dict] = {}
        for raw in items or []:
            if not isinstance(raw, dict):
                return [], "下单参数不正确"
            pid = str(raw.get("productId") or "").strip()
            if not pid:
                return [], "商品不存在"
            try:
                qty = int(raw.get("quantity", 1) or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty < 1:
                qty = 1
            if qty > 99:
                return [], "单件商品最多购买 99 件"
            sku_id = str(raw.get("skuId") or "").strip()
            key = (pid, sku_id)
            if key in merged:
                merged[key]["quantity"] += qty
                if merged[key]["quantity"] > 99:
                    return [], "单件商品最多购买 99 件"
            else:
                merged[key] = {
                    "productId": pid, "skuId": sku_id, "quantity": qty,
                    "channel": str(raw.get("channel") or channel or "live_cart"),
                }
        if not merged:
            return [], "请选择要购买的商品"
        if len(merged) > 20:
            return [], "一单最多合并 20 种商品"

        lines: list[dict] = []
        for (pid, sku_id), row in merged.items():
            product = self.products.get(pid)
            if not product:
                return [], "商品不存在或已删除"
            if not product.isOnSale:
                return [], f"商品「{product.name}」已下架"
            sku = self._find_sku(product, sku_id)
            if sku is None:
                return [], f"商品「{product.name}」规格不存在"
            if not sku.isEnabled:
                return [], f"商品「{product.name}」的规格「{sku.spec}」已停用"
            if sku.stock < row["quantity"]:
                return [], f"「{product.name} {sku.spec}」库存不足，仅剩 {max(0, sku.stock)} 件"
            lines.append({
                "product": product, "sku": sku, "quantity": row["quantity"],
                "channel": row["channel"] if row["channel"] in ORDER_CHANNEL_LABELS else "live_cart",
            })
        return lines, ""

    def create_viewer_cart_order(
        self, items: list[dict], user_id: str, user_name: str = "",
        address_id: str = "",
    ) -> tuple[Optional[OrderItem], str]:
        """观众合并结算：校验 → 锁库存 → 生成待付款订单。

        库存从「支付时扣」前移到「下单时锁」：待付款订单占用库存，避免同一件
        最后一件商品被多人同时支付成功；取消/退款时按 stockDeducted 标记回补。
        优惠券按商品金额自动选最优一张，并按行金额比例分摊到每个订单行。
        """
        with self._lock:
            lines, reason = self._build_order_lines_locked(items)
            if not lines:
                return None, reason
            # 收货地址快照：下单时从地址簿复制，之后改/删地址不影响历史订单。
            address = None
            if address_id:
                acc = self.viewers.get(user_id)
                address = next(
                    (a for a in (acc.addresses if acc else []) if a.addressId == address_id),
                    None,
                )
                if address is None:
                    return None, "收货地址不存在，请重新选择"
            now = datetime.now().isoformat()
            self._order_counter += 1
            full_address = ""
            if address is not None:
                full_address = " ".join(x for x in (address.region, address.detail) if x)

            goods_amount = round(sum(line["sku"].price * line["quantity"] for line in lines), 2)
            original_amount = round(sum(
                (line["sku"].originalPrice or line["sku"].price) * line["quantity"]
                for line in lines), 2)
            coupon = best_coupon(goods_amount)
            discount = round(min(coupon["amount"], goods_amount), 2)
            total = round(goods_amount - discount, 2)

            # 优惠按行金额比例分摊；最后一行的差额兜底，保证行实付之和 == 订单实付。
            order_lines: list[OrderLineItem] = []
            allocated = 0.0
            for idx, line in enumerate(lines):
                product, sku, qty = line["product"], line["sku"], line["quantity"]
                amount = round(sku.price * qty, 2)
                if idx == len(lines) - 1:
                    line_discount = round(discount - allocated, 2)
                elif goods_amount > 0:
                    line_discount = round(discount * amount / goods_amount, 2)
                else:
                    line_discount = 0.0
                allocated = round(allocated + line_discount, 2)
                cost = round((sku.costPrice or product.costPrice or 0) * qty, 2)
                order_lines.append(OrderLineItem(
                    productId=product.productId,
                    productName=product.name,
                    productImage=(product.images[0] if product.images else product.imageUrl) or "",
                    skuId=sku.skuId,
                    skuSpec=sku.spec,
                    quantity=qty,
                    unitPrice=sku.price,
                    originalUnitPrice=sku.originalPrice or sku.price,
                    amount=amount,
                    originalAmount=round((sku.originalPrice or sku.price) * qty, 2),
                    discountAmount=line_discount,
                    totalAmount=round(amount - line_discount, 2),
                    costPrice=sku.costPrice or product.costPrice or 0,
                    costAmount=cost,
                    channel=line["channel"],
                ))

            first = order_lines[0]
            channels = {x.channel for x in order_lines}
            order_channel = first.channel if len(channels) == 1 else "cart"
            order = OrderItem(
                orderId=f"order_{int(datetime.now().timestamp()*1000)}_{self._order_counter}",
                orderNo=self._next_order_no(),
                # 兼容字段取首行；完整明细在 items 中，旧前端/旧报表仍可读。
                productId=first.productId,
                productName=first.productName if len(order_lines) == 1
                else f"{first.productName} 等 {len(order_lines)} 件商品",
                productImage=first.productImage,
                skuId=first.skuId,
                skuSpec=first.skuSpec,
                userId=user_id or "",
                userName=user_name or "",
                quantity=sum(x.quantity for x in order_lines),
                unitPrice=first.unitPrice,
                items=order_lines,
                goodsAmount=goods_amount,
                originalAmount=original_amount,
                discountAmount=discount,
                couponId=coupon["couponId"],
                couponName=coupon["name"],
                totalAmount=total,
                costAmount=round(sum(x.costAmount for x in order_lines), 2),
                channel=order_channel,
                status="pending_pay",
                payTime="",
                shipTime="",
                createTime=now,
                isVirtual=False,
                source="real",
                addressId=address.addressId if address else "",
                receiverName=address.receiverName if address else "",
                receiverPhone=address.phone if address else "",
                receiverAddress=full_address,
                stockDeducted=True,
                stage=self.live_state.currentStage or "",
                sessionId=self.live_state.sessionId or "",
                sourceDanmakuId=self._find_trace_danmaku_locked(user_id),
            )
            # 锁库存：校验已经在 _build_order_lines_locked 完成，这里逐行扣减并写流水。
            for line in order_lines:
                product = self.products.get(line.productId)
                sku = self._find_sku(product, line.skuId) if product else None
                if product is not None and sku is not None:
                    self._change_sku_stock_locked(
                        product, sku, -line.quantity, "order_create", order,
                        operator=user_id or "viewer",
                    )
            self.orders[order.orderId] = order
            self._save_products()
            self._save_orders()
            self._broadcast_order("order_new", order)
            for pid in {x.productId for x in order_lines}:
                self._broadcast_product_sales(pid)
            # 下单数量是本场数据统计的一项，实时推给运营台（不下发观众）。
            self._broadcast("live_room_stats_updated", self.get_live_room_stats(),
                            host_only=True)
            return order, ""

    def pay_order(self, order_id: str, user_id: str) -> tuple[Optional[OrderItem], str]:
        """观众支付：pending_pay → paid；扣钱包余额 + 累销量。

        新订单在下单时已经锁库存（stockDeducted=True），支付只做钱包扣款与销量累计；
        兼容旧的待付款订单（下单时没锁库存）时，这里补扣一次并写流水。
        """
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 严格归属：空 userId 的模拟单不再对任何观众开放（运营台走 update_order_status）
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status != "pending_pay":
                return None, "订单状态不允许支付"
            lines = self._order_lines(order)
            products: list[tuple[ProductItem, Optional[SkuItem], int]] = []
            for line in lines:
                product = self.products.get(line.productId)
                if not product:
                    return None, "商品不存在"
                # 下架后不允许支付「待付款」的旧单 —— 但位置很关键：必须排在
                # status != "pending_pay" 那关**之后**，这样已付款/已发货的订单在上一关
                # 就被放行了，永远不会被「已下架」拦住（下架不影响已经成交的生意）。
                if not product.isOnSale:
                    return None, "商品已下架，无法支付"
                sku = self._find_sku(product, line.skuId)
                if sku is None:
                    return None, "商品规格不存在，无法支付"
                if not order.stockDeducted and sku.stock < line.quantity:
                    return None, "库存不足，无法支付"
                products.append((product, sku, line.quantity))
            acc = self.viewers.get(user_id)
            if acc is None:
                return None, "请先登录观众账户"
            if acc.balance < order.totalAmount:
                return None, "钱包余额不足"
            acc.balance = round(acc.balance - order.totalAmount, 2)
            # 旧单没有在下单时锁库存，支付时补扣；新单已经扣过，绝不能重复扣。
            if not order.stockDeducted:
                for product, sku, qty in products:
                    self._change_sku_stock_locked(
                        product, sku, -qty, "order_create", order,
                        operator=user_id or "viewer",
                    )
                order.stockDeducted = True
            for product, sku, qty in products:
                product.soldCount += qty
            now = datetime.now().isoformat()
            order.status = "paid"
            order.payTime = order.payTime or now
            self._save_viewers()
            self._save_products()
            self._save_orders()
            self._broadcast_order("order_updated", order)
            for pid in {x.productId for x in lines}:
                self._broadcast_product_sales(pid)
            # 付款后 GMV / 已付款单数变化，运营台的「本场数据」立即刷新
            self._broadcast("live_room_stats_updated", self.get_live_room_stats(),
                            host_only=True)
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
        """观众取消待付款订单：pending_pay → canceled，并回补下单时锁定的库存。"""
        with self._lock:
            order = self.orders.get(order_id)
            if not order:
                return None, "订单不存在"
            # 严格归属：空 userId 的模拟单不再对任何观众开放
            if order.userId != user_id:
                return None, "无权操作该订单"
            if order.status != "pending_pay":
                return None, "仅待付款订单可取消"
            if order.stockDeducted:
                self._restock_order_locked(order, "order_cancel", user_id or "viewer")
            order.status = "canceled"
            self._save_orders()
            self._broadcast_order("order_updated", order)
            for pid in {x.productId for x in self._order_lines(order)}:
                self._broadcast_product_sales(pid)
            self._broadcast("live_room_stats_updated", self.get_live_room_stats(),
                            host_only=True)
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
            refund_amount = round(sum(
                line.totalAmount for o in orders if o.status == "refunded"
                for line in self._order_lines(o)), 2)
            today_orders = sum(1 for o in effective if o.createTime[:10] == today)
            today_sales = round(sum(o.totalAmount for o in effective if o.createTime[:10] == today), 2)
            # 按商品聚合销量：按订单行聚合，购物车合并单不会漏掉其它商品
            by_product: dict[str, dict] = {}
            for o in effective:
                for line in self._order_lines(o):
                    b = by_product.setdefault(line.productId, {
                        "productId": line.productId, "name": line.productName,
                        "qty": 0, "amount": 0.0, "discount": 0.0,
                    })
                    b["qty"] += line.quantity
                    b["amount"] = round(b["amount"] + line.totalAmount, 2)
                    b["discount"] = round(b["discount"] + line.discountAmount, 2)
            by_product_list = sorted(by_product.values(), key=lambda x: x["qty"], reverse=True)
            return {
                "totalOrders": total_orders,
                "totalSales": total_sales,
                "totalQty": total_qty,
                "todayOrders": today_orders,
                "todaySales": today_sales,
                "refundCount": refund_orders,
                "refundAmount": refund_amount,
                "refundRate": round(
                    (refund_orders / (total_orders + refund_orders) * 100)
                    if (total_orders + refund_orders) else 0.0, 1),
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
                # 按订单行聚合：购物车合并单必须把销量记到每一件商品上，
                # 只读顶层 productId 会把其余商品漏掉。
                for line in self._order_lines(o):
                    s = sales.setdefault(line.productId,
                                         {"qty": 0, "amount": 0.0, "todayQty": 0})
                    s["qty"] += line.quantity
                    s["amount"] = round(s["amount"] + line.totalAmount, 2)
                    if o.createTime[:10] == today:
                        s["todayQty"] += line.quantity
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
                for line in self._order_lines(o):
                    s = sales.setdefault(line.productId,
                                         {"qty": 0, "amount": 0.0, "todayQty": 0})
                    s["qty"] += line.quantity
                    s["amount"] = round(s["amount"] + line.totalAmount, 2)
                    if o.createTime[:10] == today:
                        s["todayQty"] += line.quantity
        return sales

    # ============================================================
    # 商品经营面板：SKU / 售后 / 加购 / 优惠 / 渠道 / 付款时效
    # ============================================================
    def get_product_ops_stats(self, product_id: str = "") -> dict:
        """单品（或全部商品）经营指标。

        只统计真实观众订单（AI 测试单永远不参与），返回：
          * skuStats   —— 每个 SKU 的销量/GMV/优惠/成本/毛利/库存；
          * refund     —— 退款单数、退款金额、退款率、退款原因汇总；
          * cart       —— 加购人数、加购次数、下单人数、加购转下单转化率；
          * channels   —— 直播间小黄车/回放/商品卡/购物车来源分布；
          * payment    —— 待付款单数、平均付款时长、超时未付款单数；
          * summary    —— 商品整体销量/实收/优惠/成本/毛利。
        """
        now_dt = datetime.now()
        paid_statuses = ("paid", "shipped", "completed")
        with self._lock:
            orders = [o for o in self.orders.values() if order_is_real(o)]
            if product_id:
                if product_id not in self.products:
                    return {"success": False, "message": "商品不存在"}
                orders = [o for o in orders if self._product_has_order(o, product_id)]

            sku_rows: dict[tuple[str, str], dict] = {}
            summary = {
                "orderCount": 0, "qty": 0, "gmv": 0.0, "goodsAmount": 0.0,
                "originalAmount": 0.0, "discountAmount": 0.0, "costAmount": 0.0,
                "grossProfit": 0.0, "buyerCount": 0,
            }
            buyers: set[str] = set()
            paid_orders = 0
            pending_count = 0
            pending_amount = 0.0
            pay_seconds: list[float] = []
            timeout_count = 0
            refund = {"count": 0, "amount": 0.0, "reasons": {}}
            channels = {c["key"]: {"channel": c["key"], "label": c["label"],
                                    "orderCount": 0, "gmv": 0.0}
                        for c in ORDER_CHANNELS if c["key"] != "legacy"}
            channel_orders = {k: set() for k in channels}
            seen_sku_orders: dict[tuple[str, str], set[str]] = {}

            for order in orders:
                lines = [ln for ln in self._order_lines(order)
                         if not product_id or ln.productId == product_id]
                if not lines:
                    continue
                valid = order.status in paid_statuses
                if valid:
                    paid_orders += 1
                    if order.userId:
                        buyers.add(order.userId)
                    summary["orderCount"] += 1
                    summary["qty"] += sum(ln.quantity for ln in lines)
                    summary["gmv"] = round(summary["gmv"] + sum(ln.totalAmount for ln in lines), 2)
                    summary["goodsAmount"] = round(
                        summary["goodsAmount"] + sum(ln.amount for ln in lines), 2)
                    summary["originalAmount"] = round(
                        summary["originalAmount"] + sum(ln.originalAmount for ln in lines), 2)
                    summary["discountAmount"] = round(
                        summary["discountAmount"] + sum(ln.discountAmount for ln in lines), 2)
                    summary["costAmount"] = round(
                        summary["costAmount"] + sum(ln.costAmount for ln in lines), 2)
                    # 平均付款时长 / 超时未付款
                    created = _parse_dt(order.createTime)
                    paid_at = _parse_dt(order.payTime)
                    if created and paid_at:
                        secs = max(0.0, (paid_at - created).total_seconds())
                        pay_seconds.append(secs)
                elif order.status == "pending_pay":
                    pending_count += 1
                    pending_amount = round(
                        pending_amount + sum(ln.totalAmount for ln in lines), 2)
                    created = _parse_dt(order.createTime)
                    if created and (now_dt - created).total_seconds() > ORDER_PAY_TIMEOUT_MINUTES * 60:
                        timeout_count += 1
                elif order.status == "refunded":
                    amount = round(sum(ln.totalAmount for ln in lines), 2)
                    refund["count"] += 1
                    refund["amount"] = round(refund["amount"] + amount, 2)
                    reason = (order.refundReason or "未填写原因").strip()[:80] or "未填写原因"
                    item = refund["reasons"].setdefault(reason, {"reason": reason,
                                                                  "count": 0, "amount": 0.0})
                    item["count"] += 1
                    item["amount"] = round(item["amount"] + amount, 2)
                if order.status == "canceled":
                    continue

                # 渠道分布：按**订单行**渠道算。购物车合并单可能同时来自
                # 小黄车 + 商品卡 + 回放，用订单级 channel（会被压成 cart）
                # 会把这些入口全部吞掉，单品渠道分析就失真了。
                line_channels: dict[str, list] = {}
                for ln in lines:
                    ch = ln.channel or order.channel or "legacy"
                    line_channels.setdefault(ch, []).append(ln)
                for ch, ch_lines in line_channels.items():
                    if ch not in channels:
                        channels[ch] = {"channel": ch,
                                        "label": ORDER_CHANNEL_LABELS.get(ch, ch),
                                        "orderCount": 0, "gmv": 0.0}
                        channel_orders[ch] = set()
                    if order.orderId not in channel_orders[ch]:
                        channel_orders[ch].add(order.orderId)
                        channels[ch]["orderCount"] += 1
                    if valid:
                        channels[ch]["gmv"] = round(
                            channels[ch]["gmv"] + sum(ln.totalAmount for ln in ch_lines), 2)

                for line in lines:
                    key = (line.productId, line.skuId or "")
                    row = sku_rows.get(key)
                    if row is None:
                        product = self.products.get(line.productId)
                        sku = self._find_sku(product, line.skuId) if product else None
                        row = {
                            "productId": line.productId,
                            "productName": line.productName,
                            "skuId": line.skuId,
                            "skuSpec": line.skuSpec or (sku.spec if sku else "默认规格"),
                            "price": sku.price if sku else line.unitPrice,
                            "costPrice": (sku.costPrice if sku else 0) or line.costPrice,
                            "stock": max(0, sku.stock) if sku else 0,
                            "enabled": bool(sku and sku.isEnabled),
                            "qty": 0, "gmv": 0.0, "originalAmount": 0.0,
                            "discountAmount": 0.0, "costAmount": 0.0,
                            "grossProfit": 0.0, "orderCount": 0, "pendingCount": 0,
                        }
                        sku_rows[key] = row
                        seen_sku_orders[key] = set()
                    if valid:
                        if order.orderId not in seen_sku_orders[key]:
                            seen_sku_orders[key].add(order.orderId)
                            row["orderCount"] += 1
                        row["qty"] += line.quantity
                        row["gmv"] = round(row["gmv"] + line.totalAmount, 2)
                        row["originalAmount"] = round(
                            row["originalAmount"] + line.originalAmount, 2)
                        row["discountAmount"] = round(
                            row["discountAmount"] + line.discountAmount, 2)
                        row["costAmount"] = round(row["costAmount"] + line.costAmount, 2)
                    elif order.status == "pending_pay":
                        row["pendingCount"] += 1

            # 当前库存为 0 但有订单历史 / 尚未产生订单的 SKU 也要出现在面板里，
            # 因此再把商品目录里的 SKU 补齐一次。
            for product in self.products.values():
                if product_id and product.productId != product_id:
                    continue
                for sku in product.skus:
                    key = (product.productId, sku.skuId)
                    if key not in sku_rows:
                        sku_rows[key] = {
                            "productId": product.productId, "productName": product.name,
                            "skuId": sku.skuId, "skuSpec": sku.spec, "price": sku.price,
                            "costPrice": sku.costPrice or product.costPrice,
                            "stock": max(0, sku.stock), "enabled": sku.isEnabled,
                            "qty": 0, "gmv": 0.0, "originalAmount": 0.0,
                            "discountAmount": 0.0, "costAmount": 0.0,
                            "grossProfit": 0.0, "orderCount": 0, "pendingCount": 0,
                        }
                    else:
                        row = sku_rows[key]
                        row["price"] = sku.price
                        row["costPrice"] = sku.costPrice or product.costPrice
                        row["stock"] = max(0, sku.stock)
                        row["enabled"] = sku.isEnabled
            for row in sku_rows.values():
                row["grossProfit"] = round(row["gmv"] - row["costAmount"], 2)
                row["grossMargin"] = round(
                    (row["grossProfit"] / row["gmv"] * 100) if row["gmv"] > 0 else 0.0, 1)
            sku_list = sorted(sku_rows.values(), key=lambda x: (-x["gmv"], -x["qty"]))

            summary["grossProfit"] = round(summary["gmv"] - summary["costAmount"], 2)
            summary["grossMargin"] = round(
                (summary["grossProfit"] / summary["gmv"] * 100)
                if summary["gmv"] > 0 else 0.0, 1)
            summary["buyerCount"] = len(buyers)
            summary["avgOrderAmount"] = round(
                (summary["gmv"] / summary["orderCount"])
                if summary["orderCount"] else 0.0, 2)

            # 加购 → 下单转化：加购用户去重，转化为其中已产生有效订单的用户
            add_users = {
                e.get("userId") for e in self.cart_events
                if (e.get("userId") and (not product_id or e.get("productId") == product_id))
            }
            add_users.discard("")
            add_count = sum(
                1 for e in self.cart_events
                if not product_id or e.get("productId") == product_id
            )
            converted = add_users & buyers
            cart_stats = {
                "addUsers": len(add_users),
                "addCount": add_count,
                "orderUsers": len(converted),
                "conversionRate": round(
                    (len(converted) / len(add_users) * 100) if add_users else 0.0, 1),
            }

            total_channel_gmv = sum(c["gmv"] for c in channels.values())
            channel_list = []
            for c in channels.values():
                c = dict(c)
                c["ratio"] = round(
                    (c["gmv"] / total_channel_gmv * 100) if total_channel_gmv else 0.0, 1)
                channel_list.append(c)
            channel_list.sort(key=lambda x: -x["gmv"])

            avg_pay_seconds = round(sum(pay_seconds) / len(pay_seconds), 1) if pay_seconds else 0.0
            refund_rate = round(
                (refund["count"] / (paid_orders + refund["count"]) * 100)
                if (paid_orders + refund["count"]) else 0.0, 1)
            reasons = sorted(refund["reasons"].values(),
                             key=lambda x: (-x["count"], -x["amount"]))
            threshold = max(0, int(self.settings.system.lowStockThreshold or 0))
            low_items = [
                {"productId": r["productId"], "productName": r["productName"],
                 "skuId": r["skuId"], "skuSpec": r["skuSpec"], "stock": r["stock"]}
                for r in sku_list
                if r["enabled"] and threshold > 0 and 0 <= r["stock"] <= threshold
            ]
            product_name = (self.products.get(product_id).name
                            if product_id and product_id in self.products else "全部商品")
            return {
                "success": True,
                "productId": product_id,
                "productName": product_name,
                "summary": summary,
                "skuStats": sku_list,
                "refund": {
                    "count": refund["count"], "amount": refund["amount"],
                    "rate": refund_rate, "reasons": reasons,
                    "paidOrderCount": paid_orders,
                },
                "cart": cart_stats,
                "channels": channel_list,
                "payment": {
                    "pendingCount": pending_count,
                    "pendingAmount": pending_amount,
                    "paidCount": len(pay_seconds),
                    "avgPaySeconds": avg_pay_seconds,
                    "avgPayMinutes": round(avg_pay_seconds / 60, 1),
                    "timeoutCount": timeout_count,
                    "timeoutMinutes": ORDER_PAY_TIMEOUT_MINUTES,
                },
                "stock": {
                    "threshold": threshold,
                    "lowCount": len(low_items),
                    "lowItems": low_items[:30],
                },
            }

    def get_low_stock_overview(self) -> list[dict]:
        """当前低于设置阈值的 SKU 列表（供运营台预警条/商品页红点）。"""
        threshold = max(0, int(self.settings.system.lowStockThreshold or 0))
        if threshold <= 0:
            return []
        rows = []
        with self._lock:
            for product in self.products.values():
                for sku in product.skus:
                    if sku.isEnabled and sku.stock <= threshold:
                        rows.append({
                            "productId": product.productId,
                            "productName": product.name,
                            "skuId": sku.skuId,
                            "skuSpec": sku.spec,
                            "stock": sku.stock,
                            "threshold": threshold,
                        })
        rows.sort(key=lambda x: x["stock"])
        return rows

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
    # 敏感词拦截日志
    # ============================================================
    def log_sensitive_blocked(self, content: str, reason: str, user_id: str = "",
                              user_name: str = "", scene: str = "danmaku") -> SensitiveLogItem:
        """记录一条敏感词拦截日志并通知运营台。"""
        word = ""
        if "：" in (reason or ""):
            word = reason.split("：", 1)[-1].strip()
        elif ":" in (reason or ""):
            word = reason.split(":", 1)[-1].strip()
        with self._lock:
            item = SensitiveLogItem(
                logId=f"sl_{int(time.time() * 1000)}_{len(self.sensitive_logs) + 1}",
                userId=user_id or "",
                userName=user_name or "",
                content=(content or "")[:500],
                word=word,
                reason=reason or "",
                scene=scene,
                sessionId=self.live_state.sessionId or "",
                timestamp=datetime.now().isoformat(),
            )
            self.sensitive_logs.append(item)
            if len(self.sensitive_logs) > SENSITIVE_LOGS_MAX:
                self.sensitive_logs = self.sensitive_logs[-SENSITIVE_LOGS_MAX:]
            self._save_sensitive_logs()
        self._broadcast("sensitive_blocked", item.model_dump(), host_only=True)
        return item

    def get_sensitive_logs(self, limit: int = 100, scene: str = "") -> list[dict]:
        with self._lock:
            rows = [x for x in self.sensitive_logs if not scene or x.scene == scene]
            rows.sort(key=lambda x: x.timestamp, reverse=True)
            return [x.model_dump() for x in rows[:max(1, int(limit or 100))]]

    # ============================================================
    # AI 问答知识库
    # ============================================================
    def add_knowledge(self, question: str, answer: str, keywords: list[str] | None = None,
                      source: str = "manual", operator: str = "host",
                      kb_id: str = "") -> Optional[KnowledgeBaseItem]:
        """新增/更新一条知识库问答。

        kb_id 非空且存在 → 更新；否则按 question 归并（同问题覆盖答案），
        最后再新建。相同问题不重复堆积，避免 AI 命中时二义。
        """
        question = (question or "").strip()[:200]
        answer = (answer or "").strip()[:2000]
        if not question or not answer:
            return None
        now = datetime.now().isoformat()
        with self._lock:
            item = None
            if kb_id:
                item = next((k for k in self.knowledge_base if k.kbId == kb_id), None)
            if item is None:
                item = next((k for k in self.knowledge_base
                             if k.question == question), None)
            if item is None:
                item = KnowledgeBaseItem(
                    kbId=f"kb_{int(time.time() * 1000)}_{len(self.knowledge_base) + 1}",
                    question=question,
                    answer=answer,
                    createTime=now,
                )
                self.knowledge_base.append(item)
            item.question = question
            item.answer = answer
            item.keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()][:20]
            item.source = source or item.source or "manual"
            item.updateTime = now
            item.operator = operator or "host"
            if len(self.knowledge_base) > KNOWLEDGE_MAX:
                self.knowledge_base = self.knowledge_base[-KNOWLEDGE_MAX:]
            self._save_knowledge()
            self._broadcast("knowledge_updated", item.model_dump(), host_only=True)
            return item

    def update_knowledge(self, kb_id: str, data: dict,
                         operator: str = "host") -> Optional[KnowledgeBaseItem]:
        with self._lock:
            item = next((k for k in self.knowledge_base if k.kbId == kb_id), None)
            if item is None:
                return None
            result = self.add_knowledge(
                data.get("question", item.question),
                data.get("answer", item.answer),
                data.get("keywords", item.keywords),
                data.get("source", item.source),
                operator,
                kb_id=kb_id,
            )
            # add_knowledge 按问题归并时可能把问题改掉，保持 id 稳定即可
            return result

    def delete_knowledge(self, kb_id: str) -> bool:
        with self._lock:
            before = len(self.knowledge_base)
            self.knowledge_base = [k for k in self.knowledge_base if k.kbId != kb_id]
            if len(self.knowledge_base) == before:
                return False
            self._save_knowledge()
            self._broadcast("knowledge_updated", {"kbId": kb_id, "deleted": True},
                            host_only=True)
            return True

    def get_knowledge(self, keyword: str = "") -> list[dict]:
        with self._lock:
            kw = (keyword or "").strip().lower()
            rows = [k for k in self.knowledge_base
                    if not kw or kw in k.question.lower()
                    or kw in k.answer.lower()
                    or any(kw in x.lower() for x in k.keywords)]
            rows.sort(key=lambda x: (-x.hitCount, x.question))
            return [k.model_dump() for k in rows]

    def find_knowledge_answer(self, content: str) -> Optional[str]:
        """按关键词/问题文本匹配知识库，命中则累加 hitCount 并返回标准答案。

        匹配顺序：关键词命中 > 问题文本包含 > 双向子串。宁可不命中（回退 AI），
        也不要用一个泛化关键词给出错误的标准答案。
        """
        text = (content or "").strip()
        if not text:
            return None
        with self._lock:
            best = None
            for k in self.knowledge_base:
                if any(kw and kw.lower() in text.lower() for kw in k.keywords):
                    best = k
                    break
                key = _question_key(k.question)
                if key and (key in text or _question_key(text) in k.question):
                    best = k
                    break
            if best is None:
                return None
            best.hitCount += 1
            best.updateTime = datetime.now().isoformat()
            self._save_knowledge()
            return best.answer

    def save_reply_correction(self, danmaku_id: str, corrected_answer: str,
                              operator: str = "host") -> dict:
        """主播手动修正某条 AI 回答并沉淀为标准答案。

        做两件事：① 更新原弹幕的 replyContent；② 取原问题写入知识库。
        之后相同问题会被知识库优先命中。
        """
        corrected_answer = (corrected_answer or "").strip()
        if not corrected_answer:
            return {"success": False, "message": "标准答案不能为空"}
        with self._lock:
            danmaku = next((d for d in self.danmaku_list
                            if d.danmakuId == danmaku_id), None)
            if danmaku is None:
                return {"success": False, "message": "弹幕不存在"}
            danmaku.replyContent = corrected_answer[:2000]
            danmaku.isReplied = True
            danmaku.replyTime = datetime.now().isoformat()
            self._save_danmaku()
            question = danmaku.content
        item = self.add_knowledge(question, corrected_answer,
                                  source="reply_correction", operator=operator)
        if item is None:
            return {"success": False, "message": "标准答案保存失败"}
        self._broadcast("danmaku_updated", danmaku.model_dump(), host_only=True)
        return {"success": True, "knowledge": item.model_dump(),
                "danmaku": danmaku.model_dump()}

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
            # 上一场没走 end_live 就重复开播（比如误点）时，先把旧场次固化，
            # 否则本场全程缓冲一清空，那一场的弹幕就永久丢了。
            if self.live_state.status in ("live", "paused") and self.live_state.sessionId:
                self._archive_current_session_locked(datetime.now().isoformat())
            self.live_state.status = "live"
            self.live_state.startTime = datetime.now().isoformat()
            # 新场次：生成唯一ID并清空本场全程缓冲，从此每条弹幕都绑定到它
            self.live_state.sessionId = self._new_live_session_id()
            self.current_session_danmaku = []
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
            self._low_stock_alerted_keys.clear()
            self._save_alerts()
            self.high_freq_questions = {}
            self.virtual_freq_questions = {}
            self.violation_log.clear()
            # 弹幕历史缓冲与置顶属于「本场」：开播即新会话边界
            self.live_danmaku_history.clear()
            self.pinned_danmaku_id = ""
            # 重置本场真实信号
            self._session_real_users = set()
            # 商品点击是本场会话级统计，新一场从 0 开始
            self._product_clicks.clear()
            self._product_click_viewers.clear()
            self._click_viewers.clear()
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
            # 直播结束：把本场全程弹幕固化到独立场次文件（按 sessionId 隔离）
            archived_session = self._archive_current_session_locked(now)
            self._stop_timers()
        # 释放锁后生成报告（避免死锁）
        report = self.generate_report()
        self._broadcast("live_ended", {
            "endTime": now,
            "duration": self.live_state.liveDuration,
            "danmakuSession": archived_session,
            "report": report,
        })
        return {"success": True, "message": "直播已结束", "report": report,
                "danmakuSession": archived_session}

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
                "sessionId": s.sessionId,
                "currentStage": s.currentStage,
                "startTime": s.startTime,
                "liveDuration": s.liveDuration,
                # onlineCount 是「模拟热度基数 + 真实在线」，只适合控制台的观感展示；
                # 分析页要求只算真实用户，读 realOnlineCount（观众连接会被
                # _VIEWER_LIVE_FIELDS 裁掉，不会把运营口径下发给观众）。
                "onlineCount": s.onlineCount,
                "realOnlineCount": self._real_online,
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
                sentiment=classify_sentiment(content),
            )
            # 虚拟弹幕也属于本场：同样绑定 sessionId 并进入全程缓冲，
            # 复盘时可以看到「氛围弹幕」在整场里占了多少。
            self._bind_danmaku_session_locked(danmaku)
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
        """本场真人观众弹幕（锁内调用，演示账号不计）。"""
        return [d for d in self.danmaku_list
                if is_human_user_id(d.userId) and self._in_session(d.timestamp)]

    def _session_orders_locked(self, real_only: bool = True) -> list[OrderItem]:
        """本场订单：createTime 落在当前直播会话内；real_only 时只留真人观众（锁内调用）。

        口径的**唯一出处** —— generate_report / _recompute_real_stats_locked /
        get_session_orders 都走这里，别再各写一遍
        `order_is_human(o) and self._in_session(o.createTime)`。这件事原先已经有两份
        副本，加第三份的话，将来改口径必漏，而漏掉的那处会安静地算出另一套数字。
        """
        return [o for o in self.orders.values()
                if (not real_only or order_is_human(o)) and self._in_session(o.createTime)]

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
          inquiryCount        —— 本场发过私信的真人观众数（按 userId 去重，
                                 只数观众发出的消息，主播/AI 回复不重复计人）
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

        # 咨询人数：本场发过私信的真人观众数（按 userId 去重）。
        # 只数观众发出的消息（isFromUser），主播/AI 的回复不算新的咨询；
        # 演示账号不计。旧实现统计的是消息条数，和「人数」标签对不上。
        consulting_users = {
            uid for uid, msgs in self.private_messages.items()
            if is_human_user_id(uid)
            and any(m.isFromUser and self._in_session(m.timestamp) for m in msgs)
        }
        self.stats.inquiryCount = len(consulting_users)

        # 意向度：三个分量按真实观众数归一，再截顶到 1
        viewers = max(len(self._session_real_users), 1)
        dm_per_viewer = min(1.0, n_dm / viewers / 3.0)   # 人均 3 条弹幕视为满格
        senders = {uid for uid, msgs in self.private_messages.items()
                   if is_human_user_id(uid)
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
            if order_is_human(o) and o.status == "pending_pay")
        self.stats.humanTransferCount = sum(
            1 for uid, s in self.user_sessions.items()
            if is_human_user_id(uid) and s.status == "human_reception")
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
                self._low_stock_alerted_keys.clear()
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

        # ── 库存：按系统设置阈值，逐个 SKU 判定 ──
        # 旧实现只看「当前讲解商品」且用比例阈值，主播看不到非讲解商品/其它规格
        # 的库存告急。现在使用 SystemSettings.lowStockThreshold，按 SKU 告警；
        # 同一 SKU 在「进入告急」时只报一次，补货后移除标记，下次再低会重新报。
        threshold = max(0, int(self.settings.system.lowStockThreshold or 0))
        low_keys: set[str] = set()
        if threshold > 0:
            for product in list(self.products.values()):
                for sku in product.skus:
                    key = f"{product.productId}:{sku.skuId}"
                    if not sku.isEnabled or sku.stock > threshold:
                        continue
                    low_keys.add(key)
                    if key in self._low_stock_alerted_keys:
                        continue
                    self._low_stock_alerted_keys.add(key)
                    stock_text = "已售罄" if sku.stock <= 0 else f"仅剩{sku.stock}件"
                    alerts.append(SmartAlert(
                        alertId=f"alert_{int(time.time())}_stock_{product.productId}_{sku.skuId}",
                        type="low_stock",
                        title="库存不足提醒",
                        message=(
                            f"商品「{product.name}」规格「{sku.spec}」{stock_text}"
                            f"（预警阈值 {threshold} 件），建议补货或控单"
                        ),
                        timestamp=now,
                    ))
        # 补货/调高阈值后允许再次告警
        self._low_stock_alerted_keys &= low_keys
        self._low_stock_alerted = bool(low_keys)
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
                stages = self.high_freq_stages.get(question, {})
                # 问题归属阶段 = 出现次数最多的阶段（并列时按 LIVE_STAGES 顺序）
                stage_order = {s["key"]: i for i, s in enumerate(LIVE_STAGES)}
                stage_key = ""
                if stages:
                    stage_key = max(
                        stages.items(),
                        key=lambda kv: (kv[1], -stage_order.get(kv[0], 99)),
                    )[0]
                results.append({
                    "question": question,
                    "count": count,
                    "hasPreset": has_preset,
                    "isReal": is_real,
                    "stage": stage_key,
                    "stageLabel": STAGE_LABELS.get(stage_key, "未分阶段"),
                    "stageCounts": stages,
                    "hasKnowledge": any(
                        k.question == question or
                        (k.keywords and any(kw.lower() in question.lower()
                                            for kw in k.keywords))
                        for k in self.knowledge_base),
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

    def record_question(self, content: str, is_real: bool = False, stage: str = ""):
        """记录一个问题到高频统计（并按直播阶段归集）。

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
                if stage:
                    stages = self.high_freq_stages.setdefault(key, {})
                    stages[stage] = stages.get(stage, 0) + 1
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

        # 记录到高频统计（带上当前直播阶段）
        self.record_question(content, is_real=is_real,
                             stage=self.live_state.currentStage or "")

        # 知识库标准答案优先（主播手工维护/修正过的答案）
        kb_answer = self.find_knowledge_answer(content)
        if kb_answer:
            self.reply_danmaku(danmaku_id, kb_answer)
            return kb_answer

        # 再匹配预设话术
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
    # ============================================================
    # 复盘报告（按直播场次；商品 / 弹幕双维度；历史独立存储）
    # ============================================================
    def generate_report(self, save: bool = True, session_id: Optional[str] = None,
                        regenerated: bool = False) -> dict:
        """生成一份**绑定直播场次**的复盘报告。

        报告只统计真实注册观众（v_* 账号）在本场内的真实行为；模拟弹幕 / 模拟订单
        只用于营造氛围，排除量在 `scope` 里显式披露。每份报告独立落盘、索引可查，
        reportId 含时间戳与随机串，重新生成会新增一份，**不覆盖**历史记录。

        session_id 为空 → 复盘当前场次；传历史场次ID → 从场次归档重建（重新生成）。
        """
        sid = (session_id or "").strip()
        current_sid = self.live_state.sessionId or ""
        if sid and sid != current_sid:
            ds = self._report_dataset_from_archive(sid)
            if ds is None:
                return {"success": False, "message": "场次不存在或尚未归档"}
        else:
            if not current_sid:
                return {"success": False, "message": "尚未开播，暂无绑定场次的复盘数据"}
            with self._lock:
                ds = self._report_dataset_current_locked()
        report = self._build_report_payload(ds, regenerated=regenerated)
        if save:
            self._save_report(report)
        return report

    def _report_dataset_current_locked(self) -> dict:
        """采集当前场次的复盘数据快照（调用方需持锁）。"""
        sid = self.live_state.sessionId or ""
        items = list(self.current_session_danmaku)
        if not items:
            # 直播结束后缓冲已归档清空；从滚动列表按 sessionId 兜底，
            # 保证 end_live 自动生成报告时仍有完整场次数据。
            items = [d for d in self.danmaku_list if d.sessionId == sid]
        real_dm = [d for d in items if is_human_user_id(d.userId)]
        real_reviews = [r for r in self.reviews
                        if is_human_user_id(r.userId) and self._in_session(r.timestamp)]
        real_msgs = [m for uid, msgs in self.private_messages.items()
                     if is_human_user_id(uid)
                     for m in msgs if self._in_session(m.timestamp)]
        real_orders = self._session_orders_locked()
        mock_orders_excluded = sum(
            1 for o in self.orders.values()
            if not order_is_real(o) and self._in_session(o.createTime))
        product = self.get_current_product()
        product_id = product.productId if product else ""
        product_name = product.name if product else "未上架商品"
        start_time = self.live_state.startTime or ""
        duration = int(self.live_state.liveDuration or 0)
        online_avg = (round(self._online_sum / self._online_samples)
                      if self._online_samples else 0)
        base = int(self._online_base or 0)
        unique_real = len(self._session_real_users) or len({d.userId for d in real_dm})
        start_label = start_time[:16].replace("T", " ")
        return {
            "source": "current",
            "sessionId": sid,
            "sessionTitle": f"直播场次 · {start_label}" if start_label else "直播场次",
            "product": product,
            "productId": product_id,
            "productName": product_name,
            "startTime": start_time,
            "endTime": datetime.now().isoformat(),
            "duration": duration,
            "real_dm": real_dm,
            "all_dm": items,
            "real_reviews": real_reviews,
            "real_msgs": real_msgs,
            "real_orders": real_orders,
            "unique_real": unique_real,
            "online_peak": int(self._online_peak or 0),
            "online_avg": online_avg,
            "real_peak": int(self._online_real_peak or 0),
            "avg_real": max(0, online_avg - base) if self._online_samples else 0,
            "base": base,
            "virtual_count": sum(1 for d in items
                                 if classify_user_id(d.userId) == "virtual"),
            "mock_dm_excluded": sum(1 for d in items
                                    if classify_user_id(d.userId) in ("virtual", "mock")),
            "mock_orders_excluded": mock_orders_excluded,
            "preset_used": sum(1 for p in self.presets.values() if p.useCount > 0),
            "human_transfer": sum(
                1 for uid, s in self.user_sessions.items()
                if is_real_user_id(uid) and s.status == "human_reception"),
            "potential_orders": int(self.stats.potentialOrderCount or 0),
            "intent_score": float(self.stats.intentScore or 0),
            "mention_count": int(self.stats.mentionCount or 0),
        }

    def _report_dataset_from_archive(self, session_id: str) -> Optional[dict]:
        """从场次归档重建复盘数据（历史场次 / 重新生成）。"""
        archive = self._load_session_archive(session_id)
        if archive is None:
            return None
        meta = archive["session"]
        items = archive["danmaku"]
        sid = str(meta.get("sessionId") or session_id)
        start_time = str(meta.get("startTime") or "")
        end_time = str(meta.get("endTime") or "")
        product_id = str(meta.get("productId") or "")
        product_name = str(meta.get("productName") or "未上架商品")
        real_dm = [d for d in items if is_human_user_id(d.userId)]
        with self._lock:
            real_orders = [
                o for o in self.orders.values()
                if order_is_human(o)
                and _timestamp_in_window(o.createTime, start_time, end_time)]
            mock_orders_excluded = sum(
                1 for o in self.orders.values()
                if not order_is_human(o)
                and _timestamp_in_window(o.createTime, start_time, end_time))
            real_reviews = [
                r for r in self.reviews
                if is_human_user_id(r.userId)
                and _timestamp_in_window(r.timestamp, start_time, end_time)]
            real_msgs = [
                m for uid, msgs in self.private_messages.items()
                if is_human_user_id(uid)
                for m in msgs
                if _timestamp_in_window(m.timestamp, start_time, end_time)]
            product = self.products.get(product_id) if product_id else None
        unique_real = (int(meta.get("realUsers") or 0)
                       or len({d.userId for d in real_dm}))
        duration = int(meta.get("duration") or 0)
        title = str(meta.get("title") or "")
        if not title:
            label = start_time[:16].replace("T", " ")
            title = f"{product_name} · {label}" if label else product_name
        mention_count = self._count_product_mentions(real_dm, product, product_name)
        return {
            "source": "history",
            "sessionId": sid,
            "sessionTitle": title,
            "product": product,
            "productId": product_id,
            "productName": product_name,
            "startTime": start_time,
            "endTime": end_time or datetime.now().isoformat(),
            "duration": duration,
            "real_dm": real_dm,
            "all_dm": items,
            "real_reviews": real_reviews,
            "real_msgs": real_msgs,
            "real_orders": real_orders,
            "unique_real": unique_real,
            "online_peak": int(meta.get("peakOnline") or 0),
            "online_avg": int(meta.get("avgOnline") or 0),
            "real_peak": int(meta.get("peakRealViewers") or 0),
            "avg_real": int(meta.get("avgRealViewers") or 0),
            "base": int(meta.get("onlineBase") or 0),
            "virtual_count": sum(1 for d in items
                                 if classify_user_id(d.userId) == "virtual"),
            "mock_dm_excluded": sum(1 for d in items
                                    if classify_user_id(d.userId) in ("virtual", "mock")),
            "mock_orders_excluded": mock_orders_excluded,
            "preset_used": int(meta.get("presetUsedCount") or 0),
            "human_transfer": int(meta.get("humanTransferCount") or 0),
            "potential_orders": sum(
                1 for o in real_orders if o.status == "pending_pay"),
            "intent_score": self._compute_intent_score(
                real_dm, real_msgs, real_orders, unique_real),
            "mention_count": mention_count,
        }

    def _build_report_payload(self, ds: dict, regenerated: bool = False) -> dict:
        """把场次数据快照渲染成报告（商品 / 弹幕双维度 + 文字总结）。"""
        now = datetime.now()
        sid = ds.get("sessionId") or ""
        session_title = ds.get("sessionTitle") or "未命名场次"
        start_time = ds.get("startTime") or ""
        end_time = ds.get("endTime") or now.isoformat()
        duration = int(ds.get("duration") or 0)
        product = ds.get("product")
        product_id = ds.get("productId") or ""
        product_name = ds.get("productName") or "未上架商品"
        real_dm: list[DanmakuItem] = ds.get("real_dm") or []
        all_dm: list[DanmakuItem] = ds.get("all_dm") or []
        real_reviews: list[ReviewItem] = ds.get("real_reviews") or []
        real_msgs: list[PrivateMessageItem] = ds.get("real_msgs") or []
        real_orders: list[OrderItem] = ds.get("real_orders") or []
        unique_real = int(ds.get("unique_real") or 0)
        virtual_count = int(ds.get("virtual_count") or 0)
        mock_dm_excluded = int(ds.get("mock_dm_excluded") or 0)
        mock_orders_excluded = int(ds.get("mock_orders_excluded") or 0)
        preset_used = int(ds.get("preset_used") or 0)
        human_transfer = int(ds.get("human_transfer") or 0)
        potential_orders = int(ds.get("potential_orders") or 0)
        mention_count = int(ds.get("mention_count") or 0)

        paid_statuses = ("paid", "shipped", "completed")
        paid_orders = [o for o in real_orders if o.status in paid_statuses]
        gmv = round(sum(o.totalAmount for o in paid_orders), 2)
        sold_qty = sum(o.quantity for o in paid_orders)
        refund_count = sum(1 for o in real_orders if o.status == "refunded")
        buyer_count = len({o.userId for o in paid_orders})
        conversion_rate = (round(buyer_count / unique_real * 100, 1)
                           if unique_real else 0.0)
        by_product: dict[str, dict] = {}
        for o in paid_orders:
            b = by_product.setdefault(o.productId, {
                "productId": o.productId, "name": o.productName,
                "qty": 0, "amount": 0.0})
            b["qty"] += o.quantity
            b["amount"] = round(b["amount"] + o.totalAmount, 2)

        # ---- 弹幕维度：问题、情绪、顾虑、时间分布 ----
        real_replies = [d for d in real_dm if d.isReplied]
        question_count = sum(1 for d in real_dm if d.isQuestion)
        reply_rate = (len(real_replies) / len(real_dm) * 100) if real_dm else 0.0
        latencies: list[float] = []
        for d in real_replies:
            a, b = _parse_dt(d.timestamp), _parse_dt(d.replyTime)
            if a is not None and b is not None:
                latencies.append((b - a).total_seconds())
        avg_response = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
        sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
        barrier_counts: dict[str, int] = {}
        for d in real_dm:
            s = d.sentiment or classify_sentiment(d.content)
            sentiment_counts[s if s in sentiment_counts else "neutral"] += 1
            k = classify_barrier(d.content)
            if k != "other":
                barrier_counts[k] = barrier_counts.get(k, 0) + 1
        top_questions = self._rank_questions(real_dm, 10)
        question_categories = self._rank_question_categories(real_dm)
        failed_questions = self._get_failed_questions(real_dm)
        timeline = self._build_timeline(real_dm, start_time)

        # ---- 商品维度：每个商品的提及 / 提问 / 成交 / 退款 / 评价 ----
        product_rows: dict[str, dict] = {}

        def _row(pid: str, name: str) -> dict:
            key = pid or f"name:{name}"
            row = product_rows.get(key)
            if row is None:
                row = {
                    "productId": pid, "name": name or "未指定商品",
                    "mentions": 0, "questions": 0,
                    "orderCount": 0, "paidCount": 0, "qty": 0, "gmv": 0.0,
                    "refundCount": 0, "reviewCount": 0, "rating": None,
                    "positive": 0, "negative": 0, "_ratingSum": 0,
                }
                product_rows[key] = row
            return row

        if product_id or product_name:
            _row(product_id, product_name)
        for o in real_orders:
            row = _row(o.productId, o.productName)
            row["orderCount"] += 1
            if o.status in paid_statuses:
                row["paidCount"] += 1
                row["qty"] += o.quantity
                row["gmv"] = round(row["gmv"] + o.totalAmount, 2)
            if o.status == "refunded":
                row["refundCount"] += 1
        for r in real_reviews:
            row = _row(r.productId or product_id, r.productName or product_name)
            row["reviewCount"] += 1
            row["_ratingSum"] += r.rating
            if r.sentiment == "positive":
                row["positive"] += 1
            elif r.sentiment == "negative":
                row["negative"] += 1
        # 弹幕提及：商品名 / 卖点 / 标签命中即计一次；提问另计
        targets = []
        for row in product_rows.values():
            pid = row.get("productId") or ""
            p = self.products.get(pid) if pid else None
            if p is not None:
                keys = [p.name] + list(p.sellingPoints) + list(p.tags)
            else:
                keys = [row.get("name") or ""]
            keys = [k for k in keys if k and len(k) >= 2]
            targets.append((row, keys))
        for d in real_dm:
            text = (d.content or "").lower()
            if not text:
                continue
            for row, keys in targets:
                if any(k.lower() in text for k in keys):
                    row["mentions"] += 1
                    if d.isQuestion:
                        row["questions"] += 1
        products_out = []
        for row in product_rows.values():
            row["rating"] = (round(row["_ratingSum"] / row["reviewCount"], 1)
                             if row["reviewCount"] else None)
            row.pop("_ratingSum", None)
            products_out.append(row)
        products_out.sort(
            key=lambda r: (-r["gmv"], -r["mentions"], -r["orderCount"], r["name"]))
        top_product_name = (products_out[0]["name"] if products_out
                            else product_name)

        # ---- 评价聚合（只算本场真实评价）----
        positive_tags: dict[str, int] = {}
        concern_tags: dict[str, int] = {}
        for r in real_reviews:
            if r.sentiment not in ("positive", "negative"):
                continue
            bucket = positive_tags if r.sentiment == "positive" else concern_tags
            if r.tags:
                for t in r.tags:
                    bucket[t] = bucket.get(t, 0) + 1
            else:
                for w in (_POSITIVE_WORDS if r.sentiment == "positive"
                          else _NEGATIVE_WORDS):
                    if w in r.content:
                        bucket[w] = bucket.get(w, 0) + 1
        positive_points = [k for k, _ in sorted(
            positive_tags.items(), key=lambda kv: -kv[1])[:3]]
        concern_points = [k for k, _ in sorted(
            concern_tags.items(), key=lambda kv: -kv[1])[:3]]
        total_reviews = len(real_reviews)
        positive = sum(1 for r in real_reviews if r.sentiment == "positive")
        negative = sum(1 for r in real_reviews if r.sentiment == "negative")
        positive_rate = (positive / total_reviews * 100) if total_reviews else 0.0
        real_rating = (round(sum(r.rating for r in real_reviews) / total_reviews, 1)
                       if total_reviews else None)

        grade = self._calculate_grade(positive_rate, len(real_replies), len(real_dm))
        duration_min = duration // 60
        start_dt = _parse_dt(start_time)
        date_str = (start_dt or now).strftime("%Y-%m-%d")

        # ---- 文字总结 / 建议（全部由本场真实数据推导）----
        highlights: list[str] = []
        if real_dm:
            highlights.append(
                f"本场收到真实观众弹幕 {len(real_dm)} 条、提问 {question_count} 个，"
                f"来自 {unique_real} 位真实观众")
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
        if products_out and products_out[0]["gmv"] > 0:
            highlights.append(
                f"成交集中在「{products_out[0]['name']}」"
                f"（{products_out[0]['qty']} 件 / ¥{products_out[0]['gmv']}）")

        improvements: list[str] = []
        unanswered = sum(1 for d in real_dm if d.isQuestion and not d.isReplied)
        if unanswered:
            improvements.append(
                f"本场有 {unanswered} 个真实提问未被回复，建议补充话术或安排场控跟进")
        if barrier_counts:
            top_key = max(barrier_counts.items(), key=lambda kv: kv[1])[0]
            top_cat = next((c for c in _BARRIER_CATEGORIES
                            if c["type"] == top_key), None)
            if top_cat:
                improvements.append(
                    f"「{top_cat['title']}」类顾虑最多（{barrier_counts[top_key]} 条），"
                    f"建议{top_cat['suggestion']}")
        if not paid_orders:
            improvements.append("本场没有真实成交，建议检查讲解节奏、价格锚点与下单引导")
        if concern_points:
            improvements.append(
                "观众主要顾虑：" + "、".join(concern_points) + "，建议在讲解中主动回应")
        if not improvements:
            improvements.append("保持本场节奏，继续补充常见问题话术库")

        stats_snapshot = {
            "mentionCount": mention_count,
            "intentScore": int(ds.get("intent_score") or 0),
            "potentialOrderCount": potential_orders,
        }
        # _get_failed_questions 已经返回 dict 列表，截断后直接复用
        failed_brief = failed_questions[:10]
        categories_out = [
            {
                "type": cat["type"], "title": cat["title"],
                "count": barrier_counts.get(cat["type"], 0),
                "suggestion": cat["suggestion"],
            }
            for cat in _BARRIER_CATEGORIES
        ] + [{
            "type": "other", "title": "其他问题",
            "count": sum(1 for d in real_dm if classify_barrier(d.content) == "other"
                         and d.isQuestion),
            "suggestion": "补充通用答疑话术，覆盖更多长尾问题",
        }]

        # ---- 迭代新增：分阶段对比 / 退款汇总 / 差评痛点 / AI 运营建议 ----
        stage_comparison = []
        for stage in LIVE_STAGES:
            key = stage["key"]
            stag_dm = [d for d in real_dm if d.stage == key]
            stag_orders = [o for o in real_orders if o.stage == key]
            stag_paid = [o for o in stag_orders if o.status in paid_statuses]
            stage_comparison.append({
                "stage": key, "label": stage["label"], "icon": stage["icon"],
                "danmaku": len(stag_dm),
                "questions": sum(1 for d in stag_dm if d.isQuestion),
                "positive": sum(1 for d in stag_dm
                                if (d.sentiment or classify_sentiment(d.content))
                                == "positive"),
                "negative": sum(1 for d in stag_dm
                                if (d.sentiment or classify_sentiment(d.content))
                                == "negative"),
                "orderCount": len(stag_orders),
                "paidCount": len(stag_paid),
                "qty": sum(o.quantity for o in stag_paid),
                "gmv": round(sum(o.totalAmount for o in stag_paid), 2),
            })

        refund_orders = [o for o in real_orders
                         if o.status == "refunded"
                         or o.refundStatus in ("approved", "pending")]
        refund_groups: dict[str, dict] = {}
        for o in refund_orders:
            cat = o.refundReasonCategory or classify_refund_reason(o.refundReason)
            label = next((c["label"] for c in REFUND_REASON_CATEGORIES
                          if c["key"] == cat), "其他原因")
            g = refund_groups.setdefault(cat, {
                "category": cat, "label": label, "count": 0, "amount": 0.0})
            g["count"] += 1
            g["amount"] = round(g["amount"] + float(o.totalAmount or 0), 2)
        refund_categories = sorted(refund_groups.values(), key=lambda x: -x["count"])
        for row in refund_categories:
            row["ratio"] = (round(row["count"] / len(refund_orders) * 100, 1)
                            if refund_orders else 0.0)
        refund_summary = {
            "count": len(refund_orders),
            "amount": round(sum(o.totalAmount for o in refund_orders), 2),
            "rate": round(len(refund_orders) / len(real_orders) * 100, 1)
            if real_orders else 0.0,
            "categories": refund_categories,
            "reasons": [
                {"orderNo": o.orderNo or o.orderId,
                 "reason": o.refundReason or "未填写",
                 "category": next((c["label"] for c in REFUND_REASON_CATEGORIES
                                   if c["key"] == (o.refundReasonCategory
                                                   or classify_refund_reason(o.refundReason))),
                                  "其他原因"),
                 "amount": o.totalAmount}
                for o in refund_orders[:20]
            ],
        }

        negative_reviews = [r for r in real_reviews
                            if r.sentiment == "negative" or int(r.rating or 5) <= 2]
        pain_points: dict[str, int] = {}
        for r in negative_reviews:
            hits = [w for w in _NEGATIVE_WORDS if w in (r.content or "")]
            hits = (r.tags or []) + hits
            if not hits:
                hits = ["综合体验不佳"]
            for hit in hits[:3]:
                pain_points[hit] = pain_points.get(hit, 0) + 1
        negative_summary = {
            "count": len(negative_reviews),
            "points": [{"point": k, "count": v} for k, v in
                       sorted(pain_points.items(), key=lambda kv: -kv[1])[:8]],
            "examples": [{"content": r.content[:80], "rating": r.rating,
                          "productName": r.productName}
                         for r in negative_reviews[:5]],
        }

        # AI 运营建议：规则推导 + 本场数据，输出可直接执行的行动项
        ai_suggestions: list[str] = []
        if refund_categories:
            top_refund = refund_categories[0]
            ai_suggestions.append(
                f"退款集中在「{top_refund['label']}」（{top_refund['count']} 单），"
                f"建议下一场开播前针对该问题准备标准答疑并优化详情页说明。")
        if negative_summary["points"]:
            top_pain = negative_summary["points"][0]
            ai_suggestions.append(
                f"差评最集中的痛点是「{top_pain['point']}」，"
                f"建议安排质检复核并在直播中主动展示改进措施。")
        stage_with_gmv = max(stage_comparison, key=lambda x: x["gmv"])
        if stage_with_gmv["gmv"] > 0:
            ai_suggestions.append(
                f"成交高峰出现在「{stage_with_gmv['label']}」（GMV ¥{stage_with_gmv['gmv']}），"
                f"建议把主推商品与核心话术集中安排在该阶段。")
        stage_with_questions = max(stage_comparison, key=lambda x: x["questions"])
        if stage_with_questions["questions"] > 0:
            ai_suggestions.append(
                f"提问最集中的阶段是「{stage_with_questions['label']}」"
                f"（{stage_with_questions['questions']} 个问题），建议提前在该阶段配置场控与快捷话术。")
        if unanswered:
            ai_suggestions.append(
                f"仍有 {unanswered} 个提问未回复，建议将高频问题沉淀进 AI 知识库，"
                f"让下次直播自动命中标准答案。")
        if not ai_suggestions:
            ai_suggestions.append("本场数据平稳，建议保持现有节奏并继续积累知识库问答。")

        # ---- 复盘结论：经营结果 → 关键问题 → 可执行动作 ----
        # 旧版结论只是把弹幕/订单数复述一遍，主播看完仍然不知道下一步做什么。
        # 这里按「成绩 / 关注点 / 商品 / 售后 / 口碑 / 互动 / 下一步」组织，
        # 每一行都带具体数字或对象，直接可用来做下一场的调整。
        if not real_dm and not paid_orders:
            conclusion = (
                "【结论】本场没有真人互动与成交，现有数据不足以形成经营判断。\n"
                "【排查】确认观众是否登录、主推商品是否已挂载、开播通知是否触达老客户。\n"
                "【下一步】1) 开播前用私信或社群通知老客户；2) 开场 5 分钟做登录抽奖/福袋；"
                "3) 提前准备主推商品的 3 条核心卖点话术。"
            )
        else:
            aov = round(gmv / len(paid_orders), 2) if paid_orders else 0.0
            concl_lines = [
                f"【成绩】真人观众 {unique_real} 人、弹幕 {len(real_dm)} 条"
                f"（提问 {question_count} 个）；成交 {len(paid_orders)} 单 / GMV ¥{gmv}，"
                f"客单价 ¥{aov:.2f}，支付转化率 {conversion_rate}%。"
            ]
            if top_questions:
                top_q = top_questions[0]
                concl_lines.append(
                    f"【关注点】观众问得最多的是「{top_q['question']}」"
                    f"（{top_q['count']} 次），建议把标准答案放进快捷话术或 AI 知识库。")
            if products_out and products_out[0]["gmv"] > 0:
                top = products_out[0]
                share = round(top["gmv"] / gmv * 100, 1) if gmv else 0.0
                line = (f"【商品】「{top['name']}」贡献 {share}% 的 GMV"
                        f"（{top['qty']} 件）")
                if share >= 70 and len(paid_orders) >= 3:
                    line += "；单品依赖偏高，建议下一场增加 1-2 个备选主推分散风险"
                concl_lines.append(line + "。")
            if refund_summary["count"]:
                top_refund = (refund_summary["categories"][0]
                              if refund_summary["categories"] else None)
                line = (f"【售后】退款 {refund_summary['count']} 单"
                        f"（退款率 {refund_summary['rate']}%，"
                        f"¥{refund_summary['amount']}）")
                if top_refund:
                    line += f"，主要集中在「{top_refund['label']}」"
                concl_lines.append(line + "。")
            if negative_summary["count"]:
                top_pain = (negative_summary["points"][0]["point"]
                            if negative_summary["points"] else "综合体验")
                concl_lines.append(
                    f"【口碑】差评 {negative_summary['count']} 条，"
                    f"主要痛点是「{top_pain}」。")
            barrier_text = ""
            if barrier_counts:
                top_key = max(barrier_counts.items(), key=lambda kv: kv[1])[0]
                top_cat = next((c for c in _BARRIER_CATEGORIES
                                if c["type"] == top_key), None)
                if top_cat:
                    barrier_text = (f"；最大顾虑是「{top_cat['title']}」"
                                    f"（{barrier_counts[top_key]} 条）")
            concl_lines.append(
                f"【互动】AI 回复覆盖率 {reply_rate:.0f}%，"
                f"未答提问 {unanswered} 个{barrier_text}。")
            actions = [a.rstrip("。；;，, ") for a in (ai_suggestions or improvements)[:3]]
            if actions:
                concl_lines.append("【下一步】" + "；".join(
                    f"{i + 1}) {a}" for i, a in enumerate(actions)))
            conclusion = "\n".join(concl_lines)

        report = {
            # schemaVersion 4：新增分阶段对比、退款/差评汇总、AI 运营建议。
            "schemaVersion": 4,
            "reportId": self._new_report_id(sid),
            "sessionId": sid,
            "sessionTitle": session_title,
            "title": f"{date_str} 直播复盘报告",
            "generateTime": now.isoformat(),
            "regenerated": bool(regenerated),
            "source": ds.get("source", "current"),
            "liveDuration": duration,
            "liveDurationStr": f"{duration_min // 60}小时{duration_min % 60}分钟",
            "grade": grade,
            "gradeNote": "等级阈值按单场 50/200/500 条弹幕标定；真实场景弹幕量级更小时等级偏低属正常",
            "productName": product_name,
            "scope": {
                "caliber": "本报告只统计真实注册观众（v_* 账号）在本场直播内的弹幕 / 私信 / 订单 / 评价",
                "sessionId": sid,
                "sessionTitle": session_title,
                "startTime": start_time,
                "endTime": end_time,
                "excludedMockUsers": 10,
                "excludedVirtualDanmaku": virtual_count,
                "excludedMockDanmaku": mock_dm_excluded,
                "excludedMockOrders": mock_orders_excluded,
                "excludedMockReviews": sum(
                    1 for r in self.reviews if not is_human_user_id(r.userId)),
            },
            "summary": {
                "overallGrade": grade,
                "topProduct": top_product_name,
                "topQuestions": [q["question"] for q in top_questions[:3]],
                "highlights": highlights,
                "improvements": improvements[:5],
                "conclusion": conclusion,
            },
            # ---- 迭代新增：分阶段对比 / 退款汇总 / 差评痛点 / AI 建议 ----
            "stageComparison": stage_comparison,
            "refundSummary": refund_summary,
            "negativeReviewSummary": negative_summary,
            "aiSuggestions": ai_suggestions,
            # ---- 新：商品维度 / 弹幕维度（图表直接消费这两个对象）----
            "productDimension": {
                "sessionId": sid,
                "sessionTitle": session_title,
                "productId": product_id,
                "productName": product_name,
                "products": products_out,
                "orderCount": len(real_orders),
                "paidCount": len(paid_orders),
                "qty": sold_qty,
                "gmv": gmv,
                "buyerCount": buyer_count,
                "refundCount": refund_count,
                "conversionRate": conversion_rate,
                "byProduct": sorted(by_product.values(), key=lambda x: -x["qty"]),
                "mentionCount": mention_count,
                "reviewCount": total_reviews,
                "rating": real_rating,
            },
            "danmakuDimension": {
                "sessionId": sid,
                "sessionTitle": session_title,
                "total": len(all_dm) or len(real_dm),
                "real": len(real_dm),
                "virtual": virtual_count,
                "questionCount": question_count,
                "repliedCount": len(real_replies),
                "replyRate": round(reply_rate, 1),
                "avgResponseTime": avg_response,
                "sentiment": sentiment_counts,
                "categories": categories_out,
                "topQuestions": top_questions,
                "failedQuestions": failed_brief,
                "timeline": timeline,
            },
            # ---- 兼容旧版章节结构（导出 / 旧前端仍可读）----
            "chapter1_overview": {
                "basic": {
                    "date": date_str,
                    "duration": duration,
                    "startTime": start_time,
                    "endTime": end_time,
                },
                "audience": {
                    "peakRealViewers": int(ds.get("real_peak") or 0),
                    "avgRealViewers": int(ds.get("avg_real") or 0),
                    "uniqueRealViewers": unique_real,
                    "peakOnline": int(ds.get("online_peak") or 0),
                    "avgOnline": int(ds.get("online_avg") or 0),
                    "baseNote": {
                        "label": "模拟热度基数（不计入分析）",
                        "value": int(ds.get("base") or 0),
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
                    "intentUsers": stats_snapshot["intentScore"],
                    "potentialOrders": stats_snapshot["potentialOrderCount"],
                    "humanTransferCount": human_transfer,
                },
            },
            "chapter2_product": {
                "productName": product_name,
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
                "questionCount": question_count,
                "sentiment": sentiment_counts,
                "topQuestions": top_questions,
                "categories": categories_out,
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
                "failedQuestions": failed_brief,
            },
            "chapter6_barriers": categories_out[:-1],
            "chapter7_highlights": self._generate_highlights(real_dm, start_time),
            "chapter8_conversion": {
                "realOrders": len(paid_orders),
                "realPaidOrders": sum(1 for o in real_orders if o.status == "paid"),
                "realGMV": gmv,
                "realQty": sold_qty,
                "refundCount": refund_count,
                "buyerCount": buyer_count,
                "conversionRate": conversion_rate,
                "byProduct": sorted(by_product.values(), key=lambda x: -x["qty"]),
                "mockOrdersExcluded": mock_orders_excluded,
            },
            "chapter9_improvements": {
                "product": ["补充更多商品详情信息，增加规格参数展示",
                            "优化卖点描述，突出差异化优势"],
                "presets": ["常见问题建议配置标准话术，减少重复回答",
                            "售后话术需要完善，提高用户满意度"],
                "operation": ["建议增加互动频次，每10分钟发起一次投票/抽奖",
                              "优化直播节奏，核心商品讲解控制在5-8分钟"],
                "content": ["增加商品对比环节，帮助用户做决策",
                            "追加用户评价展示，增强社交证明"],
            },
        }
        return report

    # ---- 问题排行 / 商品提及 / 时间分布 ----
    def _rank_questions(self, items: list[DanmakuItem], top_n: int = 10) -> list[dict]:
        """从弹幕中提取提问并按频次排行（同一问句先去标点归并）。"""
        buckets: dict[str, dict] = {}
        for d in items:
            if not d.isQuestion:
                continue
            key = _question_key(d.content)
            if not key:
                continue
            row = buckets.get(key)
            if row is None:
                row = {
                    "question": (d.content or "").strip()[:60],
                    "count": 0, "realCount": 0,
                    "category": classify_barrier(d.content),
                    "lastAskedAt": d.timestamp,
                }
                buckets[key] = row
            row["count"] += 1
            if is_real_user_id(d.userId):
                row["realCount"] += 1
            if (d.timestamp or "") > (row["lastAskedAt"] or ""):
                row["lastAskedAt"] = d.timestamp
        rows = sorted(buckets.values(),
                      key=lambda r: (-r["count"], -r["realCount"],
                                     r.get("lastAskedAt") or ""))
        titles = {c["type"]: c["title"] for c in _BARRIER_CATEGORIES}
        titles["other"] = "其他问题"
        for row in rows:
            row["categoryTitle"] = titles.get(row["category"], "其他问题")
            row["hasPreset"] = self._has_preset_for(row["question"])
        return rows[:top_n]

    def _rank_question_categories(self, items: list[DanmakuItem]) -> list[dict]:
        """按问题类型聚合，给出「哪类问题问得最多」。"""
        counts: dict[str, int] = {}
        for d in items:
            if not d.isQuestion:
                continue
            k = classify_barrier(d.content)
            counts[k] = counts.get(k, 0) + 1
        suggestions = {c["type"]: c["suggestion"] for c in _BARRIER_CATEGORIES}
        titles = {c["type"]: c["title"] for c in _BARRIER_CATEGORIES}
        titles["other"] = "其他问题"
        suggestions["other"] = "补充通用答疑话术，覆盖更多长尾问题"
        return [
            {"type": k, "title": titles.get(k, "其他问题"), "count": v,
             "suggestion": suggestions.get(k, "")}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    def _has_preset_for(self, question: str) -> bool:
        q = (question or "").lower()
        for p in self.presets.values():
            if p.isEnabled and any(kw.lower() in q for kw in p.keywords):
                return True
        return False

    @staticmethod
    def _count_product_mentions(items: list[DanmakuItem], product: Any,
                                product_name: str = "") -> int:
        keys: list[str] = []
        if product is not None:
            keys = [product.name] + list(product.sellingPoints) + list(product.tags)
        elif product_name:
            keys = [product_name]
        keys = [k for k in keys if k and len(k) >= 2]
        if not keys:
            return 0
        n = 0
        for d in items:
            text = (d.content or "").lower()
            if text and any(k.lower() in text for k in keys):
                n += 1
        return n

    @staticmethod
    def _compute_intent_score(real_dm: list[DanmakuItem],
                              real_msgs: list[PrivateMessageItem],
                              real_orders: list[OrderItem],
                              unique_real: int) -> float:
        """与实时面板同口径的意向指数（历史场次可复算）。"""
        if unique_real <= 0:
            return 0.0
        dm_rate = min(1.0, len(real_dm) / unique_real / 5)
        msg_rate = min(1.0, len({m.userId for m in real_msgs}) / unique_real)
        buyers = len({o.userId for o in real_orders
                      if o.status in ("paid", "shipped", "completed")})
        order_rate = min(1.0, buyers / unique_real)
        return round(100 * (0.4 * dm_rate + 0.3 * msg_rate + 0.3 * order_rate), 1)

    @staticmethod
    def _build_timeline(real_dm: list[DanmakuItem], start_time: str,
                        max_buckets: int = 48) -> list[dict]:
        """按 5 分钟分桶的弹幕时间分布（供报告折线图）。"""
        t0 = _parse_dt(start_time)
        if t0 is None:
            return []
        buckets: dict[int, dict] = {}
        for d in real_dm:
            t = _parse_dt(d.timestamp)
            if t is None:
                continue
            offset = int((t - t0).total_seconds())
            if offset < 0:
                continue
            idx = min(offset // 300, max_buckets - 1)
            b = buckets.setdefault(
                idx, {"count": 0, "questions": 0, "positive": 0, "negative": 0})
            b["count"] += 1
            if d.isQuestion:
                b["questions"] += 1
            s = d.sentiment or classify_sentiment(d.content)
            if s in ("positive", "negative"):
                b[s] += 1
        if not buckets:
            return []
        empty = {"count": 0, "questions": 0, "positive": 0, "negative": 0}
        return [
            {"slot": f"{i * 5}-{i * 5 + 5}分钟", "minute": i * 5,
             **buckets.get(i, empty)}
            for i in range(max(buckets) + 1)
        ]

    # ---- 报告存储 / 历史 / 重新生成 ----
    def _load_session_archive(self, session_id: str) -> Optional[dict]:
        sid = (session_id or "").strip()
        if not _SESSION_ID_RE.fullmatch(sid):
            return None
        with self._lock:
            data = self._load_json(
                os.path.join(danmaku_sessions_dir(), f"{sid}.json"), None)
        if not isinstance(data, dict) or not data.get("session"):
            return None
        items: list[DanmakuItem] = []
        for raw in data.get("danmaku") or []:
            try:
                items.append(DanmakuItem(**raw))
            except Exception:
                continue
        return {"session": data.get("session") or {}, "danmaku": items}

    @staticmethod
    def _new_report_id(session_id: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "session")[:48] or "session"
        return f"report_{base}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _report_summary(report: dict) -> dict:
        dd = report.get("danmakuDimension") or {}
        conv = report.get("chapter8_conversion") or {}
        inter = (report.get("chapter1_overview") or {}).get("interaction") or {}
        return {
            "reportId": report.get("reportId", ""),
            "sessionId": report.get("sessionId", ""),
            "sessionTitle": report.get("sessionTitle") or report.get("title", ""),
            "title": report.get("title", ""),
            "generateTime": report.get("generateTime", ""),
            "liveDuration": report.get("liveDuration", 0),
            "grade": report.get("grade", ""),
            "productName": report.get("productName", ""),
            "gmv": conv.get("realGMV", 0),
            "realDanmaku": dd.get("real", inter.get("totalDanmaku", 0)),
            "questionCount": dd.get("questionCount", 0),
            "schemaVersion": report.get("schemaVersion", 1),
            "source": report.get("source", ""),
        }

    def _save_report(self, report: dict) -> None:
        """报告独立落盘 + 更新索引；同一 reportId 只写一次，绝不覆盖旧文件。"""
        rid = report.get("reportId") or self._new_report_id(
            report.get("sessionId", ""))
        report["reportId"] = rid
        with self._lock:
            self._save_json(os.path.join(REPORTS_DIR, f"{rid}.json"), report)
            index_path = os.path.join(REPORTS_DIR, REPORTS_INDEX)
            index = self._load_json(index_path, [])
            if not isinstance(index, list):
                index = []
            index = [r for r in index
                     if isinstance(r, dict) and r.get("reportId") != rid]
            index.insert(0, self._report_summary(report))
            self._save_json(index_path, index)

    def get_report_history(self) -> list[dict]:
        """历史报告摘要列表（新→旧）。索引缺失时从报告文件重建。"""
        with self._lock:
            index_path = os.path.join(REPORTS_DIR, REPORTS_INDEX)
            index = self._load_json(index_path, [])
            if not isinstance(index, list):
                index = []
            known = {r.get("reportId") for r in index
                     if isinstance(r, dict) and r.get("reportId")}
            try:
                files = os.listdir(REPORTS_DIR)
            except OSError:
                files = []
            for fn in files:
                if not fn.endswith(".json") or fn == REPORTS_INDEX:
                    continue
                rid = fn[:-5]
                if rid in known:
                    continue
                data = self._load_json(os.path.join(REPORTS_DIR, fn), None)
                if isinstance(data, dict) and data.get("reportId"):
                    index.append(self._report_summary(data))
        seen: set = set()
        out: list[dict] = []
        for row in index:
            if not isinstance(row, dict):
                continue
            rid = row.get("reportId")
            if not rid or rid in seen:
                continue
            # 只列绑定场次的报告：本功能之前生成的旧报告没有 sessionId，
            # 无法对应到任何一场直播，混进历史列表只会让用户点开后对不上场次。
            if not row.get("sessionId"):
                continue
            seen.add(rid)
            out.append(row)
        out.sort(key=lambda r: str(r.get("generateTime", "")), reverse=True)
        return out

    def compare_sessions(self, session_ids: list[str]) -> dict:
        """两场（或多场）直播数据对比：复用报告数据集，不落盘。"""
        rows = []
        for sid in dict.fromkeys(str(x) for x in (session_ids or []) if str(x)):
            try:
                report = self.generate_report(save=False, session_id=sid)
            except Exception as e:  # noqa: BLE001
                rows.append({"sessionId": sid, "error": str(e)})
                continue
            if report.get("success") is False:
                rows.append({"sessionId": sid,
                             "error": report.get("message") or "报告生成失败"})
                continue
            pd = report.get("productDimension") or {}
            dd = report.get("danmakuDimension") or {}
            rs = report.get("refundSummary") or {}
            ns = report.get("negativeReviewSummary") or {}
            rows.append({
                "sessionId": sid,
                "title": report.get("sessionTitle"),
                "startTime": (report.get("scope") or {}).get("startTime"),
                "duration": report.get("liveDuration"),
                "durationStr": report.get("liveDurationStr") or "",
                "grade": report.get("grade") or "",
                "gmv": pd.get("gmv", 0),
                "paidCount": pd.get("paidCount", 0),
                "orderCount": pd.get("orderCount", 0),
                "buyerCount": pd.get("buyerCount", 0),
                "conversionRate": pd.get("conversionRate", 0),
                "realDanmaku": dd.get("real", 0),
                "questionCount": dd.get("questionCount", 0),
                "replyRate": dd.get("replyRate", 0),
                "refundCount": rs.get("count", 0),
                "refundAmount": rs.get("amount", 0),
                "negativeReviews": ns.get("count", 0),
                "stageComparison": report.get("stageComparison") or [],
            })
        return {"sessions": rows}

    def get_report(self, report_id: str) -> Optional[dict]:
        """按 ID 读取一份历史报告（路径白名单校验，防路径穿越）。"""
        rid = (report_id or "").strip()
        if not _REPORT_ID_RE.fullmatch(rid):
            return None
        with self._lock:
            data = self._load_json(os.path.join(REPORTS_DIR, f"{rid}.json"), None)
        return data if isinstance(data, dict) else None

    def regenerate_report(self, session_id: str) -> dict:
        """基于场次归档重新生成报告：新增一份，不覆盖原报告。"""
        sid = (session_id or "").strip()
        if not sid:
            return {"success": False, "message": "缺少场次ID"}
        report = self.generate_report(session_id=sid, save=True, regenerated=True)
        if report.get("success") is False:
            return report
        return {"success": True, "report": report}

    def get_session_high_freq_questions(self, session_id: str = "",
                                        top_n: int = 10) -> dict:
        """某一场的高频问题排行（本场 / 历史任意场次）。

        只统计真实观众提问（项目既有口径）；模拟/虚拟弹幕数量在 scope 中披露。
        当前场次读内存缓冲，历史场次读场次归档文件 —— 两者用同一套提取与归并规则。
        """
        try:
            top_n = max(1, min(50, int(top_n)))
        except (TypeError, ValueError):
            top_n = 10
        sid = (session_id or "").strip()
        source = "history"
        title = ""
        if sid in ("", "current", "__current__"):
            source = "current"
            with self._lock:
                sid = self.live_state.sessionId or ""
                items = list(self.current_session_danmaku)
                if not items and sid:
                    items = [d for d in self.danmaku_list if d.sessionId == sid]
                product = self.get_current_product()
                product_name = product.name if product else "未上架商品"
                start_label = (self.live_state.startTime or "")[:16].replace("T", " ")
                title = (f"{product_name} · {start_label}"
                         if start_label else product_name)
            if not items and sid:
                archive = self._load_session_archive(sid)
                if archive is not None:
                    items = archive["danmaku"]
                    title = str(archive["session"].get("title") or title)
                    source = "history"
        else:
            archive = self._load_session_archive(sid)
            if archive is None:
                return {"success": False, "message": "场次不存在或尚未归档"}
            items = archive["danmaku"]
            title = str(archive["session"].get("title") or sid)
        real_dm = [d for d in items if is_real_user_id(d.userId)]
        return {
            "success": True,
            "sessionId": sid,
            "sessionTitle": title,
            "source": source,
            "total": len(items),
            "real": len(real_dm),
            "excludedVirtual": sum(1 for d in items
                                   if classify_user_id(d.userId) == "virtual"),
            "excludedMock": sum(1 for d in items
                                if classify_user_id(d.userId) == "mock"),
            "questionCount": sum(1 for d in real_dm if d.isQuestion),
            "items": self._rank_questions(real_dm, top_n),
            "categories": self._rank_question_categories(real_dm),
        }

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
            # 订单只保留真实注册观众的单，模拟/测试订单一律清理
            for oid in [
                oid for oid, o in self.orders.items()
                if not order_is_real(o)
            ]:
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
                # 双保险：来源必须是观众主动提交（source=user），且账号必须是
                # 真人观众。只有 source 一条判据时，将来若有别的写入路径漏打
                # source，模拟评价就会混进总结；演示账号也要一并排除。
                if r.source == "user" and is_human_user_id(r.userId):
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
