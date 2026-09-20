# DESIGN_M12-5 · 专家 shape 接入研报流水线

> SoT：`DESIGN_M12_skill_autonomy.md` v1 + `DESIGN_M12-4_runtime_integration.md` v1（已完成 runtime 接线，但「专家 shape 字段仍不消费研报流水线」为刻意留白）。
> 治理：三道闸（① py_compile ② `VERIFICATION_M12-5.md` 自审 ③ 独立子代理 `REVIEW_M12-5.md`，主代理不自签）。**先设计后实现。**

## 0. 为什么有这一档（诚实边界，非推断）

M12-1/2/3 落了专家包解析 + 注册表 + 运行时接线（chat 侧已在 M12-4 闭环），但 `DESIGN_M12-4` §1 明确：**专家 `shape` 字段注册表可填，研报流水线未接**。
即：在 `config/experts.yaml` 里给专家写了 `shape: analyst`，`run_report` 走到 Analyst 节点时**根本不读这个字段**——
专家对研报流水线等于「填了却悄悄忽略」的假配置。本档把它接上。

## 1. 目标与边界

### 接上什么
- 研报流水线（Router → Researcher → GateA → Analyst → GateB → Writer → GateC）在**对应 shape 的节点**注入该专家的角色定义/方法/包内技能 system 片段。
- 专家 `model` 字段被**消费**（该 shape 节点优先用专家指定模型，用户级 `model_override` 仍优先）—— 否则就是「能填但不消费」。
- 专家绝不拥有私有工具：检索/数据处理/导出仍由引擎 `dispatch_tool` 统一提供（沿用铁律，专家只是 persona 注入）。

### 不做什么（禁过度工程）
- 不造「多专家 fan-out」「团队型并发」—— team 型已按 §5.6 拍板默认拒绝。
- 不重写 Agent 体系——复用 M9-1 shape 驱动 + M12-4 的 `build_expert_system`。
- 不做「专家改写研报结构」之类强耦合——专家只注入 persona 片段（与 skill 片段同级），结构仍由 role 的 `output_key` 约束。

## 2. 设计要点

### 2.1 唯一解析入口（DRY，闭环 DESIGN_M12 §5.1）
把 M12-4 在 `chat_agent._resolve_expert_context` 里的解析逻辑**抽到 `tools/experts.py` 作为公共函数 `resolve_expert(text) -> (ctx, meta)`**：
- 显式 `@<专家>`（`resolve_mention`）优先，`routed=False`；
- 否则隐式 `route()`（V7 关键词打分，`>=2` 命中才选），`routed=True`；
- **任何异常 / 无匹配 / 包损坏 → 降级为 `("", None)`，绝不冒泡**（与 M12-4 V8 一致）。
- `meta = {"id","name","routed","shape","model"}`。

`chat_agent._resolve_expert_context` 改为**委托** `tools.experts.resolve_expert` 并保留原返回形（保持 M12-4 的 R1-R4/V8 测试不变）。专家 = 上层封装，解析逻辑只一份。

### 2.2 接入点：`orchestrator.make_agent` 节点（:1730-1738）
现状：`system = build_agent_system(role, reg)` → 追加 `build_skill_context(role)`。
改为新增组装函数 `build_pipeline_system(role, reg, user_text=None) -> (system, expert_meta)`：
1. `system = build_agent_system(role, reg)`
2. `skill_ctx = build_skill_context(role)`；非空则 `system += "\n\n" + skill_ctx`（**顺序不变**，先 skill）
3. 若 `user_text` 非空：
   - `expert_ctx, expert_meta = resolve_expert(user_text)`
   - `shape = reg["shape_of"][role]`
   - **仅当 `expert_meta["shape"] == shape`** 才 `system += "\n\n" + expert_ctx`（shape 过滤器 = 关键，保证 analyst 专家只进 Analyst 节点）
4. 返回 `(system, expert_meta)`。

`make_agent.node()` 改用 `build_pipeline_system`，并用 `expert_meta` 消费模型：
```
shape = reg["shape_of"][role]
expert_model = (expert_meta.get("model") or "") if (expert_meta and expert_meta.get("shape") == shape) else ""
model = model_override or expert_model or models["roles"][role]["model"]
```
（`model_override` / 用户显式 > 专家 model > role→model 映射，三段优先级，与 ChatAgent M12-4 一致。）

