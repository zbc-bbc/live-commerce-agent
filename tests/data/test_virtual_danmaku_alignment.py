"""虚拟弹幕必须与当前讲解商品对得上。

这些测试锁住三条容易回归的口径：
1. 自动弹幕里出现的是当前商品的名字 / 真实参数，而不是通用问句；
2. 品类问法不串味（吹风机不会被问「怎么吃」）；
3. 规格回复不被商品名里的规格词误触发，且精确规格键优先于同义词。
"""

import random
from pathlib import Path

from data.live_commerce.data_manager import (
    LiveCommerceDataManager,
    build_virtual_danmaku_content,
    classify_product_family,
    match_product_spec_reply,
)
from schema.live_commerce_schema import ProductItem

QUESTION_TYPES = ("price", "spec", "usage", "stock", "compare", "purchase_intent")


def make_product(**overrides) -> ProductItem:
    data = {
        "productId": "P-test",
        "name": "测试商品",
        "originalPrice": 100.0,
        "price": 100.0,
        "stock": 10,
        "category": "家居日用",
    }
    data.update(overrides)
    return ProductItem(**data)


def test_category_families_cover_live_commerce_categories():
    assert classify_product_family("食品茶饮") == "food"
    assert classify_product_family("个护电器") == "tech"
    assert classify_product_family("美妆护肤") == "beauty"
    assert classify_product_family("家居日用") == "home"
    assert classify_product_family("") == "general"


def test_every_generated_danmaku_names_the_current_product():
    product = make_product(name="智能温控吹风机 Pro", category="个护电器")
    for question_type in QUESTION_TYPES:
        for seed in range(10):
            content = build_virtual_danmaku_content(
                product, question_type, random.Random(seed)
            )
            assert "智能温控吹风机" in content


def test_product_switch_changes_next_danmaku():
    first = make_product(name="玻尿酸水光精华液 30ml", category="美妆护肤")
    second = make_product(name="智能温控吹风机 Pro", category="个护电器")
    first_texts = {
        build_virtual_danmaku_content(first, "usage", random.Random(seed))
        for seed in range(10)
    }
    second_texts = {
        build_virtual_danmaku_content(second, "usage", random.Random(seed))
        for seed in range(10)
    }
    assert all("玻尿酸水光精华液" in text for text in first_texts)
    assert all("智能温控吹风机" in text for text in second_texts)
    assert not (first_texts & second_texts)


def test_tech_products_are_never_asked_food_questions():
    product = make_product(name="智能温控吹风机 Pro", category="个护电器")
    for question_type in QUESTION_TYPES:
        for seed in range(20):
            content = build_virtual_danmaku_content(
                product, question_type, random.Random(seed)
            )
            for forbidden in ("怎么吃", "好吃", "一斤", "开袋即食", "冲泡"):
                assert forbidden not in content


def test_tea_and_sausage_get_their_own_usage_questions():
    tea = make_product(name="有机乌龙茶礼盒 250g", category="食品茶饮")
    sausage = make_product(name="低脂鸡胸肉肠 20根装", category="健康食品")
    tea_texts = {
        build_virtual_danmaku_content(tea, "usage", random.Random(seed))
        for seed in range(30)
    }
    sausage_texts = {
        build_virtual_danmaku_content(sausage, "usage", random.Random(seed))
        for seed in range(30)
    }
    assert all("开袋即食" not in text for text in tea_texts)
    assert any("冲泡" in text for text in tea_texts)
    assert all("冲泡" not in text for text in sausage_texts)


def test_spec_key_inside_product_name_does_not_hijack_price_question():
    """「北欧风台灯-三色温调光」名称里含「色温」，不能把价格问题答成色温。"""
    lamp = make_product(
        name="北欧风台灯-三色温调光",
        category="家居日用",
        price=179.0,
        originalPrice=233.0,
        specs=["色温: 3000K-6000K", "功率: 12W"],
    )
    assert match_product_spec_reply("这个北欧风台灯-三色温调光现在多少钱？", lamp) is None
    spec_reply = match_product_spec_reply(
        "这个北欧风台灯-三色温调光的色温是多少？", lamp
    )
    assert spec_reply is not None and "3000K-6000K" in spec_reply


def test_exact_spec_key_beats_alias_from_another_value():
    """问「外套」不能因为参数值里出现「面料」而答成材质。"""
    pillow = make_product(
        name="天然乳胶枕头 护颈款",
        category="家居日用",
        specs=["材质: 93%天然乳胶", "外套: 天丝面料可拆洗"],
    )
    reply = match_product_spec_reply(
        "这款天然乳胶枕头的外套是天丝面料可拆洗的吗？", pillow
    )
    assert reply is not None and "天丝面料可拆洗" in reply


def test_rule_reply_stays_on_the_current_product():
    manager = LiveCommerceDataManager.__new__(LiveCommerceDataManager)
    hair_dryer = make_product(
        name="智能温控吹风机 Pro",
        category="个护电器",
        price=599.0,
        originalPrice=779.0,
        specs=["功率: 1800W", "重量: 520g"],
    )
    price_reply = manager._generate_smart_reply(
        "这个智能温控吹风机现在多少钱？", hair_dryer
    )
    assert "智能温控吹风机" in price_reply and "599" in price_reply
    spec_reply = manager._generate_smart_reply(
        "这个智能温控吹风机的功率是多少？", hair_dryer
    )
    assert "1800W" in spec_reply


def test_no_discount_product_does_not_claim_a_discount():
    manager = LiveCommerceDataManager.__new__(LiveCommerceDataManager)
    bag = make_product(
        name="极简都市双肩包 20L",
        category="箱包配饰",
        price=259.0,
        originalPrice=259.0,
    )
    reply = manager._generate_smart_reply("这个极简都市双肩包比平时便宜吗？", bag)
    assert "和日常价一致" in reply
    assert "原价259元" not in reply


def test_demo_products_all_generate_product_anchored_danmaku():
    products_file = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "data"
        / "live_commerce"
        / "data"
        / "products_data.json"
    )
    import json

    products = [
        ProductItem(**item)
        for item in json.loads(products_file.read_text(encoding="utf-8"))[:12]
    ]
    for product in products:
        for question_type in QUESTION_TYPES:
            for seed in range(3):
                content = build_virtual_danmaku_content(
                    product, question_type, random.Random(seed)
                )
                assert product.name.split()[0] in content
