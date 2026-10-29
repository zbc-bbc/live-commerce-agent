"""商品图 AI 生成：可插拔的 provider 接口 + 商品提示词构造。

默认实现是 **Pollinations**（免费、无需 API key、一次 HTTP GET 直接拿到图片）。
接口刻意做成可替换的：以后要换成本地 Stable Diffusion、或 OpenAI / 通义万相
这类付费服务，只需新写一个 ``ImageProvider`` 子类、注册进 ``_PROVIDERS``，
再把 ``settings.IMAGE_PROVIDER`` 改掉即可 —— 调用方（路由层）一行都不用动。


关于出图质量，下面是**实测结论**，改提示词或换 provider 前务必先看：

1. **提示词里直接写中文商品名，画出来的东西基本不对。**
   实测：中文名「玻尿酸水光精华液 30ml」→ 一只冒热气的白瓷杯；
   「北欧风台灯」「无线降噪头戴耳机 H5」也都不是对应物体。
   光写品类也不救：「美妆护肤」这类中文品类词会让模型直接画一张人脸。
   换成**英文具体物体名词**才画得对（「a glass skincare serum bottle with
   a dropper」→ 正确的滴管精华瓶）。所以下面维护了一张
   「中文关键词 → 英文物体锚点」的词表 ``_OBJECT_ANCHORS``，
   提示词里只放英文锚点。**加新品类商品时优先往词表里补条目。**

2. **锚点写得越具体越准。** 实测「a modern minimalist table lamp」出图不稳，
   补成「...with a fabric lampshade and a slim metal base」后一次就出对。
   所以词表里的锚点尽量带上材质/结构。

3. **重抽确实有用。** 同一个提示词多抽几次能中（台灯 3/3 命中）。
   但 Pollinations 按提示词缓存，不加 ``seed`` 会直接返回上一张一模一样的图，
   所以这里每次调用默认随机 seed，「重新生成」才有意义。

4. **并发会 429。** 实测同时发 6 个请求，5 个被拒。这里用模块级锁串行化。
   代价是慢 —— 单张正常 1.6s ~ 45s 都有，前端必须给足 loading。

5. ``nologo=true`` **并不能可靠去掉右下角的 pollinations.ai 水印**
   （512 尺寸实测没有，768 尺寸实测有）。介意水印就得换 provider。

综合看：出图只能当「看着像那么回事」的示意/占位图，**不等于商品实拍**。
"""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from urllib.parse import quote

import httpx

from core import settings

logger = logging.getLogger(__name__)

# 默认出图尺寸（方图，够商品卡和详情弹窗用）
DEFAULT_SIZE = 768

# 命中率低的免费端点，失败重试次数（含首次）。429 是排队被拒，重试有意义。
_MAX_ATTEMPTS = 3


class ImageGenError(RuntimeError):
    """生成失败（网络、限流、返回非图片等）。由路由层转成对应的 HTTP 错误。"""


class ImageProvider(ABC):
    """图像生成 provider 接口。

    换实现只需要：继承本类 → 实现 ``generate`` → 注册进 ``_PROVIDERS`` →
    把 ``IMAGE_PROVIDER`` 指向新名字。
    """

    name: str = ""

    @abstractmethod
    async def generate(
        self, prompt: str, width: int, height: int, seed: int | None = None
    ) -> tuple[bytes, str]:
        """按提示词生成一张图。

        返回 ``(图片字节, 扩展名)``，扩展名带点，形如 ``".jpg"``。
        ``seed`` 用来让「重新生成」拿到不同的图；``None`` 表示不指定。
        失败时抛 :class:`ImageGenError`。
        """


# 生成用的锁：免费端点并发会 429，串行化比让请求互相挤掉更靠谱
_gen_lock: asyncio.Lock | None = None


def _get_gen_lock() -> asyncio.Lock:
    """惰性建锁 —— 不在 import 期建，避免和事件循环的创建顺序绑死。"""
    global _gen_lock
    if _gen_lock is None:
        _gen_lock = asyncio.Lock()
    return _gen_lock


