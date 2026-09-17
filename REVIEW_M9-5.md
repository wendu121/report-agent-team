# REVIEW_M9-5 · 研报对话式入口 独立审议（设计轮）

> **立场声明**：本文件由独立子代理在 edict-gate 第 ③ 道闸（设计阶段独立审议）产出。撰写前**未与主代理串通**，所有结论基于本人独立 `Read`/`Grep` 真实代码取证，不采信 `VERIFICATION_M9-5.md` 自述为已证实结论（仅作对照）。

---

## 一、逐项审议结论

### 1. 真控制器效应（boss 铁律）
- **实证**：`/`（router/index.ts:19）现为 `redirect: '/templates'`，M9-5 计划改为 `ChatEntry`（设计 §3.3）。`ChatEntry` 计划复用 M9-1 同款 action：`TaskSubmit.vue:84` 实际调 `task.createTask({user_task, template_id, agents?})` → `task.ts:100` `createTask` → `taskService.ts:11` `POST /tasks` → `api.py:212` `create_task`（已证真控制器：`:223/:233` 双非空校验 → 建任务 → 后台引擎 `run_report`，见 api.py:286+）。链路完整，入口确为 controller 非 viewer。
- **agents 子集覆盖**：设计 §3.1 `buildUserTask` 将 `agents` 透传至 `out.agents`，经 `createTask.agents` 落入 `api.py:267-284` 子集校验与编排覆盖（与 TaskSubmit 同范式，已实证）。✓
- **快捷模板真改 scope/constraints/output_format_spec**：`buildUserTask` 以 preset 字段填充 `user_task`（§3.1:108-113），非装饰。✓
- **结论**：真控制器效应成立，无“假配置”迹象。PASS。

### 2. 诚实边界
- **§0.3/§9 不做真 LLM 解析**：设计明确列为非目标，并规定 UI 文案不得写“智能对话/自动理解意图”。与平台 §3.5（图1 主对话页）的“模板引导式”定位一致。
- **唯一 payload 来源**：`buildUserTask` 抽离为纯函数，组件不另写映射，防“UI 改了引擎忽略”。设计层面声明充分。
- **scope 恒非空**：`DEFAULT_SCOPE` 兜底（§3.1:99,110），满足 `api.py:233` 后端硬校验。✓
- **结论**：诚实边界设计正确。NOTE：UI 文案细则需在实施轮复核是否落“不写智能对话”硬约束。

### 3. 可行性
- **实证**：`web/package.json` 仅含 `vue-tsc`/`vite build`，无 vitest/jest（已确认）。设计以 `vue-tsc --noEmit` + Node 22 `--experimental-strip-types` 真跑纯函数补偿，路径合理。
- `buildUserTask` 仅 `import type { UserTask }`（§3.1:104 类型注解，无运行时跨模块 import），可被 Node 直接执行。E2/E4/E5/E6 实证路径真实可达。✓
- **结论**：可行。PASS。

### 4. 向后兼容
- **实证**：改动清单（§4）仅新增 `chatEntry.ts`/`ChatEntry.vue`，改 `router/index.ts:19` 与 `Agents.vue:179`，**不删不改** `/templates`、`/submit` 路由定义（router:28-29 仍存在）。`UserTask` 契约（api.ts:30）不变，`CreateTaskRequest` 已含 `agents?`（api.ts:124）。无新后端端点（复用 `POST /tasks`）。
- **结论**：向后兼容成立。PASS。

### 5. 与 M9-1~4 同构 & 平台 §3.5 一致
- **同构**：复用 M9-1 `?agents=` 编排覆盖范式（TaskSubmit:57-62 已实证；ChatEntry §3.3 同读 `route.query.agents`）。
- **§3.5 一致**：平台 §3.5（DESIGN_PLATFORM_FUNCTIONS.md:189-198）定义 `/` 中央输入框 + 4 快捷模板（标准研报/竞品快评/行业扫描/财报解读）+ 切换智能体 → 产出 `user_task`。设计 §3.1 的 `CHAT_PRESETS` 四模板与 §3.2 输入/切换结构逐项对齐。✓
- **结论**：一致。PASS。

