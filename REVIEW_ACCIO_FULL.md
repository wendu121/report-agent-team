<!-- reviewed-by: independent-subagent -->
Verdict: PASS_WITH_NOTES
Major: 0
Minor:
- web/src/views/Market.vue:12-14 — 切换主 Tab 时通过 v-if/v-else-if 卸载并重挂内嵌视图，每次切换都会重新 onMounted→load() 拉取一次数据，并丢失该视图的本地筛选/关键词/弹窗状态。属"按需加载"的有意行为（不存在同时双拉取、也不存在状态串扰），但每次切回都重发请求；若后续在意可加 keep-alive 缓存，当前不阻塞。
- web/src/views/Plugins.vue:214-220 — `categories`（从 filtered 派生，用于分组渲染）与 `allCategories`（从全量 items 派生，用于下拉选项）两处重复计算分类集合。逻辑正确且正是规避 Skills 折叠 bug 的关键设计，仅属轻微冗余，非缺陷。
- web/src/layouts/DefaultLayout.vue:89 — `/templates`、`/submit` 等流转页不命中任何分支，回退到 '/'（高亮"新任务"）。此为改动前既有的回退行为，本次合并导航未引入新的高亮缺口；仅作信息记录，非回归。
- web/src/layouts/DefaultLayout.vue:24-27 — 直接访问 /plugins、/skills、/channels 时 activeMenu 统一高亮"能力市场"，符合预期语义（能力市场页内嵌这些直达路由），属设计意图而非缺陷，记录以证明确认过。

NOTES:

## 一、逐项核查结论

### (a) 正确性 — Plugins 双筛选
- `catFilter` ref 已加（Plugins.vue:196），模板新增分类 `el-select`（Plugins.vue:23-25），选项由 `allCategories` 派生（Plugins.vue:218-220，取自 `items.value` 全量，规避了 Skills.vue:19/129 那种"从 filtered 派生→选中后选项折叠"的已知小问题）。
- `filtered` 计算属性同时应用来源筛选（Plugins.vue:226）、分类筛选（Plugins.vue:227）、关键词（Plugins.vue:228-232）。两个条件用 `&&` 串联，互为 AND，逻辑正确。
- `catFilter=''` 为 falsy，`clearable` 清空后 `if (catFilter.value && ...)` 不成立即不过滤，行为正确。
- 选中分类后 `categories`（由 filtered 派生）收敛为单一分类，分组区块只剩一个，与筛选一致，无异常。
- 未发现 Vue/TS 逻辑错误；与 VERIFICATION 所述 vue-tsc -b EXIT=0 / vite build EXIT=0 一致。

### (b) Market.vue 复用 — 双击取 / 状态隔离
- `v-if="active==='plugins'"` / `v-else-if="active==='skills'"` / `v-else-if="active==='channels'"`（Market.vue:12-14）构成互斥链，任意时刻至多一个视图挂载，UI 上恰好只显示一个 Tab 内容，正确。
- 不存在"每次切换双拉取"：每个内嵌视图在其自身 `onMounted(load)`（Plugins.vue:386 / Skills.vue:244 / Channels.vue:303）各取一次；因互斥挂载，任一时刻仅一个组件存活，无并发重复请求。切换 Tab 因卸载→重挂而重取一次，属一次性按需加载，非持续双拉。
- 状态隔离：三个视图是各自独立的组件实例，互不相通，无跨 Tab 状态污染。
- 旧直达路由 `/plugins`、`/skills`、`/channels` 在 router/index.ts:26/28/30 完整保留，Market 内嵌的即为同一组件，直达访问照常工作。
- 嵌套结构：Market 外层 el-tabs（数据源/研报技能/推送渠道）内再嵌各视图自带的 h2 标题 + 市场/已安装(或 全部/已启用) 内层 el-tabs + 筛选 + 网格，与 Accio 嵌套单页市场形态一致，逻辑无碍。

### (c) 导航高亮 — activeMenu
- DefaultLayout.vue:83-93：
  - `/market` 或 `p.startsWith('/plugins'|'/skills'|'/channels')` → 返回 `/market`（高亮"能力市场"）✓
  - `p.startsWith('/agents')` → `/agents`（高亮"智能体"）✓
  - `/` → `/`；`/settings*` → 精确高亮；`/tasks*`、`/submit*` 按流转逻辑回退。
- 本改动新增的第 89 行分支正确覆盖 /market 及其内嵌直达路由；未出现新路径无法高亮的情况。

### (d) 回归风险 — 合并三入口
- 左导航由 插件/技能/消息渠道 三项合并为"能力市场"一项（DefaultLayout.vue:24-27），原三项 index 已移除。
- 全仓检索 `/plugins|/skills|/channels`：仅出现在 router/index.ts（定义）、DefaultLayout.vue（activeMenu 与注释）、Market.vue（内嵌）、ChatEntry.vue（数据拉取，非路由跳转）。**无任何 `router-link` 或 `router.push` 指向这三路由的代码**，故删除导航项不会留下失效深链。
- `/plugins` `/skills` `/channels` 路由定义仍在，直达 URL 仍可用，无破坏。

### (e) 诚实边界 — 无冒充 / 无假能力
- Market.vue 纯前端结构重组，仅 `<script setup>` 引入并内嵌既有 Plugins/Skills/Channels 组件（Market.vue:18-26），未新增任何后端端点、未新增任何"能力"；启用/配置/推送等行为与此前完全一致（真实控制器）。
- 徽标仍仅由 `builtin` 派生：Plugins.vue:44-45（官方/自定义）、Skills.vue:39-40（官方/自定义）、Channels.vue:37（内置）。**无 Accio / @publisher / 第三方作者徽标**，未冒充外部作者。
- 可用性表述沿用既有 `pluginState` / `statusLabel` / `statusType`：`coming_soon` 仍明示"后端未接入/引擎永不推送"（Plugins.vue:249-256、Channels.vue:53-55、180）。无新增伪造可用性声明。
- 分类筛选项来自真实 `category` 字段（全量 items 派生），非编造分类。
- 代码中"Accio 招牌/Accio 模式/Accio 智能体市场单列"等表述为**设计灵感的结构性说明（注释与 UI 文案）**，非宣称本产品即 Accio、亦未伪造 Accio 署名或作者归属，符合"不冒充"约定；未引入任何 Accio 品牌徽标或第三方作者署名。
- 智能体市场保持单列 `/agents`（Accio 智能体市场单列结构），未塞入三主 Tab，与对齐说明一致。

## 二、构建
依据 VERIFICATION_ACCIO_FULL.md：vue-tsc -b EXIT=0、vite build EXIT=0。本轮静态审查未发现需重跑构建方可暴露的问题，未重新执行构建（属允许范围）。

## 三、总体
- 改动收敛：4 个前端文件，0 后端改动，0 配置改动。
- 真实缺陷（MAJOR）：0。
- 诚实边界：PASS（无冒充、无假可用、无编造端点/数据）。
- 结论：PASS_WITH_NOTES。建议可合入门禁④；若后续在意 Tab 切换重拉取开销，可择机加 `<keep-alive>`（非阻塞，不计入本轮缺陷）。
