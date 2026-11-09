"""场次弹幕：绑定 sessionId、结束固化、按场次隔离与复盘筛选。

与 test_live_room.py 一样，直接构造独立的 LiveCommerceDataManager，
并把所有落盘路径重定向到 pytest 临时目录，避免污染演示数据。
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from data.live_commerce import data_manager as dm_mod
from schema.live_commerce_schema import DanmakuItem


@pytest.fixture
def dm(tmp_path, monkeypatch):
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


def _add(dm, user_id, content):
    item = DanmakuItem(
        danmakuId=f"dm_{user_id}_{len(dm.danmaku_list)}",
        userId=user_id,
        userName=user_id,
        content=content,
        timestamp=datetime.now().isoformat(),
        isQuestion=dm_mod.is_question_like(content),
    )
    dm.add_danmaku(item)
    return item


def _start(dm):
    dm.mount_live_product("P001")
    dm.set_current_product("P001")
    assert dm.start_live()["success"] is True
    dm._stop_timers()
    return dm.live_state.sessionId


def test_live_danmaku_bound_and_archived_on_end(dm):
    sid = _start(dm)
    assert sid.startswith("live_")

    good = _add(dm, "v_test01", "这个真的很好用，我很喜欢")
    bad = _add(dm, "v_test02", "太贵了，有点失望")
    question = _add(dm, "v_test03", "这个多少钱？")

    # 直播中：每条弹幕都绑定同一场次，且有情绪判定
    for d in (good, bad, question):
        assert d.sessionId == sid
        assert d.sentiment in ("positive", "negative", "neutral")
    assert good.sentiment == "positive"
    assert bad.sentiment == "negative"
    assert question.isQuestion is True

    assert dm.end_live()["success"] is True

    sessions = dm.get_danmaku_sessions()
    assert len(sessions) == 1
    meta = sessions[0]
    assert meta["sessionId"] == sid
    assert meta["total"] == 3
    assert meta["question"] == 1
    assert meta["positive"] == 1 and meta["negative"] == 1

    # 场次文件独立存在，且包含本场全部弹幕
    session_file = Path(dm_mod.danmaku_sessions_dir()) / f"{sid}.json"
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["session"]["sessionId"] == sid
    assert len(payload["danmaku"]) == 3

    # 复盘筛选
    all_rows = dm.get_session_danmaku(sid)
    assert all_rows["success"] is True and len(all_rows["danmaku"]) == 3

    questions = dm.get_session_danmaku(sid, "question")
    assert [d["content"] for d in questions["danmaku"]] == [question.content]

    emotions = dm.get_session_danmaku(sid, "emotion")
    assert {d["content"] for d in emotions["danmaku"]} == {good.content, bad.content}

    positives = dm.get_session_danmaku(sid, "positive")
    assert [d["content"] for d in positives["danmaku"]] == [good.content]

    negatives = dm.get_session_danmaku(sid, "negative")
    assert [d["content"] for d in negatives["danmaku"]] == [bad.content]

    keyword = dm.get_session_danmaku(sid, "all", "多少钱")
    assert len(keyword["danmaku"]) == 1


def test_sessions_are_isolated(dm):
    sid1 = _start(dm)
    _add(dm, "v_test01", "第一场的问题，多少钱？")
    assert dm.end_live()["success"] is True

    sid2 = _start(dm)
    assert sid2 != sid1
    _add(dm, "v_test02", "第二场很好，我喜欢")
    assert dm.end_live()["success"] is True

    first = dm.get_session_danmaku(sid1)
    second = dm.get_session_danmaku(sid2)
    assert len(first["danmaku"]) == 1
    assert len(second["danmaku"]) == 1
    assert first["danmaku"][0]["content"].startswith("第一场")
    assert second["danmaku"][0]["content"].startswith("第二场")
    assert len(dm.get_danmaku_sessions()) == 2


def test_danmaku_outside_live_is_not_bound(dm):
    item = _add(dm, "v_test01", "随便说一句")
    assert item.sessionId == ""
    assert dm.get_danmaku_sessions() == []


def test_unknown_session_is_rejected(dm):
    result = dm.get_session_danmaku("../etc/passwd")
    assert result["success"] is False
    assert result["danmaku"] == []
