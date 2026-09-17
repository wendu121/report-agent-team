# UIDESIGN・研报多 Agent 协作系统・界面设计

> **状态**：v1.4（v1.3 M1 彻底完成 + M5 字段对齐：§8 追加 `engine_events[]`）。本文件仅描述**界面交付层**，不定义也不改写引擎层逻辑。
> **纪律**：先出设计文档，不写代码。评审通过前不落地任何前端实现。
> **日期**：2026‑09‑04
> **姊妹文档**：`DESIGN.md`（v2.3，引擎层单一真相源）。本文件的全部运行时状态字段均来自 DESIGN §7，禁止在本文件 "发明" 新运行时状态。
> **本次修订变更**：基于桌面 v1.0 定稿版并入；并采纳 4 点 critique —— ① §10 与正文 v1 范围矛盾收口（引用链高亮 / 重跑已闭合，移出开放问题）；② 新增 §5.1 语义聚合映射表（UI 层渲染规则，不依赖引擎 M3）；③ §7.5 补重跑暖启动 v2 前向项；④ §6 澄清 manifest.output_format 与 user_task.output_format_spec 优先级。引擎层完全不变。

---

## 0. 元信息

| 项 | 值 |
| --- | --- |
| 项目名 | report‑agent‑team |
| 文档定位 | **UI 交付层设计**（引擎层见 `DESIGN.md`） |
| 技术栈（UI） | **Vue3 SPA；WebSocket 做状态推送；与引擎以 REST+WebSocket 解耦**（详见 §3） |
| 当前状态 | v1.4 · M1 彻底完成（§10 全收口 + Q4 重跑延后 v2 已对齐 §7.5/§9，无未拍板开放点）；M5 字段对齐追加 `engine_events[]` |

---

## 1. 边界红线（防混淆，最重要）

本文件最容易出错点：**不要把 UI 当成了编排器**。下面三条是硬边界，违反任一条即视为设计缺陷。

### 1.1 UI 是什么

- 引擎运行的**观测面与交付面**：展示任务状态、Agent 产出、Gate 决策、rework 原因、最终研报。
- 用户意图的**输入面**：提交 `user_task`、在 escalate 终止后做人工确认 / 重新发起任务。

### 1.2 UI 不是什么（禁止）

- ❌ **不定义 Agent 角色**—— 角色在 `DESIGN.md §5` / `agents/*.md`。
- ❌ **不定义编排图与路由规则**—— 在 `DESIGN.md §2` / `orchestrator.py`。
- ❌ **不定义 Gate 审查规则**—— 在 `DESIGN.md §6` / `gates/review.md`。
- ❌ **不发明运行时状态字段**—— 所有展示运行时数据来自 `DESIGN.md §7` 的状态投影（见 §8 对照表）。
- ❌ **不做工作流搭建器**—— 禁止 "拖节点改图" 式编辑，UI 只消费引擎暴露的状态，不让用户修改业务工作流图。

### 1.3 数据流方向（单向）

```
引擎 state（DESIGN §7）  ──投影──▶  UI 渲染
用户提交 user_task / escalate后人工操作  ──API──▶  引擎
```

UI **只读**引擎状态投影；UI **只回写**两类用户输入：`user_task` 提交、escalate 终止后的人工业务操作。其余业务逻辑一律由引擎计算。

---

## 2. 设计原则

1. **模板驱动**：界面形态由 "所选任务模板" 的静态描述决定；支持两种 UI 运行模式：统一外壳 / 模板自定义组件。
2. **状态同源**：UI 展示的每个运行时字段，都能在 §8 对照表里找到 DESIGN §7 的来源字段；无来源即违规。
3. **可见性优先**：v1 的最高 ROI 是 "实时追踪时间线"—— 把 Gate 在哪道卡住、为何打回、第几轮收敛可视化出来（普通单 Agent 工具没有的叙事能力）。
4. **插件化输出渲染**：输出格式由模板配置驱动，不硬编码渲染组件。
5. **不造 builder**：UI 是交付壳，不是编排平台。

