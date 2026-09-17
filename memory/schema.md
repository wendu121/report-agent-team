# memory/schema.md · 共享记忆空间 Schema 定义

> **状态**：M2 交付物（DESIGN.md v2.3 §7 的展开版，单一真相源）。
> 本文件定义 state 的全部字段、数据类型、非空约束与跨字段不变式。**LangGraph 代码（`orchestrator.py`，M3）与所有 Agent / Gate prompt 均须严格按本 schema 读写 state**——本文件是代码层与 prompt 层的共同契约。
> **纪律**：本文件只定义数据与约束，不含编排 / 路由 / 闸业务逻辑；后者见 DESIGN.md / gates/review.md。
> **日期**：2026-09-04

---

## 0. 全局硬约束（代码层 + prompt 层共守）

1. **结构化优先**：所有 Agent 产出与 Gate 产出均为机器可解析 JSON（见各节）。自由文本一律禁止，否则 state 解析失败、路由断裂。
2. **prior_versions 只做审计**：业务链路（Analyst / Writer / Gate）永远只读顶层最新产出字段（`retrieval_records` / `analysis_conclusions` / `draft_segments`），**禁止消费 `prior_versions` 做业务计算**。该字段仅用于人工审计 / 调试回溯。
3. **工作态全量替换**：Agent rework 时返回**完整修正后的数组**（全量替换顶层字段），不原地 patch 旧数组；旧值由引擎在替换前快照进 `prior_versions`。结构上杜绝旧错误条目残留（stale-error）。
4. **round 语义**：`routing_state.round` = 全局总执行轮次，首跑 = 1，每次 rework +1；`max_rounds=2` ⇒ 最多 1 次 rework（round 2 后若再需 rework，Router 直接 escalate，不进入 Agent）。
5. **JSON 崩坏容错（M3 引擎层责任）**：Agent / Gate 虽强制 JSON，但 LLM 偶发格式错乱。M3 须实现 JSON 修复 / 重试（截断补齐、re-ask 一次、schema 校验失败回退 rework）。**prompt 层只能约束，不能 100% 杜绝**——此容错在引擎代码，不在本 schema 解决。

---

## 1. 顶层任务 `user_task`

| 字段 | 类型 | 必填 | 约束 / 注释 |
|------|------|------|------------|
| `topic` | string | ✅ | 研报主题，非空，长度 ≤ 200 |
| `scope` | string[] | ✅ | 需覆盖的子问题列表，≥ 1 条；Researcher 须逐条覆盖 |
| `output_format_spec` | string | ❯ | 输出格式与引用规范；缺省回退 `template_manifest.output_format`（UIDESIGN §6）。枚举：`markdown` / `table` / `json` / `slide` |
| `constraints` | string[] | ❯ | 用户额外约束（如"必须含近一年数据""中文输出"） |

> 提交来源：UI `POST /tasks` body（UIDESIGN §3）。引擎不可修改，仅透传。

---

## 2. 产出层

### 2.1 `retrieval_records`（Researcher 写）
类型：`array<object>`，**非空数组**（检索全空 ⇒ GateA escalate）。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `id` | string | ✅ | 唯一，格式 `rec-{n}`；须被 `analysis_conclusions.source_ids` 引用 |
| `url` | string | ✅ | 来源 URL，非空，须真实可达（不得编造） |
| `title` | string | ✅ | 来源标题 |
| `snippet` | string | ✅ | 摘要 / 关键段落，非空 |
| `credibility` | string | ✅ | 可信度评级：`high` / `medium` / `low`；`low` 须在说明中解释原因 |

**非空约束**：数组长度 ≥ 1；每条 `id` 唯一。

### 2.2 `analysis_conclusions`（Analyst 写）
类型：`array<object>`，**非空数组**。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `id` | string | ✅ | 唯一，格式 `con-{n}`；须被 `draft_segments.conclusion_ids` 引用 |
| `claim` | string | ✅ | 结论陈述，非空 |
| `source_ids` | string[] | ✅ | **非空**，且每个 id 必须存在于 `retrieval_records` 的 id 集合（GateB 机器校验） |
| `confidence` | string | ✅ | 置信度：`high` / `medium` / `low` |

**非空约束**：数组长度 ≥ 1；`source_ids` 非空且全量可解析；不得出现 `retrieval_records` 中不存在的 id。