class PollinationsProvider(ImageProvider):
    """免费且无需 key 的公开端点：GET /prompt/<urlencoded prompt> 直接返回图片。

    不需要任何配置，代价是排队延迟不稳（实测 1.6s ~ 45s）、并发被拒、有弱水印。
    """

    name = "pollinations"
    BASE_URL = "https://image.pollinations.ai/prompt/"

    async def generate(
        self, prompt: str, width: int, height: int, seed: int | None = None
    ) -> tuple[bytes, str]:
        url = f"{self.BASE_URL}{quote(prompt)}"
        params = {"width": width, "height": height, "nologo": "true"}
        if seed is not None:
            params["seed"] = seed

        last_err = "未知错误"
        async with _get_gen_lock():
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    async with httpx.AsyncClient(
                        timeout=float(settings.IMAGE_GEN_TIMEOUT)
                    ) as client:
                        resp = await client.get(url, params=params, follow_redirects=True)
                except httpx.HTTPError as e:
                    last_err = f"请求图像服务失败: {e}"
                    logger.warning(f"[image_gen] 第 {attempt + 1} 次尝试失败: {last_err}")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                if resp.status_code == 429 or resp.status_code >= 500:
                    # 排队被拒 / 上游抖动：退避后重试，别把这种当成永久失败
                    last_err = f"图像服务返回 HTTP {resp.status_code}（排队或上游繁忙）"
                    logger.warning(f"[image_gen] 第 {attempt + 1} 次尝试被拒: {last_err}")
                    await asyncio.sleep(3 * (attempt + 1))
                    continue

                if resp.status_code != 200:
                    # 4xx 里除 429 外都是请求本身的问题，重试没意义
                    raise ImageGenError(f"图像服务返回 HTTP {resp.status_code}")

                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if not ctype.startswith("image/"):
                    last_err = f"图像服务返回的不是图片（{ctype or '未知 content-type'}）"
                    logger.warning(f"[image_gen] 第 {attempt + 1} 次尝试: {last_err}")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                # 正常情况下图片起码几十 KB；过小说明拿到的是错误页/占位图
                if len(resp.content) < 1024:
                    last_err = f"图像服务返回的数据过小（{len(resp.content)} 字节），疑似失败"
                    logger.warning(f"[image_gen] 第 {attempt + 1} 次尝试: {last_err}")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                }.get(ctype, ".jpg")
                return resp.content, ext

        raise ImageGenError(f"重试 {_MAX_ATTEMPTS} 次仍失败: {last_err}")


# provider 注册表：key 即 settings.IMAGE_PROVIDER 的取值
_PROVIDERS: dict[str, type[ImageProvider]] = {
    PollinationsProvider.name: PollinationsProvider,
}


def get_image_provider() -> ImageProvider:
    """按 ``settings.IMAGE_PROVIDER`` 取 provider 实例。"""
    key = (getattr(settings, "IMAGE_PROVIDER", "") or "pollinations").strip().lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        raise ImageGenError(
            f"未知的 IMAGE_PROVIDER: {key!r}（可用: {', '.join(sorted(_PROVIDERS))}）"
        )
    return cls()


