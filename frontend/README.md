# frontend · Vue 3 迁移工程（Phase 1）

主播运营台与观众端的前端框架迁移外壳。旧页面仍由原生 HTML 提供，本工程负责：

- Vue 3 + Vite 构建链；
- Vue Router（Hash 模式）管理 `/login`、`/host`、`/viewer`；
- Pinia 管理主播登录态；
- 登录页完整迁移；
- 通过 `LegacyFrame.vue` 用同源 iframe 接入旧运营台与观众 H5。

详细设计与回滚方案见 [`../docs/前端框架迁移说明.md`](../docs/前端框架迁移说明.md)。

## 开发

```bash
npm install
npm run dev
```

访问 `http://127.0.0.1:5173/app/`。Vite 会把 `/auth`、`/api`、`/uploads`、`/live-commerce`、`/viewer`、`/ws` 代理到 `127.0.0.1:8085`。

## 构建

```bash
npm run build
```

产物输出到 `app-dist/`（刻意不使用 `dist`，因为仓库根 `.gitignore` 全局忽略 `dist/`）。FastAPI 在 `src/service/service.py` 中按目录存在性把它挂载到 `/app`：

```
http://127.0.0.1:8085/app/#/login
```

## 目录

```
src/
├─ main.js                 # createApp + Pinia + Router
├─ App.vue                 # <router-view />
├─ router/index.js         # 路由表与登录守卫
├─ stores/auth.js          # 登录 / 注册 / 会话检查 / 退出
├─ components/LegacyFrame.vue
├─ views/LoginView.vue
├─ views/HostView.vue
└─ views/ViewerView.vue
```

## 约束

- Phase 1 只改基础设施，不改 `src/live_commerce/` 与 `src/viewer/` 的业务代码；
- 旧入口 `/live-commerce/`、`/login`、`/viewer` 始终可用；
- 删除 `app-dist/` 即可关闭 `/app`，无需改业务路由。
