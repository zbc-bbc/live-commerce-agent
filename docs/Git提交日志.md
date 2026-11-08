# Git 提交日志

> 项目：`live-commerce-agent` · 直播电商 AI Agent（主播运营台 + 观众 H5 直播间）
> 统计分支：`main` · 统计时间：2026-11-06
> 提交总数：**9** · 时间跨度：**2026-10-23 18:40 — 2026-10-31 11:18**（8 天）
> 变更规模：**+38,692 / −955**（含首次骨架导入）

---

## 一、提交总览

| 序号 | 提交 | 日期时间 | 类型 | 提交说明 | 文件 | 增 / 删 |
| ---: | --- | --- | --- | --- | ---: | --- |
| 1 | `ba06180` | 2026-10-23 18:40 | feat(skeleton) | 搭建直播电商项目目录骨架与 WebSocket 基础服务入口 | 108 | +16499 / −0 |
| 2 | `4523386` | 2026-10-28 17:10 | feat(auth) | 实现 JWT 双角色登录鉴权与 WebSocket 身份校验 | 9 | +8232 / −69 |
| 3 | `ed5adab` | 2026-10-29 08:59 | feat(product) | 商品模型、主播商品管理接口、上下架 WS 广播 | 4 | +4012 / −0 |
| 4 | `0b11a79` | 2026-10-29 18:32 | feat(live) | 直播带货智能体、弹幕服务、商品图片与 AI 接口 | 25 | +7238 / −673 |
| 5 | `4227ee1` | 2026-10-29 18:45 | docs | 新增项目 README（10 分钟复现指南） | 1 | +315 / −185 |
| 6 | `f3d54ea` | 2026-10-29 18:55 | feat(auth) | 主播 & 观众 JWT 身份认证与角色权限隔离（审计修复） | 3 | +135 / −14 |
| 7 | `f5d6389` | 2026-10-29 19:05 | feat(danmaku) | 直播间弹幕收发、广播与弹幕历史缓存 | 3 | +155 / −8 |
| 8 | `ac39a2e` | 2026-10-30 12:57 | feat(danmaku) | 主播弹幕置顶 / 取消置顶，主播端与观众端同步 | 4 | +1153 / −3 |
| 9 | `0aa3820` | 2026-10-31 11:18 | feat(live-order-stat) | 直播间商品挂载、商品推送、基础下单与直播数据统计 | 8 | +953 / −3 |

---

## 二、分阶段说明

### 阶段一：项目骨架（2026-10-23）

**`ba06180`** 导入工程基础与直播电商目录骨架：统一启动入口、FastAPI 服务、WebSocket 端点、环境变量模板、Docker 与 CI 配置。
本提交同时带入 `agent-service-toolkit` 的既有 Agent 示例与文档，后续提交只围绕直播电商推进。

### 阶段二：身份、商品与直播业务底座（2026-10-28 — 2026-10-29）

- **`4523386`**：建立主播 / 观众双角色 JWT 体系（PBKDF2 口令散列、7 天 / 24 小时有效期）、全局鉴权中间件、WebSocket 握手身份判定，并落地主播运营台与观众端两套前端页面。
- **`ed5adab`**：商品数据模型与主播端商品管理接口（增删改查、上下架），上下架通过 WebSocket 广播两端。
- **`0b11a79`**：接入 LangGraph 直播带货智能体、AI 话术与弹幕服务、商品图片生成接口、大模型接入配置。
- **`4227ee1`**：补充 README，形成「10 分钟从零跑通」的复现说明。

### 阶段三：权限审计与弹幕互动（2026-10-29 — 2026-10-30）

- **`f3d54ea`**：对上一轮鉴权实现做安全审计后修复——未认证统一返回 401；观众消息白名单；按角色裁剪初始状态与推送事件，堵住运营指标与私信数据向观众泄露。
- **`f5d6389`**：弹幕收发与全房间广播，服务端绑定发送者身份，保留最近 20 条历史供新连接拉取。
- **`ac39a2e`**：主播置顶 / 取消置顶弹幕，置顶态随初始状态与广播同步到双端；同期提交技术文档。

### 阶段四：直播下单与数据统计（2026-10-31）

- **`0aa3820`**：直播间商品挂载与讲解切换、商品推送、基础下单流程、直播数据统计接口，并补充 `tests/service/test_live_room.py` 自动化测试。

---

## 三、逐次提交明细

### 1. `ba06180` — 项目骨架与 WebSocket 入口

- **日期**：2026-10-23 18:40
- **变更**：108 个文件，+16,499 行
- **核心内容**：
  - `src/run_service.py`：统一启动入口（uvicorn、热重载、WebSocket 心跳）
  - `src/service/service.py`：FastAPI 应用、路由注册、静态挂载、`/health`
  - `src/service/live_commerce_ws.py`：`/ws/live-commerce` 基础端点
  - `.env.example`、`pyproject.toml`、`requirements.txt`、Docker / CI / pre-commit 配置
  - `src/agents/`、`src/core/`、`src/memory/` 等 Agent 基础模块

### 2. `4523386` — JWT 双角色登录鉴权