### 2.3 文本来源
`make_agent.node` 从 `state["user_task"]` 取文本：`user_text = json.dumps(user_task, ensure_ascii=False)`（字段名+值全参与关键词匹配，`route()` 需 ≥2 命中才选，误匹配阈值已保守）。

### 2.4 多专家同 shape / 租户隔离
- 同 shape 多个启用专家：`resolve_expert` 取**首个匹配**（确定性），不做 fan-out。
- 按租户隔离：`_root()`→`tenancy.account_root()`，专家注册表与包各账号独立；零专家 → `load_registry()` 返回 `[]` → 行为完全不变（向后兼容）。

## 3. 改动清单（精确）
| 文件 | 改动 |
|---|---|
| `tools/experts.py` | 新增 `resolve_expert(text) -> (ctx, meta)`（显式 @ → 隐式 route → 降级不冒泡）；保留 `resolve_mention`/`route`/`build_expert_system` 不动 |
| `chat_agent.py` | `_resolve_expert_context` 委托 `tools.experts.resolve_expert`（行为不变，删重复逻辑） |
| `orchestrator.py` | 新增 `build_pipeline_system(role, reg, user_text=None)`；`make_agent.node` 用其组装 system 并消费 `expert_meta["model"]`（shape 匹配才注入） |
| `tests/test_m12_5_shape.py` | 单测 V1-V6（见 §4） |
| `scripts/verify_m12_5_e2e.py` | 一次性租户根 + 播种 1 个 analyst 专家 → 跑 `build_pipeline_system("Analyst", ...)` 断言注入（不依赖真网） |

## 4. 验收矩阵（V1-V6）
- **V1 显式 @**：`user_task` 含 `@financial-statement-analyst` 且 shape=analyst → `build_pipeline_system("Analyst",...)` 的 system 含 `【专家：...】`；`build_pipeline_system("Researcher",...)` 不含 → shape 过滤生效。
- **V2 隐式 route**：无 @ 但含高密度关键词（如「财报 利润 负债 现金流」）→ `Analyst` 节点注入，其它节点不注入。
- **V3 低置信**：普通任务（无命中）→ `expert_meta is None`，system 与基线一致（无 `【专家】` 标记），无回归。
- **V4 shape 错配**：一个 shape=writer 的专家 + `Researcher` 节点 → 不注入（证明仅 shape 匹配节点生效，不污染其它阶段）。
- **V5 model 消费**：专家 `model: longcat-foo` + shape=analyst → `Analyst` 节点 `expert_meta.model == "longcat-foo"`，且 `model_override` 优先于它（用户显式胜出）。
- **V6 退化**：注册表指一个**包损坏**的专家 → `resolve_expert` 降级 `("", None)`，`build_pipeline_system` 不抛、不注入、行为回退基线（防御纵深，与 M12-4 V8 一致）。

## 5. 诚实边界
- 专家**不改研报结构**：只注入 persona 片段（同 skill 片段层级）；产出 JSON schema 仍由 role `output_key` 约束。
- team 型专家：注册表加载即被拒（`read_expert` 抛 `ExpertError`），`list_experts` 跳过 → **永不进流水线**（与 §5.6 拍板一致）。
- 同 shape 多个专家：仅首个注入（确定性；不做 fan-out，避免过度工程）。
- 前端未展示「本任务用的专家」：后端 `expert_meta` 已在 routing_state 可观测，UI 后续档（本档不动 UI）。

## 6. 风险与对照
- 回归面：`make_agent` 是全部研报节点的工厂；改动须保证「无专家时行为逐字节不变」。V3 专门守这道。
- 与 M12-4 的关系：M12-4 接的是 **chat 侧**（`ChatAgent.step`），本档接的是 **研报流水线侧**（`make_agent`）。两条路径共用 `resolve_expert` 单一入口。
- 测试环境：单测用 FakeBundle/RecordingLLM + 临时租户根（同 M12-4 `scripts/verify_m12_e2e.py` 的 `TENANTS_ROOT` 一次性隔离法），不污染真实 `config/experts.yaml`。