### 2.3 `draft_segments`（Writer 写）
类型：`array<object>`，**非空数组**。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `id` | string | ✅ | 唯一，格式 `seg-{n}` |
| `section` | string | ✅ | 章节名（如"市场概况""风险分析"） |
| `content` | string | ✅ | 段落正文，非空 |
| `conclusion_ids` | string[] | ✅ | **非空**，且每个 id 必须存在于 `analysis_conclusions` 的 id 集合（引用链机器校验） |

**非空约束**：数组长度 ≥ 1；`conclusion_ids` 非空且全量可解析。

### 2.4 `tool_status`
类型：`array<object>`。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `agent` | string | ✅ | 调用方：Researcher / Analyst / Writer |
| `tool` | string | ✅ | 工具名：web_search / data_proc / doc_export |
| `ok` | boolean | ✅ | 是否成功 |
| `error` | string | ❯ | `ok=false` 时必填，描述异常 |

**追加规则**：`tool_status` 由**引擎**在真实工具调用时写入——含 Researcher 检索预取（首轮 + 返工定向补充）、Analyst 计算求值（`tool_requests`）、Writer 引用章节重建与落盘，以及任一工具抛错；同一 Agent 一次多工具可含多条记录。Agent 输出中的 `tool_status` 字段**一律被忽略**（见下方「M4 语义变更」）。任一 `ok=false` ⇒ 该 Agent 产出标记异常 ⇒ 进入 Gate ⇒ Gate 据严重度判 rework / escalate（不静默崩）。

> **M4 语义变更（重要）**：`tool_status` 一律由**引擎**写入，反映真实工具执行结果。
> Agent 输出中的 `tool_status` 字段被**忽略**——M3 阶段它由 LLM 自称产出（说 `ok=true` 就是 true），
> 属于"假工具调用"，无法作为审计依据。若某轮未真实调用任何工具，则不产生记录（不补默认成功项）。

### 2.6 `search_results`（引擎写，M4）

类型：`array<object>`，元素结构同 `retrieval_records` 的字段契约（`id` / `url` / `title` / `snippet` / `credibility`），另含 `_query`（该条来源自哪次检索）。

**用途**：
1. **溯源校验基准**：Agent 节点与 GateA 双重校验 `retrieval_records.url` 必须命中本集合，未命中即判"疑似编造"（根治 DESIGN §10「大模型编造引用」风险）；
2. **审计**：记录本轮真实检索到了什么，与模型筛选后的结果可对照。

**约束**：按 url 去重、统一重编号 `rec-1..rec-n`；返工时**追加一次定向补充检索**并与既有集合合并（不重复全量检索——同 query 重搜结果相同，纯属浪费且修不好覆盖度）。

### 2.7 `report_path`（引擎写，M4）

类型：`string | null`。`doc_export` 落盘后的文件路径；未配置 `export_dir` 或导出失败则为 `null`（导出失败另写一条 `tool_status.ok=false`）。

### 2.5 `prior_versions`
类型：`object`，key = Agent 名。

```yaml
prior_versions:
  researcher: retrieval_records_snapshot[]   # 每元素为一轮重跑前的完整 retrieval_records 数组
  analyst:   analysis_conclusions_snapshot[]
  writer:    draft_segments_snapshot[]
```

**约束**：仅审计用途；业务链路不读。每次 rework 前由引擎快照当前顶层数组追加进对应列表。**下游 Agent / Gate 禁止读取本字段做业务判断**（见 §0-2）。

---

## 3. Gate 输出 `gate_output`（结构化，Router 解析依据）

类型：`object`，Gate 必须输出完整结构体（**禁止仅自然语言**）。

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `decision` | enum | ✅ | `advance` / `rework` / `escalate` |
| `reason` | string | ✅ | 可读评审理由 |
| `eval_score` | float \| null | ❯ | [0-1]；eval 失败时置 `null`（见 §6 降级） |
| `problem_points` | string[] | ✅ | 具体问题点；非空数组（无问题填 `["无"]`）；作 `rework_reason` 透传 Agent |

**非空约束**：四字段必出；`problem_points` 至少含一项。

---

## 4. 路由状态 `routing_state`（单一收敛，Router 维护）

