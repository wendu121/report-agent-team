# 独立审议 · M8-5 子集编排（第三闸 / edict-gate）

> ⛔ **第 18 轮（UI 设计系统重做 · 2026-09-16）状态：BLOCKED —— 独立审议未执行。**
> 原因：全部 7 个子代理在 2026-09-16 14:17–14:20 因 API **429** 失败，配额将于 **14:56:07 (UTC+8)** 重置，无法派独立 reviewer。
> 处置：**主代理未自签、未 commit**；自审见 `VERIFICATION.md §17`，补审待办见本文件末尾「§18 补审待办」。
> ⚠️ 下文所有 `<!-- reviewed-by: independent-subagent -->` 标记**仅代表其各自轮次（M8-5 / M9-1）**，**不覆盖本轮 UI 重做**。
> ✅ **补审已执行（2026-09-16）**：第 18 轮 UI R3+R4 独立审议现已完成，权威结论见本文件最末「## 第 18 轮补审 · UI R3+R4（独立审议结论 · 2026-09-16）」。原 429 BLOCKED 记录作历史留痕保留。

**审议人**：independent-subagent（独立，未由主代理签署）
**日期**：2026-09-08
**范围**：`orchestrator.py` `server/admin.py` `server/api.py` `server/engine_client.py` `server/engine_runner.py` + `VERIFICATION.md §14`

---

## 一句话结论

**PASS** — 九项审查标准全部经代码核对 + 真实离线 stub e2e + 单元测试验证，无 BLOCK 级问题。发现 3 处低危文档/链路小瑕疵（不阻塞）。

---

## 发现表

| 严重度 | 项 | 证据 | 建议 |
|---|---|---|---|
| 低 | 链路中 `gates` 为死参数（引擎侧不消费） | `server/engine_runner.py:58` 仅透传 `agents`；`run_report(agents=…, gates=…)` 形参 `gates` 在 `build_graph` 中未被使用（`orchestrator.py:971-999` 由 `GATE_NAME` 从 `agents` 推导 gate）。`gates` 仅作 UI/校验配对元数据 | 可接受（设计如此）。若想消除歧义，可在 `run_report`/`engine_runner` 加一行断言 `gates == {AGENT_GATE_PAIR[a] for a in agents}` 防御性校验，或删掉 `gates` 形参 |
| 低 | 过时注释（M8-5 已落地但注释仍称"待 M8-5"） | `server/api.py:241`「子集编排待 M8-5 引擎改造」；`server/admin.py:736-742` 段头 + `:872` `meta.constraint`「子集编排待 M8-5」 | 改注释为"已由 M8-5 落地"，避免误导后续维护者 |
| 低 | 任务摘要提示写 gate 节点名为 `gate_a/gate_b/gate_c`，实际为 `gate_researcher/gate_analyst/gate_writer` | `orchestrator.py:982` `"gate_" + role.lower()` | 仅提示文案误差，非代码缺陷；无需改码 |
| 信息 | `api.py:337` `emit_task_escalated(..., task.routing_state.last_gate or "GateC")` 默认 "GateC" | 子集在 GateA 升级时 `last_gate` 已是真实值，不会落到默认；安全 | 无动作 |

---

## 逐条核对（真实测试，不轻信摘要）

**① 向后兼容（agents=None）** ✅
- `build_graph`：`chain = agents or ["Researcher","Analyst","Writer"]`（`orchestrator.py:977`），缺省即原 3+3 全链。
- 实测 `run_report(task, stub, agents=None)` → `status=done, last_gate=GateC`，retrieval/analysis/draft 三字段齐全，`prior_versions` 键 = `['Researcher','Analyst','Writer']`。
- 结论：与原硬编码 6 节点图行为一致。

**② 子集正确性** ✅
- `_make_route_router`/`_make_route_after_gate`（`:911-935, :950-965`）+ `build_graph` 中 `is_terminal=(role==chain[-1])`（`:982`）。
- 实测：
  - `[Researcher]` → `done @ GateA`（末闸 `is_terminal=True` 落定，`:845` `if is_terminal and decision=="advance": rs["status"]="done"`）→ 验证 `route_after_gate` 末位 return `END`（`:964`）。
  - `[Researcher,Analyst]` → `done @ GateB`。
  - `[Writer]`（非前缀）→ `escalated @ GateC`（设计行为，见 ⑦）。

**③ prior_versions 双键 bug 已根除** ✅（B2 真实修复）
- init 改为 `{role: [] for role in (agents or FULL)}`（`:1026`，**大写**）；`make_agent` 写 `pv[role]`（`:736-737`，`role` 来自 `chain` 大写）。
- 实测 E2 `prior_versions` 键仅 `['Researcher']`、E3c 仅 `['Researcher','Analyst']`——无任何小写 `researcher` 重复键。确认旧小写 init 已彻底移除。

**④ 孤儿闸校验** ✅（实测）
- `admin._validate_template`：`expected_gates={AGENT_GATE_PAIR[a] for a in ags}`；`set(gts)!=expected_gates → 400`（`:823-835`，`AGENT_GATE_PAIR` 定义于 `:755`）。
- 直接调用：`agents=["Writer"],gates=["GateA"]` → 含明确中文错误「期望 ['GateC']，收到 ['GateA']」；`agents=["Researcher"],gates=["GateA"]` → `errs=[]`。

