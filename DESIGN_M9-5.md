# DESIGN_M9-5 · 研报对话式入口（Conversational Entry）

> 状态：待评审（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议），全 PASS 才动代码
> 关联：[DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.5（对话式入口 spec）、[API_SPEC.md](./API_SPEC.md)（user_task 契约）、[DESIGN_M9-4.md](./DESIGN_M9-4.md)（范式镜像）
> 立约：UI 必须是 controller 非 viewer；禁"假配置"；禁过度工程；先设计后编码；无 boss 显式「推送」指令不 `git push`。

---

## §0 范围裁定

### §0.1 领域替换
对话式入口是 Accio 图1「主对话页」的平台级功能，**直接适用**研报场景（"帮我写份 XX 行业研报"），**无电商内容搬入**，无需领域替换。

### §0.2 真·控制器（boss 铁律）
对话入口**不是装饰聊天框**：它结构化产出符合 `API_SPEC` 的 `user_task`，并调用真实 `POST /tasks`（`server/api.py:212` 已为真控制器：校验 topic+scope 非空 → 建任务 → 后台启动引擎 `run_report`），触发真实研报执行。禁用/留空 topic 真不提交；快捷模板真改变 scope/constraints/output_format_spec；"切换智能体"真改变编排子集（覆盖模板默认 agents，`api.py:267-284`）。

### §0.3 不做清单（诚实边界 + 禁过度工程）
- **不做真 LLM 意图解析**（平台设计 §9 Q3）：入口为**模板引导式**，非 chatbot。用户原文 → `topic`；快捷模板 → 结构化 `scope/constraints/output_format_spec`。**绝不声称**能解析任意自然语言为结构化字段。
- **不做任务历史列表页**：平台设计 §4 注记的"任务历史"缺口（无 `/tasks` 列表页）**不在 M9-5 范围**，单开里程碑（后端 `list_tasks` 已存在 `api.py:552`，但列表 UI 属范围蔓延）。
- **不新增后端端点**：复用既有 `POST /tasks`，不破坏 `API_SPEC` 契约。
- **不改动 UserTask 契约**：4 字段（topic/scope/constraints/output_format_spec）原样。

---

## §1 现状实证（基于真实代码，非印象）

- `web/src/router/index.ts:19`：`{ path: '/', redirect: '/templates' }` —— 当前首页是模板选择重定向。
- `web/src/views/TemplateSelect.vue`：当前 `/templates` 页（选模板 → `/submit`）。
- `web/src/views/TaskSubmit.vue`：`POST /tasks` 的前端载体；`onSubmit` 调 `task.createTask({user_task, template_id, agents?})` → 路由 `/tasks/:id`（TaskTracking）。`presetAgents` 读 `route.query.agents`（M9-1 市场「对话」入口）。**M9-5 复用同一 store action**。
- `web/src/stores/task.ts:100` `createTask` → `apiCreateTask`（`services/taskService.ts:11` `POST /tasks`）→ 真实建任务+引擎。
- `web/src/types/api.ts:30` `UserTask{topic,scope[],output_format_spec,constraints[]}`；`:124` `CreateTaskRequest{user_task,template_id?,agents?}`。
- `server/api.py:212` `create_task`：**topic 与 scope 均不可为空**（`:223` `:233` 双校验），缺 `template_id` 回落首个模板（`:249`）。`agents` 非空子集覆盖模板（`:267-284`）。
- `web/src/views/Agents.vue:179` `goChat(a)` → `router.push({ path: '/submit', query: { agents: a.id } })` —— 市场「对话」现回跳 `/submit`；M9-5 改为回跳 `/`，闭合至对话入口。
- 前端无 vitest/jest（`package.json` 仅 `vue-tsc`/`vite build`）→ 门禁 ① 用 `vue-tsc --noEmit` 类型校验；控制器效应实证用 Node 22 `--experimental-strip-types` 真跑纯函数 `buildUserTask`（抽离为可单测模块）。

---

## §2 Goals / Non-Goals

### Goals
1. `/` 改造为对话式研报入口（`ChatEntry.vue`）：中央输入 + 4 快捷模板 + 内联智能体选择 + 「切换智能体」链接。
2. 抽离纯函数 `buildUserTask(input, presetId?, agents?)`（`web/src/utils/chatEntry.ts`）为唯一 payload 来源，确保组件与验证共用同一逻辑（真控制器，无水份）。
3. 提交 → 真实 `createTask`（`POST /tasks`）→ TaskTracking；scope 恒非空满足后端契约。
4. 闭环「切换智能体」：`Agents.vue` 对话 → `/?agents=<id>`；`ChatEntry` 读 `route.query.agents` → 真实编排子集覆盖。
5. 向后兼容：`/templates`、`/submit` 不改、仍可直访；不破坏 `API_SPEC`。

### Non-Goals
- 真 LLM 对话解析（见 §0.3）。
- 任务历史列表页（见 §0.3）。
- 新后端端点 / UserTask 契约变更。

---

## §3 架构

```
ChatEntry.vue (/)                      ← 对话式入口（M9-5 新增）
   ├─ 中央输入 textarea（topic 原文）
   ├─ 4 快捷模板 chip（CHAT_PRESETS）
   ├─ 内联智能体选择（Researcher/Analyst/Writer toggle，默认全选）
   ├─ 「切换智能体」→ /agents
   └─ 提交 → buildUserTask(...) → task.createTask({user_task, template_id, agents?})
                                        │  POST /tasks（真实控制器，server/api.py:212）
                                        ▼
                              建任务 + 后台引擎 run_report
                                        ▼
                              router → /tasks/:id（TaskTracking，WS 实时追踪）
```

### §3.1 纯函数（唯一 payload 来源）
`web/src/utils/chatEntry.ts`：
```ts
export interface ChatPreset {
  id: string;
  label: string;
  template_id?: string;            // 关联后端模板（缺省回落首个）
  scope: string[];
  constraints: string[];
  output_format_spec: string;
}
export const CHAT_PRESETS: ChatPreset[] = [
  { id: 'standard', label: '标准研报', template_id: 'standard_research',
    scope: ['行业概况','竞争格局','关键风险','核心结论'],
    constraints: ['数据须标注来源','结论须可证伪'],
    output_format_spec: '结构化 Markdown：摘要/行业/竞争/风险/结论' },
  { id: 'competitor', label: '竞品快评', template_id: 'competitor_review',
    scope: ['竞品矩阵','差异化','份额'],
    constraints: ['对比须成对','引用公开数据'],
    output_format_spec: '对比表 + 一句话结论' },
  { id: 'scan', label: '行业扫描', template_id: 'industry_scan',
    scope: ['产业链','政策','技术趋势'],
    constraints: ['覆盖最近 12 个月'],
    output_format_spec: '行业地图 + 趋势清单' },
  { id: 'earnings', label: '财报解读', template_id: 'earnings_read',
    scope: ['三表','关键比率','现金流'],
    constraints: ['比率须同比/环比','附注异常须提示'],
    output_format_spec: '财务摘要 + 异常提示' },
];
const DEFAULT_SCOPE = ['行业概况','竞争格局','关键风险'];
export function buildUserTask(
  input: string,
  presetId?: string,
  agents?: string[]
): { user_task: UserTask; template_id?: string; agents?: string[] } {
  const topic = (input || '').trim();
  if (!topic) throw new Error('topic 不可为空');           // E4 守卫
  const preset = CHAT_PRESETS.find((p) => p.id === presetId);
  const user_task: UserTask = {
    topic,
    scope: preset?.scope?.length ? preset.scope : DEFAULT_SCOPE,   // E5 恒非空
    constraints: preset?.constraints ?? [],
    output_format_spec: preset?.output_format_spec ?? '',
  };
  const out: { user_task: UserTask; template_id?: string; agents?: string[] } = { user_task };
  if (preset?.template_id) out.template_id = preset.template_id;
  if (agents?.length) out.agents = agents;                  // E6 编排覆盖
  return out;
}
```
> 纯函数无副作用、无跨模块 import（仅 `import type { UserTask }`），可被 Node `--experimental-strip-types` 直接执行验证。

### §3.2 组件（ChatEntry.vue）
- `topic` 输入框（placeholder "需要一份什么研报？"）。
- 4 chip 绑定 `selectedPreset`，点击设 `presetId`。
- 内联智能体 toggle：`knownAgents = ['Researcher','Analyst','Writer']`；`selectedAgents` 默认全选；导出为 `agents` 子集（仅当用户取消某角色时传，减少噪音）。
- 提交 `onSubmit`：`const { user_task, template_id, agents } = buildUserTask(topic, presetId, selectedAgents)`，调 `task.createTask({ user_task, ...(template_id?{template_id}:{}), ...(agents?{agents}:{}) })` → `router.push({name:'TaskTracking', params:{taskId}})`。
- 提交按钮 `:disabled="!topic.trim()"`（E4 前端守卫）。
- 「切换智能体」→ `router.push('/agents')`。

### §3.3 路由与闭环
- `router/index.ts:19`：`{ path: '/', component: ChatEntry, ... }`（移除 redirect）。
- `Agents.vue:179`：`goChat` → `router.push({ path: '/', query: { agents: a.id } })`（闭合至对话入口）。
- `ChatEntry` `onMounted`/watch 读 `route.query.agents` → 预置 `selectedAgents`（与 TaskSubmit 同范式）。**注（NOTE-1）**：对话入口的内联表单（topic + 模板 + 智能体选择）即平台 §3.5 所述"任务提交确认"层，提交即建任务，有意跳过独立 `/submit` 页，属合理简化非缺陷。

---

## §4 前端改动清单
| 文件 | 改动 |
|---|---|
| `web/src/utils/chatEntry.ts` | 新增：`CHAT_PRESETS` + `buildUserTask`（纯函数，唯一 payload 来源） |
| `web/src/views/ChatEntry.vue` | 新增：对话式入口页 |
| `web/src/router/index.ts` | `/` → ChatEntry（移除 redirect）；import ChatEntry |
| `web/src/views/Agents.vue` | `goChat` 回跳 `/?agents=<id>`（闭环） |
| `web/src/layouts/DefaultLayout.vue` | 首页/Logo 指向 `/`（已存在，确认无误即可，通常不改） |

> 无新后端端点、无新 bind mount（纯前端 + 复用 `/tasks`）。后端零改动。

---

## §5 数据流
输入原文 → `buildUserTask`（topic+scope/constraints/output_format_spec+agents） → `task.createTask` → `POST /tasks`（真实校验+建任务+引擎） → WS `task_done`/`task_escalated`（M9-4 推送钩子生效） → TaskTracking 展示。与既有契约完全一致。

---

## §6 校验（门禁 ①）
- 前端 `vue-tsc --noEmit`（类型校验 ChatEntry.vue + chatEntry.ts + router 改动）替代 py_compile。
- `buildUserTask` 用 Node 22 `node --experimental-strip-types m9-5_verify.ts` 真跑 E2/E4/E5/E6。

---

## §7 热加载
纯前端，无后端配置/重启；`vite dev` HMR 即时生效。无新 SoT 文件（快捷模板内置于 `CHAT_PRESETS`，非 YAML——属前端静态配置，与 skills/channels 的"需 admin 改"不同，快捷模板无需运行时热改，故不入 config/）。

---

## §8 验证计划（E1–E7）
| # | 验证项 | 方法 | 预期 |
|---|---|---|---|
| E1 | `/` 解析到 ChatEntry | `vue-tsc` + 路由存在 | 无 redirect，组件可编译 |
| E2 | 快捷模板填充 user_task | Node 跑 `buildUserTask('x','competitor')` | scope/constraints/output_format_spec 来自 preset |
| E3 | 提交触发真实建任务 | Node + 本地 stub `POST /tasks`（镜像 api.py 双校验）→ `buildUserTask` 产物 POST → 返回 task_id | 真实控制器（非空 scope 被接受） |
| E4 | 空 topic 不提交 | `buildUserTask('')` 抛错 + 按钮 `:disabled` | 守卫生效 |
| E5 | scope 恒非空 | `buildUserTask('某主题')`（无 preset） | scope = DEFAULT_SCOPE，满足后端契约 |
| E6 | agents 子集覆盖 | `buildUserTask('x',undefined,['Researcher'])` | out.agents=['Researcher']，流入 createTask → 引擎编排覆盖 |
| E7 | 向后兼容 | `/templates`、`/submit` 仍可直访 + `vue-tsc` 干净 | 无契约破坏 |

> E3 用 Python 起 stub HTTP（`POST /tasks` 镜像 `api.py:223/233` 校验，返回 `{"task_id":...}`），Node 侧 `buildUserTask` 产物 `fetch` POST 验证被接受——证明入口真驱动后端（非装饰）。

---

## §9 诚实边界
- H1 模板引导式，非 LLM chatbot：文档/UI 文案称"快捷模板/引导"，**不写**"智能对话/自动理解意图"。
- H2 buildUserTask 为唯一 payload 来源：组件不另写一份映射，避免"假配置"（UI 改了但引擎忽略）。
- H3 scope 恒非空：满足后端硬校验，入口不会产出被拒的空 scope。
- H4 不声称任务历史：列表页明确不在 M9-5（见 §0.3）。
- H5 后端零改动：复用 `POST /tasks`，诚实标注"无新端点"。

---

## §10 提交纪律
- `git commit`（不 push）：`git add report-agent-team` 后 commit，含 `DESIGN_M9-5.md` / `VERIFICATION_M9-5.md` / `REVIEW_M9-5.md` + 代码。
- 无 boss 显式「推送」指令不 `git push`。
- 三道闸：① `vue-tsc` + Node 验证 ② `VERIFICATION_M9-5.md` 自审（§17 设计 + §18 实施 E1-E7）③ 独立子代理 `REVIEW_M9-5.md`（设计轮 + 实施轮，主代理不自签）。

---

## §11 与 M9-1~M9-4 同构 & 与平台 §3.5 一致
- 复用 M9-1 `?agents=` 编排覆盖范式（TaskSubmit 已证）。
- 复用 M9 统一视觉（白底 + 绿 `#10B981`，见 DESIGN_PLATFORM_FUNCTIONS.md §6）。
- 与 §3.5 一致：`/` 对话入口 + 快捷模板 + 切换智能体；最终产出 `user_task` 不破坏契约。
- 平台 §3.5 将随 M9-5 交付标注 ✅（route 表 + 路线图）。