### 6. 禁过度工程 / 范围
- **实证**：任务历史列表页正确排除（§0.3，并引用平台 §4 缺口说明 line 234-237）。设计未引入新 bind mount、新 SoT 文件（CHAT_PRESETS 内置前端，§7 已论证不入 config/）。
- **结论**：范围克制，无蔓延。PASS。

---

## 二、最终裁定

**PASS_WITH_NOTES**

- **MAJOR = NONE**（真控制器效应、诚实边界、向后兼容三项核心门禁均无破坏；未见需返修的设计缺陷）。
- 主代理自审（VERIFICATION §17.8）结论与本人独立取证一致，但本裁定独立于其自述。

---

## 三、NOTES（非阻塞改进项）

1. **平台 §3.5 line 196 的“跳任务提交确认”差异**：平台 spec 写“输入后 → 解析为 user_task → 跳任务提交确认 → 执行”，而设计 §3 直接 `createTask → router.push(TaskTracking)`，**跳过 /submit 确认页**。属合理简化（仍产出合规 user_task，不破契约），但与 spec 字面有出入；建议实施轮在文档注明“直接建任务，无需二次确认”，或在 §3.5 标注差异，避免后续误解。
2. **切换智能体闭环的单 agent 语义**：`Agents.vue:179` `goChat(a)` 仅传单 `a.id`（如 `Researcher`），而 `ChatEntry` §3.2 默认全选三 agent。设计 §3.3 仅写“预置 selectedAgents”，未明“单 agent 时是否覆盖默认值”。建议实施轮明确：单 agent 回跳时 `selectedAgents=[该agent]`（覆盖默认全选）还是“预选该 agent 并保留其余”——影响最终 `agents` 透传（§3.1 E6 仅在取消角色时传）。属实现细节，不阻塞设计。
3. **UI 文案硬约束落地**：§9 H1 要求 UI 禁写“智能对话”，此约束依赖实施轮遵守；设计轮已声明，建议实施轮在 VERIFICATION §18.4 显式核对。
4. **E3 stub 验证的环境耦合**：E3 依赖 Python stub 镜像 api.py 校验，与真实后端并行；建议实施轮注明 stub 仅验证“非空 scope 被接受”语义，不等于端到端联调。

---

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 设计独立审议 · 独立子代理，主代理严禁自签 -->

---

## 实施独立审议（第 2 轮）

> **立场声明**：本实施轮由独立子代理在 edict-gate 第 ③ 道闸（实施阶段独立审议）独立产出。撰写前**未与主代理串通**；全部结论基于本人独立 `Read` 真实代码 + 交叉运行 `m9-5_verify.ts` / `vue-tsc` / `pytest` 取证，不采信 `VERIFICATION_M9-5.md` 自述为已证结论（仅作对照）。

---

### 1. 真控制器效应（boss 铁律）

- **实证（代码 Read）**：`ChatEntry.vue:121` 调 `task.createTask({user_task, ...})` → `stores/task.ts:100` `createTask` → `services/taskService.ts:11` `api.post('/tasks')` → `server/api.py:212` `create_task`（真控制器）。链路完整，入口确为 controller 非 viewer。
- **E3 真 POST 被接受（独立运行取证）**：
  ```
  PASS E3_real_post_accepted :: status=201 task_id=stub-pl1wrsb8gn
  PASS E3_empty_scope_rejected :: status=400
  ```
  进程内 stub 镜像 `api.py:223/233` 双非空校验，合法 user_task 被接受、空 scope 被拒 → 驱动真实后端校验，证明非装饰。
- **agents 子集覆盖**：`ChatEntry.vue:119` 将 `selectedAgents`（非全选时）经 `createTask.agents` 透传；`api.py:267-284` 以 `request.agents` 覆盖模板编排（代码 Read + E6 实证）。
- **快捷模板真改 scope/constraints（E2 实证）**：
  ```
  PASS E2_preset_scope :: ["竞品矩阵","差异化","份额"]
  PASS E2_preset_template :: competitor_review
  ```
- **结论**：真控制器效应成立，无「假配置」。`buildUserTask` 为唯一 payload 来源（`ChatEntry.vue:116` 直调，组件无第二份映射）。PASS。

### 2. 诚实边界