# 中文关键词 → 英文物体锚点。**顺序敏感：先匹配到的先赢**，所以更具体的关键词
# （「电动牙刷」「翻译笔」）必须排在更宽泛的（「牙刷」「笔」）前面。
#
# 为什么必须有这张表：见模块 docstring 第 1 条 —— 中文写进提示词画出来就是错的。
# 锚点里带上材质/结构（见第 2 条）能明显提高命中率。
_OBJECT_ANCHORS: tuple[tuple[tuple[str, ...], str], ...] = (
    # —— 个护电器 / 数码 3C ——
    (("翻译笔", "翻译机", "translator"), "a slim handheld translator pen device with a small screen"),
    (("吹风机", "电吹风"), "a modern hair dryer with a matte white body"),
    (("电动牙刷",), "a rechargeable electric toothbrush standing upright"),
    (("剃须刀", "刮胡刀"), "an electric shaver with a charging dock"),
    (("卷发棒", "直发梳", "夹板"), "a hair styling iron with a slim ceramic barrel"),
    (("美容仪", "洁面仪"), "a handheld skincare beauty device"),
    (("耳机", "耳麦"), "over-ear wireless headphones lying flat, no person"),
    (("音箱", "音响"), "a portable bluetooth speaker with a fabric mesh front"),
    (("充电宝", "移动电源"), "a slim portable power bank with a usb cable"),
    (("充电器", "充电头"), "a compact white wall charger"),
    (("键盘",), "a mechanical keyboard with backlit keys, top-down view"),
    (("鼠标",), "a wireless computer mouse"),
    (("数据线", "充电线"), "a neatly coiled white usb-c cable"),
    (("摄像头",), "a small webcam on a stand"),
    (("手表", "手环"), "a smart watch with a sport band"),
    (("支架",), "an adjustable aluminium phone stand"),
    # —— 美妆护肤 ——
    (("精华", "水光"), "a glass skincare serum bottle with a dropper"),
    (("面霜", "乳液", "眼霜"), "a frosted glass jar of face cream with the lid off"),
    (("面膜",), "a sealed sheet mask packet with one folded mask beside it"),
    (("口红", "唇釉"), "a single lipstick tube standing upright"),
    (("粉底", "气垫", "bb霜"), "a round cushion foundation compact, open with a mirror"),
    (("香水",), "a faceted glass perfume bottle"),
    (("洁面", "洗面奶"), "a squeeze tube of facial cleanser"),
    (("防晒",), "a white bottle of sunscreen"),
    (("洗发", "护发", "发膜"), "a pump bottle of shampoo"),
    (("牙膏",), "a tube of toothpaste standing on its cap"),
    # —— 食品茶饮 / 健康食品 ——
    (("茶",), "a gift box of tea with a sealed tea tin beside it"),
    (("咖啡",), "a bag of roasted coffee beans with a filled cup"),
    (("坚果",), "a resealable pouch of mixed nuts with some spilling out"),
    (("花胶", "燕窝"), "a bowl of ready-to-eat nourishing soup in a gift box"),
    (("益生菌",), "a box of probiotic sachets with one sachet leaning against it"),
    (("鸡胸",), "a vacuum-sealed pack of cooked chicken breast"),
    (("蛋白粉",), "a tub of protein powder with a measuring scoop"),
    (("维生素", "钙片", "鱼油"), "an amber bottle of supplement tablets with the lid off"),
    (("零食", "饼干", "糖果"), "a sealed snack pouch with a few pieces beside it"),
    (("果汁", "饮料", "气泡水"), "a capped glass bottle of juice"),
    # —— 家居日用 ——
    (("靠垫", "靠枕", "腰靠"), "an ergonomic lumbar support back cushion with a mesh cover"),
    (("枕头", "枕"), "a white contoured memory foam pillow with a removable cover"),
    (("台灯", "落地灯", "灯"), "a modern minimalist table lamp with a fabric lampshade and a slim metal base"),
    (("被", "床品", "四件套"), "a neatly folded quilted duvet"),
    (("床垫",), "a rolled-up mattress standing upright"),
    (("收纳",), "a stack of translucent stackable storage boxes"),
    (("保温杯", "水杯", "杯"), "a stainless steel insulated water bottle with a screw lid"),
    (("毛巾", "浴巾"), "a neatly folded stack of cotton towels"),
    (("香薰", "精油"), "a glass reed diffuser bottle"),
    (("拖鞋",), "a pair of soft house slippers"),
    # —— 箱包配饰 / 服饰 ——
    (("双肩包", "背包"), "a minimalist urban backpack standing upright"),
    (("手提包", "女包", "单肩"), "a structured leather handbag"),
    (("钱包",), "a slim leather wallet, slightly open"),
    (("行李箱", "拉杆箱"), "a hard-shell rolling suitcase"),
    (("帽",), "a plain baseball cap"),
    (("围巾",), "a neatly folded knit scarf"),
    (("眼镜", "墨镜"), "a pair of sunglasses with the arms folded"),
    (("项链", "手链", "耳环", "首饰"), "a delicate gold necklace laid flat on a white surface"),
    (("衬衫",), "a folded cotton dress shirt"),
    (("t恤", "T恤"), "a folded plain cotton t-shirt"),
    (("裤",), "a pair of neatly folded trousers"),
    (("裙",), "a flowing dress on a wooden hanger"),
    (("外套", "羽绒", "卫衣"), "a puffer jacket on a hanger"),
    (("鞋", "靴"), "a pair of clean white sneakers side by side"),
    (("袜",), "several pairs of folded cotton socks"),
    # —— 母婴 / 宠物 ——
    (("奶瓶",), "a baby feeding bottle with a silicone teat"),
    (("纸尿裤", "尿不湿"), "a pack of baby diapers with one folded diaper beside it"),
    (("玩具", "积木"), "a colourful wooden children's stacking toy"),
    (("猫粮", "狗粮", "宠物粮"), "a bag of pet food with a filled bowl"),
    (("猫砂",), "a bag of cat litter"),
)

