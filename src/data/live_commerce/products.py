"""
直播电商商品资料库
包含 12 个模拟商品：名称、卖点、价格、优惠规则、库存和注意事项。

`sold_base`（展示用销量基数）的取值口径：模拟单停用前，观众端商品卡读的是
`salesQty` = 该商品**全部订单**（含模拟）的成交件数。为了让改造后观众看到的
「已售 N 件」一个数字都不跳，`sold_base` 取的是：「改造前含模拟件的成交件数」
减去「真实观众成交件数」的差值，即模拟单恰好贡献的那部分。
这个值只在观众端展示，运营台与复盘报告一律读 `realSalesQty`（纯真实），不受影响。
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Promotion:
    """优惠规则"""
    rule_id: str
    description: str  # 面向消费者的描述
    logic: str  # 计算逻辑描述，供 tool 解析
    valid_from: str
    valid_to: str


@dataclass
class Product:
    """商品资料"""
    id: str
    name: str
    category: str
    price: float  # 原价
    selling_points: list[str]
    description: str
    specs: dict[str, str]  # 规格参数
    stock: int
    promotions: list[Promotion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 注意事项（如禁忌、限购）
    image_url: str = ""
    # 展示用销量基数：只叠加到观众端商品卡的「已售 N 件」，**不参与任何经营统计**。
    # 模拟单停用后（成交只能来自真实注册观众），若不给自己一个基数，12 件商品会
    # 集体显示「已售 0 件」。取值见下方注释，与改造前的观众端显示保持一致。
    sold_base: int = 0


# ============================================================
# 12 个商品
# ============================================================

PRODUCT_01 = Product(
    id="P001",
    name="玻尿酸水光精华液 30ml",
    category="美妆护肤",
    price=299.00,
    selling_points=[
        "三重玻尿酸分子，深层补水锁水",
        "德国进口原料，敏感肌可用",
        "清爽不黏腻，妆前打底必备",
    ],
    description="采用大分子+中分子+小分子三重玻尿酸，由内到外层层补水。特别添加神经酰胺修复肌肤屏障，适合所有肤质。",
    specs={"容量": "30ml", "保质期": "3年", "产地": "中国广州", "适合肤质": "所有肤质"},
    stock=5000,
    sold_base=27,
    promotions=[
        Promotion(
            rule_id="PROMO_A",
            description="买2瓶立减50元，相当于每瓶仅274元",
            logic="买2件总价=原价*2-50",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
        Promotion(
            rule_id="PROMO_B",
            description="买3瓶送旅行装5ml小样，赠品价值99元",
            logic="买3件总价=原价*3，赠送旅行装",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["每人限购5瓶", "孕妇请咨询医生后使用", "如出现过敏请立即停用"],
)

PRODUCT_02 = Product(
    id="P002",
    name="智能温控吹风机 Pro",
    category="个护电器",
    price=599.00,
    selling_points=[
        "智能温控每秒检测100次，不伤发质",
        "11万转无刷马达，强劲速干",
        "负离子护发，减少毛躁",
    ],
    description="高端智能吹风机，搭载AI温控芯片，实时监测出风口温度。配备磁吸式风嘴，轻松打造各种造型。",
    specs={"功率": "1800W", "噪音": "≤65dB", "重量": "520g", "线长": "1.8m"},
    stock=1200,
    sold_base=65,
    promotions=[
        Promotion(
            rule_id="PROMO_C",
            description="直播间专享价直降100元，到手仅499元",
            logic="单件直降100元，售价=原价-100",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["电压220V，海外使用需变压器", "保修期1年，注册延保半年"],
)

PRODUCT_03 = Product(
    id="P003",
    name="有机乌龙茶礼盒 250g",
    category="食品茶饮",
    price=168.00,
    selling_points=[
        "福建安溪有机认证茶园直供",
        "传统炭焙工艺，回甘持久",
        "精美礼盒包装，送礼佳选",
    ],
    description="精选安溪高海拔有机铁观音，经资深制茶师传统炭焙工艺精制。茶汤金黄明亮，兰香浓郁，七泡有余香。",
    specs={"净含量": "250g（两罐装）", "保质期": "18个月", "储藏方式": "阴凉干燥处"},
    stock=3000,
    sold_base=42,
    promotions=[
        Promotion(
            rule_id="PROMO_D",
            description="买一送一（同款），相当于每盒仅84元",
            logic="买1件送1件，售价=原价，得2盒",
            valid_from="2026-07-01",
            valid_to="2026-07-10",
        ),
    ],
    notes=["食品类非质量问题不支持退换", "孕妇可适量饮用", "建议用100°C沸水冲泡"],
)

PRODUCT_04 = Product(
    id="P004",
    name="人体工学护腰靠垫",
    category="家居日用",
    price=198.00,
    selling_points=[
        "分区支撑设计，贴合腰椎曲线",
        "高弹记忆棉，久坐不塌陷",
        "透气网布面料，四季通用",
    ],
    description="专为久坐人群设计的护腰靠垫，采用三区支撑结构，有效缓解腰椎压力。背部弹力带可固定于各种椅背。",
    specs={"尺寸": "40×35×12cm", "材质": "记忆棉+透气网布", "承重": "不限"},
    stock=2500,
    sold_base=32,
    promotions=[
        Promotion(
            rule_id="PROMO_E",
            description="两件9折，三件8折",
            logic="买2件每件9折，买3件及以上每件8折",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["建议手洗外套，不可漂白", "记忆棉开封后48小时恢复原状"],
)

PRODUCT_05 = Product(
    id="P005",
    name="无线降噪头戴耳机 H5",
    category="数码3C",
    price=899.00,
    selling_points=[
        "自适应主动降噪，最高-40dB",
        "Hi-Res认证音质，支持LDAC",
        "60小时超长续航，快充10分钟用5小时",
    ],
    description="旗舰级无线降噪耳机，自研降噪芯片实时适配环境。40mm镀钛振膜单元，呈现丰富细节。折叠便携设计。",
    specs={"类型": "头戴式", "蓝牙": "5.3", "续航": "60小时（降噪关）", "重量": "250g"},
    stock=800,
    sold_base=42,
    promotions=[
        Promotion(
            rule_id="PROMO_F",
            description="直播间下单送收纳盒+音频线（价值128元礼包）",
            logic="售价=原价，赠送配件礼包",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
        Promotion(
            rule_id="PROMO_G",
            description="限时24小时秒杀价799元",
            logic="单件秒杀价=原价-100",
            valid_from="2026-07-05",
            valid_to="2026-07-06",
        ),
    ],
    notes=["头梁可调节，适合不同头型", "防水等级IPX4（防溅水）", "Type-C充电口"],
)

PRODUCT_06 = Product(
    id="P006",
    name="益生菌固体饮料 30条/盒",
    category="健康食品",
    price=128.00,
    selling_points=[
        "6种进口益生菌+2种益生元",
        "活菌添加量≥200亿CFU/条",
        "常温保存，无需冷藏",
    ],
    description="科学配比6株优质益生菌，特别添加低聚果糖和水苏糖两种益生元。每条独立包装，可直接口服或加入温水。",
    specs={"规格": "2g×30条/盒", "活菌量": "≥200亿CFU/条", "保质期": "24个月"},
    stock=4500,
    sold_base=62,
    promotions=[
        Promotion(
            rule_id="PROMO_H",
            description="买2盒减30元，买3盒减60元",
            logic="买2件总价=原价*2-30，买3件及以上总价=原价*数量-60",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["建议不超过45°C温水冲服", "避免与抗生素同时服用", "孕妇及婴幼儿请遵医嘱"],
)

PRODUCT_07 = Product(
    id="P007",
    name="极简都市双肩包 20L",
    category="箱包配饰",
    price=259.00,
    selling_points=[
        "防泼水面料，雨天出行无忧",
        "多隔层收纳，电脑仓可放15.6寸笔记本",
        "背部透气海绵垫，夏天不闷热",
    ],
    description="都市通勤极简双肩包，主仓大开口设计方便取物。前袋配有钥匙挂扣和笔插位，侧面水壶袋。",
    specs={"容量": "20L", "重量": "420g", "材质": "涤纶+PU涂层", "尺寸": "45×30×15cm"},
    stock=1800,
    sold_base=60,
    promotions=[],
    notes=["不可机洗，建议湿布擦拭", "最大承重8kg"],
)

PRODUCT_08 = Product(
    id="P008",
    name="即食花胶羹 6碗装",
    category="食品茶饮",
    price=138.00,
    selling_points=[
        "精选缅甸花胶，胶原蛋白满满",
        "搭配红枣枸杞桂圆，古法炖制",
        "开碗即食，方便快捷",
    ],
    description="传统滋补花胶羹，每碗含3g干花胶。零添加防腐剂，高温灭菌真空封装。适合作为下午茶或宵夜。",
    specs={"规格": "6碗/箱（180g/碗）", "保质期": "12个月", "储藏方式": "阴凉干燥"},
    stock=2000,
    sold_base=40,
    promotions=[
        Promotion(
            rule_id="PROMO_I",
            description="早餐时段下单立减20元，实付118元",
            logic="单件售价=原价-20（限7:00-10:00）",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["孕妇建议咨询医生", "对鱼类过敏者请勿食用", "开碗后请尽快食用"],
)

PRODUCT_09 = Product(
    id="P009",
    name="AI智能翻译笔 T1",
    category="数码3C",
    price=399.00,
    selling_points=[
        "支持126种语言翻译，中英互译准确率99%",
        "离线也能用，内置16国语种离线包",
        "拍照翻译功能，菜单路牌一扫即译",
    ],
    description="便携AI翻译笔，搭载自研神经网络翻译引擎。2.0寸触摸屏，支持语音、文字、拍照三种输入方式。",
    specs={"屏幕": "2.0寸触摸屏", "联网": "WiFi/离线", "电池": "1500mAh", "重量": "85g"},
    stock=1500,
    sold_base=49,
    promotions=[],
    notes=["首次使用需联网激活", "离线包需提前下载", "不支持SIM卡"],
)

PRODUCT_10 = Product(
    id="P010",
    name="天然乳胶枕头 护颈款",
    category="家居日用",
    price=229.00,
    selling_points=[
        "93%天然乳胶含量，泰国进口原料",
        "波浪形人体工学设计，贴合颈椎",
        "蜂窝透气孔，防螨抑菌",
    ],
    description="采用泰国天然乳胶原料，邓禄普工艺一体成型。波浪形双高度设计，侧睡仰睡都舒适。",
    specs={"尺寸": "60×40×12/10cm", "材质": "93%天然乳胶", "外套": "天丝面料可拆洗"},
    stock=3500,
    sold_base=32,
    promotions=[
        Promotion(
            rule_id="PROMO_J",
            description="买一对（2个）特惠价399元，省59元",
            logic="买2件总价=399",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["乳胶枕不可暴晒", "少数人初期使用可能不适应高度变化", "建议搭配枕套使用延长寿命"],
)

PRODUCT_11 = Product(
    id="P011",
    name="低脂鸡胸肉肠 20根装",
    category="健康食品",
    price=45.00,
    selling_points=[
        "鸡胸肉含量≥85%，高蛋白低脂肪",
        "0淀粉添加，健身减脂人群首选",
        "独立包装，开袋即食",
    ],
    description="精选去皮鸡胸肉，经72小时低温腌制工艺锁住鲜美。每100g仅含1.2g脂肪，蛋白质含量28g。",
    specs={"规格": "20根/袋（30g/根）", "保质期": "9个月", "储藏方式": "常温避光"},
    stock=8000,
    sold_base=51,
    promotions=[
        Promotion(
            rule_id="PROMO_K",
            description="买3袋送1袋，实付3袋得4袋",
            logic="买3件送1件，总价=原价*3，得4袋",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["开封后未食用完请冷藏", "对鸡肉过敏者禁用"],
)

PRODUCT_12 = Product(
    id="P012",
    name="北欧风台灯-三色温调光",
    category="家居日用",
    price=179.00,
    selling_points=[
        "三档色温调节：暖黄/暖白/冷白",
        "无级亮度调节，触控操作",
        "无蓝光危害，护眼认证",
    ],
    description="简约北欧设计，灯杆可多角度调节。内置36颗高显指LED灯珠，Ra≥95还原真实色彩。USB充电口设计。",
    specs={"功率": "12W", "色温": "3000K-6000K", "材质": "铝合金+ABS", "寿命": "约50000小时"},
    stock=2200,
    sold_base=9,
    promotions=[
        Promotion(
            rule_id="PROMO_L",
            description="今晚下单即赠USB充电插头（价值29元）",
            logic="售价=原价，赠送USB充电头",
            valid_from="2026-07-01",
            valid_to="2026-07-31",
        ),
    ],
    notes=["请使用5V/2A以上充电头", "灯杆关节处勿过度用力弯折"],
)


ALL_PRODUCTS: list[Product] = [
    PRODUCT_01, PRODUCT_02, PRODUCT_03, PRODUCT_04,
    PRODUCT_05, PRODUCT_06, PRODUCT_07, PRODUCT_08,
    PRODUCT_09, PRODUCT_10, PRODUCT_11, PRODUCT_12,
]

PRODUCT_MAP: dict[str, Product] = {p.id: p for p in ALL_PRODUCTS}


def get_product(product_id: str) -> Product | None:
    """通过商品ID查找商品"""
    return PRODUCT_MAP.get(product_id)


def search_products(query: str) -> list[Product]:
    """根据关键词搜索商品（名称、描述、卖点）"""
    q = query.lower()
    results = []
    for p in ALL_PRODUCTS:
        if q in p.name.lower() or q in p.description.lower():
            results.append(p)
            continue
        for sp in p.selling_points:
            if q in sp.lower():
                results.append(p)
                break
        if q in p.category.lower():
            if p not in results:
                results.append(p)
    return results


def list_categories() -> dict[str, list[Product]]:
    """按分类列出所有商品"""
    cats: dict[str, list[Product]] = {}
    for p in ALL_PRODUCTS:
        cats.setdefault(p.category, []).append(p)
    return cats
