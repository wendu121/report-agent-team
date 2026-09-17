# REVIEW · M10-P4（独立审议）

<!-- reviewed-by: independent-subagent -->
<!-- reviewer: 独立子代理（未参与实现） -->
<!-- date: 2026-09-11 -->

> ### ⚠️ 审议后状态回填（由主代理追加，**未改动任何原裁定**）
>
> 本审议提出的 **M1–M5 + N1 共 6 项**已于审议后**全部修复并回归**，
> 详见 `VERIFICATION_M10-P4.md` §9「独立审议发现项闭环」。
>
> 补充说明：
> - 本轮修复过程中**又发现 1 项审议未覆盖的阻塞级缺陷**（§4.6：注入素材正文含方括号
>   `[Free]`/`[12.5%]`/`[2025]` → `StubLLMClient._extract_injected_records()` 朴素
>   depth 计数在字符串内部误增减 → 合法 JSON 数组被截断 → 整任务 escalate）。
>   该缺陷在修复 M5（抽取器）后、accio_tariff 与 crossref 真正开始返回素材时才暴露。
> - 修复后测试数：**73 passed → 79 passed**；容器 e2e 双链回到 `status=done`，
>   序列化告警 0。
> - 下方**原文裁定（PASS_WITH_NOTES / MAJOR=0 / PARTIAL）保持原样**，未作任何改写。

## 审议范围与方法

本人**未参与** M10-P4 的实现，仅基于以下亲自阅读的代码 / 亲自跑出的输出做裁定：

- **代码**：`orchestrator.py`（L92-1234）、`tools/data_sources.py`（L477-567、L765-863、L846-863、L959-1055）、`server/api.py`（L40-189、L237-251、L440-501）、`config/agents_library.yaml`、`config/model_mapping.yaml`、`config/skills.yaml`、`config/plugins.yaml`、`agents/{marketscout,ecomanalyst,sourcingadvisor}.md`、`skills/{ecom_competitor_matrix,tariff_cost_model,product_selection}.md`、`tests/test_tools.py`、`tests/test_api_boundary.py`、`web/src/types/api.ts`。
- **命令实证**：`pytest tests/ -q` → **73 passed**；自写离线复现脚本（`.review_repro.py`，已删除）用 `StubLLMClient` + `MockProvider` 构造「两 Agent 共用同一闸」的链验证声明 1，并直接调用 `_extract_keyword` / 各 API 模型验证声明 2/3/4。
- **未依赖** `VERIFICATION_M10-P4.md` 的转述结论，所有裁定均对照源码与实跑输出。

---

## 逐条裁定

### 声明 1：共享闸角色串位已修复 → **PASS**

**根因确属真实**。源码 `orchestrator.py:597`：
```python
reviews = {a["gate"]: a["id"] for a in agents.values()}
```
是 1:1 反查表。实跑复现打印：
```
reg['reviews'] = {'GateA': 'MarketScout', 'GateB': 'SourcingAdvisor', 'GateC': 'Writer'}
```
其中 `GateB` 本应同时映射 `Analyst`/`EcomAnalyst`/`SourcingAdvisor`，但被最后写入者静默覆盖为 `SourcingAdvisor` —— 覆盖问题**属实**。

**修复是否贯通全链路**：是。我复现了链 `["Researcher","Analyst","EcomAnalyst","Writer"]`（Analyst 与 EcomAnalyst 共用 GateB），monkeypatch `build_gate_user` 捕获每次被审角色，输出：
```
GateA -> Researcher
GateB -> Analyst
GateB -> EcomAnalyst      ← 共享 GateB，角色正确，未串
GateC -> Writer
```
`build_graph`（L1255-1257）对 `for role in chain` 逐个 `make_gate(..., role=role)`，`make_gate`（L995、L1003）→ `build_gate_user`（L455）/ `call_eval`（L691）均优先用显式 `role`；两个 router（L1169、L1212）优先读 `rs["last_gate_role"]`（L1087 记录），仅在 `last_gate_role` 缺失时回落 `reg["reviews"]`。

**是否仍有代码路径依赖错误的 `reviews`**：现存活路径均已修正。`reg["reviews"]` 仅作为「`last_gate_role` 缺失时的回落值」存在，而正常引擎流中 `last_gate_role` 总由闸节点写入，故实测不会触发错误值。全仓 `reg["reviews"]` 引用点（`orchestrator.py:455/691/1169/1212`）全部带 `role`/`last_gate_role` 优先保护。**本审议最关键的一点：未发现存活路径仍被错误 `reviews` 击穿。**

