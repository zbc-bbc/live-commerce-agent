# live-commerce-agent

> **传智杯参赛项目** · 直播电商 AI Agent —— 主播运营台 + 观众 H5 直播间
>
> 一套服务、一个端口、一条 WebSocket，同时支撑**桌面端主播运营台**与**移动优先观众直播间**，
> 由 AI Agent 负责话术生成、弹幕问答、场控提示与直播复盘。

---

## 复现性声明（比赛 2.2）

本项目严格遵循「可复现、可验证、可审查」原则，评审可在 **10 分钟内**从零跑通全部功能：

| 声明项 | 说明 |
| --- | --- |
| 依赖来源 | 全部为 PyPI 公开包，无私有源、无内部镜像、无本地编译依赖 |
| 外部服务 | **无强制外部服务**。未配置大模型时，AI 话术自动回落到内置模板，全流程仍可演示 |
| 初始化 | 首次启动自动建库（`auth.db` / `viewers.db`）、建目录、生成演示数据，**无需手工导入 SQL** |
| 配置 | `.env.example` 覆盖全部变量，每项均有可用默认值，复制即可运行 |
| 数据 | 直播间数据、报告、上传文件、归档均落在项目内 `src/data/live_commerce/`，随时可删可重建 |
| 环境 | 无 Docker、无 GPU、无数据库服务端要求；Windows / Linux / macOS 均可 |
| 实测环境 | Windows 11 + Python 3.14.3（源码兼容 `>=3.12,<3.15`），浏览器 Chrome / Edge |

