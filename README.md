# 直播带货管理平台 - Live Commerce Platform

基于 Agent Service Toolkit（LangGraph + FastAPI + Streamlit）构建的 B2B 直播带货管理系统。

## 项目架构

```
┌───────────────────────────────────────────────┐
│                 用户访问层                      │
│  浏览器 (HTML看板)    Streamlit (中控台)       │
└──────────┬───────────────────────┬────────────┘
     WebSocket                    HTTP/SSE
┌──────────▼───────────────────────▼────────────┐
│              FastAPI 服务层                    │
│  ┌──────────────┐ ┌──────────┐ ┌───────────┐  │
│  │ WebSocket    │ │ Agent API│ │ Auth API  │  │
│  │ /ws/live-    │ │ /agent/  │ │ /auth/    │  │
│  │ commerce     │ │          │ │           │  │
│  └──────┬───────┘ └────┬─────┘ └─────┬─────┘  │
└─────────┼──────────────┼──────────────┼────────┘
          │              │              │
┌─────────▼──────────────▼──────────────▼────────┐
│            数据管理层 (DataManager)              │
│ 产品/弹幕/私信/会话/话术/黑名单/评价/统计      │
└────────────────────┬───────────────────────────┘
                     │
┌────────────────────▼───────────────────────────┐
│             持久化层 (JSON / SQLite)             │
│  products / danmaku / settings / sessions       │
└────────────────────────────────────────────────┘
```

## 功能模块

### 1. 直播电商 Agent
- LangGraph StateGraph 驱动，10 个工具函数
- 商品搜索、详情查询、优惠计算、库存检查、推荐、日志、报告

### 2. Streamlit 场控中控台
- 商品卡片切换 / 弹幕互动 / 场控面板
- 5 阶段直播导航（暖场→产品介绍→促销逼单→答疑互动→收尾总结）
- 弹幕意图识别 + 异议安抚话术自动生成
- AI 话术生成器 / 直播复盘报告

### 3. 独立 HTML 后台看板
- 直播控制台 / 弹幕与 AI 问答 / 商品数据分析
- 用户评价管理 / 直播复盘报告 / 系统设置
- ECharts 数据可视化（弹幕趋势、观众画像、高频问题）

### 4. 实时 WebSocket 通信
- 40+ 消息类型：直播控制、商品管理、弹幕互动、私信、统计等
- 每 5 秒自动推送统计数据

### 5. 用户认证系统
- JWT Token 认证
- 用户注册 / 登录 / Token 刷新 / 退出
- 管理员角色权限

## 项目依赖

- Python >= 3.12, < 3.15
- FastAPI ~= 0.138.2（Web 框架）
- Uvicorn（ASGI 服务器）
- Streamlit ~= 1.58.0（前端界面）
- LangGraph ~= 1.2.7（AI Agent 框架）
- LangChain ~= 1.3.11
- Pydantic v2（数据模型）
- SQLite / aiosqlite（数据库）
- python-jose + passlib（JWT 认证）
- httpx ~= 0.28.1（HTTP 客户端）
- python-dotenv（环境配置）
- ECharts（前端图表，已内置）

完整依赖见 `pyproject.toml`

## 快速启动

### 1. 环境准备
```bash
# 安装 Python 3.12+
# 推荐使用 uv 或 pip
```

### 2. 配置环境变量
```bash
# 复制环境变量模板
cp .env.example .env
# 编辑 .env，至少配置一个 LLM API Key
# 例如 DeepSeek（本项目默认）：
#   DEEPSEEK_API_KEY=你的密钥
#   DEFAULT_MODEL=deepseek-chat
```

> 这一份 `.env` 配置只是**兜底**。运营台登录后可在「直播系统设置 → 🔌 大模型接入端口」
> 里增删多个 OpenAI 兼容端口（DeepSeek / 通义 / 智谱 / 硅基流动 / Kimi / 本地 Ollama、vLLM…），
> 并指定其中一个为**当前启用**。AI 的文字功能（话术生成、弹幕/私信自动回复、弹幕总结）
> 都走当前启用的端口；一个都不配时回退到上面的 `.env` 默认模型。API Key 只存在服务端
> （`src/data/live_commerce/data/llm_endpoints.json`），页面上只显示掩码 `sk-1***cd12`。

### 3. 安装依赖
```bash
# 使用 uv（推荐）
uv sync --frozen

# 或使用 pip
pip install -e .
```

### 4. 启动服务

**方式一：直接运行（开发）**
```bash
# 终端 1：启动后端 API 服务（端口 8085）
python src/run_service.py

# 终端 2：启动前端页面（端口 8501）
streamlit run src/streamlit_app.py
```

**方式二：Docker 部署**
```bash
docker compose watch
```

### 5. 访问服务

| 服务 | 地址 |
|------|------|
| Streamlit 前端 | http://localhost:8501 |
| 独立 HTML 看板（主播/运营台） | http://localhost:8085/live-commerce/ |
| 登录页面 | http://localhost:8085/live-commerce/login.html |
| 观众端 · 直播间 H5 | http://localhost:8085/viewer |
| API 文档 | http://localhost:8085/docs |
| API 健康检查 | http://localhost:8085/health |

### 6. 使用流程