# 提示词尾巴：承载「白底 / 棚拍 / 无人 / 无文字」这些硬约束，**永远不截断**。
#
# 「no people」特意重复了不同说法（no person / no model / no hands）：实测只写一次
# 时，「over-ear wireless headphones」被画成了「一个人戴着耳机」。
_TAIL = (
    "single product only, centered on a pure white seamless background, "
    "soft studio softbox lighting, high detail, sharp focus, catalog shot, "
    "no people, no person, no model, no hands, no text, no watermark"
)

# 提示词总长上限，纯粹兜底防超长。
#
# 正常长度是「前缀 47 + 锚点 ≤ 80 + 尾巴 195 ≈ 324」，所以这里给到 400 —— 也就是
# 说**正常路径永远不会触发截断**，它只在有人往词表里塞了超长锚点时兜底。
# 早先这里设 300，结果把锚点从中间切断了（「...with a mesh cover」→「with a mes」），
# 提示词变成半截单词。截断本身不可怕，**切在词中间才是**，所以下面按词边界切。
_PROMPT_MAX = 400


def _object_anchor(name: str, category: str) -> str:
    """从名称/品类里找英文物体锚点；没命中返回空串。

    先查商品名再查品类 —— 品类太粗（「家居日用」下面同时有靠垫、枕头、台灯），
    只用品类必然画错。
    """
    for hay in (name.lower(), category.lower()):
        if not hay:
            continue
        for keys, anchor in _OBJECT_ANCHORS:
            if any(k.lower() in hay for k in keys):
                return anchor
    return ""


def build_product_prompt(product) -> str:
    """由商品信息拼出商品图提示词（纯函数，便于单测与复现）。

    **只放英文**：商品名/品类/卖点在提示词里都是中文，而实测（见模块 docstring）
    中文对这些模型基本等于噪声，会让画面跑偏，所以这里一律不写进去，
    只输出英文物体锚点 + 固定尾巴。

    没命中词表的商品退化成一张「中性包装图」而不是硬塞中文名 —— 宁可画一张
    平淡的白底产品图，也不要画错物体（比如给精华液画出一只杯子）。
    同时打 warning，方便发现词表缺项。
    """
    name = (getattr(product, "name", "") or "").strip()
    category = (getattr(product, "category", "") or "").strip()

    anchor = _object_anchor(name, category)
    if anchor:
        head = f"professional e-commerce product photography of {anchor}"
    else:
        logger.warning(
            f"[image_gen] 商品未命中物体词表，退化为中性包装图: "
            f"name={name!r} category={category!r}（建议往 _OBJECT_ANCHORS 补条目）"
        )
        head = (
            "professional e-commerce product photography of a single plain "
            "product package box standing upright"
        )

    prompt = f"{head}, {_TAIL}"
    if len(prompt) > _PROMPT_MAX:
        # 正常长度到不了这里（见 _PROMPT_MAX 注释）。真要截，截头不截尾 ——
        # 尾巴是白底/无人/无文字的硬约束。**按最后一个空格切**，别切出半截单词。
        budget = max(0, _PROMPT_MAX - len(_TAIL) - 2)
        cut = head.rfind(" ", 0, budget)
        if cut <= 0:
            cut = budget
        logger.warning(
            f"[image_gen] 提示词超长（{len(prompt)} > {_PROMPT_MAX}），锚点被截断: {head!r}"
        )
        prompt = f"{head[:cut].rstrip()}, {_TAIL}"
    return prompt


async def generate_product_image(
    product,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
    seed: int | None = None,
) -> tuple[bytes, str, str]:
    """生成一张商品图，返回 ``(图片字节, 扩展名, 实际使用的提示词)``。

    提示词一并返回是为了落盘/排查时能知道这张图是拿什么描述生成的。

    ``seed`` 不传时随机取值 —— 必须随机，否则 Pollinations 按提示词缓存，
    「重新生成」会返回上一张一模一样的图（实测字节数完全相同）。
    需要复现某张图时把当时的 seed 传进来。
    """
    prompt = build_product_prompt(product)
    if seed is None:
        seed = random.randrange(1, 2**31 - 1)
    provider = get_image_provider()
    logger.info(
        f"[image_gen] provider={provider.name} seed={seed} 生成商品图: {prompt[:80]}..."
    )
    data, ext = await provider.generate(prompt, width, height, seed=seed)
    return data, ext, prompt
