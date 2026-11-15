#!/usr/bin/env python3
"""给直播间灌入一批「真实走完流程」的演示观众账号与订单数据。

用途：观众端氛围、观众列表、观众画像（模拟）等演示场景。运营台的**真人口径**
（订单管理、退款审核、本场数据总览、电商转化漏斗、本场商品 Top）只统计真人
观众，并按 demo_viewer_ids.json 排除这些演示账号，所以不会污染主播自己的数据。

为什么不用直接往 JSON 里塞假订单：
    观众端下单、加购、评价都走线上链路，手写 JSON 很容易绕过库存、钱包、
    购物车、访客时长这些联动，下一次启动还会被口径过滤掉。

    所以这里全部走**线上真实链路**：
        注册/登录 → 保存收货地址 → 发弹幕 → 点商品 → 加购物车
        → 下单 → 支付 →（主播发货）→ 确认收货 → 评价 / 申请退款
    拿到的账号可以真的登录观众端，订单也能在运营台发货/退款。数据由服务进程
    正常落盘（viewers.db、orders_data.json、viewer_visits.json、danmaku_data.json）。

    ⚠️ 私信默认不生成：收件箱是主播面对真人的工作台，演示账号不往里塞会话。
    确实要演示 AI 客服/收件箱时再加 --with-private-messages，并可用
    scripts/purge_demo_private_messages.py 随时清掉。

用法（先启动服务，默认 8085 端口）：
    .venv/Scripts/python.exe scripts/seed_demo_viewers.py
    .venv/Scripts/python.exe scripts/seed_demo_viewers.py --viewers 30 --host-pass host123456
    .venv/Scripts/python.exe scripts/seed_demo_viewers.py --no-start-live   # 已在直播中，沿用本场

脚本可重复执行：昵称固定、密码固定，已存在就登录复用账号，只会在当前场次追加
新订单。用过的演示账号会登记到 data/demo_viewer_ids.json。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

try:
    import websockets
except ImportError:  # pragma: no cover
    print("缺少 websockets，请用项目虚拟环境运行：.venv/Scripts/python.exe", file=sys.stderr)
    raise

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_PASSWORD = "demo123456"

# 脚本创建的演示账号登记表。运营台的私信收件箱只服务真人账号，
# 这些「演示观众」的私信/会话不应出现在主播工作台上；登记后可用
# scripts/purge_demo_private_messages.py 一键清理，也避免每次重跑再混回来。
ROOT = Path(__file__).resolve().parents[1]
DEMO_IDS_FILE = ROOT / "src" / "data" / "live_commerce" / "data" / "demo_viewer_ids.json"


def record_demo_ids(user_ids: list[str]) -> None:
    """把本轮使用的演示账号登记到 demo_viewer_ids.json（去重、稳定排序）。"""
    ids = {x for x in user_ids if x}
    try:
        if DEMO_IDS_FILE.exists():
            with open(DEMO_IDS_FILE, encoding="utf-8") as f:
                ids.update(json.load(f) or [])
    except (OSError, json.JSONDecodeError):
        pass
    DEMO_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DEMO_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)

# 固定昵称池：重复执行时走「登录」而不是无限注册新号。
NICKNAMES = [
    "林小满", "夏日柠檬", "橘子汽水", "晚风来信", "南山有鹿", "一口芝士",
    "芝士奶盖", "半糖去冰", "晚睡选手", "栖迟", "柚见花开", "小鹿乱撞",
    "草莓味的猫", "不二青年", "月亮邮差", "阿栀", "十七", "晴天收藏家",
    "路过的风", "小满未满", "木子李", "陈小七", "远方来客", "星星点灯",
    "云间月", "卖火柴的鱼", "可可西里", "慢慢来", "青柠气泡", "欢喜同学",
]

# 弹幕：故意都用**非问句**。问句会触发一次 AI 回复（最长 20s/条），
# 这批数据只需要真实互动量，不需要把 LLM 配额打满。
DANMAKU_LINES = [
    "主播讲得挺清楚的", "这个颜色好看", "支持一下", "已关注了",
    "刚需，先蹲一波", "看起来质感不错", "价格挺香的", "准备下单",
    "回购第三次了", "包装很用心", "发货速度快", "喜欢这个款式",
]

# 观众私信（纯人工链路，不会触发 AI 回复）。每位观众一条，让收件箱、
# 「咨询人数」和观众画像的会话数据一起有内容。
PRIVATE_LINES = [
    "主播，今天拍的什么时候发货呀？",
    "这款有现货吗，想多买两件",
    "请问支持七天无理由吗",
    "刚下单了，麻烦帮忙看一下地址",
    "有没有优惠券可以叠加",
    "这个和之前那款有什么区别呀",
    "收到货了，包装很好，谢谢主播",
    "想问下这个适合送人吗",
    "库存还多吗，怕抢不到",
    "可以开发票吗",
    "发货是顺丰还是中通呀",
    "想再买一份，还有赠品吗",
]

STAGES = ["warmup", "flash_sale", "deep_dive", "interact", "closing"]

REGIONS = [
    ("广东省", "深圳市", "南山区科技园"),
    ("浙江省", "杭州市", "西湖区文三路"),
    ("江苏省", "南京市", "鼓楼区中山路"),
    ("上海市", "上海市", "浦东新区张江路"),
    ("四川省", "成都市", "武侯区天府大道"),
    ("北京市", "北京市", "朝阳区望京街"),
    ("湖北省", "武汉市", "洪山区光谷大道"),
    ("山东省", "青岛市", "市南区香港路"),
    ("福建省", "厦门市", "思明区软件园"),
    ("湖南省", "长沙市", "岳麓区麓山南路"),
]


@dataclass
class ViewerCtx:
    nickname: str
    user_id: str
    token: str
    address_id: str
    ws: Any = None
    order_id: str = ""
    order_paid: bool = False
    order_amount: float = 0.0


@dataclass
class Plan:
    """一个观众的完整行为脚本（索引对应订单序号）。"""
    click_products: list[str] = field(default_factory=list)
    add_cart: bool = False
    order: bool = False
    pay: bool = False
    refund: bool = False
    ship_and_complete: bool = False
    review_rating: int = 0
    review_text: str = ""


class WSClient:
    def __init__(self, ws: Any):
        self.ws = ws

    async def send(self, msg_type: str, data: dict | None = None) -> None:
        await self.ws.send(json.dumps({"type": msg_type, "data": data or {}},
                                      ensure_ascii=False))

    async def expect(self, result_type: str, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"等待 {result_type} 超时")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=left)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == result_type:
                return msg.get("data") or {}

    async def request(self, msg_type: str, data: dict, result_type: str,
                      timeout: float = 30.0) -> dict:
        await self.send(msg_type, data)
        return await self.expect(result_type, timeout)


def http_login(base_url: str, username: str, password: str) -> str:
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"主播登录失败 HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上服务 {base_url}: {e}")
    token = payload.get("access_token") or ""
    if not token:
        raise SystemExit(f"主播登录响应里没有 access_token: {payload}")
    return token


def ws_url(base_url: str, token: str = "", role: str = "viewer") -> str:
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    netloc = parts.netloc or "127.0.0.1:8085"
    q = f"role={quote(role)}"
    if token:
        q += f"&token={quote(token)}"
    return f"{scheme}://{netloc}/ws/live-commerce?{q}"


def build_plan(count: int, products: list[str]) -> list[Plan]:
    """生成行为分布：25 人看、18 人点、3 人只加购、10 人下单、9 人付款、5 人收货。"""
    plans = [Plan() for _ in range(count)]
    if not products:
        return plans

    click_idx = list(range(min(18, count)))
    cart_only_idx = [i for i in (18, 19, 20) if i < count]
    order_idx = [i for i in (0, 2, 4, 6, 8, 10, 12, 14, 16, 20) if i < count]
    cart_extra = [i for i in (1, 5, 9, 13) if i < count]

    for i in click_idx:
        first = products[i % len(products)]
        second = products[(i + 3) % len(products)]
        plans[i].click_products = [first] if i % 3 == 2 else [first, second]
    for i in cart_only_idx:
        plans[i].add_cart = True
    for i in cart_extra:
        plans[i].add_cart = True

    paid_idx = [i for i in order_idx if i != 10]
    for i in order_idx:
        plans[i].order = True
    for i in paid_idx:
        plans[i].pay = True

    if len(order_idx) > 7:
        plans[order_idx[7]].refund = True       # 第 8 单申请退款（待审核）
    for i in order_idx[:5]:
        plans[i].ship_and_complete = True       # 前 5 单发货并确认收货
    reviews = {order_idx[0]: (5, "东西很好，包装也仔细，下次还来"),
               order_idx[1]: (4, "整体不错，发货速度挺快的"),
               order_idx[2]: (3, "还可以，中规中矩吧")}
    for i, (rating, text) in reviews.items():
        plans[i].review_rating = rating
        plans[i].review_text = text
    return plans


async def host_prepare(base_url: str, token: str,
                       start_live: bool) -> tuple[WSClient, Any, list[str]]:
    ws = await websockets.connect(ws_url(base_url, token, "host"), ping_interval=20,
                                  max_size=None)
    client = WSClient(ws)
    await client.expect("init_state", timeout=15)

    data = await client.request("get_products", {}, "get_products_result")
    on_sale = [p for p in (data.get("products") or [])
               if p.get("isOnSale") and (p.get("stock") or 0) > 0]
    products = [p["productId"] for p in on_sale]
    if not products:
        raise SystemExit("没有可售商品，先去主播端上架商品再跑")
    random.shuffle(products)

    if start_live:
        result = await client.request("start_live", {}, "start_live_result")
        if not result.get("success"):
            # 没有讲解商品时 start_live 会拒绝：先指定一个再试。
            await client.request("set_product", {"productId": products[0]},
                                 "set_product_result")
            result = await client.request("start_live", {}, "start_live_result")
        if not result.get("success"):
            raise SystemExit(f"开播失败：{result.get('message')}")
    return client, ws, products


async def open_viewer(base_url: str, nickname: str, password: str,
                      region: tuple[str, str, str]) -> ViewerCtx:
    ws = await websockets.connect(ws_url(base_url, "", "viewer"), ping_interval=20,
                                  max_size=None)
    client = WSClient(ws)
    await client.expect("init_state", timeout=15)

    # 固定昵称池可能和仓库里历史测试账号重名（密码不同）。按固定顺序探测候选：
    # 先登录、再注册，命中即用 —— 后缀是确定性的，重复执行会复用同一批账号，
    # 不会每跑一次就多出一堆新号。
    candidates = [nickname] + [f"{nickname}{i:02d}" for i in range(1, 10)]
    result: dict = {}
    for cand in candidates:
        result = await client.request(
            "viewer_login", {"nickname": cand, "password": password},
            "viewer_login_result",
        )
        if result.get("success"):
            nickname = cand
            break
        result = await client.request(
            "viewer_register", {"nickname": cand, "password": password},
            "viewer_register_result",
        )
        if result.get("success"):
            nickname = cand
            break
    if not result.get("success"):
        await ws.close()
        raise RuntimeError(f"注册/登录失败：{result.get('message')}")

    user = result.get("user") or {}
    token = result.get("token") or ""
    ctx = ViewerCtx(nickname=user.get("userName") or nickname,
                    user_id=user.get("userId") or "",
                    token=token, address_id="", ws=ws)

    province, city, street = region
    addr = await client.request("save_address", {
        "receiverName": ctx.nickname,
        "phone": "13" + "".join(str(random.randint(0, 9)) for _ in range(9)),
        "region": f"{province} {city}",
        "detail": f"{street}{random.randint(1, 200)}号",
        "isDefault": True,
    }, "save_address_result")
    if addr.get("address"):
        ctx.address_id = addr["address"].get("addressId") or ""
    return ctx


async def run(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    token = http_login(base_url, args.host_user, args.host_pass)
    host, host_ws, products = await host_prepare(base_url, token, not args.no_start_live)
    print(f"✅ 主播已连接，可售商品 {len(products)} 个")

    count = max(1, args.viewers)
    plans = build_plan(count, products)
    contexts: list[tuple[int, ViewerCtx]] = []
    order_seq = 0
    errors: list[str] = []

    try:
        for i in range(count):
            nickname = NICKNAMES[i % len(NICKNAMES)]
            plan = plans[i]
            try:
                ctx = await open_viewer(base_url, nickname, args.password,
                                        REGIONS[i % len(REGIONS)])
                contexts.append((i, ctx))
                client = WSClient(ctx.ws)

                # 真实弹幕（非问句，不触发 AI 回复）
                await client.send("send_danmaku",
                                  {"content": DANMAKU_LINES[i % len(DANMAKU_LINES)]})
                await client.expect("send_danmaku_result", timeout=10)

                # 私信默认不生成：收件箱是主播的真实工作台，演示账号不往里塞会话。
                # 需要演示 AI 客服/收件箱时再显式加 --with-private-messages。
                if args.with_private_messages:
                    await client.request(
                        "send_host_msg",
                        {"content": PRIVATE_LINES[i % len(PRIVATE_LINES)]},
                        "send_host_msg_result", timeout=10)

                # 商品点击：同一连接两次之间至少 0.5s（服务端有防抖）
                for k, pid in enumerate(plan.click_products):
                    if k:
                        await asyncio.sleep(0.55)
                    res = await client.request("click_live_product", {"productId": pid},
                                               "click_live_product_result")
                    # 演示账号的点击按设计不计入运营台真实口径，不算脚本错误。
                    if not res.get("counted") and "真人" not in (res.get("message") or ""):
                        errors.append(f"{ctx.nickname} 点击未计入：{res.get('message')}")

                if plan.add_cart:
                    pid = products[(i + 1) % len(products)]
                    res = await client.request("add_cart", {
                        "productId": pid, "quantity": 1, "channel": "live_cart",
                    }, "add_cart_result")
                    if not res.get("success"):
                        errors.append(f"{ctx.nickname} 加购失败：{res.get('message')}")

                if plan.order and not args.no_orders:
                    stage = STAGES[min(order_seq // 2, len(STAGES) - 1)]
                    order_seq += 1
                    await host.request("switch_stage", {"stage": stage},
                                       "switch_stage_result")
                    pid = products[i % len(products)]
                    res = await client.request("place_order", {
                        "productId": pid, "quantity": 1,
                        "addressId": ctx.address_id, "channel": "live_cart",
                    }, "place_order_result", timeout=20)
                    order = res.get("order") or {}
                    if not res.get("success") or not order.get("orderId"):
                        errors.append(f"{ctx.nickname} 下单失败：{res.get('message')}")
                        continue
                    ctx.order_id = order["orderId"]
                    ctx.order_amount = float(order.get("totalAmount") or 0)

                    if plan.pay:
                        # 演示账号会被重复使用，钱包余额跨轮次累积消耗；先充值
                        # 保证本轮支付成功，否则漏斗的「完成支付」会莫名其妙掉数。
                        await client.request("recharge", {"amount": 5000},
                                             "recharge_result", timeout=10)
                        pay = await client.request("pay_order", {"orderId": ctx.order_id},
                                                   "pay_order_result", timeout=20)
                        if pay.get("success"):
                            ctx.order_paid = True
                        else:
                            errors.append(f"{ctx.nickname} 支付失败：{pay.get('message')}")

                    if plan.refund and ctx.order_paid:
                        refund = await client.request("request_refund", {
                            "orderId": ctx.order_id, "reason": "临时不想要了",
                            "refundType": "refund",
                        }, "request_refund_result")
                        if not refund.get("success"):
                            errors.append(f"{ctx.nickname} 退款申请失败：{refund.get('message')}")

                print(f"  [{i + 1}/{count}] {ctx.nickname} {ctx.user_id} "
                      f"订单={ctx.order_id or '-'} 已付={'是' if ctx.order_paid else '否'}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{nickname}: {e}")
                print(f"  [{i + 1}/{count}] {nickname} 失败：{e}", file=sys.stderr)
            await asyncio.sleep(0.1)

        # 登记本轮演示账号，供收件箱清理脚本识别（不影响订单/图表口径）
        record_demo_ids([ctx.user_id for _, ctx in contexts])

        # 主播发货 → 观众确认收货 → 评价
        shipped: list[tuple[int, ViewerCtx]] = []
        for idx, ctx in contexts:
            plan = plans[idx]
            if plan.ship_and_complete and ctx.order_paid and ctx.order_id:
                res = await host.request("update_order_status",
                                         {"orderId": ctx.order_id, "status": "shipped"},
                                         "update_order_status_result")
                if res.get("success"):
                    shipped.append((idx, ctx))
                else:
                    errors.append(f"{ctx.nickname} 发货失败")

        for idx, ctx in shipped:
            client = WSClient(ctx.ws)
            res = await client.request("confirm_receipt", {"orderId": ctx.order_id},
                                       "confirm_receipt_result")
            if not res.get("success"):
                errors.append(f"{ctx.nickname} 确认收货失败：{res.get('message')}")

        for idx, ctx in shipped:
            plan = plans[idx]
            if not plan.review_rating or not ctx.order_id:
                continue
            client = WSClient(ctx.ws)
            res = await client.request("submit_review", {
                "productId": products[idx % len(products)],
                "orderId": ctx.order_id,
                "rating": plan.review_rating,
                "content": plan.review_text,
            }, "submit_review_result")
            if not res.get("success"):
                errors.append(f"{ctx.nickname} 评价失败：{res.get('message')}")

        # 拉一次运营台口径。注意：这些数字只含真人观众，演示账号按设计被排除，
        # 所以正常情况下会明显小于本次脚本造出的互动量。
        funnel = await host.request("get_conversion_funnel", {},
                                    "get_conversion_funnel_result", timeout=20)
        session = await host.request("get_session_orders", {"limit": 1},
                                     "get_session_orders_result", timeout=20)
        stats = session.get("stats") or {}
        steps = " → ".join(f"{s.get('label')} {s.get('count')}"
                           for s in (funnel.get("steps") or []))
        print("\n===== 本场真人数据（演示账号不计入）=====")
        print("转化漏斗：" + (steps or "无"))
        print(f"订单数：{stats.get('orderCount', '-')}  "
              f"支付单：{stats.get('paidCount', '-')}  GMV：{stats.get('gmv', '-')}")
    finally:
        for _, ctx in contexts:
            try:
                await ctx.ws.close()
            except Exception:
                pass
        try:
            await host_ws.close()
        except Exception:
            pass

    if errors:
        print("\n⚠️ 部分操作未成功（漏斗数字可能偏小）：", file=sys.stderr)
        for e in errors[:20]:
            print("  - " + e, file=sys.stderr)
    print(f"\n完成：创建/复用观众 {len(contexts)} 个，新增订单 "
          f"{sum(1 for _, c in contexts if c.order_id)} 笔。"
          f"\n账号密码统一为：{args.password}（昵称可在观众端直接登录）")
    return 0 if contexts else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="给直播间灌入真实流程的观众与订单数据")
    parser.add_argument("--base-url", default="http://127.0.0.1:8085")
    parser.add_argument("--host-user", default="host01")
    parser.add_argument("--host-pass", default="host123456")
    parser.add_argument("--viewers", type=int, default=24, help="观众账号数量（默认 24）")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="观众账号统一密码")
    parser.add_argument("--no-start-live", action="store_true",
                        help="复用当前直播场次，不重新开播")
    parser.add_argument("--no-orders", action="store_true",
                        help="只补观众/弹幕/点击，不新增订单（用于给已有数据加人气）")
    parser.add_argument("--with-private-messages", action="store_true",
                        help="额外让演示账号给主播发私信（默认不发，避免污染真实收件箱）")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