> ### 架构取舍：双模式模板 UI 架构（方案 B，渐进式启用）
>
> 本 UI 支持两种 UI 运行模式，由`template_manifest.ui_mode`控制。
>
> 1. `unified_shell`【**v1 完整实现，默认模式**】：统一 UI 外壳。更换模板仅改变表单字段、时间线节点集合、输出渲染插件，页面整体骨架保持不变；新增模板无需修改 UI 业务代码。
> 2. `custom_component`【**预留扩展，v1‑v2 不实现，v3 启用**】：模板包可携带完全自定义前端组件，支持异构交互界面（聊天、看板等）。
>    - 安全约束：自定义组件运行于隔离 iframe 沙箱；仅调试 / 管理员模式允许加载；普通业务用户不可使用；
>    - 契约约束：通过 postMessage 代理访问引擎能力，状态严格遵循`DESIGN.md §7`schema；声明`custom_ui_contract_version`做版本兼容校验；
>    - 即便使用自定义 UI，**审计包、gate 评审记录全部由后端引擎生成，前端组件不可篡改审计数据**。
>
> > 重要：v1‑v2 阶段全部业务模板均使用`unified_shell`模式；`custom_component`仅做架构预留，不用于生产业务。

---

## 3. 技术解耦（UI ↔ 引擎）

| 通道 | 方式 | 说明 |
| --- | --- | --- |
| 提交任务 | REST `POST /tasks` | body = `user_task`（DESIGN §7 结构） |
| 状态推送 | WebSocket `/tasks/{id}/stream` | 每次 Agent 产出 / Gate 决策 / 轮次变化推送增量事件 |
| 结果拉取 | REST `GET /tasks/{id}` | 返回完整 state 投影（含 `gate_review_history`） |
| 人工复核操作 | REST `POST /tasks/{id}/review` | escalate 终止后人工确认 / 重跑发起，回写引擎 |
| 审计包下载 | REST `GET /tasks/{id}/audit‑export` | v1 实现，输出完整审计归档包 |

> UI 不知道 LangGraph /new‑api / Tavily 的存在；它只认 "事件流 + 状态投影"。引擎需提供一份**事件契约**（Agent 产出事件、Gate 决策事件、轮次事件），本文件 §5 的时间线即基于该契约。契约完整细节在引擎 M3 落地时细化，本文件只声明契约的事件枚举。

> WebSocket 事件枚举示例（M3 细化完整 payload，此处仅定义 type）

```
# event_type 枚举
event_type:
  - agent_complete
  - gate_complete
  - rework_trigger
  - round_update
  - tool_error
  - task_done
  - task_escalated
payload: {} # payload全部字段来自DESIGN §7状态
```

---

## 4. 信息架构（v1 页面树，unified_shell 模式）

```
flowchart LR
    A[模板选择] --> B[任务提交]
    B --> C[运行追踪]
    C --> D{终态?}
    D -->|done| E[结果/研报]
    D -->|escalated| F[人工复核]
    F --> E
    C -.rework 回退.-> C
```

| 页面 | 作用 | 数据来源（DESIGN 字段 / UI 静态配置） |
| --- | --- | --- |
| 模板选择 | 选任务模板，看简介 | `template_manifest`（§6，静态配置） |
| 任务提交 | 填 `user_task` | `user_task` |
| 运行追踪 | 实时时间线 | `routing_state` + `gate_review_history` + 各产出层 |
| 结果 / 研报 | 展示输出内容 + 引用 | `draft_segments` / 任务产出，由`output_format`插件渲染 |
| 人工复核 | escalate 终止时确认 / 发起重跑 | `gate_review_history` + `routing_state.status=escalated` |

> 注：当模板`ui_mode=custom_component`，以上页面全部由模板内自定义组件接管，v1 不实现该能力。

---

## 5. 核心界面：实时追踪时间线（v1 重点，unified_shell 模式）

> 本系统 UI 差异化界面。**时间线存在两套视图，由全局调试模式开关切换；后端 WebSocket 原始事件保持不变，仅 UI 层做视图聚合转译。**

### 5.1 两种视图模式