**⑤ 透传链路** ✅
- `EngineProcess`/`launch_engine` 接 `agents/gates` 写入 `input_data["agents"/"gates"]`（`engine_client.py:35-44, 59-60, 168-179`）；`engine_runner.py:58` 读 `agents`；`api.py:297-298` `launch_engine(agents=task.agents, gates=task.gates)`。
- `create_task` 把模板 `agents/gates` 落库为任务元数据并回传（`api.py:257-283, 358-359`）。子集值全链路贯通到引擎启动。

**⑥ rework 越界诚实升级** ✅（实测）
- `_make_route_router` rework 分支：`return tgt.lower() if tgt in chain else "escalate"`（`:933`）——子集外 rework 目标**不**静默跑全链，而是 escalate。
- 单测：`[Researcher]` 子集下 rework 目标 `Analyst`（不在 chain）→ `escalate`；目标 `Researcher`（在 chain）→ `researcher`。

**⑦ 自审诚实度（VERIFICATION.md §14）** ✅ 无夸大
- B1（done 硬编码 GateC）：§14.4 明确说明原 `if gate_name=="GateC"` 及修复（`is_terminal` 形参 + `build_graph` 传参）；与代码 `:845, :982` 一致。
- B2（prior_versions 双键）：§14.4 如实记录 + 复测键干净；与本次实测一致。
- 非前缀子集 escalate：§14.3（E3 `[Analyst,Writer]→escalated@GateB`、E3b `[Writer]→escalated@GateC`）+ §14.5 明确「是设计行为，非缺陷」（下游缺上游源 → 闸诚实升级）。本报告实测 `[Writer]` 亦 `escalated@GateC`，与文档吻合，**无 overclaim**。
- HTTP 真实引擎未跑（429 配额）：§14.5 诚实声明，正确性由 stub 离线 e2e（E1-E5）+ E4a-d 覆盖。本报告未做 429 实测，接受该诚实边界。

**⑧ 无范围蔓延** ✅
- git 改动仅：`orchestrator.py` `server/{admin,api,engine_client,engine_runner}.py` `VERIFICATION.md` + 新增未跟踪 `DESIGN_M8-5.md`。**无前端改动**（无 `web/` 改动），**未新增角色**，`ReportState` 字段定义未动，`model_mapping.yaml` 未触，agents/gates prompt 文件未动。符合裁定。

**⑨ 代码质量** ✅
- 全 5 文件 `py_compile` 通过。
- 全文 grep `route_router|route_after_agent|route_after_gate` 仅命中新 `_make_*` 定义与调用（`orchestrator.py:911,938,950,986,991,994`），**无旧名残留引用**。
- 未发现未定义 import 或静默失败路径。

---

## B1 / B2 修复确认（从代码+测试推理，非信任摘要）

- **B1 真修**：`make_gate(gate_name, llm, models, is_terminal=False)`（`orchestrator.py:760`），终态逻辑改为 `if is_terminal and decision=="advance"`（`:845`），由 `build_graph` 按 `role==chain[-1]` 决定末闸（`:982`）。实测 `[Researcher]→done@GateA`、`[Researcher,Analyst]→done@GateB` 证明末闸不再绑死 GateC。
- **B2 真修**：`run_report` init `prior_versions={role: [] for role in (agents or FULL)}`（`:1026`，大写），与 `make_agent` 写入键 `pv[role]`（`:736-737`，大写）一致。实测各子集 `prior_versions.keys()` 均为纯大写无重复，旧小写 `researcher` 并存现象已消失。

---

## 非前缀子集 escalate 行为

正确作为**设计行为**（非缺陷）记录于 `VERIFICATION.md §14.3 / §14.5`，且被本审议实测（E3b `[Writer]→escalated@GateC`）佐证。下游 Agent 依赖上游真实产物，缺上游源时闸诚实升级而非编造——与「UI 必须是控制器而非查看器 / 拒绝假配置」原则一致（子集是用户真实选择，引擎如实执行）。**文档无 overclaim，结论成立。**

---

## 标记确认

文件末尾独立议定标记已写入：`<!-- reviewed-by: independent-subagent -->`

---

## 总评

M8-5 达成「模板所选 agent/gate 子集真正驱动引擎流水线」的目标，且未引入假配置、未越界触碰前端/字典/角色定义。两处自纠 bug（B1/B2）经验证确已修复，自审文档诚实无夸大。3 处低危瑕疵均为注释/死参数层面的可优化项，不阻塞合入。

**裁定：PASS**

<!-- reviewed-by: independent-subagent -->

---

## M9-1 独立审议（第 16 轮 · 2026-09-08）

**审议人**：independent-subagent（独立，未由主代理签署）
**对象**：`DESIGN_M9-1.md` + 代码实证（`orchestrator.py` / `server/admin.py` / `VERIFICATION.md §15`）
**方法**：所有结论均来自 Read/Grep 实证，非信任文档摘要。

### 1. 代码事实核实（设计断言 vs 源码）

