<!-- reviewed-by: independent-subagent -->
# REVIEW — UI 对齐 Accio 模板重建（里程碑 UI-ACCIOSYNC）

> 三道闸 · 第 ③ 道：独立子代理审议
> 审议日期：2026-09-10　方式：通读实际文件 + 独立重跑 gate ①（未信任 implementer 摘要）

## Summary
本子代理独立读取了全部改动文件与未改动页面，并重跑了 `typecheck`/`build`（均 EXIT=0）。
整体实现与自审摘要基本一致：el-header 已移除、app-main 恢复 24px 兜底、5 项导航与路由对齐、EntityCard 单一组件经插槽在 4 个市场页复用、orphan 路由（/templates、/submit）仍可直达。
未发现阻断级（MAJOR）缺陷。发现 2 处 MINOR（Channels 的 coming_soon 仍显示删除按钮；ChatEntry 短视口下垂直居中可能裁顶）与若干未实测项（GAP）。

## Verdict
**PASS_WITH_NOTES** —— 可进入 `git commit` + Docker 重建部署；建议顺手修掉 2 处 MINOR。

---

## MAJOR findings
无。

---

## MINOR findings

### M1. Channels 的 coming_soon 项仍暴露「删除」按钮（与 Plugins 不一致）
`web/src/views/Channels.vue:57-77` 中 `#actions` 插槽：
- 启用/停用/测试发送均用 `v-if="p.status !== 'coming_soon'"` 守卫（正确）；
- 但删除按钮 `v-if="!p.builtin"`（第 72-76 行）**未加 coming_soon 守卫**。若一个 coming_soon 且非内置的渠道，会显示「删除」这一可操作按钮。
对照 `web/src/views/Plugins.vue:42-66`：coming_soon 分支只渲染「即将推出」标签，连删除都隐藏。
影响：非「冒充可用」（诚实文案已为「即将推出」+ opacity 0.7 + 不接入提示），但属于未完成占位项上残留的可写操作，与 Plugins 行为不一致。
建议：删除按钮加 `p.status !== 'coming_soon'` 守卫，与 Plugins 对齐。

### M2. ChatEntry 在极短视口下可能裁顶（垂直居中 + 无滚动）
`web/src/views/ChatEntry.vue:168-175`：
```css
.chat-page { min-height: 100%; display: flex; align-items: center; justify-content: center; padding: 0; }
```
`app-shell` 为 `height:100vh`（DefaultLayout.vue:119-121），`el-main` 为该列 flex 子项 `flex:1`，故 `min-height:100%` 可解析、整体居中正确、无双滚动。但若对话框卡片本身高度超过视口（极低屏/大字号），`align-items:center` 会使顶部溢出且外层无 `overflow:auto`，顶部内容不可滚动触及。
影响：仅极端短视口；常规桌面/笔记本无碍。建议父容器加 `overflow:auto` 或改用 `margin:auto` 上下留白方案消除隐患。

---

## Gaps (unverified)
- **无真机浏览器实测**：响应式 `@media(max-width:860px)`（DefaultLayout.vue:269-300）仅静态通读确认逻辑（侧栏 68px、文字隐藏、app-main 12px），未在窄屏真机/DevTools 验证图标是否居中、有无横向溢出。归为 GAP，不假设 PASS。
- **ChatEntry 短视口裁顶**见 M2，因无浏览器无法定量复现，列为间隙。
- **DESIGN_UI_ACCIOSYNC.md 缺失**：任务清单与自审均引用 `web/DESIGN_UI_ACCIOSYNC.md` 作为设计基线，但该文件在磁盘上不存在（Read 返回 “File does not exist”）。故「设计差异对比」无法核对，本审议仅基于自审清单 + 实际代码。属文档 GاP，非代码缺陷。
- **el-main padding 覆盖未实测**：`.app-main{padding:24px}`（DefaultLayout.vue:265-268）依赖 scoped `[data-v]` 属性提升特异性以盖过 Element Plus `.el-main` 默认 padding。静态推断成立，但未在运行实例中量测实际留白像素。

