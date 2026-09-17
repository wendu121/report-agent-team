# DESIGN_M9-1 · 真·智能体市场（Agent Library，G5 用热加载做对）

> 元信息：起草 2026-09-08（M8 配置层全交付 `aaa009a`/`7098251` 后，M9 首里程碑）
> 范围裁定：AskUserQuestion boss 拍板「真·智能体市场（推荐）」
> 状态：**待评审**（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议，全 PASS 才动代码）
> 关联：
> - 承接 [DESIGN_SETTINGS_CONSOLE.md](./DESIGN_SETTINGS_CONSOLE.md) 的 **G5**（Agent 数量扩展，原「字典外置 + 受控重启」）
> - 修正 [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.1 的文档漂移（见 §0.3）
> - 复用 M8-5 子集编排（[DESIGN_M8-5.md](./DESIGN_M8-5.md)）+ M8-2/3 prompt 编辑器（[web/src/views/settings/Agents.vue](./web/src/views/settings/Agents.vue)）

---

## 0. 范围裁定与文档漂移纠正

### 0.1 boss 拍板（AskUserQuestion 2026-09-08）
M9-1 交付到**真·智能体市场**层：
- Accio 图6 风格卡片市场页 `/agents`（公开/个人 tabs + 搜索筛选 + 头像卡）
- 后台 `config/agents_library.yaml` 接管 orchestrator 的 4 个硬编码字典
- orchestrator **每次 run 热加载**（照搬 `load_model_mapping` 模式，**零重启**）
- UI 可新增自定义 Agent（自动建 `agents/<role>.md` + `gates/<gate>.md` + `model_mapping` 条目 + 配异基座 Gate）
- 内置 Agent 只读，自定义可删

### 0.2 把 G5 的「受控重启」消掉（工程洞察）
原 G5（`DESIGN_SETTINGS_CONSOLE.md §4.3`）假定「新增角色需 api 容器受控重启一次」。
但 M8-5 已实证：**图是每次 `run_report` 现建的**（`build_graph` 在 `run_report` 内调用，orchestrator.py:1008）。
而 `model_mapping` 已是 `run_report` 内 `load_model_mapping()` 热加载（:1004→:415）。
→ 把 4 字典也改为「每次 run 从 yaml 读」即热加载、**零重启、零容器重启**。比原 G5 更克制，正合 boss「反感过度工程」立约。

### 0.3 纠正 DESIGN_PLATFORM_FUNCTIONS.md §3.1 的漂移（必须如实记）
该节写「新增 Agent 关联 tool+gate → 与 M8-5 的 `agents_registry.yaml` 打通」。
**事实**：M8-5 交付的是「子集编排」（`build_graph(agents=子集)` 动态建图），**从未创建 `agents_registry.yaml`**（M8-5 选了子集编排而非外置，见 DESIGN_M8-5 §0.2）。
→ 本设计用 **`config/agents_library.yaml`** 作为真正 SoT，从 orchestrator 4 字典外置而来。实施后将回改 `DESIGN_PLATFORM_FUNCTIONS.md §3.1` 与 §7，消除漂移。

---

## 1. 当前引擎/配置真实状态（代码实证，非凭记忆）

### 1.1 orchestrator.py 硬编码字典（:40-59）
```python
PROD_KEY   = {"Researcher":"retrieval_records", "Analyst":"analysis_conclusions", "Writer":"draft_segments"}
TOOL       = {"Researcher":"web_search", "Analyst":"data_proc", "Writer":"doc_export"}  # ⚠️ 运行时无 TOOL[role] 读取（grep 证实仅定义处），系死字典
GATE_NAME  = {"Researcher":"GateA", "Analyst":"GateB", "Writer":"GateC"}
GATE_REVIEWS = {"GateA":"Researcher", "GateB":"Analyst", "GateC":"Writer"}
```
消费点：
- `make_agent` :574 `prod_key = PROD_KEY[role]`；工具分发按 `if role=="Researcher"/"Analyst"/"Writer"` 硬编码（:593/621/633/671/703）
- `make_gate` :751 `role = GATE_REVIEWS[gate_name]`
- `build_agent_system` :334 `PROD_KEY[role]`；`build_gate_system` :347 读**单一** `gates/review.md`（全闸共用一文件）
- `build_gate_user` :385/386 `GATE_REVIEWS`/`PROD_KEY`；:395/403 硬编码 `if gate_name=="GateC"/"GateB"` 决定附 report_markdown / 检索上下文
- `machine_check` :426/442/459 硬编码 `if gate_name=="GateA"/"GateB"/"GateC"` 三段校验逻辑
- `call_eval` :496 `PROD_KEY[GATE_REVIEWS[gate_name]]`
- `build_graph` :972 `GATE_NAME[role]` 推导闸节点

### 1.2 诚实约束（决定设计边界）
- **C1 工具分发硬编码**：`make_agent` 按角色名分支调 web_search/data_proc/doc_export。新增角色必须让分发**配置化（shape 驱动）**，否则仍是假扩展。
- **C2 单文件 Gate prompt**：`gates/review.md` 全闸共用。新增闸需 per-gate 文件 **或** 单文件内新增节 + `build_gate_system` 改分段加载。
- **C3 机器校验按闸名硬编码**：`machine_check` 三段逻辑其实只依赖**产出形态**（retrieval/analysis/draft），与闸名无关 → 可改为 **shape 驱动**复用，无需为自定义闸写新分支（见 §3.4）。

---

## 2. 设计目标（Goals / Non-Goals）

### 2.1 必须做到（Goals）
- G1：`config/agents_library.yaml` 成为 Agent 角色 SoT（shape/tool/output_key/gate/visibility/builtin），接管 4 硬编码字典。
- G2：orchestrator **每次 run 热加载** registry（mirror `load_model_mapping`），零重启。
- G3：`make_agent` **shape 驱动**——工具分发 + 上游上下文注入 + 特殊工具（search/data_proc/doc_export）按 shape 参数化，消除角色名硬编码。
- G4：`machine_check` / `build_gate_user` **shape 驱动**——自定义闸复用 3 套质量校验（非 LLM-only 降级）。
- G5：UI `/agents` 市场页（Accio 图6：公开/个人 tabs + 搜索筛选 + 头像卡 + 对话/配置按钮）。
- G6：UI 可新增自定义 Agent（选 shape + 命名 + 描述）→ 复合写 `agents/<role>.md` + `gates/<gate>.md` + `model_mapping` 条目 + registry 条目，校验**异基座**硬约束。
- G7：`KNOWN_AGENTS`/`KNOWN_GATES`（admin.py:752-753，M8-4 硬编码）改为**派生自 library**，使自定义 Agent 自动可用于 M8-4 模板子集（M8-5 真驱动）。
- G8：内置 Agent 在市场只读、可跳 `/settings/agents` 编辑 prompt（复用 M8-2）；自定义可删（复合清理 + 审计）。
- G9：market 列表公开 GET（仿 `/templates`），新增/删除 admin CRUD（仿 templates），复用 `_atomic_write`/`_write_audit`。

### 2.2 不做（Non-Goals，防范围蔓延）
- N1：可视化 LangGraph 节点编辑器（DESIGN_SETTINGS_CONSOLE N1，过度工程）。
- N2：G6 输出格式渲染（pptx/docx/pdf，推 M9 后续）。
- N3：M9-2 数据源插件 / M9-3 技能库 / M9-4 推送渠道 / M9-5 对话入口（本里程碑只做 M9-1 智能体市场）。
- N4：多用户/RBAC（沿用 M8 单 admin token 模式）。
- N5：自定义 Agent 的 shape 任意扩展（如「调用外部 API 的新工具类型」）——M9-1 仅支持现有 3 种 shape（researcher/analyst/writer），新工具类型属未来。

---

## 3. 架构方案

### 3.1 `config/agents_library.yaml`（新 SoT）
```yaml
# 智能体库 · 接管 orchestrator.py:40-59 的 4 个硬编码字典
# 热加载：orchestrator.run_report 每次 load_agent_registry()（mirror load_model_mapping）
agents:
  - id: Researcher
    name: 研究员
    avatar: ""                       # 空=前端渲染首字母圆
    tags: [调研, 信息检索]
    description: 负责信息检索与事实核证，产出 retrieval_records
    shape: researcher               # researcher|analyst|writer（决定工具/产出/闸校验形态）
    tool: web_search
    output_key: retrieval_records
    gate: GateA
    visibility: public
    builtin: true
  - id: Analyst
    name: 分析师
    shape: analyst
    tool: data_proc
    output_key: analysis_conclusions
    gate: GateB
    visibility: public
    builtin: true
  - id: Writer
    name: 撰稿人
    shape: writer
    tool: doc_export
    output_key: draft_segments
    gate: GateC
    visibility: public
    builtin: true
# 自定义 Agent 示例（UI 新增后自动追加）：
#  - id: Coder
#    name: 代码分析师
#    shape: researcher               # shape 决定 output_key（researcher→retrieval_records），不自由填
#    tool: web_search
#    output_key: retrieval_records   # ⚠️ 由 shape 强制推导（§7）；自定义 researcher 形必须写此值，machine_check shape=researcher 只读 retrieval_records
#    gate: GateD
#    visibility: private
#    builtin: false
```
**派生关系**（运行时由 `load_agent_registry` 计算，不另存）：
- `PROD_KEY[role] = output_key`
- `TOOL[role] = tool`
- `GATE_NAME[role] = gate`
- `GATE_REVIEWS[gate] = role`（反查）
- `gate_shape[gate] = agents[role_of_gate].shape`

### 3.2 orchestrator 热加载（`load_agent_registry`）
```python
def load_agent_registry(path=None) -> dict:
    import yaml
    p = Path(path) if path else (BASE / "config" / "agents_library.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    agents = {a["id"]: a for a in data.get("agents", [])}
    # 派生反查表
    gate_of = {a["id"]: a["gate"] for a in agents.values()}
    reviews = {a["gate"]: a["id"] for a in agents.values()}
    shape_of = {a["id"]: a["shape"] for a in agents.values()}
    gate_shape = {a["gate"]: a["shape"] for a in agents.values()}
    return {"agents": agents, "gate_of": gate_of, "reviews": reviews,
            "shape_of": shape_of, "gate_shape": gate_shape}
```
`run_report` 内 `reg = load_agent_registry()`（mirror `load_model_mapping`），透传进 `build_graph(..., reg=reg)` 与各 node 工厂。删除模块级 `PROD_KEY/TOOL/GATE_NAME/GATE_REVIEWS`（:40-59）。

### 3.3 `make_agent` shape 驱动（核心重构）
按 `reg["shape_of"][role]` 分支（替代按角色名）：
| shape | tool 调用 | 上游上下文注入 | 特殊 | prod_key |
|---|---|---|---|---|
| researcher | `tools.web_search.search_many` + 来源溯源硬校验 | 无（源头） | 允许空产出（交 Gate 判 escalate） | retrieval_records |
| analyst | `tools.data_proc.run_requests`（AST 白名单） | 注入 `retrieval_records` | tool_requests 二次生成 | analysis_conclusions |
| writer | `tools.doc_export.ensure_citation_section` + `export` | 注入 `analysis_conclusions` | 引用章节确定性重建 | draft_segments |

实现：从 `reg` 取 `shape/tool/output_key`，把现有 `if role=="X"` 三处改为 `if shape=="X"`（逻辑块原样搬，仅把 `role` 字符串比对换成 `shape` 比对）。`build_agent_system` 的 `PROD_KEY[role]` → `reg["agents"][role]["output_key"]`。

### 3.4 `machine_check` / `build_gate_user` / `build_gate_system` shape 驱动（C2/C3 解决）
- `machine_check(gate_name, state)`：先 `shape = reg["gate_shape"][gate_name]`，再 `if shape=="researcher": ...原 GateA 逻辑... elif shape=="analyst": ...原 GateB... elif shape=="writer": ...原 GateC...`。三段逻辑**原样搬**，仅闸门判定键从 `gate_name` 字符串改为 `shape`。→ 自定义闸获一等公民质量校验。
- `build_gate_user`：`:395 if gate_name=="GateC"` → `if reg["gate_shape"][gate_name]=="writer"`（附 report_markdown）；`:403 if gate_name=="GateB"` → `if shape=="analyst"`（附检索上下文）。
- `make_gate`：`role = GATE_REVIEWS[gate_name]` → `reg["reviews"][gate_name]`。
- `build_gate_system`（per-gate 文件加载，R1/C2）：`:347 body=_load_md("gates/review.md")` 改为先查 `gates/<gate_name>.md`，存在则读该文件，否则回落 `gates/review.md`（内置 3 闸维持现状）。→ 自定义闸 GateD 读独立 `gates/gate_d.md`，不污染 review.md。

### 3.5 `build_graph` 适配
`:972 GATE_NAME[role]` → `reg["gate_of"][role]`；其余动态建图逻辑（M8-5 已验证）不变。`is_terminal` 仍由 `role==chain[-1]` 决定。

### 3.6 `call_eval` / `build_agent_system` 适配
- `:496 PROD_KEY[GATE_REVIEWS[gate_name]]` → `reg["agents"][reg["reviews"][gate_name]]["output_key"]`
- `:334 PROD_KEY[role]` → `reg["agents"][role]["output_key"]`

---

## 4. 后端 admin API（server/admin.py 扩展）

### 4.1 市场读取（公开，仿 `/templates`）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/agents-library` | 公开列表（市场页用，无 admin token）。返回 agents 数组（id/name/avatar/tags/description/shape/gate/visibility/builtin）+ meta（registry 文件、hot_reload 说明） |

### 4.2 管理 CRUD（admin token，仿 templates）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/agents-library` | 同公开列表（带校验状态） |
| POST | `/api/v1/admin/agents-library` | **复合新增自定义 Agent**（见 §4.3） |
| DELETE | `/api/v1/admin/agents-library/{id}` | 复合删除自定义 Agent（builtin 拒删；清理 md/gate/model_mapping/registry + 审计） |

### 4.3 `POST /admin/agents-library` 复合写（真·驱动的关键）
请求体 `AgentLibPost`：
```python
class AgentLibPost(BaseModel):
    id: str                      # 角色逻辑名（AGENT_NAME_RE 正则，大写约定 Researcher 风）
    name: str
    shape: str                   # researcher|analyst|writer（枚举校验）
    description: str = ""
    tags: List[str] = []
    avatar: str = ""
    gate: str                    # 新闸名（GATE_NAME_RE，唯一，如 GateD）
    # 模型绑定（默认异基座：role=custom/LongCat-2.0, gate=cloudflare/llama-3.3-70b）
    role_base: str = "custom"; role_model: str = "LongCat-2.0"
    gate_base: str = "cloudflare"; gate_model: str = "llama-3.3-70b"
```
执行（原子 + 审计 + 校验前置）：
1. 校验：`id` 匹配 `AGENT_NAME_RE` 且不在 library；`shape∈{researcher,analyst,writer}`；`gate` 唯一；`role_base != gate_base`（异基座硬约束，违反 400）。
2. 建 `agents/<id>.md`（skeleton：从对应 shape 模板生成，含一级标题 + 目标/工具/禁止三段骨架）。
3. 建 `gates/<gate>.md`（skeleton：shape 对应闸规则，角色名替换为 `id`）。
4. 改 `config/model_mapping.yaml`：加 `roles.<id>` + `gates.<gate>`（用现有 ruamel round-trip，保留注释）。
5. 改 `config/agents_library.yaml`：追加 agent 条目（ruamel round-trip，保留注释）。
6. 每步写审计（`.audit/agents` / `.audit/gates` / `.audit/models` / `.audit/agents-library`）。
7. 任何一步失败 → 全回滚（删已建文件 + 回退 yaml）+ 400 + 审计 rejected。

### 4.4 `KNOWN_AGENTS`/`KNOWN_GATES`/`AGENT_GATE_PAIR` 派生（G7）
admin.py:752-755 三处硬编码：
- `KNOWN_AGENTS = ["Researcher","Analyst","Writer"]`（:752）
- `KNOWN_GATES = ["GateA","GateB","GateC"]`（:753）
- `AGENT_GATE_PAIR = {"Researcher":"GateA","Analyst":"GateB","Writer":"GateC"}`（:755）

→ 全部改为**派生自 `agents_library.yaml`**（mirror `_agent_names`）：
- `_known_agents()` = registry agents 的 id 列表
- `_known_gates()` = registry agents 的 gate 列表
- `_agent_gate_pair()` = `{a["id"]: a["gate"] for a in agents}`（即 `load_agent_registry` 已算的 `gate_of`）

**关键（B1 修正）**：`:830` `_validate_template` 的配对校验 `expected_gates = {AGENT_GATE_PAIR[a] for a in ags}` 必须改为 `{_agent_gate_pair().get(a) for a in ags}`（对自定义 agent 不再 `KeyError`→500）。否则 E5（`agents=["Researcher","Coder"]`）在模板创建即崩，G7「真驱动闭环」在自己最关键证据点断裂（独立审议第 16 轮 B1 已纠出）。

→ M8-4 `_validate_template` 子集校验、M8-5 子集编排自动接纳自定义 Agent（真驱动闭环）。

### 4.5 `DELETE /admin/agents-library/{id}` 复合清理
- builtin → 400「内置 Agent 不可删」。
- 删 `agents/<id>.md` + `gates/<gate>.md` + library 条目 + model_mapping `roles.<id>`/`gates.<gate>`。
- 审计每步；失败回滚。

---

## 5. 前端市场页（web/src/views/Agents.vue 新建 + 路由）

### 5.1 路由（web/src/router/index.ts）
```ts
import AgentsMarket from '@/views/Agents.vue';
// ...
{ path: '/agents', name: 'AgentsMarket', component: AgentsMarket, meta: { title: '智能体市场' } },
{ path: '/agents/:id', name: 'AgentDetail', component: AgentDetail.vue, meta: { title: '智能体详情' } },
```
侧边栏（DefaultLayout.vue）加「智能体」入口（Accio 风格：左 sidebar 固定，市场/设置并列）。

### 5.2 `/agents` 市场页（Accio 图6，视觉见 DESIGN_PLATFORM_FUNCTIONS §6）
- 顶部：页面标题 + 一行描述 + Tabs（公开 / 个人）。
- 搜索框（按 name/tags/description 过滤）+ 形状筛选（研究员/分析师/撰稿人）。
- 卡片网格（响应式 3/6 列）：头像（圆形，空 avatar 渲染首字母）+ 名称 + 标签 + 描述 + 两个动作：
  - **对话** → `/submit?agents=<id>`（跳提交页，预选该 agent 为子集，走 M8-5 真驱动；多 agent 用逗号）。
  - **配置** → `/settings/agents/<id>`（复用 M8-2 编辑器，仅自定义/内置均可看 prompt；内置只读提示）。
- 右上「+ 新增智能体」→ 新增向导（见 §5.3）。
- 数据：`GET /api/v1/agents-library`（公开）。

### 5.3 新增向导（对话框/抽屉）
- 字段：名称 / 角色逻辑名(id，正则校验) / shape 下拉（研究员/分析师/撰稿人，带说明）/ 描述 / 标签 / 头像(可选) / 闸名(自动建议 `Gate`+大写首字母，可改) / 模型绑定（默认异基座，可改）。
- 实时校验：异基座红框 + 保存禁用（mirror Models.vue 的 `isViolation`）。
- 提交：`POST /api/v1/admin/agents-library`；成功刷新列表 + `ElMessage.success`。

### 5.4 视觉（DESIGN_PLATFORM_FUNCTIONS §6 色板，勿另起炉灶）
卡片白底 `#FFFFFF` + 圆角 8-12px + 1px `#E5E7EB`；主色 `#10B981`；页面底 `#F9FAFB`；标题 `#111827`；副标题 `#6B7280`。

---

## 6. 数据流（端到端）
```
市场页 /agents
  ├─ 浏览内置/自定义 Agent（GET /agents-library，公开）
  ├─ 新增 → POST /admin/agents-library（复合写 md+gate+mapping+registry，异基座校验）
  ├─ 对话 → /submit?agents=Researcher,Coder
  │         └─ TaskSubmit 解析 query → 选该子集模板（或建临时子集）→ create_task(agents=[...])
  │              └─ M8-5 build_graph(agents=[Researcher,Coder]) 真跑（Coder 按 researcher shape 驱动）
  └─ 配置 → /settings/agents/<id>（M8-2 编辑 prompt，热加载）
```

---

## 7. 校验规则（后端，写盘前置）
- `id` 匹配 `^[A-Za-z0-9_-]+$`，且不在 library（409 冲突）。
- `shape ∈ {researcher, analyst, writer}`（枚举，否则 400）。
- `gate` 匹配 `^[A-Za-z0-9_-]+$`，且不在 library gates（409）。
- **硬约束**：`role_base != gate_base`（防自审包庇，400）。
- `output_key` 由 shape 决定（researcher→retrieval_records / analyst→analysis_conclusions / writer→draft_segments），不允许自由填（防 make_agent 产出键错配）。
- model_mapping 改动走现有 `_validate_mapping`（异基座 + 存在性）。
- 复合写任意步失败 → 回滚 + 400 + rejected 审计。

---

## 8. 热加载 / 原子写 / 审计（复用现有）
- 热加载：`load_agent_registry()` 在 `run_report` 内调用（mirror `load_model_mapping`），改完 yaml 下一个任务即生效，**无需重启**。
- 原子写：复用 `_atomic_write`（admin.py:166）。
- 审计：复用 `_write_audit`（admin.py:172），subdir 分 `agents`/`gates`/`models`/`agents-library`。
- yaml 保留注释：library 与 model_mapping 均用 ruamel round-trip（model_mapping 已有；library 新文件初建也用 round-trip 以便后续保留注释）。

---

## 9. 验证计划（三道闸证据）

| 项 | 方法 | 预期 |
|---|---|---|
| ① 校验 | `py_compile` orchestrator.py / server/admin.py；`npm run build`（前端 typecheck） | 0 错 |
| ② 自审 | VERIFICATION.md §15：设计偏差 + 诚实约束 + 复合写回滚论证 | 见下 |
| ③ 独立审议 | REVIEW.md 第 16 轮（派独立子代理，严禁主代理自签） | PASS/NOTES |
| **E1 全量回归** | stub-LLM `mode=pass`，不传 agents → 行为同 M8-5 | status=done，3 节点产出齐全 |
| **E2 热加载实证** | 改 `agents_library.yaml`（如临时加 Coder shape=researcher）→ stub run `agents=["Researcher","Coder"]` | Coder 按 researcher shape 真跑（retrieval_records 有值），证明热加载 + shape 驱动 |
| **E3 复合写实证** | `POST /admin/agents-library` 建 Coder+GateD（异基座）→ `GET /agents-library` 出现 → `GET /admin/agents-library` 校验 ok | 4 文件落盘（md/gate/mapping/library）+ 审计齐全 |
| **E4 异基座拦截** | POST 建 Coder 但 `role_base==gate_base` | 400 不落盘 + rejected 审计 |
| **E5 子集接纳** | 建 Coder 后 `POST /admin/templates` agents=["Researcher","Coder"] | 通过（KNOWN_AGENTS 派生，M8-5 真驱动闭环） |
| **E6 删除清理** | `DELETE /admin/agents-library/Coder` | md/gate/mapping/library 四项均清理 + 审计；再跑 E2 子集失败（已删） |
| **E7 机器校验复用** | E2 的 Coder 闸 GateD 故意喂空 retrieval → machine_check shape=researcher 分支 → escalate | 证明自定义闸获一等公民校验（非 LLM-only） |

> stub-LLM 不触发真实 new-api 配额（当前 429 规避态），E1-E7 可离线跑通。

---

## 10. 诚实边界 / 风险

- **R1 单文件 Gate prompt（C2）**：新增闸默认写 `gates/<gate>.md` **新文件**，`build_gate_system` 需支持 per-gate 文件（先查 `gates/<gate>.md`，缺则回落 `gates/review.md`）。M9-1 不拆 review.md，新闸独立文件，内置 3 闸维持 review.md 现状。
- **R2 shape 仅 3 种（N5）**：自定义 Agent 只能 researcher/analyst/writer 之一；新工具类型（如调外部 API）不在 M9-1。
- **R3 死字典 TOOL 移除**：`TOOL`（:45-49）运行时无读取，外置时并入 registry `tool` 字段，删除模块级 `TOOL`（避免双真相源）。
- **R4 builtin 保护**：内置 3 Agent/Gate 不可删、registry 条目 `builtin:true` 前端只读。
- **R5 复合写回滚**：4 处写（md/gate/mapping/library）任一步失败须全回滚——实现用「先写临时 + 全成功才落盘」或「顺序写 + 异常逆序清理」，E3/E6 实证。
- **R6 无任务列表页缺口**：Accio 图1「任务历史」侧栏尚未实现（DESIGN_PLATFORM_FUNCTIONS §4 已记），本里程碑不解决，推 M9-5。

---

## 11. 不做清单（存档备查）
见 §2.2 N1-N5。可视化节点编辑器、G6 渲染、M9-2~5、RBAC、新工具类型——均不在本里程碑。

---

## 12. 提交纪律
- 本地 `git commit --only report-agent-team`，**不 push**（boss 指令）。
- 改动集（预估）：`orchestrator.py`（删 4 字典→`load_agent_registry` + shape 驱动 make_agent/machine_check/build_gate_user/build_gate_system/make_gate/build_graph/call_eval/build_agent_system）、`config/agents_library.yaml`（新）、`server/admin.py`（agents-library CRUD + KNOWN_AGENTS/KNOWN_GATES/AGENT_GATE_PAIR 三处派生 + `:830` 配对校验改用派生表）、`web/src/router/index.ts` + `web/src/views/Agents.vue` + `web/src/views/AgentDetail.vue` + `DefaultLayout.vue`（侧边栏入口）、`DESIGN_PLATFORM_FUNCTIONS.md`（§3.1/§7 纠偏）、`VERIFICATION.md`（§15）、`REVIEW.md`（第 16 轮）。
- **删除模块级四字典前的回归防护**：提交前全仓 `grep -rn "PROD_KEY\|TOOL\[\|GATE_NAME\[\|GATE_REVIEWS\["` 确认除 orchestrator 内部外零运行时引用（N3：web 端不 import 这些常量；server 内 engine_runner.py:58 仅为注释）；若有外部引用须先改后再删，避免 KeyErrors。
- 三道闸全 PASS 才 commit；独立审议 `reviewed-by` 严禁主代理自签。
- 文档漂移回改：§0.3 所述 `DESIGN_PLATFORM_FUNCTIONS.md` 修订随本里程碑入库。

---

## 13. 评审修订记录

| 轮次 | 日期 | 状态 | 评审者 | 主要修订 |
|---|---|---|---|---|
| 草案 | 2026-09-08 | 待评审 | （主代理） | 初版：热加载版 G5 + Accio 图6 市场 + shape 驱动重构 + 复合写 |