- **H1 / R1（UI 禁「智能对话」）**：独立 Read `ChatEntry.vue` 文案 —— 仅「需要一份什么研报？」「选个模板」「智能体组合」「生成研报」「切换智能体 →」，**无「智能对话 / 自动理解意图」字样**。落实 H1 / NOTE-3。
- **H2 / R2（唯一 payload 来源）**：`buildUserTask`（`chatEntry.ts:60`）为组件唯一映射，已证。PASS。
- **H3 / R3（scope 恒非空）**：独立运行 `PASS E5_default_scope_nonempty :: ["行业概况","竞争格局","关键风险"]` → 满足 `api.py:233` 硬校验。PASS。
- **H4 / R4（任务历史排除）**：未新增 `/tasks` 列表页（router 无该路由），平台 §4 缺口如实保留。PASS。
- **H5 / R5（后端零改动）**：`git status` 显示 `server/` 未进入修改列表；`api.py` 改动计数为 0。PASS。

### 3. 向后兼容

- **实证（代码 Read）**：`router/index.ts:30-31` `/templates`、`/submit` 路由定义**原样保留**，仍可直访；无 redirect 残留（`:21` 为 `component: ChatEntry`，非 redirect）。
- **UserTask 契约不变**：`chatEntry.ts` 产出字段完全对齐 `api.ts:30` 的 `UserTask`。
- **vue-tsc 干净（独立运行取证）**：`VUE_TSC_EXIT=0`（零类型错误）。
- **pytest 基线不变（独立运行取证）**：`3 failed, 38 passed` —— 与设计轮/基线一致的 **TD-009 预存债**（3 失败），M9-5 零 Python 改动未增减失败数。PASS。

### 4. 与 M9-1~4 同构 & 平台 §3.5 一致

- **同构**：复用 M9-1 `?agents=` 范式 —— `Agents.vue:178-181` `goChat` 回跳 `/?agents=<id>`，`ChatEntry.vue:84-90` `onMounted` 读 `route.query.agents` 预置编排子集（覆盖默认全选）。与 TaskSubmit 同读范式。
- **§3.5 一致**：四快捷模板（标准/竞品/行业扫描/财报解读）与 `DESIGN_PLATFORM_FUNCTIONS.md:194` 逐项对齐；统一绿 `#10B981` 视觉（ChatEntry `.chip.active`/`.chat-submit`）。
- **结论**：一致。PASS。

---

## 最终裁定

**PASS_WITH_NOTES**

- **MAJOR = NONE**：真控制器效应（ChatEntry→POST /tasks→引擎，E3 真 POST 201）、诚实边界（H1-H5 全落实，UI 无禁用文案、scope 恒非空、后端零改动）、向后兼容（`/templates`+`/submit` 可直访、UserTask 契约不变、vue-tsc 0 错、pytest 基线 3 失败未变）三项核心门禁均无破坏。
- 主代理自审（VERIFICATION §18.7）结论与本人独立取证一致，但本裁定独立作出，不依赖其自述。

---

## NOTES（非阻塞改进项）

1. **平台 §3.5 交付标注未同步**：设计契约 §11 承诺「平台 §3.5 随 M9-5 交付标注 ✅」；独立 Read 确认 `DESIGN_PLATFORM_FUNCTIONS.md:301` 路线图 M9-5 行**仍无 ✅ 已交付**标记（现状为「待补」）。属文档同步遗漏，非代码缺陷；建议后续补标 ✅（本代理仅追加 REVIEW，不动其他文件）。
2. **「跳过 /submit 确认页」差异**：设计轮 NOTE-1 已记。实施确认 ChatEntry 内联表单即「任务提交确认」层，直接 `createTask → TaskTracking`，E3 已证为真控制器；合理简化，非缺陷。
3. **E3 为语义验证非端到端**：`m9-5_verify.ts` 用进程内 stub 镜像后端双校验，证明「合法 user_task 被接受 = 真控制器」，不等于未来真实 LLM e2e；属已知边界，已在脚本标注。
4. **单 agent 回跳语义已落实**：`Agents.vue` `goChat` 传单 `a.id` → ChatEntry `onMounted` 预置 `selectedAgents=[该id]`（覆盖默认全选），E6 `agents` 子集透传确认；设计轮 NOTE-2 闭环。

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 实施独立审议第 2 轮（独立子代理，主代理严禁自签） -->