---

## What was confirmed by reading files

1. **布局/留白兜底** — DefaultLayout.vue:265-268 `app-main` 设 `padding:24px`（移动端 12px），`.app-shell{height:100vh}`。所有顶层视图根容器均无自有外边距，依赖该兜底：`Agents/Plugins/Skills/Channels` 的 `.market` 仅 `padding:4px`（各视图 style）；`TaskSubmit.vue`（无 `<style>` 块）、`TemplateSelect.vue`（无 `<style>` 块）、`TaskTracking.vue:134 track-page` 仅 flex gap、`TaskResult.vue`/`TaskReview.vue` 根无外边距、`settings/*` 仅有内部组件 padding（grep 确认无页面级 padding）。结论：全站内容不再贴边。
2. **ChatEntry 衔接** — `.chat-page{min-height:100%;padding:0}`（ChatEntry.vue:168-175）承接全局 padding，无双重留白。
3. **导航保真** — 5 项 index `/`、`/agents`、`/plugins`、`/skills`、`/channels`（DefaultLayout.vue:14-37）与 router/index.ts:21-29 完全对应；`activeMenu`（DefaultLayout.vue:93-104）逐项精确匹配，/tasks 回退 /templates、/submit 回退 /submit；设置下拉 `goSettings` 推 `/settings/{models,agents,gates,templates}`（DefaultLayout.vue:113-115），route 中 4 个子页均存在（router/index.ts:37-43）。
4. **EntityCard 复用 + 无死 CSS** — 单组件 `web/src/components/EntityCard.vue:1-23` 经 `badges/status/extra/actions` 插槽在 Agents/Plugins/Skills/Channels 四页复用（见各视图 `<EntityCard ...><template #...>`）。Grep 四个市场视图：旧类 `.agent-card`/`.market-grid` 均未出现；`.card-top`/`.avatar` 仅存在于 EntityCard.vue 自身 scoped 样式（合法内部样式），视图层无残留死 CSS。
5. **响应式收起** — `@media(max-width:860px)`（DefaultLayout.vue:269-300）：aside→68px、隐藏 `.brand-text`/`.el-menu-item span`/`.user-meta`/`.debug-label`/`.settings-trigger span`/`.user-role`；app-main→12px。设置按钮保留图标+caret，调试开关可操作。
6. **Orphan 路由** — `/templates`（TemplateSelect）、`/submit`（TaskSubmit）仍在 router/index.ts:30-31，import 路径有效、文件存在、自身无破损 import，可经直链访问，属设计保留兜底。
7. **coming_soon 诚实性** — Plugins.vue:42-66 与 Channels.vue 状态标签均渲染「即将推出」，且引擎层不外发（既有契约，本里程碑未改）。Channels 的 `#extra` 对 coming_soon 显式提示「尚未接入，引擎永不推送」（Channels.vue:53-55）。⚠️ 唯一瑕疵见 M1。

---

## What you executed
- `cd web && npm run typecheck` → `vue-tsc --noEmit` **EXIT=0**（独立重跑，确认 gate ① 类型检查通过）。
- `cd web && npm run build` → `vue-tsc -b && vite build` **EXIT=0**，1749 modules transformed，产物 `dist/` 生成（独立重跑，确认 gate ① 构建通过）。
- 通读：DefaultLayout.vue、router/index.ts、ChatEntry.vue、EntityCard.vue、utils/emoji.ts、Agents.vue、Plugins.vue、Skills.vue、Channels.vue、TaskSubmit.vue、TemplateSelect.vue、TaskTracking.vue、TaskResult.vue、TaskReview.vue；grep `web/src/views/settings` 确认无页面级 padding；grep 四市场视图确认无旧卡片死 CSS。
- **未执行**：浏览器真机渲染 / 窄屏 DevTools 实测（环境无 GUI 浏览器），相关项列为 GAP。
