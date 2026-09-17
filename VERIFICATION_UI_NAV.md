# VERIFICATION_UI_NAV.md · 侧栏导航高亮一致性修复（M9 收尾）

## 变更概述
修复 `web/src/layouts/DefaultLayout.vue` 侧栏菜单的 `activeMenu` 高亮逻辑，使 M9 各功能页在激活时菜单项精确高亮，而非错误 fallback 到「模板选择」。

## 根因（实测）
`activeMenu` computed 仅精确处理了 `/settings`、`/submit`、`/agents`，未覆盖 `/plugins`、`/skills`、`/channels`、`/`（研报入口）。这些页面激活时菜单错误高亮到 `/templates`（默认返回项）。同时 `/` 首页作为 M9-5 对话式入口没有任何菜单项，用户落地后无法从菜单返回。

## 修复内容（共 13 行新增）
1. 新增「研报入口」菜单项（`index="/"`，图标 `ChatDotRound`），置于菜单顶部——首页可回。
2. `activeMenu` computed 补全各顶层菜单项精确高亮：`/`、`/submit`、`/agents`、`/plugins`、`/skills`、`/channels` 按前缀匹配。
3. `/tasks/*` 流转页按含 `/submit` 与否高亮到提交/模板，其余默认 `templates`。

## 校验（实测）
- `npm run typecheck`（vue-tsc --noEmit）：**EXIT 0**，`ChatDotRound` 图标经 main.ts 全局注册可用，无未使用变量/类型错误。
- `npm run build`：**EXIT 0**，14.08s 构建成功，产物正常产出。
- `git diff --stat`：仅 `web/src/layouts/DefaultLayout.vue` 变更（13 增）；session-backup/wiki 等为工作区既有未跟踪改动，未卷入。

## 范围/边界
- 纯前端 UI 一致性与可达性修复，不涉及引擎/API/数据逻辑，后端零改动。
- 不新增功能，仅修正导航高亮与首页入口——符合「UI 是 controller 且导航精确」治理要求。
- 未触碰 `.env`/`.secrets`/配置，无安全影响。

## 自审结论
变更聚焦、无越界、无过度工程，构建通过。待独立 reviewer 审议后提交。