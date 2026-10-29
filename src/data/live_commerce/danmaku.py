"""
模拟弹幕问题库（50+条）
覆盖：价格咨询、规格/功效、售后、适用人群、优惠计算、异常问题
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Danmaku:
    """一条模拟弹幕"""
    id: str
    text: str
    target_product_id: str | None  # None = 通用问题
    category: str  # price / spec / after_sale / audience / promotion_calc / objection / anomaly


# 50+ 条弹幕
DANMAKU_QUESTIONS: list[Danmaku] = [
    # ---- 价格咨询 (8条) ----
    Danmaku("D001", "这个精华液多少钱一瓶？", "P001", "price"),
    Danmaku("D002", "吹风机打折吗？现在买多少钱？", "P002", "price"),
    Danmaku("D003", "乌龙茶礼盒价格有点贵，能便宜点吗？", "P003", "price"),
    Danmaku("D004", "靠垫什么价格？有优惠吗？", "P004", "price"),
    Danmaku("D005", "耳机899太贵了吧，同款别家才700多", "P005", "price"),
    Danmaku("D006", "益生菌一盒多少钱？买得多有优惠不？", "P006", "price"),
    Danmaku("D007", "双肩包259？能再少点吗主播？", "P007", "price"),
    Danmaku("D008", "花胶羹6碗138，平均一碗23块？", "P008", "price"),

    # ---- 规格/功效 (10条) ----
    Danmaku("D009", "精华液适合油皮吗？会油吗？", "P001", "spec"),
    Danmaku("D010", "吹风机噪音大不大？宿舍用会不会吵？", "P002", "spec"),
    Danmaku("D011", "乌龙茶有没有农残检测报告？", "P003", "spec"),
    Danmaku("D012", "靠垫能用在车上吗？开车时能用吗？", "P004", "spec"),
    Danmaku("D013", "耳机戴着会夹头吗？我头比较大", "P005", "spec"),
    Danmaku("D014", "益生菌需要冷藏保存吗？", "P006", "spec"),
    Danmaku("D015", "双肩包放得下15.6寸游戏本吗？", "P007", "spec"),
    Danmaku("D016", "花胶羹腥不腥？怕腥的人能喝吗？", "P008", "spec"),
    Danmaku("D017", "翻译笔支持日语吗？我下学期去日本留学", "P009", "spec"),
    Danmaku("D018", "乳胶枕头有味道吗？晒晒能散吗？", "P010", "spec"),

    # ---- 售后 (6条) ----
    Danmaku("D019", "精华液用了过敏怎么退？", "P001", "after_sale"),
    Danmaku("D020", "吹风机保修多久？坏了找谁？", "P002", "after_sale"),
    Danmaku("D021", "耳机如果降噪有问题能换吗？", "P005", "after_sale"),
    Danmaku("D022", "鸡胸肉肠发到海南会不会坏？", "P011", "after_sale"),
    Danmaku("D023", "台灯收到不亮怎么办？", "P012", "after_sale"),
    Danmaku("D024", "双肩包拉链坏了能修吗？", "P007", "after_sale"),

    # ---- 适用人群 (6条) ----
    Danmaku("D025", "孕妇可以用这个精华液吗？", "P001", "audience"),
    Danmaku("D026", "鸡胸肉肠小孩能吃吗？", "P011", "audience"),
    Danmaku("D027", "益生菌多大的宝宝可以吃？", "P006", "audience"),
    Danmaku("D028", "乳胶枕头颈椎不好的人适合吗？", "P010", "audience"),
    Danmaku("D029", "花胶羹男生能吃吗？还是女生吃比较好？", "P008", "audience"),
    Danmaku("D030", "翻译笔适合老年人用吗？操作简单吗？", "P009", "audience"),

    # ---- 优惠计算 (8条) ----
    Danmaku("D031", "精华液买两瓶总共多少钱？帮我算算", "P001", "promotion_calc"),
    Danmaku("D032", "吹风机直播间价499，还能领券吗？", "P002", "promotion_calc"),
    Danmaku("D033", "乌龙茶买一送一，那我买2盒是得4盒吗？", "P003", "promotion_calc"),
    Danmaku("D034", "靠垫我买三个的话每个多少钱？", "P004", "promotion_calc"),
    Danmaku("D035", "耳机现在买799，明天会恢复原价吗？", "P005", "promotion_calc"),
    Danmaku("D036", "益生菌买3盒减60，那我买4盒呢？还减60吗？", "P006", "promotion_calc"),
    Danmaku("D037", "鸡胸肉肠买3送1，我买6袋得多少袋？", "P011", "promotion_calc"),
    Danmaku("D038", "乳胶枕头买一对399，比我单买两个省多少？", "P010", "promotion_calc"),

    # ---- 异议处理 (7条) ----
    Danmaku("D039", "你这个精华液和xx大牌比怎么样？人家才贵几十", "P001", "objection"),
    Danmaku("D040", "吹风机是杂牌吧？没听过，不敢买", "P002", "objection"),
    Danmaku("D041", "耳机评价说底噪明显，是不是品控不行？", "P005", "objection"),
    Danmaku("D042", "益生菌吃了真的有效果吗？感觉是智商税", "P006", "objection"),
    Danmaku("D043", "网上都说你这个乳胶枕是合成胶，真的天然吗？", "P010", "objection"),
    Danmaku("D044", "花胶羹一碗就这么点，性价比不高啊", "P008", "objection"),
    Danmaku("D045", "鸡胸肉肠和超市卖的一样，为什么要在这买？", "P011", "objection"),

    # ---- 异常/刁钻问题 (7条) ----
    Danmaku("D046", "我全都要能打几折？", None, "anomaly"),
    Danmaku("D047", "主播你能用日语介绍一下翻译笔吗？", "P009", "anomaly"),
    Danmaku("D048", "你说了这么多，敢不敢先送一波福利？", None, "anomaly"),
    Danmaku("D049", "这个茶喝了能减肥吗？", "P003", "anomaly"),
    Danmaku("D050", "耳机能不能连两台手机？我要同时用", "P005", "anomaly"),
    Danmaku("D051", "我住国外能发货吗？运费多少？", None, "anomaly"),
    Danmaku("D052", "你推荐的这个好像不如我自己选的，你行不行", None, "anomaly"),

    # ---- 额外通用弹幕 ----
    Danmaku("D053", "今天有什么秒杀商品吗？", None, "price"),
    Danmaku("D054", "主播皮肤好好，用的什么护肤品？", "P001", "spec"),
    Danmaku("D055", "第一次来直播间，有什么推荐？", None, "spec"),
    Danmaku("D056", "全场满减吗？有没有跨店活动？", None, "promotion_calc"),
    Danmaku("D057", "下单后多久发货？", None, "after_sale"),
    Danmaku("D058", "这个品牌是正品吗？有授权吗？", "P005", "objection"),
    Danmaku("D059", "我上次买了个便宜的，没用几次就坏了", "P005", "objection"),
    Danmaku("D060", "主播男的女的？说话很好听", None, "anomaly"),
]


def get_danmaku_by_category(category: str) -> list[Danmaku]:
    """按分类筛选弹幕"""
    return [d for d in DANMAKU_QUESTIONS if d.category == category]


def get_danmaku_for_product(product_id: str) -> list[Danmaku]:
    """获取特定商品相关的弹幕"""
    return [d for d in DANMAKU_QUESTIONS if d.target_product_id == product_id]