1. **默认：用户‑语义叙事视图（面向业务人员）**
   - 将底层原始事件做语义聚合、业务化转译；展示业务可读描述，例：`🔍调研完成 → 🔁被GateA打回：子问题覆盖不全 → 🔍重新执行调研 → ✅GateA审核放行`。
   - 屏蔽底层技术事件标识符：`agent_complete`、`gate_complete`等技术名词不对外展示；`prior_versions`历史快照完全不可见。
2. **🔧调试模式开启：原始事件视图（面向 Prompt 工程师、测试）**
   - 原样透传 WebSocket 原始事件流；完整展示原始事件类型、`prior_versions`历史快照、原始 state 片段，用于问题排查。

#### 5.1.1 语义聚合映射规则（UI 层渲染规则，不依赖引擎 M3）

> 下方映射是 **UI 渲染规则**，不属于引擎逻辑；引擎只需保证事件枚举与 payload 字段来自 DESIGN §7。v1 前端依此表实现"原始事件 → 业务叙事"转译，无需等 M3。

| 原始事件（event_type） | payload 关键字段 | 用户叙事视图文案（示例） |
| --- | --- | --- |
| `agent_complete`（Researcher） | `agent=Researcher` | 🔍 调研完成（N 条素材） |
| `agent_complete`（Analyst） | `agent=Analyst` | 📊 分析完成（M 条结论） |
| `agent_complete`（Writer） | `agent=Writer` | 📝 撰稿完成 |
| `gate_complete`（advance） | `decision=advance`, `gate` | ✅ <Gate> 审核放行 |
| `gate_complete`（rework） | `decision=rework`, `rework_reason` | 🔁 被 <Gate> 打回：<rework_reason> |
| `rework_trigger` | `rework_target_agent` | ↩ 重新执行 <agent> |
| `round_update` | `round`, `max_rounds` | 第 N / max_rounds 轮 |
| `tool_error` | `agent`, `tool` | ⚠️ <agent> 工具 <tool> 异常 |
| `task_done` | `status=done` | 🎉 研报生成完成 |
| `task_escalated` | `status=escalated` | ⛔ 已升级（需人工复核） |

### 5.2 时间线节点类型（底层原始事件，引擎输出不变）

每个节点 = 一次引擎事件，字段全部来自 DESIGN §7：

| 节点 | 触发 | 展示字段（来源） |
| --- | --- | --- |
| Agent 产出 | 某 Agent 完成 | Agent 名 + 产出摘要（`retrieval_records` / `analysis_conclusions` / `draft_segments` 条数 / 预览） |
| Gate 决策 | 一道闸完成 | `gate_output.decision` + `reason` + `eval_score` + `problem_points`；颜色：advance = 绿 /rework = 琥珀 /escalate = 红 |
| rework 回退 | `decision=rework` | 箭头指回 `rework_target_agent` + 标注 `rework_reason` |
| 轮次 | `routing_state.round` 变化 | 顶部进度 "第 N /max_rounds 轮" |
| 工具异常 | `tool_status.ok=false` | 节点角标告警，关联 Agent |
| 终态 | `status=done \| escalated` | 收束到结果页 / 复核页 |

### 5.3 交互

- 点节点展开：看该 Agent 完整产出数组 / 该 Gate 完整 `gate_output`（含 `problem_points`）。
- 引用链高亮：在 writer 段点某 `conclusion_ids` → 跳到对应 analyst 结论 → 跳到 `source_ids` 检索记录。

> 备注：引用链跳转仅**纯前端视图定位**，不会修改、重跑引擎业务逻辑。

- 暂停 / 继续：仅前端暂停渲染，不改引擎（可选，v1 可不做）。

> 普通用户默认不展示 `prior_versions`历史快照；**仅调试模式开启查看历史快照入口**。

### 5.4 为什么这事值得做

单 Agent 工具只给 "最终答案"；本系统因有 Gate + rework 环，能讲清楚**"错误被闸在第几道、打回原因、第几轮收敛"**—— 这条时间线本身就是产品卖点，且数据全来自已有 state，零额外埋点。

---

## 6. 模板驱动：template_manifest（静态配置）

> 这是本文件**唯一新增的静态配置**（不属于引擎运行时 state）。它描述 "一个任务模板长什么样、UI 怎么呈现"，由 UI 与 orchestrator 共同消费。放在 `templates/<name>/manifest.yaml`。