| 字段 | 类型 | 维护方 | 约束 |
|------|------|--------|------|
| `round` | int | Router | 全局总执行轮次，首跑 = 1，rework +1 |
| `max_rounds` | int | 配置 | 默认 2；到达即 escalate（Router 判，Gate 不判） |
| `last_gate` | enum \| null | Router | `GateA` / `GateB` / `GateC`；初始 null |
| `status` | enum | Router | `running` / `done` / `escalated` |
| `rework_target_agent` | string \| null | Router | `Researcher` / `Analyst` / `Writer`；advance / escalate 时 null |
| `rework_reason` | string \| null | Router | 来自 `gate_output.reason` / `problem_points`；透传 Agent |
| `gate_review_history` | gate_output[] | Router | 每次闸门结果追加，交付附完整审计 |
| `engine_events` | engine_event[] | 引擎（M3 新增） | 引擎层事件留痕，仅审计；见 §4.1 |

**§4.1 `engine_events`（引擎层审计，非闸门评审）**

类型：`array<object>`，元素结构（公共字段 `agent` / `round` 视事件而定）：

```yaml
# ① Agent 产出不可用（M3 起）
engine_event:
  event: "agent_output_unusable"
  agent: Researcher | Analyst | Writer
  reason: str      # LLM 调用失败 / JSON 解析失败 / 生产数组缺失为空
  round: int       # 发生时的全局轮次

# ② 引用章节被代码确定性重建（M4 起）
engine_event:
  event: "writer_citation_regenerated"
  agent: Writer
  reason: str      # 引用章节不完整（缺 id / 缺 url / 含非检索来源 url）→ 由 doc_export 重建

# ③ 闸 LLM 不可用但代码硬校验通过 → 降级放行（M5 起）
engine_event:
  event: "gate_llm_unavailable_degraded_advance"
  gate: GateA | GateB | GateC
  reason: str      # 闸 LLM 输出不可解析（已重试 N 次）且代码硬校验通过 → 记审计后放行
  round: int
```

**语义分档**（UI 调试视图据此上色）：

| event | 严重度 | 含义 |
| --- | --- | --- |
| `agent_output_unusable` | 琥珀（已重试/返工） | Agent 这一轮没产出可用结果 |
| `writer_citation_regenerated` | 蓝（代码已修复） | 模型给的引用章节不合规，引擎已确定性重建，终稿可信 |
| `gate_llm_unavailable_degraded_advance` | 琥珀（降级放行） | 本闸的 LLM 主观评审**缺失**，放行依据仅是代码硬校验，需人工留意 |

**为什么单列而不并入 `gate_review_history`**：
1. `gate_review_history` 语义是"闸门评审结果"，引擎层产出崩坏不是闸门评审，混入会污染审计口径；
2. Agent 重试成功后 `rework_reason` 会被清空，若不留痕，"崩坏过几次、因何崩坏"将彻底丢失——这与系统"可审计"的核心目标冲突。

**触发时机**：Agent 节点的 JSON 容错（解析 → 重试一次 → 仍失败）与 LLM 调用异常；产出的 `gate_output.decision=rework` 仍是回 Router 的统一信号。

> **轮次边界（硬）**：Gate 不写轮次逻辑、不判断 `max_rounds`；仅识别业务致命问题（检索全空、编造引用）输出 `decision=escalate`。`max_rounds` 超限一律 Router 直接生成 escalate（DESIGN §2 职责边界澄清）。

---

## 5. 引用强制链（跨字段不变式）

```
draft_segments.conclusion_ids ⊇ 引用 analysis_conclusions.id
analysis_conclusions.source_ids ⊇ 引用 retrieval_records.id
```

任一环断（id 为空 / 不存在）⇒ 对应 Gate 判 rework。Writer 段不得出现 `analysis_conclusions` 外的断言；Analyst 结论不得出现 `retrieval_records` 外的来源。

---

## 6. eval 降级规则

`eval_score` 来自大模型评估调用，会遇 429 / 超时。失败时：
- `eval_score = null`；
- Gate 主流程不阻断，仅执行业务规则评审；
- `eval_score` 仅辅助，绝不做硬性 rework 阈值（rework 由 Gate 业务规则驱动，阈值待 M5 跑通后校准写入配置）。

---

## 7. 与 LangGraph 代码对接备注（M3）

- 本 schema 一切字段为 state 的**必填 / 可选**约定；`orchestrator.py` 须用 `TypedDict` / `Pydantic` 声明并校验。
- Agent 节点返回 dict 须直接 merge 进对应顶层字段（**全量替换**语义，见 §0-3）。
- Gate 节点返回 `gate_output` dict，Router 条件边依 `decision` 分支。
- 所有数组字段在节点入口做"非空 + id 存在性"**代码层校验**，失败即触发 rework（不依赖 LLM 判断）。
- `prior_versions` 的快照时机：Agent 节点在写出新顶层数组前，先将旧数组 append 进对应列表。
