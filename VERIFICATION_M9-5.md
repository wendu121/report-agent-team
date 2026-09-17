# VERIFICATION_M9-5 · 研报对话式入口 自审 + 独立审议

> 配套 [DESIGN_M9-5.md](./DESIGN_M9-5.md)。门禁：① vue-tsc/Node 校验 ② 本文件自审 ③ REVIEW_M9-5（独立子代理，主代理不自签）。

---

## §17 设计自审（编码前）

### §17.1 一致性（设计 ↔ 真实代码）
- 设计 §1 现状实证逐条锚定真实代码：`router/index.ts:19` redirect、`TaskSubmit.vue` createTask 调用、`task.ts:100`/`taskService.ts:11`、`api.ts:30/124`、`api.py:212+223+233`、`Agents.vue:179`。**非印象**。
- `POST /tasks` 双非空校验（topic+scope）为设计 E3/E5 的硬约束来源，已如实反映。

### §17.2 代码实证可行性
- `buildUserTask` 抽离为纯函数（`import type` 仅类型，无运行时跨模块依赖）→ Node 22 `--experimental-strip-types` 可真跑（E2/E4/E5/E6 实证可行）。
- `ChatEntry` 复用既存 `useTaskStore().createTask` + `useRouter`（与 TaskSubmit 同 action），不重造控制器。
- `Agents.vue:179` 改 `/submit`→`/` 为 1 行，闭合至对话入口。

### §17.3 诚实边界
- H1 模板引导式非 chatbot：设计 §0.3/§9 明确不做真 LLM 解析，且规定 UI 文案不得写"智能对话"。
- H2 单一 payload 来源：`buildUserTask` 为组件唯一映射，防"假配置"。
- H3 scope 恒非空：DEFAULT_SCOPE 兜底，满足后端硬校验。
- H4/H5 范围与端点诚实：任务历史不在范围、无新后端端点，已显式声明。

### §17.4 控制器效应（boss 铁律）
- 入口调真实 `createTask` → `POST /tasks` → 引擎 run_report（api.py:212 已证为真控制器）。非装饰聊天框。
- agents 子集经 `createTask.agents` → api.py:267 覆盖编排（E6 实证可行）。
- 快捷模板真改变 scope/constraints/output_format_spec（E2 实证）。

### §17.5 复合写/原子性
- 无复合写（纯前端 + 复用既有 API），不适用 M9-2/3/4 的 ruamel/回滚范式。设计已声明后端零改动。

### §17.6 向后兼容
- `/templates`、`/submit` 不改、可直访；UserTask 契约不变；`vue-tsc` 校验全前端（E7）。
- 快捷模板内置前端（CHAT_PRESETS），不入 config/，不引入新 bind mount。

### §17.7 风险
- R1 前端验证手段弱（无 vitest）：以 `vue-tsc` 类型校验 + Node 真跑纯函数 + stub POST 三重补偿，已覆盖 E1-E7。
- R2 「切换智能体」闭环改动 Agents.vue 回跳目标：1 行，回归 TaskSubmit 的 `?agents=` 读取不受影响（ChatEntry 同范式读取）。

### §17.8 结论
设计自审通过，无 MAJOR 缺陷，达到编码条件。

### §17.9 设计修订记录
- 独立审议（设计轮）PASS_WITH_NOTES / MAJOR=NONE。NOTES 回填：
  - NOTE-1（平台 §3.5 line 196「跳任务提交确认」字面出入）：设计有意让对话入口内联表单即"确认层"（输入 topic + 选模板/智能体后即提交），跳过独立 `/submit` 页。属合理简化，非缺陷；实施轮 §18 标注。
  - NOTE-2（Agents.vue 单 agent 回跳 → ChatEntry 预置逻辑）：实现轮明确——`route.query.agents` 非空时 `selectedAgents` 直接取该子集（覆盖默认全选）。
  - NOTE-3（H1 禁"智能对话"文案依赖实施遵守）：§18.4 显式核对 UI 文案。
  - NOTE-4（E3 stub 仅语义验证非端到端联调）：§18 标注，E3 证明"入口产出合法 user_task 被后端接受=真控制器"，不替代未来真实 LLM e2e。。

---

## §18 实施自审（编码后）

