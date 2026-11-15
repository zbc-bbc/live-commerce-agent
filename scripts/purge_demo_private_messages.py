#!/usr/bin/env python3
"""清掉「演示账号」的私信会话，只保留真实注册观众的收件箱。

背景：scripts/seed_demo_viewers.py 会创建一批演示观众（账号本身走真实注册链路，
用于撑起漏斗/订单/GMV）。这些账号的订单要保留，但私信收件箱是主播面对真人的
工作台，不该混进演示会话。

本脚本只删**私信**，不删账号、订单、进场记录，也不动观众画像/漏斗统计：

  1. 从 data/demo_viewer_ids.json 读出演示账号 ID（种子脚本每次运行都会登记）；
  2. 从 private_msgs_data.json 删除这些账号的私信线程；
  3. 把它们会话上的未读数/最近消息清空（会话本身保留，观众画像仍按真实观众算）；
  4. 编辑前自动备份到 data/backups/。

必须**先停止服务**再运行：数据管理器会把内存里的私信写回磁盘，边跑边改会被覆盖。

    .venv/Scripts/python.exe scripts/purge_demo_private_messages.py
    .venv/Scripts/python.exe scripts/purge_demo_private_messages.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src" / "data" / "live_commerce" / "data"
BACKUP_DIR = DATA_DIR / "backups"
PRIVATE_MSGS = DATA_DIR / "private_msgs_data.json"
SESSIONS = DATA_DIR / "sessions_data.json"
DEMO_IDS = DATA_DIR / "demo_viewer_ids.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def server_running(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="清掉演示账号的私信，只保留真实注册观众")
    parser.add_argument("--base-url", default="http://127.0.0.1:8085",
                        help="用于检测服务是否已停止")
    parser.add_argument("--dry-run", action="store_true", help="只打印将删除的内容，不落盘")
    args = parser.parse_args()

    if server_running(args.base_url) and not args.dry_run:
        print("❌ 服务仍在运行。请先停止 src/run_service.py，再执行本脚本，"
              "否则内存里的私信会被重新写回。", file=sys.stderr)
        return 2

    demo_ids = {str(x) for x in (load_json(DEMO_IDS, []) or []) if str(x)}
    if not demo_ids:
        print(f"❌ 没有找到演示账号登记表：{DEMO_IDS}", file=sys.stderr)
        print("   先跑一次 seed_demo_viewers.py（会写入登记表），或手工补充 ID。",
              file=sys.stderr)
        return 2

    messages = load_json(PRIVATE_MSGS, {})
    sessions = load_json(SESSIONS, [])

    hit_ids = [uid for uid in messages if uid in demo_ids]
    msg_count = sum(len(messages[uid]) for uid in hit_ids)
    session_hit = [s for s in sessions if s.get("userId") in demo_ids]

    print(f"演示账号 {len(demo_ids)} 个，命中私信线程 {len(hit_ids)} 个、"
          f"消息 {msg_count} 条、会话 {len(session_hit)} 个")
    for uid in hit_ids[:10]:
        last = (messages[uid][-1].get("content") if messages[uid] else "") or ""
        print(f"  - {uid}: {len(messages[uid])} 条 · {last[:32]}")
    if len(hit_ids) > 10:
        print(f"  … 其余 {len(hit_ids) - 10} 个")

    if args.dry_run:
        print("dry-run：未修改任何文件")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for path in (PRIVATE_MSGS, SESSIONS):
        if path.exists():
            shutil.copy2(path, BACKUP_DIR / f"{path.name}.{stamp}.bak")
    print(f"已备份到 {BACKUP_DIR}")

    for uid in hit_ids:
        messages.pop(uid, None)
    # 会话本身保留（观众画像仍按已注册观众统计），但清掉未读与最近的私信预览，
    # 否则侧栏红点、未读汇总仍会指向一条已经不存在的消息。
    cleared_sessions = 0
    for s in sessions:
        if s.get("userId") in demo_ids:
            if s.get("unreadCount") or s.get("lastMessage"):
                cleared_sessions += 1
            s["unreadCount"] = 0
            s["lastMessage"] = ""
            s["lastMessageTime"] = ""

    with open(PRIVATE_MSGS, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    with open(SESSIONS, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

    print(f"✅ 已删除 {len(hit_ids)} 个演示账号的私信线程（{msg_count} 条消息），"
          f"清理 {cleared_sessions} 个会话的未读/预览。")
    print("   账号、订单、进场记录未改动。重启服务后收件箱只显示真实注册观众。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