1. 打开 Streamlit 前端 → 注册账号 → 选择「直播助手」
2. 左侧下拉框选择商品 → 点击「开始直播」
3. 输入弹幕或点击预设按钮 → AI 自动回复
4. 使用场控面板进行阶段切换 / 促销 / 复盘
5. 或访问独立 HTML 看板体验完整功能

### 7. 观众端单独部署

观众端（`src/viewer/index.html`）与主播运营台**不共用目录、可分开部署**：把整个 `src/viewer/` 目录拷到
nginx / OSS / CDN 等静态托管即可，运营台可以留在内网，两者只共享后端（WebSocket + HTTP API）。

该页面是**单个自包含 HTML**：CSS/JS 全内联，无外链样式、字体、图片和第三方脚本，商品图是 `data:` URI。
所以它没有构建步骤，直接拷文件就行。

**配置后端地址**（页面顶部 `window.LC_API`，只有这一处要改）——两种方式，任选其一：

1. 把 `window.LC_API = ''` 改成后端地址，例如 `'https://api.example.com'`
2. 页面地址后带查询参数（**优先于**上面的常量）：`https://cdn.example.com/lc-viewer/?api=https://api.example.com`
   —— 适合同一份静态文件同时连测试/生产两套后端

留空 `''` 表示与页面同源。本地开发走这一条：页面由同一个服务在 `/viewer` 托管，行为与拆分前一致。

注意：

- 地址请写成「协议 + 主机 [+ 端口] [+ 可选路径前缀]」，**不要带结尾斜杠**；只填裸域名（如 `192.168.1.9:8085`）会自动补当前页面的协议。
- **HTTPS 页面只能连 https/wss 后端**，否则浏览器会拦混合内容。
- 页面必须经 `http(s)` 打开；直接双击本地文件（`file://`）连不上后端。
- 改了页面里的地址后，如果 CDN 有缓存要**刷一下缓存**，否则会看到"配置没生效"。
- `/viewer` 没有结尾斜杠的那个 URL 是正规入口（可直接用）；带斜杠的 `/viewer/` 会 307 跳回 `/viewer`，属正常。
- 跨域上传头像依赖后端已有的 CORS 配置（`allow_origins=["*"]`），无需额外设置。

## 项目文件结构

```
├── src/
│   ├── run_service.py              # 服务启动入口
│   ├── streamlit_app.py            # Streamlit 前端（含直播中控台）
│   ├── agents/
│   │   ├── live_commerce_agent/    # 直播电商 Agent
│   │   │   ├── __init__.py
│   │   │   ├── live_commerce_agent.py
│   │   │   └── tools.py            # 10 个工具函数
│   │   └── agents.py               # Agent 注册中心
│   ├── data/
│   │   └── live_commerce/          # 直播电商数据层
│   │       ├── __init__.py
│   │       ├── data_manager.py     # 核心业务逻辑（1654 行）
│   │       ├── products.py         # 12 个模拟商品
│   │       ├── danmaku.py          # 弹幕模板
│   │       └── data/               # JSON 持久化数据
│   ├── schema/
│   │   ├── live_commerce_schema.py # 直播电商数据模型
│   │   └── auth_schema.py          # 认证数据模型
│   ├── service/
│   │   ├── service.py              # FastAPI 主服务
│   │   ├── live_commerce_ws.py     # WebSocket 处理器
│   │   ├── auth.py                 # JWT 认证
│   │   ├── auth_routes.py          # 认证 API
│   │   ├── admin_routes.py         # 管理员 API
│   │   └── utils.py                # 工具函数
│   ├── client/
│   │   └── client.py               # API 客户端
│   ├── core/
│   │   ├── settings.py             # 项目配置
│   │   └── llm.py                  # LLM 模型实例化
│   ├── memory/                     # 数据持久化
│   └── voice/                      # 语音功能
├── src/live_commerce/              # 主播/运营台前端（与观众端分开部署）
│   ├── index.html                  # 直播管理后台（1223 行）
│   ├── login.html                  # 登录页面
│   └── echarts.min.js              # 图表库
├── src/viewer/                     # 观众端（自包含，可整目录单独部署）
│   └── index.html                  # 直播间 H5（单文件，无外链资源）
├── .env                            # 环境配置
├── pyproject.toml                  # 项目依赖
└── README.md                       # 本文件
```

## 技术栈

| 层次 | 技术选型 |
|------|---------|
| 后端框架 | FastAPI + Uvicorn |
| AI Agent | LangGraph StateGraph |
| LLM 模型 | DeepSeek / OpenAI / Anthropic / Groq |
| 前端（中控台） | Streamlit + 自定义 CSS |
| 前端（看板） | HTML/JS + ECharts + WebSocket |
| 实时通信 | WebSocket |
| 数据库 | SQLite / JSON 文件 |
| 数据模型 | Pydantic v2 |
| 认证 | JWT（python-jose + passlib） |

## 开发说明

- 服务支持热重载（`MODE=dev` 时代码变动自动重启）
- 默认使用 DeepSeek Chat 模型；运营台「直播系统设置 → 大模型接入端口」可按当前启用的端口改走别的 OpenAI 兼容服务
- 数据库默认使用 SQLite
- 商品数据存储在 `src/data/live_commerce/data/products_data.json`