| # | 设计断言 | 实证结果 | 判定 |
|---|---|---|---|
| F1 | `orchestrator.py:40-59` 确有 4 个硬编码字典，TOOL 为死字典 | 实测 `:40-59` 四字典齐备；`grep TOOL[` 仅命中 `:45` 定义处，运行时零读取 | ✅ 如实 |
| F2 | `make_agent`（:573）按 `if role=="Researcher/Analyst/Writer"` 硬编码分发 (:593/621/633/671/694/703) | 实测六处分支与行号完全吻合 | ✅ 如实 |
| F3 | `build_gate_system`（:347）读单一 `gates/review.md` | 实测 `:347 body=_load_md("gates/review.md")` | ✅ 如实 |
| F4 | `build_gate_user`（:385/386 用 GATE_REVIEWS/PROD_KEY；:395 GateC 附 report_markdown；:403 GateB 附检索上下文） | 实测 `:385/386/395/403` 与描述一致 | ✅ 如实 |
| F5 | `machine_check`（:426/442/459）三段仅依赖产出形态，与闸名无关 | 实测三段分别操作 `retrieval_records`/`analysis_conclusions`/`draft_segments`，无闸名特异性逻辑 | ✅ 如实（shape 复用成立） |
| F6 | `build_graph`（:972 `GATE_NAME[role]`）；`run_report`（:1004→:415 热加载；:1008 `build_graph` 在 `run_report` 内） | 实测 `:1004 load_model_mapping`、`:1008 build_graph(...)`；图每次现建 | ✅ 热加载零重启论断成立 |
| F7 | `admin.py:752-753` `KNOWN_AGENTS`/`KNOWN_GATES` 硬编码 `["Researcher","Analyst","Writer"]`/`["GateA","GateB","GateC"]` | 实测 `:752-753` 与描述一致；`:166 _atomic_write`、`:172 _write_audit` 可复用；templates CRUD `:888-927` 可仿 | ✅ 如实 |
| F8 | 提出 `AGENT_GATE_PAIR` 需一并派生 | **设计完全未提此常量**；实测 `admin.py:755` 存在第三处硬编码 `{"Researcher":"GateA","Analyst":"GateB","Writer":"GateC"}`，且 `:830` 在 `_validate_template` 中 `expected_gates = {AGENT_GATE_PAIR[a] for a in ags}` 用于配对校验 | ❌ 漏提（见阻塞 B1） |

### 2. 发现的问题

#### 阻塞（BLOCKED 项）
- **B1 · G7「真驱动闭环」实际断裂（核心价值受损）**
  `DESIGN_M9-1 §4.4` 与 `VERIFICATION.md §15.4` 仅声明把 `KNOWN_AGENTS`/`KNOWN_GATES`（:752-753）改为派生自 library，却**漏提紧邻的 `AGENT_GATE_PAIR`（:755）**。该常量在 `server/admin.py:830` 被 `_validate_template` 用于「gates 必须与 agents 配对」硬校验。
  后果：即便 KNOWN_AGENTS 派生成功接纳 `Coder`，执行 E5（`POST /admin/templates agents=["Researcher","Coder"]`）时 `:830` 会对 `AGENT_GATE_PAIR["Coder"]` 触发 `KeyError`（未兜底）→ 500 崩溃，模板创建失败。即设计自诩的「UI 增角色 → 引擎真驱动」闭环在 E5 这一最关键证据点**直接断裂**。
  修正：§4.4/§15.4 须增补「`AGENT_GATE_PAIR` 一并派生自 `gate_of`（`{a["id"]: a["gate"] for a in agents}`），或 `:830` 改为 `{reg["gate_of"][a] for a in ags}`」。否则 G7 属 overclaim。

#### 低危（PASS_WITH_NOTES 待修）
- **N1 · `output_key` 自相矛盾（字段错配隐患）**
  `§3.1` 示例（:117-125）给自定义 `Coder` 写 `output_key: code_records`，但 `§7`（:272）写明「output_key 由 shape 决定（researcher→retrieval_records…），不允许自由填」。二者直接冲突。
  正确机制（决定 E7 机器校验能否复用）：自定义 researcher 形 agent 必须写入顶层 `retrieval_records`（machine_check shape=researcher 只读该字段，见 F5）。若实现误信 §3.1 示例写 `code_records`，则 GateD 校验 `retrieval_records` 为空→误 escalate，且下游 Analyst 注入的 `retrieval_records` 取不到上游。须以 §7 为权威、改正 §3.1 示例为 `output_key: retrieval_records`，并在实现层强制绑定（不暴露自由填）。
- **N2 · `build_gate_system` 的 per-gate 文件加载未进 §3.4 实施草图**
  R1/C2（设计 §10 / §15.2）明确「`build_gate_system` 先查 `gates/<gate>.md`，缺则回落 `gates/review.md`」，但 §3.4 列出的重构对象只含 `machine_check`/`build_gate_user`/`make_gate`/`build_agent_system`/`build_graph`/`call_eval`，**漏列 `build_gate_system`**；§12 提交集也未列。需求已承认，仅靠阅读者记忆兜底，建议在 §3.4 显式补一条 `build_gate_system` 改造，避免实现漏做新闸 prompt 加载。
- **N3 · 删除模块级四字典前的全仓消费面扫描**
  实证：server 内除 `engine_runner.py:58`（仅为注释「gates 由 GATE_NAME 推导」，无运行时 `GATE_NAME[` 消费）与 `admin.py` 的 `GATE_NAME_RE`（正则，非字典）外，未发现 orchestrator 以外的运行时消费；web 端不 import 这些常量。删除影响面基本收敛于 orchestrator 内部，但设计未要求提交前 `grep PROD_KEY|GATE_NAME|GATE_REVIEWS|TOOL` 全仓确认。建议 §12 增一步「删除前全仓 grep 零外部引用」作为回归防护。

### 3. 审议结论

