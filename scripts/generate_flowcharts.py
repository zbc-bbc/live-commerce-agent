# -*- coding: utf-8 -*-
"""生成技术文档所需的四张业务流程图（PNG）。

只依赖 Pillow，不引入 Graphviz / Mermaid CLI；输出到 docs/images/。
运行：python scripts/generate_flowcharts.py
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "images"

WIDTH = 1500
MARGIN_X = 60
TOP = 160
BOTTOM = 90
MAIN_CX = 600
MAIN_W = 720
BRANCH_CX = 1180
BRANCH_W = 420
GAP = 48
MERGE_DX = 110

NAVY = "#1F3864"
BLUE = "#2E5496"
LIGHT = "#EEF2F7"
DECISION_FILL = "#FFF7E6"
DECISION_BORDER = "#D97706"
BRANCH_FILL = "#F0FDF4"
BRANCH_BORDER = "#10B981"
ERROR_FILL = "#FEF2F2"
ERROR_BORDER = "#EF4444"
TEXT = "#1F2937"
MUTED = "#64748B"
ARROW = "#475569"

CJK_PUNCT = "，。；：、！？（）【】《》「」“”‘’"
CLOSING = "，。；：、！？）】》」”’"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_CANDIDATES[1] if bold else FONT_CANDIDATES[0]
    for candidate in (path, *FONT_CANDIDATES):
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


F_TITLE = _font(40, True)
F_SUB = _font(22)
F_NODE = _font(25)
F_NODE_BOLD = _font(25, True)
F_TAG = _font(18, True)
F_FOOT = _font(18)


# ── 文本换行：中文逐字、英文/标识符按词，避免把路径与接口名拆断 ──
def _tokens(text: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch in CJK_PUNCT:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
            out.append(" ")
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _hard_split(token: str, font, max_width: int) -> list[str]:
    parts, cur = [], ""
    for ch in token:
        if font.getlength(cur + ch) <= max_width:
            cur += ch
        else:
            if cur:
                parts.append(cur)
            cur = ch
    if cur:
        parts.append(cur)
    return parts


def wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw in str(text).split("\n"):
        line = ""
        for tok in _tokens(raw):
            if tok == " ":
                if line and not line.endswith(" "):
                    line += " "
                continue
            candidate = line + tok
            if font.getlength(candidate) <= max_width or (tok in CLOSING and line):
                line = candidate
                continue
            if line.strip():
                lines.append(line.rstrip())
            if font.getlength(tok) > max_width:
                parts = _hard_split(tok, font, max_width)
                lines.extend(parts[:-1])
                line = parts[-1]
            else:
                line = tok
        if line.strip() or not lines:
            lines.append(line.rstrip())
    return lines or [""]


def text_height(lines: list[str], font, line_gap: int = 8) -> int:
    return len(lines) * (font.size + line_gap) - line_gap


def draw_text_block(draw, lines, font, color, cx, cy):
    line_h = font.size + 8
    total = len(lines) * line_h - 8
    y = cy - total / 2
    for line in lines:
        draw.text((cx, y + font.size / 2), line, font=font, fill=color, anchor="mm")
        y += line_h


def arrow(draw, points, color=ARROW, width=3):
    draw.line(points, fill=color, width=width, joint="curve")
    (x1, y1), (x2, y2) = points[-2], points[-1]
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length < 2:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = 13
    draw.polygon([
        (x2, y2),
        (x2 - ux * size + px * size * 0.55, y2 - uy * size + py * size * 0.55),
        (x2 - ux * size - px * size * 0.55, y2 - uy * size - py * size * 0.55),
    ], fill=color)


def draw_label(draw, x, y, text):
    box = draw.textbbox((x, y), text, font=F_TAG, anchor="mm")
    draw.rounded_rectangle(
        [box[0] - 7, box[1] - 4, box[2] + 7, box[3] + 4],
        radius=6, fill="white", outline="#E2E8F0", width=1,
    )
    draw.text((x, y), text, font=F_TAG, fill="#334155", anchor="mm")


def process_node(draw, cx, y, text, tag="", fill=LIGHT, border=BLUE, w=MAIN_W):
    tag_space = 28 if tag else 0
    lines = wrap(text, F_NODE, w - 70)
    h = max(74, text_height(lines, F_NODE) + 34 + tag_space)
    draw.rounded_rectangle(
        [cx - w / 2, y, cx + w / 2, y + h],
        radius=16, fill=fill, outline=border, width=3,
    )
    if tag:
        draw.text((cx - w / 2 + 18, y + 16), tag, font=F_TAG, fill=border, anchor="lm")
    draw_text_block(draw, lines, F_NODE, TEXT, cx, y + h / 2 + tag_space / 2)
    return h


def terminal_node(draw, cx, y, text, w=MAIN_W):
    lines = wrap(text, F_NODE_BOLD, w - 90)
    h = max(66, text_height(lines, F_NODE_BOLD) + 30)
    draw.rounded_rectangle(
        [cx - w / 2, y, cx + w / 2, y + h],
        radius=h / 2, fill=NAVY, outline=NAVY, width=3,
    )
    draw_text_block(draw, lines, F_NODE_BOLD, "#FFFFFF", cx, y + h / 2)
    return h


def decision_node(draw, cx, y, text, w=MAIN_W):
    lines = wrap(text, F_NODE_BOLD, int(w * 0.60))
    h = max(122, text_height(lines, F_NODE_BOLD) + 70)
    pts = [(cx, y), (cx + w / 2, y + h / 2), (cx, y + h), (cx - w / 2, y + h / 2)]
    draw.polygon(pts, fill=DECISION_FILL, outline=DECISION_BORDER)
    draw.line(pts + [pts[0]], fill=DECISION_BORDER, width=3, joint="curve")
    draw_text_block(draw, lines, F_NODE_BOLD, "#7C2D12", cx, y + h / 2)
    return h


def measure_branch(items):
    heights = []
    for item in items:
        lines = wrap(item, F_NODE, BRANCH_W - 54)
        heights.append(max(66, text_height(lines, F_NODE) + 28))
    return heights


def draw_branch(draw, y, items, heights, fill, border):
    cy = y
    for idx, (item, h) in enumerate(zip(items, heights)):
        draw.rounded_rectangle(
            [BRANCH_CX - BRANCH_W / 2, cy, BRANCH_CX + BRANCH_W / 2, cy + h],
            radius=14, fill=fill, outline=border, width=3,
        )
        draw_text_block(draw, wrap(item, F_NODE, BRANCH_W - 54), F_NODE, TEXT,
                        BRANCH_CX, cy + h / 2)
        if idx < len(items) - 1:
            arrow(draw, [(BRANCH_CX, cy + h), (BRANCH_CX, cy + h + GAP)],
                  color=border)
        cy += h + GAP


def draw_header(draw, title, subtitle):
    draw.text((MARGIN_X, 56), title, font=F_TITLE, fill=NAVY, anchor="lm")
    draw.text((MARGIN_X, 108), subtitle, font=F_SUB, fill=MUTED, anchor="lm")
    draw.line([(MARGIN_X, 132), (WIDTH - MARGIN_X, 132)], fill="#D9E2F3", width=2)


def layout(nodes):
    """返回每个节点的位置与分支位置。"""
    placed = []
    y = TOP
    for node in nodes:
        t = node["type"]
        if t in ("start", "end"):
            lines = wrap(node["text"], F_NODE_BOLD, MAIN_W - 90)
            h = max(66, text_height(lines, F_NODE_BOLD) + 30)
        elif t == "decision":
            lines = wrap(node["text"], F_NODE_BOLD, int(MAIN_W * 0.60))
            h = max(122, text_height(lines, F_NODE_BOLD) + 70)
        else:
            tag_space = 28 if node.get("tag") else 0
            lines = wrap(node["text"], F_NODE, MAIN_W - 70)
            h = max(74, text_height(lines, F_NODE) + 34 + tag_space)

        item = {"node": node, "y": y, "h": h, "branch": None}
        if t == "decision" and node.get("branch"):
            heights = measure_branch(node["branch"])
            bh = sum(heights) + GAP * (len(heights) - 1)
            item["branch"] = {"y": y, "heights": heights, "height": bh}
            if node.get("branch_end", "stop") == "merge":
                y = max(y + h, y + bh) + GAP
            else:
                y = y + h + GAP
        else:
            y += h + GAP
        placed.append(item)
    return placed, y + BOTTOM


def render(filename, title, subtitle, nodes):
    placed, height = layout(nodes)

    img = Image.new("RGB", (WIDTH, int(height)), "white")
    draw = ImageDraw.Draw(img)
    draw_header(draw, title, subtitle)

    # ① 所有节点与分支框
    for item in placed:
        node, y, h = item["node"], item["y"], item["h"]
        if node["type"] in ("start", "end"):
            terminal_node(draw, MAIN_CX, y, node["text"])
        elif node["type"] == "decision":
            decision_node(draw, MAIN_CX, y, node["text"])
        else:
            process_node(draw, MAIN_CX, y, node["text"], tag=node.get("tag", ""),
                         fill=node.get("fill", LIGHT), border=node.get("border", BLUE))
        if item["branch"]:
            draw_branch(draw, item["branch"]["y"], node["branch"],
                        item["branch"]["heights"],
                        node.get("branch_fill", BRANCH_FILL),
                        node.get("branch_border", BRANCH_BORDER))

    # ② 箭头
    labels = []
    for idx, item in enumerate(placed):
        node, y, h = item["node"], item["y"], item["h"]
        is_decision = node["type"] == "decision" and item["branch"]
        next_top = placed[idx + 1]["y"] if idx + 1 < len(placed) else None

        if is_decision:
            merge = node.get("branch_end", "stop") == "merge"
            if merge:
                branch = item["branch"]
                branch_bottom = branch["y"] + branch["height"]
                next_y = max(y + h, branch_bottom) + GAP
                # 主线（继续条件）
                arrow(draw, [(MAIN_CX, y + h), (MAIN_CX, next_y)])
                labels.append((MAIN_CX + 34, (y + h + next_y) / 2,
                               node.get("main_label", "是")))
                # 分支合流
                arrow(draw, [
                    (BRANCH_CX, branch_bottom),
                    (BRANCH_CX, next_y - 24),
                    (MAIN_CX + MERGE_DX, next_y - 24),
                    (MAIN_CX + MERGE_DX, next_y),
                ])
                arrow(draw, [(MAIN_CX + 90, branch["y"] + 33),
                             (BRANCH_CX - BRANCH_W / 2, branch["y"] + 33)])
            else:
                if next_top is not None:
                    arrow(draw, [(MAIN_CX, y + h), (MAIN_CX, next_top)])
                    labels.append((MAIN_CX + 34, (y + h + next_top) / 2,
                                   node.get("main_label", "是")))
                branch = item["branch"]
                arrow(draw, [(MAIN_CX + 150, y + h / 2),
                             (BRANCH_CX - BRANCH_W / 2, branch["y"] + 33)])
            labels.append((BRANCH_CX - BRANCH_W / 2 - 34, y + h / 2 - 4,
                           node.get("branch_label", "否")))
        else:
            if next_top is not None:
                arrow(draw, [(MAIN_CX, y + h), (MAIN_CX, next_top)])

    for x, y, text in labels:
        draw_label(draw, x, y, text)

    draw.text((WIDTH / 2, height - 44), "直播电商 AI 运营助手 · 技术文档",
              font=F_FOOT, fill=MUTED, anchor="mm")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    img.save(path, "PNG", optimize=True)
    print("saved", path)


UPLOAD = [
    {"type": "start", "text": "开始：主播在商品编辑页选择本地图片"},
    {"type": "process", "text": "前端校验文件类型为图片、大小 ≤ 3MB，使用 FormData 调用 POST /api/product/upload-image", "tag": "① 前端"},
    {"type": "decision", "text": "Content-Type 是否以 image/ 开头？",
     "branch_label": "否（失败）", "branch_end": "stop", "main_label": "是",
     "branch": ["返回 400「仅支持上传图片」", "结束：本次上传失败"],
     "branch_fill": ERROR_FILL, "branch_border": ERROR_BORDER},
    {"type": "process", "text": "检查扩展名是否在白名单（png/jpg/jpeg/gif/webp/bmp/svg）；不在则按 Content-Type 兜底扩展名", "tag": "② 服务端"},
    {"type": "decision", "text": "分块读取时累计大小是否超过 3MB？",
     "branch_label": "是（超限）", "branch_end": "stop", "main_label": "否",
     "branch": ["关闭并删除已写入的临时文件", "返回 413「图片过大（上限 3MB）」", "结束：本次上传失败"],
     "branch_fill": ERROR_FILL, "branch_border": ERROR_BORDER},
    {"type": "process", "text": "以 prod_<uuid>.<ext> 为名写入 src/data/live_commerce/data/uploads/", "tag": "③ 落盘"},
    {"type": "process", "text": "返回 { url: \"/uploads/prod_xxx.png\", size }，由 /uploads 静态路由对外提供访问"},
    {"type": "process", "text": "前端通过 WebSocket save_product 把 URL 写回商品 images[0] / imageUrl，并刷新商品卡", "tag": "④ 回写"},
    {"type": "end", "text": "结束：图片上传完成，商品图在主播端与观众端同步生效"},
]

REFUND = [
    {"type": "start", "text": "开始：观众在「我的订单」对已支付订单发起退款"},
    {"type": "process", "text": "观众填写退款原因（≤200 字），WebSocket 发送 request_refund { orderId, reason }", "tag": "① 观众端"},
    {"type": "process", "text": "服务端校验：订单存在、属于当前观众、状态 ∈ paid/shipped/completed、无 pending/approved 申请", "tag": "② 数据层"},
    {"type": "process", "text": "登记 refundStatus=pending、refundReason、refundApplyTime；订单状态与库存/余额暂不变"},
    {"type": "process", "text": "广播 order_updated，运营台「退款审核」出现待处理记录"},
    {"type": "decision", "text": "主播审核是否同意退款？",
     "branch_label": "否（拒绝）", "branch_end": "stop", "main_label": "是（同意）",
     "branch": ["refundStatus=rejected", "记录 refundReviewTime 与拒绝理由", "订单状态保持原状，不返充", "结束：拒绝退款"],
     "branch_fill": ERROR_FILL, "branch_border": ERROR_BORDER},
    {"type": "process", "text": "同意：_apply_refund_locked 回补库存与销量、向观众钱包返充订单金额", "tag": "③ 退款落地"},
    {"type": "process", "text": "订单 status=refunded、refundStatus=approved、记录 refundReviewTime"},
    {"type": "process", "text": "广播 order_updated 与 product_sales，双端订单/商品/钱包同步刷新"},
    {"type": "end", "text": "结束：退款审核完成，历史申请保留可追溯"},
]

DANMAKU = [
    {"type": "start", "text": "开始：观众或系统产生一条弹幕，进入 add_danmaku()"},
    {"type": "process", "text": "持锁执行：判定情绪 sentiment（未提供时按关键词分类）", "tag": "① 采集"},
    {"type": "decision", "text": "当前直播状态是否为 live / paused？",
     "branch_label": "否（未开播）", "branch_end": "merge", "main_label": "是",
     "branch": ["不绑定场次，sessionId 保持空串", "仍进入全局滚动列表，但不属于任何场次"]},
    {"type": "process", "text": "确保本场 sessionId 存在，把弹幕绑定到当前场次，并追加到 current_session_danmaku", "tag": "② 绑定"},
    {"type": "process", "text": "追加到全局 danmaku_list 与本场最近 N 条历史缓冲（供中途进入的观众拉取）"},
    {"type": "process", "text": "真实用户（v_*）且非违规时 totalDanmaku++；仅可持久化账号触发 _save_danmaku() 落盘"},
    {"type": "process", "text": "广播 new_danmaku，主播端与观众端实时渲染"},
    {"type": "process", "text": "直播结束 end_live：_archive_current_session_locked() 汇总场次统计与在线/真实观众/话术/接管指标", "tag": "③ 固化"},
    {"type": "process", "text": "写入 danmaku_sessions/<sessionId>.json：{ session: meta, danmaku: [...] }"},
    {"type": "process", "text": "更新 danmaku_sessions/index.json：按 sessionId 去重、按开播时间倒序，只存摘要"},
    {"type": "process", "text": "清空 current_session_danmaku，广播 live_ended（含归档摘要与报告）"},
    {"type": "end", "text": "结束：本场弹幕已固化，可被复盘与排行接口读取"},
]

REPORT = [
    {"type": "start", "text": "开始：直播结束自动触发 / 主播点击「生成本场报告」/ 历史记录「重新生成」"},
    {"type": "decision", "text": "是否指定历史场次 sessionId？",
     "branch_label": "否（当前场次）", "branch_end": "merge", "main_label": "是（历史场次）",
     "branch": ["读取当前场次内存快照", "缓冲为空时按 sessionId 从滚动列表兜底"]},
    {"type": "process", "text": "读取 danmaku_sessions/<sessionId>.json，解析场次 meta 与全部弹幕", "tag": "① 取数"},
    {"type": "process", "text": "按 startTime ~ endTime 过滤真实订单、评价、私信；读取归档中的在线/真实观众/话术/接管指标"},
    {"type": "process", "text": "构建统一数据集：真实弹幕、全部弹幕、真实订单、真实评价、真实私信、排除量", "tag": "② 数据集"},
    {"type": "process", "text": "商品维度：提及数、提问数、订单/支付/件数、GMV、退款、评价、评分"},
    {"type": "process", "text": "弹幕维度：提问、回复率、平均响应、情绪、问题分类、高频问题 TOP、未答问题、5 分钟时间轴", "tag": "③ 双维度"},
    {"type": "process", "text": "生成文字章节：场次概览、商品表现、弹幕互动、转化、客服、风险、亮点、改进建议"},
    {"type": "process", "text": "生成唯一 reportId：report_<sessionId>_<时间戳>_<6位随机串>"},
    {"type": "process", "text": "写入 reports/<reportId>.json；向 reports/index.json 插入摘要（按 reportId 去重、新报告置顶）", "tag": "④ 存储"},
    {"type": "process", "text": "读取历史列表时跳过无 sessionId 的旧报告；按 generateTime 倒序返回"},
    {"type": "process", "text": "前端渲染 KPI、四类图表、文字结论与明细；支持下载 JSON、打开历史、按场次重新生成"},
    {"type": "end", "text": "结束：报告独立留存，旧记录永不覆盖"},
]


def main():
    render("flow_image_upload.png", "图片上传流程",
           "主播上传商品图：前端校验 → 服务端白名单/大小校验 → 原图落盘 → WebSocket 回写商品", UPLOAD)
    render("flow_refund_review.png", "退款审核流程",
           "观众申请只登记、不动账；主播同意后才回补库存、钱包返充并置为 refunded", REFUND)
    render("flow_danmaku_archive.png", "弹幕采集固化流程",
           "直播中按场次绑定并实时广播；直播结束整场固化到独立 JSON 与索引", DANMAKU)
    render("flow_report_generation.png", "复盘报告生成流程",
           "当前/历史场次统一数据集 → 商品与弹幕双维度 → 唯一 reportId 独立存储", REPORT)


if __name__ == "__main__":
    main()