> 安全备注：manifest 仅做 UI 静态渲染配置，**不能覆盖 / 改写 agents/*.md、gates/review.md 业务逻辑**；业务逻辑仍然由引擎读取 md 文件执行。manifest 只控制 UI 表单、时间线展示、渲染开关、UI 运行模式。

```
template_manifest:
  id: research-report
  name: 研报
  description: 调研→分析→撰稿三角色协作，带三道审核闸

  # UI模式二选一：unified_shell(默认v1实现) / custom_component(v3预留)
  ui_mode: unified_shell
  # ui_mode: custom_component
  # custom_ui:
  #   entry: ./components/main.vue
  #   expose_api: true
  #   custom_ui_contract_version: "1.0"

  agents: [Researcher, Analyst, Writer]     # UI 据此渲染时间线 Agent 节点顺序
  gates: [GateA, GateB, GateC]              # UI 据此渲染闸节点
  output_format: markdown                   # 输出渲染插件枚举：markdown | table | json | slide
  ui_hints:
    submit_fields: [topic, scope, output_format_spec, constraints]  # 映射 user_task 字段
    show_citation_chain: true              # 是否启用引用链高亮
```

**output_format 优先级（澄清）**：`manifest.output_format` 是模板默认值；用户在提交页可经 `user_task.output_format_spec` 覆盖；结果页渲染以 **`user_task.output_format_spec` 为准**（缺省回退 manifest 值）。两者为同一概念的"默认值 / 用户覆盖"关系，非两个独立字段。

**关键**：v1 仅落地 `research‑report` 一个模板；`output_format`为插件枚举，v1 仅实现`markdown`渲染插件，`table/json/slide`做定义预留，后续迭代实现。
当`ui_mode: unified_shell`，新增业务模板只增加 manifest + agents/*.md + gates/*.md，UI 业务代码无需改动。

---

## 7. 各页面详设（v1 草图，unified_shell 模式）

### 7.1 模板选择页

- 卡片列表，每张卡读一个 `template_manifest`（name/description/agents 数 /gates 数 /ui_mode）。
- 仅调试管理员账号可见标记：`custom_component`类型模板；普通用户只展示`unified_shell`模板。
- 点 "开始"→ 任务提交页，携带该模板的 `submit_fields`。

### 7.2 任务提交页

- 表单字段 = 当前模板 `ui_hints.submit_fields`，对应 `user_task` 四字段（topic/scope/output_format_spec/constraints）。
- scope 支持多行（子问题列表）；constraints 自由文本。
- 提交 → `POST /tasks`，跳转运行追踪页。

### 7.3 运行追踪页

- 顶部：任务主题 + 轮次进度 `round / max_rounds` + 状态徽标 + 调试模式开关。
- 主体：§5 时间线，WebSocket 增量追加节点；根据调试开关自动切换【用户叙事视图 / 原始事件视图】。
- 底部：实时引用链状态（可点开）。

### 7.4 结果 / 输出页

- 根据`template_manifest.output_format`（经 user_task 覆盖，见 §6）调用对应渲染插件；v1 实现 markdown 插件，其余格式预留。
- `status=done`：渲染任务输出（研报 Markdown 含独立 "引用 / 来源" 章节，源自 `draft_segments` 的引用 ID 展开）。
- 附 "评审留痕" 折叠区：完整 `gate_review_history`（每道闸 decision/reason/eval_score/problem_points）。
- 提供审计包下载按钮。

### 7.5 人工复核页（escalate 终止时）

> ⚠️重要：本页面是**任务已经 escalate 终止后的事后处理**。v1 不实现 "把人作为 Gate 插入工作流中间做实时审批"；中间闸门全部 AI 自动执行；人只能在任务终止后选择接收半成品或者发起全新任务。

- 展示半成品内容 + `routing_state` 失败原因 + `gate_review_history` 评审留痕。
- v1 仅一个可执行操作（补充约束重跑延后至 v2，见 §9 v2 阶段）：
  1. **确认接收半成品**：不重新运行 Graph；任务状态保持`escalated`，保存人工备注，交付半成品结果。
- 【v2 实现】补充约束后重跑：**生成全新 task_id 的新任务**，合并原有`user_task`与用户新增约束提交；**禁止原地继续驱动已经终止的旧 Graph 实例**。
- 此页即 DESIGN §2 "Escalate 不空结束" 的落地界面。

> ⚠️ 重跑成本前向项（v2 实现）：v2 重跑为全新 task_id 从零执行，会丢弃前几轮已产出的有效成果（重复检索 / 分析开销）。v2 建议将旧任务的 `prior_versions` 作为**只读暖启动参考**随新 `user_task` 一并提交（仅作上下文 hint，不 mutation 已终止 Graph），降低重复成本。

---

## 8. 字段对照表（防混淆的硬保证）

UI 展示字段 ↔ DESIGN §7 / UI 静态配置来源，一一对应，无来源即违规：

| UI 展示 | 来源 | 所在节 |
| --- | --- | --- |
| 任务主题 / 范围 / 格式 / 约束 | `user_task.*` | DESIGN §7 顶层任务 |
| 调研素材条目 | `retrieval_records[]` | DESIGN §7 产出层 |
| 分析结论条目 | `analysis_conclusions[]` | DESIGN §7 产出层 |
| 研报段落 | `draft_segments[]` | DESIGN §7 产出层 |
| 工具告警 | `tool_status[].ok=false` | DESIGN §7 产出层 |
| 历史快照（审计用，调试模式可见） | `prior_versions` | DESIGN §7 产出层 |
| 引擎审计事件序列（审计用，调试模式可见） | `engine_events[]` | DESIGN §7 路由状态（元素结构见 `memory/schema.md` §4.1）；**仅原始事件视图展示，用户‑语义叙事视图过滤隐藏** |
| 闸决策 / 理由 / 分 / 问题点 | `gate_output.*` | DESIGN §7 Gate 输出 |
| 审计链路 | `gate_review_history[]` | DESIGN §7 路由状态 |
| 轮次 / 最大轮次 | `routing_state.round` / `max_rounds` | DESIGN §7 路由状态 |
| 当前闸 / 状态 | `routing_state.last_gate` / `status` | DESIGN §7 路由状态 |
| 返工目标 / 原因 | `routing_state.rework_target_agent` / `rework_reason` | DESIGN §7 路由状态 |
| 半成品 + 留痕（escalate） | `Escalate 产出` + `gate_review_history` | DESIGN §2 |
| 模板名称、描述、表单配置、ui_mode、output_format | `template_manifest` | UIDESIGN §6（**仅静态配置，不属于引擎运行时 state**） |
| token 消耗统计（v2） | 引擎侧扩展字段 | v2 阶段再在引擎补充 state 字段 |

> 新增 UI 字段若不能在此表找到来源，必须先回 `DESIGN.md` 增补状态定义，禁止 UI 侧私自新增运行时状态。

---

## 9. 阶段规划

| 阶段 | UI 范围 | 说明 |
| --- | --- | --- |
| **v1** | 模板选择 + 提交 + 实时追踪时间线（双视图） + 结果输出插件（仅 markdown） + 人工复核（仅确认接收半成品） + 审计包导出 | 单模板（研报）；`unified_shell`完整落地；`custom_component`仅 manifest 占位，不实现加载逻辑 |
| **v2** | 配置台（`debug_mode` 开关控调试能力）；补充约束重跑；重跑暖启动；扩展输出渲染插件 (table/json 仅开发预留) | 改 `model_mapping` / 闸阈值 / 模板 prompt；只读配置回写，不改业务图 |
| **v3** | 完整启用`ui_mode:custom_component`；iframe 沙箱、postMessage 代理；开发第一个异构样例模板 | 引擎侧需先完成自定义组件配套能力，v1‑v2 不启用 |

> v3 是否做取决于业务是否真要异构 UI；v1/v2 已足够交付研报团队价值，避免过度设计。

---

## 10. 开放问题（M1 已全部收口，v1/v2 实现边界已标记）

1. 【已决策】UI 技术栈：Vue3 SPA，WebSocket 实现实时推送；可被 unified‑inbox 内嵌集成。
2. 【已决策】实时通道：采用 WebSocket；轮询仅作为降级备选，v1 不实现轮询。
3. 【已决策】引用链高亮：v1 实现，模板默认开启 `show_citation_chain: true`。
4. 【已决策】人工复核‑补充约束重跑：v1 不实现，仅保留「确认接收半成品」；重跑能力延后至 v2。
5. 【已决策】配置台 (v2) 权限：v2 不实现完整 RBAC，使用全局 `debug_mode` 布尔开关控制调试能力；完整多用户权限后置。
6. 【已决策】审计包导出接口：v1 必须实现，输出完整归档包（报告 + gate_review_history + prior_versions 快照）。

👉 UIDESIGN 至此 M1 彻底完成，没有未拍板的开放点。

---

## 11. 修订记录

> **排版说明（2026‑09‑05）**：本节曾存在时序倒挂与 v1.2 重复登记两处**登记笔误**——v1.3 条目排在 v1.2「M1 锁版修订」之前，且出现两个均标注 v1.2 的条目（系同一变更被登记两次）。现已按时间线由旧到新重排，并将重复的 v1.2 两条**合并为一条、原文分列保留**。**所有版本的变更描述文字一字未改**，本次仅为登记排版修正，不构成契约变更。

- **草稿 v0.1（2026‑09‑04）**：首次起草。确立 "UI 交付层 / 引擎层单一真相源" 边界红线；定义模板驱动原则、实时追踪时间线、template_manifest 静态配置、字段对照表、v1‑v3 阶段。
- **v0.2（2026‑09‑04）评审修订版**：补充 WebSocket 事件枚举；澄清 escalate 接口行为；补充 manifest 安全备注；增加审计导出接口；澄清 v1 人工复核为事后处理；prior_versions 调试模式可见。
- **v1.0（2026‑09‑04）桌面定稿版**：采纳三点评审意见——① 时间线双视图；② 双模式模板 UI（方案 B 渐进式）；③ output_format 插件化渲染。
- **v1.1（2026‑09‑04）评审 pushback 修订**：并入 v1.0 并采纳 4 点 critique——① §10 与正文 v1 范围矛盾收口（引用链高亮 / 重跑已闭合，移出开放问题）；② 新增 §5.1.1 语义聚合映射表（UI 层渲染规则，不依赖 M3）；③ §7.5 补重跑暖启动 v2 前向项；④ §6 澄清 manifest.output_format 与 user_task.output_format_spec 优先级。版本由"定稿"调整为"评审修订"，待 boss 最终确认。
- **v1.2（2026‑09‑04）§10 Q1 拍板 + M1 锁版修订**（原登记为两条同号条目，实为同一变更的两次登记，已合并去重，两条原文逐字保留）：
  - **① §10 Q1 拍板**：§0 元信息补 Vue3 技术栈决策（Vue3 SPA + WebSocket + REST/WS 解耦）；§10 Q1 标记「已决策」；保留 v1.1 四处澄清。
  - **② M1 锁版修订**：§0 元信息补 Vue3 技术栈决策（Q1 正式拍板：Vue3 SPA + WebSocket 实时推送 + REST/WebSocket 与引擎解耦，可被 unified‑inbox 内嵌）；§10 开放问题 1 标记「已决策」并保留 2‑6 不变；其余 v1.1 澄清全部保留。M1 全部闭环，无遗留开放决策。
- **v1.3（2026‑09‑04）M1 彻底完成**：§10 全收口（Q2–Q6 已决策，整段替换为定稿文本）；**Q4 决策反转——「补充约束重跑」延后至 v2**，已对齐 §7.5（v1 仅保留确认接收半成品）与 §9（v2 阶段补重跑）；M1 无未拍板开放点。
- **v1.4（2026‑09‑05）M5 字段对齐（append‑only）**：§8 字段对照表**追加一行** —— `engine_events[]`（引擎审计事件序列，仅 🔧调试模式「原始事件视图」展示，用户‑语义叙事视图过滤隐藏），未改动表中任何既有条目。起因：M5 实跑新增 `gate_llm_unavailable_degraded_advance` 降级放行审计事件，若不登记则调试面板缺失该线索，业务侧不受影响。版本 v1.3 → v1.4。**本条仅追加字段映射与版本标记，未修改任何锁定契约正文。**