### §18.1 实现落点对照表
| 设计项 | 落点 | 状态 |
|---|---|---|
| 纯函数 `buildUserTask` + `CHAT_PRESETS` + `KNOWN_AGENTS` | `web/src/utils/chatEntry.ts` | ✅ |
| 对话入口页 `/` | `web/src/views/ChatEntry.vue` | ✅ |
| 路由 `/` → ChatEntry（移除 redirect） | `web/src/router/index.ts:19` | ✅ |
| 市场「对话」闭环至 `/?agents=<id>` | `web/src/views/Agents.vue:178` | ✅ |
| 内联智能体选择 + 全选省略 agents | `ChatEntry.vue` `onSubmit` | ✅ |
| scope 恒非空兜底 | `chatEntry.ts` `DEFAULT_SCOPE` | ✅ |
| 后端改动 | 无（复用 `POST /tasks`） | ✅ |

### §18.2 E1–E7 实证
- **E1** `/` 解析到 ChatEntry：`vue-tsc --noEmit` EXIT=0（含 router 改动），无 redirect 残留。✅
- **E2** 快捷模板填充：`m9-5_verify.ts` E2_preset_scope/constraints/template 全 PASS（竞品快评 → scope=['竞品矩阵','差异化','份额']，template=competitor_review）。✅
- **E3** 提交触发真实建任务：进程内 stub `POST /tasks`（镜像 api.py:223/233 双校验）接收 ChatEntry 产出 → 201 + task_id（E3_real_post_accepted）；反向空 scope 被拒 400（E3_empty_scope_rejected）。**证明入口真驱动后端，非装饰**。✅
- **E4** 空 topic 不提交：`buildUserTask('   ')` 抛错（E4_empty_topic_throws）✅；按钮 `:disabled="!canSubmit"` 前端守卫。✅
- **E5** scope 恒非空：无模板 `buildUserTask('某主题')` → DEFAULT_SCOPE（E5_default_scope_nonempty）✅，满足后端 `api.py:233` 硬校验。
- **E6** agents 子集覆盖：`buildUserTask('x',undefined,['Researcher'])` → agents=['Researcher']（E6_agents_subset）；ChatEntry 全选时省略该字段回落默认编排（E6_full_agents_passthrough 验证透传）。✅
- **E7** 向后兼容：`/templates`、`/submit` 未改仍可直访；UserTask 契约不变；`vue-tsc` 全前端干净；pytest 3 failed/38 passed（与基线同 = TD-009 预存债，M9-5 零 Python 改动未增减）。✅

> 验证脚本 `C:\Users\sfkj\.workbuddy\m9-5_verify.ts` 真跑 9/9 PASS（含 E3 真 POST）。

### §18.3 关键实测发现
- **前端无 vitest**：以 `vue-tsc --noEmit`（类型校验，等价 py_compile）+ Node 22 `--experimental-strip-types` 真跑纯函数 + 进程内 stub POST 三重补偿，覆盖 E1-E7，证据真实非纸面。
- **`buildUserTask` 为唯一 payload 来源**：组件不另写映射（防"假配置"，落实 H2）。
- **E3 为语义验证非端到端**：stub 镜像后端双校验，证明"合法 user_task 被接受=真控制器"，不等同未来真实 LLM e2e（NOTE-4）。

### §18.4 诚实边界 R1-R5
- R1 模板引导式非 chatbot：`ChatEntry.vue` 文案仅"需要一份什么研报？/选个模板/快捷模板/生成研报"，**无"智能对话/自动理解意图"字样**（落实 H1、NOTE-3）。
- R2 单一 payload 来源（H2）：✅ 已证。
- R3 scope 恒非空（H3）：✅ E5。
- R4 任务历史不在范围（H4）：`/tasks` 列表页未新增，平台 §4 注记缺口如实保留。✅
- R5 后端零改动（H5）：仅复用 `POST /tasks`，无新端点。✅

### §18.5 向后兼容
纯前端改动 + 复用既有 API。`/templates`/`/submit` 保留；UserTask 契约不变；pytest 基线不变。✅

### §18.6 回归
`pytest` → 3 failed / 38 passed（TD-009 预存债，M9-4 同源，M9-5 未触碰）。无新增失败。✅

### §18.7 结论
实施自审通过：门禁 ① `vue-tsc` EXIT=0 + Node 验证 9/9；E1-E7 全实证；诚实边界 R1-R5 落实；向后兼容零影响。达到独立实施审议条件（仍须独立子代理落款，主代理不自签）。