> 残留风险见「发现的问题」M1（该错误表是留给未来维护者的陷阱）。

---

### 声明 2：API 传输层闭集已放开 → **PASS**

`server/api.py` 中 `RoutingState.last_gate`/`rework_target_agent`（L90/92）、`ToolStatus.agent`（L135）、`GateReview.gate`（L77）、`EngineEvent.agent/gate`（L64-65）、`ReviewRequest.rework_target_agent`（L212）均已改为 `str`。实跑构造全部自定义名 payload，均不再抛错：
```
OK  ToolStatus(agent="MarketScout")
OK  GateReview(gate="GateD")
OK  RoutingState(rework=EcomAnalyst)
OK  ReviewRequest(rework=EcomAnalyst)
OK  EngineEvent(agent=EcomAnalyst,gate=GateD)
```
**新风险是否可接受**：放开的是「模型层」闭集，但 `create_task`（L319-336）在端点层对 `agents` 做 `_known_agents()` 白名单校验、`plugins`/`model`/`gate_model` 均校验存在性；`rework_target_agent` 由引擎 router 用 `tgt in chain` 兜底（非法即 escalate）。因此非法值**不会**进入引擎逻辑，风险被端点层约束吸收，权衡合理。

---

### 声明 3：产出模型与引擎 shape 已对齐，序列化告警 36→0 → **PASS**（附 2 项未提及风险）

字段声明与实现一致，逐一核对：
- `RetrievalRecord.source: Optional[str] = None`（L108）✓
- `ToolStatus.source: Optional[str] = None`（L138）、`error: Optional[str] = None`（L141）✓
- `AnalysisConclusion.confidence: Optional[str] = None`（L121）✓
- `_validate_records`（L237-251）显式 `model(**item)`，shape 漂移即抛 `ValidationError`（fail loud）✓

测试 `test_projection_models_emit_no_serializer_warning` / `test_task_response_serializes_engine_output_without_warning` 确实钉死「零序列化告警」，`pytest` 全绿。**裁决「36→0」可信。**

**关键质疑 — `confidence` 类型裁定的权威契约**：
- `API_SPEC.md:501` 示例写 `"confidence": 0.85`（float）；
- `agents/analyst.md:36` 写 `"confidence":"high"`（string）—— 即 LLM 运行时真实产出；
- `web/src/types/api.ts:51` 声明 `confidence: number;`（**前端类型仍是 number**）。

实现者「以 prompt 为准」对齐为 `str`，与运行时产出一致、后端正确；但实现者**只把 API_SPEC 记为遗留项（L1），完全漏查了前端 `api.ts` 的 `number` 类型**。目前 `web/` 无任何组件读取 `confidence`（grep 仅命中类型声明），故**暂无非崩溃性运行错误**，但前后端契约已不一致，属未闭环的修复。见 M2。

**关键质疑 — fail loud 是否变成「整任务崩溃」**：是「fail loud」且**被捕获**。源码 `server/api.py:496` 的 `except Exception as e` 会捕获 `ValidationError`（已验证 `issubclass(ValidationError, Exception) is True`），将任务置为 `ESCALATED` 并附原因，**不会**使整个 API 进程崩溃或让任务卡死。故「本可降级的告警」并未变成硬崩。但代价是：**单条记录 shape 漂移会让整份报告被丢弃（整任务 escalate）**，比旧版「带告警透传」更激进——见 M3。

---

### 声明 4：`_extract_keyword` 修复整句查询击穿联想词源 → **PARTIAL**

核心修复成立：纯 CJK 整句（如「无线蓝牙耳机跨境选品与关税成本分析」）正确抽到「无线蓝牙耳机」；英文短语保留空格（「wireless earbuds」不被切词）；整句→0 结果的问题被解决。实现者自测覆盖的用例均通过（`test_tools.py` 中 7 项）。

**但我自设计的、报告未出现的坏 case 暴露了未测边界**（实跑输出）：