- **真伪（是否假配置）**：✅ 引擎驱动链路真实。shape 驱动 `make_agent` + 派生 `GATE_NAME/GATE_REVIEWS` + 复合写 md/gate/mapping/library 的设计在机制上能真正驱动流水线，非「UI 可改引擎忽略」。但 **G7 闭环因 B1（漏 `AGENT_GATE_PAIR`）在 E5 实际断裂**，当前文档状态若照抄实现会产出假闭环。修正 B1 后即为真驱动。
- **shape 正确性**：⚠️ 机制正确。`machine_check`/`build_gate_user` 改 shape 驱动后，GateD（shape=researcher）确能复用 GateA 校验（只读 `retrieval_records`）。**唯一字段错配风险即 N1**：`output_key` 必须与 shape 强制绑定（§7），否则自定义闸校验与跨 agent 上下文注入错位。只要落实 §7 绑定，无字段错配。
- **过度工程**：✅ 克制。N1-N5（无节点编辑器/G6 渲染/RBAC/新工具类型/M9-2~5）边界清晰，复合写 4 文件为「真驱动」所必需，无冗余。ruamel 保注释对 model_mapping 已落地、对 library 新文件合理。未见可删减项。
- **回归风险**：⚠️ 低危可控。不传 `agents` 时 `build_graph`（:967）默认 3+3，向后兼容成立（E1）。§3.2–3.6 已覆盖 orchestrator 内全部 4 字典消费点（:334/385/426/496/574/577/751/972），外部消费面经实证基本为零（N3 补一道 grep 即可闭口）。无破坏 M8-5 子集编排的迹象。
- **诚实性**：⚠️ 整体诚实（C1-C3、R1-R6、N1-N5、D1-D3 均如实标注），但有一处 **overclaim**：§15.4 称「形成完整闭环，杜绝假配置」，而 B1 使之在 E5 不成立；另 §3.1 示例与 §7 矛盾未标注（N1）。修正 B1/N1 后诚实性无虞。

### 裁定：BLOCKED

**阻塞项（必须修，否则照文档实现会产出自喻的假闭环）**：
- **B1**：`§4.4` 与 `VERIFICATION.md §15.4` 必须补「`AGENT_GATE_PAIR`（admin.py:755）一并派生自 library 的 `gate_of`」，并相应改 `admin.py:830` 配对校验；否则 E5（`agents=["Researcher","Coder"]`）对 `AGENT_GATE_PAIR["Coder"]` KeyError，G7 真驱动闭环断裂。

**必须修的 NOTES（非阻塞但落地前需消解）**：
- **N1**：以 §7 为权威，改正 §3.1 示例中 `Coder.output_key: code_records` → `retrieval_records`，实现层强制 shape→output_key 绑定，消除字段错配隐患。
- **N2**：§3.4 实施草图与 §12 提交集补列 `build_gate_system` 的 per-gate 文件加载改造（R1/C2 已要求，但当前草图漏列）。
- **N3**：§12 增「删除模块级四字典前全仓 `grep` 零外部运行时引用」防护步骤。

> 说明：F1–F8 中除 F8（即 B1）外，设计对 orchestrator 的代码事实描述全部准确，shape 驱动重构在机制上可行且克制。唯一阻断是「派生清单漏了 `AGENT_GATE_PAIR`」这一处具体、可定位的遗漏，修后即应可 PASS。

<!-- reviewed-by: independent-subagent -->

---

## M9-1 独立审议修订确认（第 16 轮补审 · 2026-09-08）

> 视角：与第 16 轮同一独立子代理。本轮只审「B1/N1/N2/N3 四项修订是否到位」，不重新全盘审；核查均亲手 Read `DESIGN_M9-1.md` / `VERIFICATION.md` / `server/admin.py`，未信摘要。

### 逐项闭合核对

**B1 · `AGENT_GATE_PAIR`（admin.py:755）派生 + :830 配对校验改法 —— 闭合 ✅**
- `DESIGN_M9-1.md §4.4`（:214-227）已显式列出三处硬编码 `KNOWN_AGENTS`(:752)/`KNOWN_GATES`(:753)/`AGENT_GATE_PAIR`(:755)，并给出 `_agent_gate_pair() = {a["id"]: a["gate"] for a in agents}`（即 `load_agent_registry` 的 `gate_of`）。
- 关键修正到位（:225）："`admin.py:830` `_validate_template` 的配对校验 `expected_gates = {AGENT_GATE_PAIR[a] for a in ags}` 必须改为 `{_agent_gate_pair().get(a) for a in ags}`"，并明确若不改则 E5（`agents=["Researcher","Coder"]`）对 `AGENT_GATE_PAIR["Coder"]` KeyError→500。→ 原 B1 断裂点已消除。
- `VERIFICATION.md §15.4`（:907-911）同步补全三处派生 + :830 改用派生表 + 注明「独立审议第 16 轮 B1 已纠出，属设计层遗漏，已在本 §15.4 与 DESIGN_M9-1 §4.4 修正」。
- 实证代码 `server/admin.py`：`:755 AGENT_GATE_PAIR = {...}` 确为真实待改代码，`:830 expected_gates = {AGENT_GATE_PAIR[a] for a in ags}` 确为真实配对逻辑——修订方向相反、方向正确。
- **微小残余（非阻塞）**：`§2.1 G7`（:70）目标句仍只写「`KNOWN_AGENTS`/`KNOWN_GATES`（:752-753）改为派生自 library」，未把 `AGENT_GATE_PAIR`（:755）一并点名。但 §4.4/§15.4 已完整覆盖，机制无歧义，G7 闭环在文档层面已闭合，故不升级为阻塞。

