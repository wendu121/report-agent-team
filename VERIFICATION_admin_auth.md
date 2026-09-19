# 自审（门禁②）：修复设置控制台页面 401 级联登出

## 1. 现象与根因（实测，非猜测）
- 现象：主账号点开设置控制台（渠道 / 数据源插件 / 智能体市场 / 技能库 / 任务模板）即被踢回登录页。
- 后端 `_require_admin`（`server/admin.py:159`）：要求请求带 `Authorization: Bearer <JWT>`（登录态）或 `X-Admin-Token`（应急后门，未配置即不存在）。
- 实测（`scripts/diag_admin_auth.py`，在 report-api 容器内对运行实例打真实 JWT）：
  - 所有 `/api/v1/admin/*` 端点：**带 Bearer → 200**，**不带 → 401**（含 models/agents/providers/mcp-servers/gates/templates/agents-library/plugins/skills/channels/reflections/lessons/experts）。
  - 后端**本身正常**，之前"后端 `_require_admin` 损坏"的判断被本次实证推翻。
- 真根因在**前端**：6 个控制台视图用原生 `fetch()` 调 `/admin/*`，原生 fetch 不走 `web/src/api/client.ts` 的 axios 拦截器 → **请求完全不带 Bearer** → 后端判 401 → 响应拦截器 `status===401` 时 `setToken('')`+广播 `rat:unauthorized` → 跳登录（级联登出）。
- 佐证：模板页 `API='/api/v1/admin'`，`${API}/templates` 实际命中 `/api/v1/admin/templates`（admin 端点），同样缺 Bearer。

## 2. 修复（落盘文件）
- `web/src/api/client.ts`：新增 `authFetch(input, init?)` 包装原生 fetch，自动读 `getToken()` 注入 `Authorization: Bearer`，默认补 `Content-Type: application/json`（调用方已设则保留），其余委托原生 fetch。给公开端点带 Bearer 无害。
- 6 视图 `fetch(` → `authFetch(` 并加 `import { authFetch } from '@/api/client';`：
  `Channels.vue` / `Plugins.vue` / `Agents.vue` / `Skills.vue` / `settings/Templates.vue` / `settings/TemplateEdit.vue`。
- 不含 `/admin` 的原生 fetch（download.ts 自带 Bearer、stores/template.ts 与 ChatEntry.vue 的 `/templates`、`/plugins`、`/models` 均为公开端点）保持不变，无级联风险。

## 3. 门禁①编译校验
- `docker compose build web` → `vue-tsc -b && vite build` **✓ built in 9.70s**，1786 模块转换，**0 TS 错误**。
- 初次构建曾 FAIL：门禁③独立审议抓出 3 个视图（Channels/Skills/Templates）漏加 `authFetch` import（`Cannot find name`），已补齐后重建通过。

## 4. 部署与产物核验
- `docker compose up -d web`（EXIT=0）。
- 运行容器 `report-web` 当前产物 `index-DjJMRl1p.js`（与构建输出哈希一致，证明为新包）。
- 包内 `grep -c 'new Headers' index-*.js` → **1**（authFetch 函数体标记；axios 拦截器用 `config.headers` 不走 `new Headers`，故该命中唯一对应 authFetch）→ 修复已真实上线。

## 5. 诚实边界
- 浏览器实点（登入→进设置页→确认不跳登录）受沙箱限制未跑（browser-skill CLI 未装）；但根因链路已三层实证：① 后端带 Bearer 全 200（diag 脚本）；② 前端源码所有 /admin fetch 已改 authFetch（Grep 复核无残留原生 fetch 打 /admin）；③ 运行包含 authFetch 且哈希匹配新构建。
- 公开端点（`/channels` `/plugins` `/skills` `/agents-library` `/templates`）被统一加上 Bearer——后端实测带不带都 200，属无害增强。
- 未改动后端 `_require_admin`（实测正常，无需改）；未触碰鉴权逻辑。