| 输入 | 抽取结果 | 问题 |
|---|---|---|
| `iPhone 16 Pro Max 手机壳` | `iPhone`（truncated=True） | 中英混合，按空白切成片段后取**最长英文片段**，丢掉真实商品「手机壳」；下游淘宝联想词变成查 iPhone 而非手机壳 |
| `HS 8518.30 耳机` | `8518.30`（truncated=True） | 含 HS 码片段被当作核心词，联想词接口查无意义数字 |
| `蓝牙耳机 case 保护套` | `蓝牙耳机`（truncated=True） | 中文最长片段胜出，丢掉「case 保护套」，仍误靶向 |
| `！！！` | `！！！`（truncated=True） | 纯标点非「空」，仍会向联想词接口发 HTTP 请求；且 `truncated=True` 标注误导（并未截断，只是原样保留） |
| `😀🔥` | `😀🔥`（truncated=True） | 同上，emoji 触发无意义请求 |

- **引入错误数据的风险属实**：前 3 行说明混合语种查询会把检索**误靶向**到英文/数字片段，丢掉真实中文商品意图。实现者用「诚实标注」辩护——我核实了标注确实进了下游：`SearchTool.search_many`（L1007）把 `note`（`（引擎整句已截取核心词「X」）`）写入 `snippet`，而 `snippet` 正是注入给 Researcher/MarketScout 的字段（L442-447 `_fmt_retrieval_records`）。所以 Agent **能读到**「这是截断词的结果」。但标注只说明「词被截断」，**未说明真实意图是手机壳**，Agent 仍可能把 iPhone 联想词当成手机壳相关素材——检索质量降级未被标注消除，只是「不假装等价」。
- **「空查询不发请求」保证不严密**：仅对空/纯空白/None 返回 `""`（L522-524）。`！！！`/`😀🔥` 等非空垃圾仍会发真实 HTTP（`test_suggest_providers_empty_query_makes_no_request` 只测了空白，未覆盖垃圾输入）。
- **文档瑕疵（NIT）**：`data_sources.py:477-488` 的修复说明注释**重复粘贴了两遍**（L477-482 与 L483-488 几乎完全一致），属复制遗留。

结论：主功能缺陷已修复，但边界（混合语种误靶向、垃圾输入发请求）是真实弱点，且**未被任何测试覆盖**。

---

## 发现的问题

| 编号 | 严重度 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| M1 | MINOR | `reg["reviews"]` 仍是「被覆盖后的错误 1:1 表」且作为 router 回落值保留（`orchestrator.py:597/1169/1212`）。当前存活路径因 `last_gate_role` 总被写入而安全，但这是留给未来维护者的陷阱：任何「直接构造 state / 复用旧状态」的调用都会拿到错误角色。 | 复现打印 `GateB→SourcingAdvisor`；注释自称「兼容历史状态」 | 删除该表或改为 `gate_roles: gate -> [role,...]` 的 1:N 结构；router 回落不应依赖被覆盖值 |
| M2 | MINOR | `confidence` 后端改 `str` 后，前端 `web/src/types/api.ts:51` 仍是 `confidence: number`，前后端契约不一致；实现者仅在 VERIFICATION 记了 API_SPEC，漏了前端类型。 | 实跑 grep + 读 `api.ts:51` | 把 `api.ts` 的 `confidence` 改为 `string`，或后端改回枚举并同步 API_SPEC；二者必须对齐 |
| M3 | MINOR | `_validate_records` fail loud 后被 `server/api.py:496` 的宽泛 `except` 捕获 → 单条记录 shape 漂移会使**整份报告被丢弃（任务 escalate）**，比旧版「带告警透传」更激进。实现者只强调「fail loud 正面」，未说明该代价。 | 实跑确认 `issubclass(ValidationError, Exception)`；L496 捕获后置 ESCALATED | `_validate_records` 内对单条做 try/except，坏记录 quarantine（记 engine_event）而非整任务 escalate；或至少把「形状漂移」与「引擎异常」区分 |
| M4 | MINOR | 选品维度在 artifacts 间自相矛盾：Agent 模板 `sourcingadvisor.md:43-55` 用**四维**（需求/竞争/成本/合规，权重 3/3/3/1），而技能 `product_selection.md` 与 `skills.yaml:83-93` 描述为**六维**（需求/供给/竞争/利润/合规/履约）；DESIGN §2.3 更把技能命名「选品四维评估」却写六维。SourcingAdvisor 同时 injected 两者 → prompt 自相矛盾。 | 读 `sourcingadvisor.md:43-55` 与 `product_selection.md` 全文 | 统一为同一维度框架（建议六维），并修正 DESIGN 技能命名 |
| M5 | MINOR | 声明 4 的边界弱点未被测试覆盖：混合语种误靶向（`iPhone 16 Pro Max 手机壳`→`iPhone`）、垃圾输入发请求（`！！！`/`😀🔥`）。测试只锁了「已实现行为」，未探测真实弱点。 | 复现输出 + `test_tools.py` 用例比对 | 补 `test_extract_keyword` 混合语种/垃圾输入用例，断言至少「不向接口发无意义请求」 |
| N1 | NIT | `data_sources.py:477-488` 修复说明注释重复粘贴两段。 | 读 L477-488 | 删去重复段 |