- **日期**：2026-10-28 17:10
- **变更**：9 个文件，+8,232 / −69
- **核心内容**：
  - 新增 `src/service/auth.py`（主播账号、JWT 签发、PBKDF2 散列）、`viewer_auth.py`（观众账号）
  - 新增 `auth_routes.py`（注册 / 登录 / 当前用户）、`auth_middleware.py`（按路径校验角色）
  - `live_commerce_ws.py`：握手校验 `scope`，区分主播 / 观众 / 游客
  - 新增 `src/live_commerce/index.html`（主播运营台）、`src/viewer/index.html`（观众 H5）

### 3. `ed5adab` — 商品模型与管理接口

- **日期**：2026-10-29 08:59
- **变更**：4 个文件，+4,012 行
- **核心内容**：
  - `src/data/live_commerce/data_manager.py`：商品与直播间数据持久化
  - `src/schema/product_schema.py`：商品字段与校验
  - `src/service/product_routes.py`：`/api/products` 增删改查、上下架
  - 上下架状态经 WebSocket 广播到主播端与观众端

### 4. `0b11a79` — 直播业务与 AI 能力

- **日期**：2026-10-29 18:32
- **变更**：25 个文件，+7,238 / −673
- **核心内容**：
  - `src/agents/live_commerce_agent/`：LangGraph 直播带货智能体与工具集
  - `src/service/ai_live.py`、`ai_endpoints.py`：话术生成、弹幕回复、大模型接入管理
  - `src/service/image_gen.py`、`product_image_routes.py`：商品图生成
  - `src/data/live_commerce/danmaku.py`、`products.py`、`llm_endpoint_store.py`
  - `src/live_commerce/login.html`、`echarts.min.js`；前端页面同步扩展

### 5. `4227ee1` — README 复现文档

- **日期**：2026-10-29 18:45
- **变更**：1 个文件，+315 / −185
- **核心内容**：双端架构与访问地址、功能清单、权限矩阵、10 分钟复现步骤、常见问题

### 6. `f3d54ea` — 鉴权审计修复

- **日期**：2026-10-29 18:55
- **变更**：3 个文件，+135 / −14
- **核心内容**：
  - `auth_middleware.py`：未认证 / 角色不符统一返回 401
  - `live_commerce_ws.py`：观众消息白名单；初始状态按角色裁剪（主播 14 键 / 观众 7 键）；推送事件剥离运营指标
  - `README.md`：同步权限矩阵与协议说明

### 7. `f5d6389` — 弹幕收发与历史缓存

- **日期**：2026-10-29 19:05
- **变更**：3 个文件，+155 / −8
- **核心内容**：
  - 观众发弹幕、服务端广播全房间
  - 身份由握手信息绑定，不采信客户端自报 `sender` / `role`
  - 开播后保留最近 20 条弹幕，新连接可拉取
  - `README.md` 补充弹幕协议

### 8. `ac39a2e` — 弹幕置顶双端同步

- **日期**：2026-10-30 12:57
- **变更**：4 个文件，+1,153 / −3
- **核心内容**：
  - 主播端 `pin_danmaku` / `unpin_danmaku`，仅主播可操作
  - 置顶内容进入初始状态与实时广播，主播端、观众端同步展示
  - 新增 `docs/传智杯技术文档.md`；README 补充置顶说明

### 9. `0aa3820` — 商品挂载、下单与数据统计

- **日期**：2026-10-31 11:18
- **变更**：8 个文件，+953 / −3
- **核心内容**：
  - `src/service/live_room_routes.py`：直播商品挂载 / 推送接口
  - `src/service/live_commerce_ws.py`：挂载商品、切换讲解商品等消息
  - `src/data/live_commerce/data_manager.py`：订单与直播统计数据
  - `src/live_commerce/index.html`、`src/viewer/index.html`：双端下单与统计展示
  - `tests/service/test_live_room.py`：直播间接口自动化测试

---

## 四、原始记录

```text
$ git log --oneline --reverse
ba06180 feat(skeleton): 搭建直播电商项目目录骨架与 WebSocket 基础服务入口
4523386 feat(auth): 实现JWT双角色登录鉴权与websocket身份校验
ed5adab feat(product): 商品模型、主播商品管理接口、上下架ws广播
0b11a79 feat(live): 直播带货智能体、弹幕服务、商品图片与AI接口
4227ee1 docs: add project README.md
f3d54ea feat(auth): 实现主播&观众JWT身份认证与角色权限隔离
f5d6389 feat(danmaku): 实现直播间弹幕收发、广播与弹幕历史缓存
ac39a2e feat(danmaku): 主播弹幕置顶/取消置顶，主播端与观众端同步
0aa3820 feat(live-order-stat): 实现直播间商品挂载、商品推送、基础下单与直播数据统计
```

> 说明：以上仅统计 `main` 分支。仓库另存在 `backup/shallow-history` 分支（含早期骨架的重复提交与上游 CI 提交），不计入正式迭代历史。

---

## 五、核验命令

```bash
git log --oneline --reverse          # 9 次提交，按时间正序
git log --shortstat --reverse        # 每次提交的文件数与增删行
git show --stat <commit>             # 单次提交的文件清单
git log --format='%h %ad %s' --date=format:'%Y-%m-%d %H:%M'
```
