"""直播间商品挂载 / 推送 / 基础下单 / 本场统计的数据层测试。

这些测试直接构造一个独立的 ``LiveCommerceDataManager``，并把所有落盘路径
指向 pytest 的临时目录，避免污染本地的演示数据。
"""

from datetime import datetime

import pytest

from data.live_commerce import data_manager as dm_mod


@pytest.fixture
def dm(tmp_path, monkeypatch):
    """独立数据管理器：所有 JSON/SQLite 落盘重定向到 tmp_path。"""
    for name, filename in {
        "PRODUCTS_FILE": "products.json",
        "DANMAKU_FILE": "danmaku.json",
        "SETTINGS_FILE": "settings.json",
        "BLACKLIST_FILE": "blacklist.json",
        "PRESETS_FILE": "presets.json",
        "REVIEWS_FILE": "reviews.json",
        "SESSIONS_FILE": "sessions.json",
        "PRIVATE_MSGS_FILE": "private_msgs.json",
        "QUICK_REPLIES_FILE": "quick_replies.json",
        "ORDERS_FILE": "orders.json",
        "VIEWERS_FILE": "viewers.json",
        "ALERTS_FILE": "alerts.json",
    }.items():
        monkeypatch.setattr(dm_mod, name, str(tmp_path / filename))
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(dm_mod, "REPORTS_DIR", str(reports))

    manager = dm_mod.LiveCommerceDataManager()
    manager._stop_timers()
    yield manager
    manager._stop_timers()


def go_live(dm, product_id="P001"):
    """把管理器置为直播中（不启动后台定时器，避免测试间互相干扰）。"""
    dm.mount_live_product(product_id)
    dm.set_current_product(product_id)
    dm.live_state.status = "live"
    dm.live_state.startTime = datetime.now().isoformat()
    dm.live_state.liveDuration = 0


def test_mount_and_unmount_product(dm):
    events = []
    dm.register_ws_callback(events.append)

    # 默认 P002 已上架，先撤下，验证挂载/撤下两个方向
    product, reason = dm.unmount_live_product("P002")
    assert reason == ""
    assert product is not None and product.isOnSale is False
    assert "P002" not in {p.productId for p in dm.get_mounted_products()}
    assert any(e["type"] == "live_product_unmounted" and e["data"]["productId"] == "P002"
               for e in events)

    events.clear()
    product, reason = dm.mount_live_product("P002")
    assert reason == ""
    assert product.isOnSale is True
    assert "P002" in {p.productId for p in dm.get_mounted_products()}
    assert any(e["type"] == "live_product_mounted"
               and e["data"]["product"]["productId"] == "P002" for e in events)
    assert any(e["type"] == "product_updated"
               and e["data"] == {"productId": "P002", "isOnSale": True} for e in events)

    # 挂载幂等：重复挂载不报错
    again, reason = dm.mount_live_product("P002")
    assert again is not None and reason == ""

    # 不存在的商品
    missing, reason = dm.mount_live_product("P999")
    assert missing is None and reason == "商品不存在"


def test_push_product_card_requires_mounted_product(dm):
    events = []
    dm.register_ws_callback(events.append)

    dm.mount_live_product("P003")
    events.clear()
    card, reason = dm.push_product_card("P003")
    assert reason == ""
    assert card["productId"] == "P003"
    assert card["name"]
    assert "salesQty" in card and "pushId" in card and card["pushedAt"]
    pushed = [e for e in events if e["type"] == "product_card_push"]
    assert len(pushed) == 1
    assert pushed[0]["data"]["productId"] == "P003"

    dm.unmount_live_product("P003")
    card, reason = dm.push_product_card("P003")
    assert card is None
    assert "未挂载" in reason


def test_product_click_stats_are_per_session(dm):
    # 未开播：点击不计入本场统计
    result = dm.record_product_click("P001", "v_test01")
    assert result["success"] is True and result["counted"] is False

    go_live(dm)
    for _ in range(3):
        result = dm.record_product_click("P001", "v_test01")
        assert result["success"] is True and result["counted"] is True

    stats = dm.get_live_room_stats()
    assert stats["clickCount"] == 3
    assert stats["clickViewerCount"] == 1
    row = next(r for r in stats["productStats"] if r["productId"] == "P001")
    assert row["clicks"] == 3 and row["mounted"] is True

    # 新开一场：点击归零（start_live 只划会话边界，挂载不受影响）
    started = dm.start_live()
    assert started["success"] is True
    dm._stop_timers()
    assert dm.get_live_room_stats()["clickCount"] == 0


def test_order_creation_and_stats(dm):
    go_live(dm)
    dm.get_or_create_viewer("v_test02", "测试观众")

    order, reason = dm.create_viewer_order("P001", "v_test02", "测试观众", 2)
    assert reason == ""
    assert order is not None
    assert order.status == "pending_pay"
    assert order.isVirtual is False
    assert order.totalAmount == pytest.approx(2 * order.unitPrice)

    stats = dm.get_live_room_stats()
    assert stats["orderCount"] == 1
    assert stats["pendingPayCount"] == 1
    row = next(r for r in stats["productStats"] if r["productId"] == "P001")
    assert row["orderCount"] == 1 and row["paidCount"] == 0

    paid, reason = dm.pay_order(order.orderId, "v_test02")
    assert reason == "" and paid.status == "paid"
    stats = dm.get_live_room_stats()
    assert stats["paidOrderCount"] == 1
    assert stats["paidQty"] == 2
    assert stats["gmv"] == pytest.approx(paid.totalAmount)
    assert stats["clickToOrderRate"] == 0.0  # 无点击时不做虚高转化率

    # 未挂载商品不能下单（观众端看不到，但服务端仍要兜住构造请求）
    dm.unmount_live_product("P002")
    bad, reason = dm.create_viewer_order("P002", "v_test02", "测试观众", 1)
    assert bad is None and reason == "商品已下架"