---

## 实现者未提及的风险

1. **前端 `confidence` 类型契约缺口（M2）**：实现者称「产出模型与引擎 shape 已对齐」，但只核对了后端模型与 API_SPEC，未核对前端 `api.ts`。当前虽无运行错误（前端未消费该字段），却埋下「后端发 string、前端当 number」的潜在破裂点。
2. **fail-loud 的整任务 escalate 代价（M3）**：把「可降级告警」升级为「整任务失败」。这是设计取舍，但实现者未向评审/用户说明——一旦引擎偶发产出一条畸形记录，用户将拿到「escalated 无报告」而非「带瑕疵报告」。
3. **电商技能按 `target_roles:[analyst]` 注入，会顺带进入内置 `Analyst`**：运行默认链 `[Researcher,Analyst,Writer]` 时，内置 Analyst 也会拿到 `ecom_competitor_matrix/tariff_cost_model/product_selection` 三个电商技能。这非缺陷（技能是通用分析框架），但意味着「新增电商能力」的隔离只发生在 Agent 层（`builtin:false`），技能层是角色级共享——需 boss 知悉此副作用。
4. **`reg["reviews"]` 残留错误表（M1）**：详见上。

---

## 测试质量评估

- **`tests/test_api_boundary.py`（新增 14 项）**：质量**良好**。不是橡皮图章——它构造真实自定义名 payload 断言「不抛 `ValidationError`」、断言零序列化告警、`_validate_records` 既断言返回模型实例又断言对漂移抛错。这些用例能真正钉死声明 2/3 的回归。
- **`tests/test_tools.py` 中 `_extract_keyword`（7 项）**：覆盖声明 4 的**主路径**（CJK 整句、已短、去修饰、英文保空格、英文整句、空安全、标注）。但**未覆盖**混合语种与垃圾输入边界（M5），故我发现的误靶向/无意义请求弱点不会被这些测试捕获——属于「照实现写断言、未探弱点」的盲区。
- **整体**：73 passed 真实有效（亲自运行）。未发现「断言了错误行为」的带偏测试；`_validate_records_fails_loud_on_shape_drift` 断言抛错，`test_suggest_providers_empty_query_makes_no_request` 断言空查询不发请求——均正确。

---

## 结论

- **总体裁定：PASS_WITH_NOTES**
- **MAJOR 问题数：0**
- 4 项声明裁定：声明 1 **PASS**、声明 2 **PASS**、声明 3 **PASS**、声明 4 **PARTIAL**。
- **是否建议 commit**：**建议 commit，但须先消化 M2/M3 两项 MINOR**（尤其 M2 前端 `confidence` 类型与后端不一致属明确契约未闭环；M3 建议评估是否对单条坏记录做 quarantine 而非整任务 escalate）。其余 M1/M4/M5/N1 可后续排期，不阻塞本里程碑。

**理由**：实现者的 4 项声明基本经得起独立核验——共享闸串位是真实 bug 且已被全链路贯通修复、闭集放开且风险被端点层吸收、模型对齐使序列化告警归零、整句击穿联想词源的主修复成立。本人未发现的实现者「夸大/不实」之处；但实现者**漏报了两处真实弱点**（前端 `confidence` 契约缺口、fail-loud 的整任务 escalate 代价）与**声明 4 的未测边界**（混合语种误靶向、垃圾输入发请求），这些属诚实边界内的遗漏，不构成否决 commit 的 MAJOR 缺陷，但应在合并前补全以兑现「零密钥、不污染、不编造」的工程纪律。