> 复现过程中若端口被占用、依赖安装缓慢等，见 [④ 常见问题](#常见问题)。

---

## ① 项目简介

`live-commerce-agent` 面向中小商家「一人开播」场景：主播在运营台完成选品、开播、话术、场控、答疑、复盘，
观众在手机浏览器打开直播间即可看播、发弹幕、下单、评价、私信。两端共用一套服务与一条长连接，
AI Agent 在后台承担话术生成、弹幕意图识别与自动回复、高频问题聚类、直播复盘报告。

### 双端架构

| 端 | 访问地址 | 形态 | 身份 | Token 有效期 |
| --- | --- | --- | --- | --- |
| 主播运营台 | `http://<HOST>:<PORT>/live-commerce/` | 桌面端 Web，左侧导航 | 主播账号（`auth.db`） | 7 天 |
| 观众直播间 | `http://<HOST>:<PORT>/viewer` | 移动优先 H5 单页，底部浮层操作 | 观众账号（`viewers.db`）或游客 | 24 小时 |
| 登录页 | `http://<HOST>:<PORT>/login` | 登录 / 注册 | 主播账号 | — |
| 实时通道 | `ws://<HOST>:<PORT>/ws/live-commerce` | WebSocket 长连接 | 按握手凭据判定 | 随 Token |

### 主播运营台（9 个功能页面）

| 页面 | 能力 |
| --- | --- |
| 🎮 直播控制台 | 开播 / 下播、当前讲解商品切换、实时数据面板、场控智能提示 |
| 📦 商品管理 | 商品增删改、上下架、AI 生成商品图、库存与售价维护 |
| 💬 弹幕与AI问答 | 弹幕流、意图识别、AI 回复、话术生成与一键发送 |
| 📊 商品数据分析 | 销量 / GMV / 转化可视化（ECharts） |
| 👥 观众列表 | 在线观众、会话明细、观众侧操作 |
| ✉️ 私信收件箱 | 观众私信收发、未读角标、快捷回复 |
| ⭐ 用户评价管理 | 真实观众评价的查看与处理 |
| 📋 直播复盘报告 | 自动生成并归档复盘报告 |
| ⚙️ 直播系统设置 | 大模型接入端口、敏感词、预设话术、导出等 |

### 观众端 H5 直播间

移动优先单页：商品卡 / 购物车、公屏弹幕、点赞互动、下单与支付、充值、订单查询、收货确认、评价、私信主播。
未登录为**游客态**，仅可看播与浏览公开商品；下单、评价、私信等操作需观众 JWT（服务端二次校验绑定身份）。

### 权限与数据隔离

- **角色由凭据决定，不由客户端自报**：握手时校验 JWT 的 `scope`，`host` → 主播，`viewer` → 观众，
  无 token 且未自称主播 → 游客；带了无效 / 过期 token 或自称 `host` 却无凭据，握手阶段直接关闭（`1008`）。
- **消息白名单**：观众连接只允许调用 `_VIEWER_ALLOWED` 中列出的消息类型，未列出的类型一律要求主播角色。
- **按角色裁剪下发的数据**：主播 `init_state` 含 13 个键（统计、设置、敏感词、私信会话、告警等），
  观众只拿 6 个公开键（`role` / `liveStatus` / `currentProduct` / `products` / `hostProfile` / `allDanmaku`）；
  观众侧商品列表**只含已上架商品**，运营指标（AI 回复量、意图分、私信量）不下发。
- **观众操作身份服务端绑定**：`send_danmaku` / `place_order` / `recharge` 等不采信客户端自报 `userId`，
  统一由服务端从握手身份取值，避免越权读取他人余额、订单。

### 第一轮交付（已验收）

目录骨架、`.env.example` 环境变量模板、基础 WebSocket 服务入口（`/ws/live-commerce`）、
非法消息返回 `error`、`/health` 健康检查。后续轮次在此骨架上按模块递进，详见第 ⑤ 节。

> 说明：早期骨架实现了应用层 `ping` / `pong`；当前版本已改为由 uvicorn 承担协议级心跳
> （`ws_ping_interval=20s` / `ws_ping_timeout=20s`），应用层未知消息（含 `ping`）统一返回 `error`。

---

## ② 技术栈

| 层次 | 选型 | 说明 |
| --- | --- | --- |
| 语言 / 运行时 | Python 3.12 – 3.14 | `pyproject.toml` 约束 `>=3.12,<3.15` |
| Web 框架 | FastAPI + Uvicorn | HTTP 路由、OpenAPI 文档、ASGI 生命周期 |
| 实时通信 | WebSocket（Starlette） | 单一端点 `/ws/live-commerce`，消息信封 `{type, data, timestamp}` |
| 鉴权 | 自实现 JWT（HMAC-SHA256） | 双角色 `scope=host/viewer`，PBKDF2-HMAC-SHA256 口令散列，仅用标准库 + `sqlite3` / `aiosqlite` |
| AI 编排 | LangGraph / LangChain | `src/agents/live_commerce_agent/` 业务智能体，OpenAI-compatible 接入 |
| 前端 | 原生 HTML / CSS / JavaScript | 无构建步骤、无 npm；运营台与观众端均为单文件页面 |
| 可视化 | ECharts（本地离线副本） | `src/live_commerce/echarts.min.js`，断网可用 |
| 持久化 | JSON 文件 + SQLite | 运行时业务数据为 JSON；账号与 Token 为 SQLite（WAL） |
| 工程 | Git 细粒度分模块提交 | 每次 commit 对应一轮 AI 交互，见第 ⑤ 节 |

---

## ③ 目录结构

```text
live-commerce-agent/
├── src/
│   ├── run_service.py                 # 项目统一启动入口（uvicorn / 端口 / 热重载 / WS 心跳）
│   ├── service/
│   │   ├── service.py                 # FastAPI 应用、路由注册、静态挂载、/health
│   │   ├── live_commerce_ws.py        # WebSocket 核心处理（握手鉴权、消息路由、广播）
│   │   ├── auth.py                    # 主播账号与 JWT（7 天，scope=host）
│   │   ├── viewer_auth.py             # 观众账号与 JWT（24 小时，scope=viewer）
│   │   ├── auth_routes.py             # /auth/* 注册、登录、当前用户
│   │   ├── auth_middleware.py         # 全局 JWT 中间件（按路径校验角色）
│   │   ├── admin_routes.py            # /admin/* 用户管理（仅主播）
│   │   ├── product_routes.py          # /api/products 商品管理
│   │   ├── upload_routes.py           # /api/avatar/upload 头像上传
│   │   ├── product_image_routes.py    # /api/product/{id}/generate-image 商品图生成
│   │   ├── ai_live.py                 # AI 话术 / 回复 / 摘要业务
│   │   ├── ai_endpoints.py            # 大模型接入端口管理
│   │   └── image_gen.py               # 图片生成 provider（默认 pollinations）
│   ├── live_commerce/                 # 主播运营台前端
│   │   ├── index.html                 # 9 个功能页面 + 左侧导航
│   │   ├── login.html                 # 登录 / 注册（/login）
│   │   └── echarts.min.js             # 离线图表库
│   ├── viewer/
│   │   └── index.html                 # 观众端移动优先 H5 直播间（/viewer）
│   ├── agents/
│   │   └── live_commerce_agent/       # AI Agent 业务包
│   │       ├── live_commerce_agent.py # LangGraph 图与提示词
│   │       └── tools.py               # 商品检索、话术、统计等工具函数
│   ├── data/live_commerce/            # 业务数据层
│   │   ├── data_manager.py            # 统一数据管理（商品/弹幕/订单/会话/统计）
│   │   ├── danmaku.py                 # 弹幕数据模型与聚合
│   │   ├── products.py                # 商品数据访问
│   │   ├── llm_endpoint_store.py      # 大模型接入端口持久化
│   │   ├── products_data.json         # 商品种子数据
│   │   ├── data/                      # 运行时 JSON（首次启动自动生成）
│   │   │   ├── reports/               # 复盘报告输出
│   │   │   └── uploads/               # 商品图 / 头像上传目录（对外 /uploads）
│   │   └── archives/                  # 直播场次归档
│   ├── schema/                        # 数据模型与校验（Pydantic）
│   ├── core/                          # 配置与模型注册（settings、models）
│   ├── client/                        # 通用 HTTP 客户端
│   ├── memory/                        # 会话记忆（checkpointer / store）
│   ├── voice/                         # 语音输入输出（可选）
│   └── streamlit_app.py               # 通用 Agent 调试台（可选，不参与直播业务）
├── .env.example                       # 环境变量模板
├── requirements.txt                   # 依赖清单
├── pyproject.toml / uv.lock           # 项目元数据与锁定依赖（uv 可选）
├── LICENSE
└── README.md
```

> 运行时数据（`data/*.json`、`reports/`、`uploads/`、`archives/`、`*.db`）已在 `.gitignore` 中排除，
> 仅保留目录骨架，首次启动会自动重建。

---

## ④ 10 分钟快速复现

> 全程约 8–10 分钟：克隆 1 分钟 → 装依赖 4–6 分钟 → 配置 1 分钟 → 启动 1 分钟 → 验证 1–2 分钟。

### 0. 环境准备

| 项 | 要求 |
| --- | --- |
| Python | **3.12 – 3.14**（`python --version` 确认） |
| Git | 任意近期版本 |
| 端口 | 默认 `8080`（可用 `PORT` 修改） |
| 浏览器 | Chrome / Edge 最新版 |

### 1. 获取代码

```bash
git clone https://github.com/zbc-bbc/live-commerce-agent.git
cd live-commerce-agent
```

### 2. 安装依赖

```bash
# Windows (PowerShell / CMD)
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

可选（更快）：已安装 [uv](https://docs.astral.sh/uv/) 时

```bash
uv venv && uv pip install -r requirements.txt
```

> 依赖较重（LangChain / LangGraph 全家桶），首次安装约 200–300 MB，请预留 5 分钟。

### 3. 配置环境变量

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

**最小可运行配置：全空即可启动。** 需要 AI 能力时再按需填写：

| 变量 | 必填 | 默认 / 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | 否 | `0.0.0.0` / `8080` |
| `MODE` | 否 | 填 `dev` 开启 uvicorn 热重载；留空为普通模式 |
| `AUTH_SECRET` | 生产必填 | 全局 Bearer 校验密钥；**留空则 API 不校验 Bearer**（演示可用，生产必须设置） |
| `DEFAULT_MODEL` | 否 | 兜底大模型，如 `deepseek-chat`；未配置「大模型接入端口」时生效 |
| `COMPATIBLE_MODEL` / `COMPATIBLE_API_KEY` / `COMPATIBLE_BASE_URL` | 否 | 接入任意 OpenAI-compatible 服务的三件套 |
| `OPENAI_API_KEY` 等 | 否 | 其余厂商 Key，按需填写 |
| `DATABASE_TYPE` | 否 | `sqlite` 或 `postgres`；不填即内存 / 默认存储 |
| `IMAGE_PROVIDER` | 否 | 默认 `pollinations`，免费、无需 Key，用于 AI 商品图 |

> 主播端可在「⚙️ 直播系统设置 → 大模型接入端口」在线添加多个 OpenAI-compatible 端点并启用其一，
> 演示时无需改 `.env` 重启。未配置任何端点时，AI 话术回落到内置模板，功能仍可完整演示。

### 4. 启动服务

```bash
# 在项目根目录执行（确保 .env 位于当前目录）
python src/run_service.py
```

正常输出（首次启动会自动建库、建目录、生成演示数据）：

```text
INFO:service.service:Agent loaded: live-commerce-agent
INFO:service.service:Live Commerce 数据已刷新
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

### 5. 验证清单

按顺序执行，全部通过即复现成功：

```bash
# ① 健康检查
curl http://127.0.0.1:8080/health
# → {"status":"ok"}

# ② 接口文档（自动生成的 OpenAPI）
# 浏览器打开 http://127.0.0.1:8080/docs

# ③ 注册主播账号（首个注册账号自动成为 admin）
curl -X POST http://127.0.0.1:8080/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"host01","password":"host123456","email":"host01@example.com"}'
# → 201 {"message":"Registration successful. Please log in.", ...}

# ④ 登录获取主播 JWT（同时下发 HttpOnly cookie 供运营台整页导航使用）
curl -X POST http://127.0.0.1:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"host01","password":"host123456"}'
# → {"access_token":"eyJ...","token_type":"bearer","expires_in":604800,...}

# ⑤ 未登录访问运营台应被拦截
curl -i http://127.0.0.1:8080/live-commerce/
# → HTTP/1.1 401 Unauthorized（登录后为 200）
```

浏览器验证（推荐）：

1. 打开 <http://127.0.0.1:8080/login> → 「注册」页签 → 注册并登录；
2. 自动进入运营台 <http://127.0.0.1:8080/live-commerce/>，左侧 9 个页面可切换；
3. 另开一个**手机尺寸**窗口（或手机同局域网访问 `http://<本机IP>:8080/viewer`）打开观众端；
4. 运营台「开始直播」、上架商品、发弹幕 → 观众端实时收到；
5. 观众端发弹幕 / 下单 / 私信 → 运营台弹幕页、控制台、私信收件箱实时收到。

### 6. WebSocket 连通性验证

浏览器 WS API 无法设置请求头，因此 token 走 query 参数：

```text
ws://<HOST>:<PORT>/ws/live-commerce?token=<JWT>        # 带 token：按 scope 判定主播/观众
ws://<HOST>:<PORT>/ws/live-commerce?role=viewer        # 游客：仅公开数据
```

命令行快速验证（使用 `uvicorn[standard]` 已附带的 `websockets`）：

```python
# ws_probe.py
import asyncio, json, websockets

async def main(token: str):
    url = f"ws://127.0.0.1:8080/ws/live-commerce?token={token}"
    async with websockets.connect(url) as ws:
        msg = json.loads(await ws.recv())
        print(msg["type"], msg["data"]["role"], len(msg["data"]["products"]))

asyncio.run(main("把上一步的 access_token 粘到这里"))
```

预期输出：`init_state host 14`（主播收到 13 个键的完整状态；游客只收到 6 个公开键，
商品列表仅含已上架商品）。发送未知类型消息会返回：

```json
{"type":"error","data":{"message":"未知消息类型: xxx"},"timestamp":"..."}
```

### 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `ModuleNotFoundError: No module named 'fastapi'` | 未激活虚拟环境，或用了系统 Python 3.8；确认 `python --version` ≥ 3.12 |
| 启动报 `[WinError 10048] 端口被占用` | 改 `.env` 中 `PORT`，或结束占用进程 |
| 访问 `/live-commerce/` 返回 401 | 未登录，属预期；先访问 `/login` 登录 |
| 页面能开但数据为空 | 首次启动会自动生成演示数据；查看终端 `Live Commerce 数据已刷新` |
| AI 功能无响应 | 未配置大模型端点，话术走内置模板；在「系统设置 → 大模型接入端口」添加即可 |
| 修改 HTML 不生效 | 页面每次请求从磁盘读取，刷新即可；若开 `MODE=dev`，运行数据已排除热重载 |

---

## ⑤ 开发迭代说明

本项目采用 **AI 结对开发 + 细粒度 Git 提交**：**每完成一轮 AI 交互产出的可运行增量，即提交一次**，
commit message 统一为 `<type>(<scope>): <中文摘要>`，`scope` 对应模块名。
评审可通过 `git log --stat` 逐条回溯「一次 AI 对话 → 一个提交 → 一个模块增量」的对应关系。

### 提交规范

| type | 含义 |
| --- | --- |
| `feat` | 新增功能模块 |
| `fix` | 缺陷修复 |
| `docs` | 文档 |
| `refactor` | 不改变行为的重构 |
| `chore` | 工程配置、依赖 |

粒度原则：一次提交只承载一个可独立验证的模块（如「鉴权」「商品」「WebSocket 广播」），
避免「一次提交改十个模块」的不可审查历史。

### 迭代记录

| 轮次 | 提交 | 日期 | 模块（scope） | 交付内容 |
| --- | --- | --- | --- | --- |
| 第 1 轮 | `ba06180` | 2026-10-23 | `skeleton` | 目录骨架、`.env.example` 环境变量模板、`/ws/live-commerce` 基础 WebSocket 端点、非法消息 `error`、`/health` 健康检查 |
| 第 2 轮 | `4523386` | 2026-10-28 | `auth` | JWT 双角色鉴权（主播 7 天 / 观众 24 小时）、全局 JWT 中间件、WebSocket 握手身份校验与消息白名单、运营台整页登录闸门 |
| 第 3 轮 | `ed5adab` | 2026-10-29 | `product` | 商品数据模型与校验、主播商品管理接口（增改 / 上下架）、上下架状态 WebSocket 广播、观众端仅下发已上架商品 |
| 第 4 轮 | `0b11a79` | 2026-10-29 | `live` | 直播电商 AI Agent（话术 / 回复 / 摘要）、弹幕与私信、订单与充值、AI 端点管理、商品图生成、上传、运营台 9 页前端与观众端 H5 |
| 第 5 轮 | 本次提交 | 2026-10-29 | `docs` | 复现文档：环境、依赖、变量模板、启动与接口验证、迭代记录 |

### 评审如何核对

```bash
git log --oneline --stat        # 每个提交改动的文件与行数，模块边界清晰
git show <commit>               # 查看某一轮的完整增量
```

---

## ⑥ License

本项目基于 [Agent Service Toolkit](https://github.com/JoshuaC215/agent-service-toolkit) 扩展开发，
沿用其 **MIT License**（见 [LICENSE](LICENSE)），Copyright (c) 2024 Joshua Carroll。
直播电商业务模块（`src/agents/live_commerce_agent/`、`src/service/live_commerce_ws.py`、
`src/live_commerce/`、`src/viewer/` 等）由本项目团队实现，同样以 MIT 协议开源。