**N1 · §3.1 示例 `output_key` 与 §7 矛盾 —— 闭合 ✅**
- `DESIGN_M9-1.md §3.1` 自定义 `Coder` 示例（:117-125）现为 `shape: researcher` + `output_key: retrieval_records`，并注明「⚠️ 由 shape 强制推导（§7）；自定义 researcher 形必须写此值，machine_check shape=researcher 只读 retrieval_records」。原 `code_records` 矛盾项已消除。
- `§7`（:284）权威规则「`output_key` 由 shape 决定（researcher→retrieval_records / analyst→analysis_conclusions / writer→draft_segments），不允许自由填」与示例一致。字段错配隐患清除。

**N2 · §3.4 漏 `build_gate_system` per-gate 加载 —— 闭合 ✅**
- `DESIGN_M9-1.md §3.4`（:165）已补条目：`build_gate_system（per-gate 文件加载，R1/C2）：:347 body=_load_md("gates/review.md") 改为先查 gates/<gate_name>.md，存在则读该文件，否则回落 gates/review.md（内置 3 闸维持现状）`。→ 自定义闸 GateD 读独立 `gates/gate_d.md`，不污染 review.md。
- `§12` 提交集（:335）orchestrator 改动已含 `build_gate_system`；`§10 R1`（:319）亦重申 per-gate 回落策略。阅读者不再需凭记忆兜底。

**N3 · §12 缺删除四字典前全仓 grep 防护 —— 闭合 ✅**
- `DESIGN_M9-1.md §12`（:336）已增：「删除模块级四字典前的回归防护：提交前全仓 `grep -rn "PROD_KEY\|TOOL\[\|GATE_NAME\[\|GATE_REVIEWS\["` 确认除 orchestrator 内部外零运行时引用（N3：web 端不 import 这些常量；server 内 engine_runner.py:58 仅为注释）；若有外部引用须先改后再删，避免 KeyErrors。」——已补防护步骤。

### 有无新阻塞
- 复核四项修订涉及段落（§2.1/§3.1/§3.4/§4.4/§7/§10/§12 及 VERIFICATION §15.4），未发现新的阻塞级问题。B1 闭合后 E5「UI 增角色 → 引擎真驱动」链路在设计层自洽。

**裁定：PASS**

<!-- reviewed-by: independent-subagent -->

---

## M9-1 独立审议（第 17 轮 · 实施 · 2026-09-08）

**审议人**：independent-subagent（独立，未由主代理签署）
**对象**：实施代码（`orchestrator.py` / `server/admin.py` / `server/api.py` / `web/src/*`）+ `VERIFICATION.md §16`
**方法**：所有结论来自 `Read`/`Grep`/`py_compile` 实证，不信任 `VERIFICATION.md §16` 摘要。第 16 轮审议只审设计文档，本轮首审**落地代码**，故能发现设计层看不到的实现回归。

### 1. 逐项核对（A1–A16）

| # | 核查项 | 实证结果 | 判定 |
|---|---|---|---|
| A1 | `PROD_KEY/TOOL[/GATE_NAME[/GATE_REVIEWS[` 零残留 | grep 仅命中 `:38`、`:437` **两条注释**；`PROD_KEY =`/`TOOL =`/`GATE_NAME =`/`GATE_REVIEWS =` 赋值 **0 命中** | ✅ |
| A2 | `load_agent_registry()` 存在 + DEFAULT 兜底 | `:434` 定义；`:443` `if not p.exists(): data={"agents": list(DEFAULT_REGISTRY["agents"].values())}`；`DEFAULT_REGISTRY` 见 `:43`（全 3 内置） | ✅ |
| A3 | `make_agent`（:622）shape 驱动 | `shape=reg["shape_of"][role]`(:632)；`allow_empty=(shape=="researcher")`(:635)；工具 `if shape=="researcher"`(:651)；上游注入 `if shape=="analyst"/elif shape=="writer"`(:679/691)；溯源校验 `if shape=="researcher"`(:753)；doc_export `if shape=="writer"`(:762)——**全为 shape 比对，无角色名** | ✅ |
| A4 | `machine_check`（:461）shape 驱动 + 自定义闸复用 | `shape=reg["gate_shape"][gate_name]`(:470)；三段 `if shape=="researcher"/"analyst"/"writer"`(:471/487/504)。GateD(shape=researcher) 自动复用 GateA 校验逻辑 | ✅ |
| A5 | `build_gate_system`（:349）per-gate 回落 review | `:356-357` 先查 `gates/<gate_name>.md`，`exists()` 否则 `_load_md("gates/review.md")` | ✅ |
| A6 | `build_graph`（:1045）派生全链 + 节点命名 | `chain = list(agents) if agents else default_chain(reg)`(:1053)；节点 `role.lower()`(:1057)、`gate_"+role.lower()`(:1058)；`make_gate(..., is_terminal=(role==chain[-1])...)`(:1060) | ✅ |
| A7 | `run_report` 热加载 + prior_versions 大写键 | `reg=load_agent_registry()`(:1094)；`build_graph(...,agents=agents,reg=reg)`(:1098)；`prior_versions={role:[] for role in (list(agents) if agents else default_chain(reg))}`(:1106)——键为大写 role | ✅ |
| A8 | `StubLLMClient.complete` shape 驱动 | `:137` 取 `shape=kw.get("shape")`；agent 分支 `if shape=="researcher"/"analyst"/"writer"`(:156/174/185) 真驱动自定义角色 | ⚠️ 见 N1 |
| B9 | `KNOWN_AGENTS/KNOWN_GATES/AGENT_GATE_PAIR` 已删，改派生 | 赋值 0 命中；改 `_agent_names()`(:380)/`_known_gates()`(:784)/`_agent_gate_pair()`(:788) | ✅ 但见 **阻塞 B1** |
| B10 | `:830` 配对校验 `.get(a)` 消 KeyError | `pair=_agent_gate_pair(); expected_gates={pair.get(a) for a in ags}`(:875-876)，未知 agent 不 KeyError | ✅（上轮 B1 真闭合） |
| B11 | `POST /admin/agents-library` 4 写 + round-trip + 异基座 + 回滚 | agents/<id>.md(:1195) / gates/<gate>.md(:1201) / model_mapping.yaml(ruamel :1207-1215) / agents_library.yaml(round-trip :1220-1231)；`role_base==gate_base→400`(:1166)；异常全回滚删文件+还原两 yaml(:1234-1256)；`_write_audit` 全程留痕 | ✅ |
| B12 | `DELETE /admin/agents-library/{aid}` 内置拒删 + 悬空防护 | `if target.get("builtin")`→400(:1273)；删前扫描 templates 引用 aid/gate→400 点名模板(:1280-1294) | ✅ |
| C13 | `CreateTaskRequest.agents` + 子集透传 + 未知 400 | `agents:Optional[List[str]]=None`(:152)；`tpl_agents=list(request.agents)`(:283)、`tpl_gates=[pair[a] for a in request.agents if a in pair]`(:284)；未知 `INVALID_AGENTS`→400(:274-282) | ✅ |
| C14 | 前端市场页/入口/预选 | `web/src/views/Agents.vue` 存在；`router/index.ts` `/agents`(:18,meta 智能体市场)；`DefaultLayout.vue` 智能体菜单(:15-17)；`TaskSubmit.vue` 读 `route.query.agents`(:58) 并 `agents: presetAgents` 提交(:87) | ✅ |
| D15 | `py_compile` 三模块 | 0 错（`COMPILE_OK_0_ERRORS`） | ✅ |
| D16 | §16 E1–E7 与代码一致 + R6 诚实 | E1–E7 代码路径均可达；R6 明确「真实 LLM 未跑，stub 离线实证」(:992)，无 overclaim | ✅ 但见 **B1**（自审未捕获） |

