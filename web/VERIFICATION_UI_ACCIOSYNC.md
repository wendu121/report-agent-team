# VERIFICATION — UI 对齐 Accio 模板重建（里程碑 UI-ACCIOSYNC）

> 三道闸 · 第 ② 道：主代理自审（独立审议见 `REVIEW_UI_ACCIOSYNC.md`）
> 日期：2026-09-09　负责人：main-agent

## 0. 范围与意图
将 report-web 前端从「研报系统后台风」重构为贴合 boss 提供的 Accio Work 模板的「通用 Agent 平台风」。
确认项（AskUserQuestion 选择「全做 A+B+C」）：
- A 导航精简 5 项 + 首页对话入口 + 统一卡片
- B 渠道大卡片 + 按分类分区
- C 头像 emoji 化 + 响应式收起侧栏

## 1. 改动文件清单
| 文件 | 改动类型 | 关键内容 |
|---|---|---|
| `DESIGN_UI_ACCIOSYNC.md` | 新增 | 差异对比 / 信息架构 / 分阶段清单 / 验收标准 |
| `web/src/layouts/DefaultLayout.vue` | 重写 | 导航 5 项（新任务/智能体/插件/技能/消息渠道）；移除原研报入口/模板选择/提交任务/设置子菜单；品牌区 📑 渐变 logo；左下 user-zone（调试开关 + 设置下拉→`/settings/<cmd>`）；移除顶部 `el-header`；`app-main` 恢复 `padding:24px`（移动端 12px）；`@media(max-width:860px)` 收起侧栏文字 |
| `web/src/views/ChatEntry.vue` | 重写 | Accio 聊天对话框：bot 名片 + 引导气泡 + 场景 chip + 智能体组合 chip + composer（切换智能体 popover / 插件 / 生成研报）；保留 `buildUserTask→createTask→TaskTracking` 真实提交；`onMounted` 处理 `?agents=` 回跳；hero 卡改为 `min-height:100%` 适配全局 padding |
| `web/src/components/EntityCard.vue` | 新增 | 统一市场卡片：props(avatarText/color/name/sub/desc/tags) + 插槽(badges/status/extra/actions)；圆角 14px、悬停绿描边 |
| `web/src/utils/emoji.ts` | 新增 | `agentGlyph/pluginGlyph/skillGlyph/channelGlyph` 按 shape/category/name 推导 emoji，替代字母头像 |
| `web/src/views/Agents.vue` | 改造 | 引入 EntityCard + agentGlyph；删旧卡片样式 |
| `web/src/views/Plugins.vue` | 改造 | 引入 EntityCard + pluginGlyph；**B2 按分类分区**（移除 catFilter select，新增 `itemsByCat(cat)` + `.cat-section/.cat-title` 渲染） |
| `web/src/views/Skills.vue` | 改造 | 引入 EntityCard + skillGlyph；删旧卡片样式 |
| `web/src/views/Channels.vue` | 改造 | 引入 EntityCard + channelGlyph；端点输入 / coming_soon 提示移入 `#extra` 插槽；删旧卡片样式 |

## 2. 第 ① 道：校验（自动化）
| 校验 | 命令 | 结果 |
|---|---|---|
| 类型检查 | `npm run typecheck`（vue-tsc --noEmit） | ✅ EXIT=0 |
| 生产构建 | `npm run build`（vue-tsc -b && vite build） | ✅ EXIT=0，11.19s，产物 dist/ 生成 |

## 3. 布局回归（本次新增修复项）
移除顶部 `el-header` 后 `app-main` 曾置 `padding:0`，导致未自带内边距的页面贴边。
已核对并修复：
- `DefaultLayout.vue` `.app-main` 恢复 `padding:24px`（移动端 12px），与 Element Plus `el-main` 默认行为对齐，统一兜底所有未自垫页面。
- 已逐一核对各页根容器是否自带 padding：
  - 自垫页：`ChatEntry`（hero 卡，已改 `min-height:100%;padding:0` 以衔接全局 padding，避免双重留白）
  - 依赖全局 padding 的页（已确认无自有外边距，由 app-main 兜底）：`Agents/Plugins/Skills/Channels`（`.market` 仅 `padding:4px`）、`TaskSubmit(.submit-page)`、`TemplateSelect(.tpl-page)`、`TaskTracking(.track-page)`、`TaskResult(.result-page)`、`TaskReview(.review-page)`、`settings/*`（`.settings-*` 仅 `max-width`，无外边距）
- 结论：全站内容区不再贴边，且 ChatEntry hero 卡无双重留白。

## 4. 路由与导航一致性
- `router/index.ts` 未改，仍含 `/templates`、`/submit`、`/tasks/*`、`/settings/*`、`/`（ChatEntry）、`/agents|/plugins|/skills|/channels`。
- `activeMenu` 高亮：5 项精确匹配；`/settings/*` 精确高亮子项；`/tasks/*` 回退 `/templates`；`/submit` 回退 `/submit`。
- ⚠️ **已知孤儿入口（非缺陷，设计保留）**：`/templates`（TemplateSelect）与 `/submit`（TaskSubmit）已不在主导航。ChatEntry 提交走 `createTask` 直连 TaskTracking，**不经** TemplateSelect/TaskSubmit。两页仍可经直接 URL 访问，作为兜底；设计文档已记录为「保留入口」。如需彻底移除需另立任务（避免误删后端模板拉取逻辑）。
- `/settings/<cmd>` 4 个下拉项（models/agents/gates/templates）对应既有 settings 页，路由已就绪。

## 5. 诚实边界（coming_soon / 不可用项）
- 卡片 `coming_soon` 状态（插件/渠道）真实渲染「即将推出」且不冒充可用——引擎层 `ComingSoonProvider/ComingSoonChannel` 永不外发（M9-2/M9-4 既有契约，本里程碑未改动）。
- 数据源/技能/渠道的「启用/停用/连接/测试发送」按钮调用既有 admin API，行为未变。

## 6. 待独立审议（第 ③ 道）重点核查项
1. 移除 `el-header` 是否引入其他隐式依赖（如页面级标题组件、面包屑）。
2. `app-main` 全局 padding 是否对已存在的弹窗/抽屉定位产生影响（弹窗为 `teleport` 到 body，不受影响）。
3. EntityCard 插槽契约（badges/status/extra/actions）在 4 个市场页的复用是否符合单一数据源原则。
4. 响应式 `@media(max-width:860px)` 仅收起侧栏文字，主内容区是否仍可用（已 typecheck + 构建通过，缺真机窄屏实测）。

## 7. 自审结论
第 ① 道校验全绿；第 3、4、5 项人工核对通过；孤儿路由为设计保留项，已记录。
**待第 ③ 道独立子代理 REVIEW 通过后方可 `git commit` + Docker 重建部署。**