### 2. 发现的问题

#### 阻塞级（必须修，合入前必改）
- **B1 · `admin.py:915-916` 遗留未定义常量引用（M9-1 重构引入的真实回归）**
  M9-1 删除了模块级 `KNOWN_AGENTS`/`KNOWN_GATES`/`AGENT_GATE_PAIR` 三个常量（B9 已确认删除），但在 `list_templates` 的 `meta` 块里**漏改两处引用**：
  ```python
  "known_agents": KNOWN_AGENTS,   # :915
  "known_gates":  KNOWN_GATES,    # :916
  ```
  全仓 grep `KNOWN_AGENTS|KNOWN_GATES|AGENT_GATE_PAIR` 仅存注释(:757)+这两处用法，**无任何赋值**。→ 调用 `GET /admin/templates` 必抛 `NameError`→**HTTP 500**。
  性质：第 16 轮审议只审设计文档（`KNOWN_*` 应派生），从未看到落地代码，故该实现残留逃逸；`py_compile` 不报（运行时才炸），`VERIFICATION §16` 的 E1「py_compile 0 错」与 E5「POST 模板」均未触达该 GET 路径，自审漏检。
  修正（一行级）：`:915-916` 改为 `"known_agents": _agent_names(), "known_gates": _known_gates()`（两函数已存在），或删除这两个 meta 键。修复后 `GET /admin/templates` 不再崩溃。

#### 低危（非阻塞，建议顺手修）
- **N1 · `StubLLMClient.complete` 的 gate rework 场景仍硬编码 `GateA`**（:144 `if role == "GateA"`）。仅影响 stub 在 `scenario="rework"` 下对**内置 GateA** 的复现；自定义 researcher 形闸（GateD）在 stub rework 场景会直接 advance，无法经 stub 走 rework 路径。生产 `NewApiLLMClient` 忽略此分支、不受影响，故仅测试便利性瑕疵。
- **N2 · `POST` 回滚的 orphan 文件泄漏（边角）**：`:1238-1242` 回滚仅当 `mapping_before/library_before is not None` 才还原；若首次新增自定义 Agent 前两 yaml 不存在、且写它们之后失败，则新建的 yaml 不会被清理。正常路径不触发，属极端边界。

### 3. 审议结论（逐条）

- **真伪 · 是否假配置**：✅ **非假配置。** 链路真实贯通——UI 新增自定义 Agent → `POST /admin/agents-library` 复合写 4 处（prompt md / gate md / model_mapping / library）→ `load_agent_registry()` 热加载 library → `make_agent` 按 shape 驱动 → `StubLLMClient`/`NewApiLLMClient` 真驱动自定义角色产出。不是「UI 可改、引擎忽略」。上轮担心的 `AGENT_GATE_PAIR` KeyError（E5 断裂）已在代码层用 `_agent_gate_pair().get(a)` 真正闭合。
- **回归风险**：⚠️ **主体安全，但存在一处真实回归。** 不传 `agents` 时 `default_chain(reg)` 取 builtin 派生全链、`prior_versions` 大写键，向后兼容成立；M8-5 子集编排仍工作（`create_task` 子集覆盖 + `pair[a]` 派生 gates，E5 路径代码可达）。**但 B1（`:915-916` 未定义 `KNOWN_AGENTS/KNOWN_GATES`）会让既有的 `GET /admin/templates` 管理端直接 500**，是 M9-1 重构自身造成的回归，合入前必须修。
- **过度工程**：✅ **克制。** 热加载、派生表、复合写 4 文件均为「真驱动」所必需，无冗余抽象；ruamel 保注释复用既有机制。未见可删项（`Agents.vue`/`AgentDetail.vue`/settings 联动属市场页功能范畴，不过度）。
- **诚实性**：✅ **R6 如实。** `VERIFICATION §16.3` 明确标注「真实 LLM 未跑，stub 离线实证覆盖、不 claim 已通」(:992)，E1–E7 表头亦注明 stub 离线(:928)。无 overclaim。唯一瑕疵：`§16.1` 自审计声称已把 `KNOWN_*` 改为派生，**未记录自己漏改 `:915-916` 引用**——属自审盲区，非刻意隐瞒，修复 B1 后即闭合。

### 裁定：PASS_WITH_NOTES

**必须修 NOTES（合入前阻塞级，须先修）**：
- **B1**：`server/admin.py:915-916` 的 `KNOWN_AGENTS`/`KNOWN_GATES` 为未定义常量，导致 `GET /admin/templates` 必 500。改为 `_agent_names()`/`_known_gates()` 或删除该两 meta 键。一行级修复，但为 M9-1 重构引入的回归，须先修再合入。

**低危 NOTES（非阻塞）**：
- **N1**：stub gate rework 场景硬编码 `GateA`（:144），自定义闸 stub rework 路径不可达（生产无影响）。
- **N2**：`POST` 回滚在「首建前 yaml 不存在且中途失败」的边界会残留新文件（极低概率）。

> 说明：除 B1 这一处实现残留外，M9-1 全部设计意图（shape 驱动重构、4 字典外置、复合写真驱动、子集透传、诚实标注）均在代码中**真实落地**，非假配置、非过度工程。B1 是删除常量时遗漏的最后一处引用，修复成本极低，修后应予 PASS。

<!-- reviewed-by: independent-subagent -->

---

## §18 补审待办 · UI 设计系统重做（第 18 轮 · BLOCKED 待审 · 2026-09-16）

> 本轮独立审议**未执行（429）**。配额恢复后，独立子代理须按下列清单补审，通过后方可 commit。**主代理不得代签。**

**待审对象**：`web/src/style.css`、`web/src/components/PageHead.vue`（新增）、`web/src/components/EntityCard.vue`、`web/src/utils/emoji.ts`、`web/src/views/{Plugins,Agents,Channels,Skills}.vue`，以及 `VERIFICATION.md §17`

**必查项**：
1. **CSS 完整性**：`style.css` 令牌自洽；Element 主题块**仅 1 份**（本轮曾出现重复块，已删）；无遗留 `.market{}` / `.market-head` / `.filters{}` / `.grid{}`。
2. **页头接线**：4 页是否均 `import PageHead` 并使用 `<PageHead>`；`icon` 值（Box / Cpu / Promotion / MagicStick）是否真存在于 `@element-plus/icons-vue`。
3. **卡片回退**：`EntityCard.vue` 的 `icon` 可选、`avatarText` 回退可用（无 icon 时仍渲染文字头像）。
4. **零业务改动**：4 页 diff 应仅 class/结构/样式，接口调用、状态、插槽逻辑未变。
5. **并发冲突后一致性**：确认无「半迁移」状态（例如根已 `.page` 但筛选条仍是旧 `.filters`）。
6. **构建独立复跑**：`npm run build` 须 EXIT=0。

**取证要求**：逐项 `Read`/`Grep` 实证，不得采信 `VERIFICATION.md §17` 摘要；通过后由独立子代理在**文末追加本轮结论**并自行写入 `<!-- reviewed-by: independent-subagent -->`。

> **✅ 已于 2026-09-16 由 `REVIEW_UI_R2.md` 承接并闭环（本节 BLOCKED 记录至此失效，仅作历史留痕）。**
> 上述 6 项必查全部落入 `REVIEW_UI_R2.md` 的复核清单并已实证（令牌无重复块 / 旧类名 0 残留 / 图标名逐个 glob 核对 / 零业务改动 / 构建独立复跑 EXIT=0）。该文件经 `§5` 首轮 PASS_WITH_NOTES 后，又对 post-fix 增量出具 `§6` **PASS（rev2: post-fix）**，末尾标记 `<!-- reviewed-by: independent-subagent (rev2: post-fix) -->` 覆盖 `web/src` 全部未提交改动（9 改 + 4 新 + 3 处修复）。**本节的 BLOCKED 状态不再适用于当前 web/src 改动。**

---

## 第 18 轮补审 · UI R3+R4（独立审议结论 · 2026-09-16）

**审议人**：independent-subagent（独立，主代理未代签）
**范围**：UI R3（9 设置页 + Market.vue 令牌化重做 + style.css 骨架 L416-450）+ R4（ChatEntry.vue 移除聊天框齿轮/插件 popover）
**方法**：全部结论来自亲手 Read / Grep / 独立 `npm run build` 实证，未采信 `VERIFICATION.md §17/§17.6` 摘要；构建日志 `build_review_r4.log`。

### 一句话结论

**PASS_WITH_NOTES** —— 无 BLOCK 级问题。A–D 四项全过；2 处低危注记（均非阻塞）。

### 发现表

| 严重度 | 项 | 证据 | 建议 |
|---|---|---|---|
| 低 | style.css 新骨架块含 3 处硬编码 hex（`.note-ok`/`.note-err`） | `style.css:440` `#a7f3d0`；`style.css:445` `#fecaca` / `#b91c1c` | 改引用令牌（`--brand-soft`/`--danger` 族，或补 `--ok-border`/`--danger-border`） |
| 低 | ChatEntry.vue 残留插件拉取逻辑（成死代码，无害） | `ChatEntry.vue:324-366`（`plugins`/`selectablePlugins`/`selectedPlugins`/`fetchPlugins`）+ `:451` send 仍读 `selectedPlugins`，但勾选 UI 已删 → 恒空 → `undefined` → 后端默认全源；非悬空引用、vue-tsc 通过 | 后续可清理 `fetchPlugins()` 调用，当前不影响行为与构建 |
| 信息 | ChatEntry.vue 残留大量硬编码 hex | `ChatEntry.vue:530 #fff`、`:548 #06b6d4` 等（旧聊天页样式） | 属 R3 范围外，按指示仅记录不阻塞 |

### 逐条核对（A–D）

**A. 零硬编码 hex（10 个 R3 目标 vue 文件）✅**
- 独立 grep 严格模式 `#[0-9a-fA-F]{3,8}[^0-9a-zA-Z#]`（排除 `#default` 等 slot 伪命中），10 文件**全部 0 命中**：`Market.vue`、`settings/{Models,Agents,Gates,Templates,TemplateEdit,CustomProviders,McpServers,AssetCenter,Experts}.vue`。
- 一致性：9 个 settings 文件均 `import PageHead` + 使用 `<PageHead icon title sub #actions>` 与 `<StatStrip :stats>`（grep 佐证 PageHead 9/9、StatStrip 9/9）；`.blk` / `.blk-head` 在 9 文件全量出现。`Market.vue` 按设计仅配色、无 PageHead，正确（不视为缺陷）。
- 偏差（低）：`style.css`「设置页通用骨架」块（L416-450）在 `.note-ok`/`.note-err`（L439-447）写入 3 处硬编码 hex，与本轮「颜色全引用令牌」原则相悖；该文件非 10 目标 vue 之一，故不触发 A 硬门禁，但属设计系统自身漏 token 化（见发现表低项）。

**B. R4 完整性 ✅**
- `ChatEntry.vue` 模板中「数据源插件」齿轮按钮 + popover 已彻底移除：composer（L177-203）仅剩 textarea + 发送钮。
- grep `Setting|pluginIcon|statusLabel|settingsPop|pop-plugin|pp-|composer-popover|badge` → 唯一命中 `L401 router.push('/settings/custom-providers')`（无关路由），无 R4 相关 dangling 引用。
- 智能体选择 popover 完好：`L84-106`（`.pop-title`/`.pop-agents`/`.pop-agent-icon`/`agentIcon(a.toLowerCase())`）。
- 发送流未动：`onSend` `L446-453` 仍 `plugins: selectedPlugins.value.length ? selectedPlugins.value : undefined`；勾选 UI 删除后 `selectedPlugins` 恒空 → `undefined` → 后端默认全源，**行为保真**（与 `VERIFICATION.md §17.6:1025-1027` 一致）。
- `VERIFICATION.md §17.6` 所记死代码清理（settingsPop/statusLabel/pluginIcon/Setting 导入 + 相关 CSS）与代码一致；vue-tsc 通过（见 D）。

**C. 行为保真（抽查 <script> 纯逻辑未改）✅**
- Experts：`listExperts/toggleExpert/convertExpert/listProposals/acceptProposal/rejectProposal` 全在（`L230-333`），`loadAll`/`onMounted` 完好。
- Models：`api.get/put /admin/models` + `loadAudit` + `isViolation` + `onMounted(load)`（`L194-252`）。
- Agents/Gates：`onBeforeRouteLeave`(`L304`/`L311`) + `onBeforeUnload`(`L315`/`L322`)+清理 + `DOMPurify(marked.parse→sanitize)`(`L188`/`L201`，XSS 红线保留) + CRUD + audit；Gates 同构。
- Templates：`fetch GET/DELETE /api/v1/admin/templates` + `onDelete` 确认 + `onMounted(load)`（`L66-104`）。
- TemplateEdit：`fetch POST/PUT` + audit（`L103-165`）。
- CustomProviders/McpServers：`customProviderService`/`mcpServerService` CRUD + 对话框校验（`L135-213`）。
- AssetCenter：`api` reflections/lessons accept/reject + 刷新（`L117-171`）。
- 上述 <script> 除 import 调整外无逻辑编辑，符合「纯视觉重做」。

**D. 独立构建 ✅**
- PowerShell 运行 `& npm.cmd run build *> build_review_r4.log` → `BUILD_EXIT=0`，1780 模块；CSS `index-Ba0x-hU0.css` **402.10 kB**（较 R2 的 `index-Dh0jvU4v.css` 405 kB 下降 ~3 kB，与 R4 删除死 CSS 一致，互相印证）。

### 总评

UI R3（9 设置页 + Market 令牌化、PageHead+StatStrip+.blk 骨架统一）与 R4（ChatEntry 齿轮/popover 移除）独立复核通过：10 目标 vue 文件零硬编码 hex（A 过），R4 移除干净无悬空引用（B 过），9 设置页 <script> 行为全部保真（C 过），独立构建 EXIT=0（D 过）。仅 2 处低危注记：①`style.css` 骨架块 `.note-ok`/`.note-err` 仍硬编码 3 色（建议补令牌，非 vue 门禁范围）；②`ChatEntry.vue` 残留插件拉取逻辑已成无害死代码（行为保真、vue-tsc 通过）。无阻塞项。

**裁定：PASS_WITH_NOTES**

<!-- reviewed-by: independent-subagent -->
