# gates/review.md · 三道闸审核规则与 Prompt 模板

> **状态**：M2 交付物。DESIGN §6 的展开；三闸 = edict-gate 三道门禁的运行时实例（校验 → 自审 → 独立审议）。
> **加载方**：`orchestrator.py` 在 GateA / GateB / GateC 节点读取对应段组装 Gate prompt。
> **日期**：2026-09-04
> **Constitution 对齐**：本文件遵循 `E:\第二电脑\CONSTITUTION.md` v1.0.0 原则 II（提交时三道闸）和原则 IV（持续对齐）。每条 Gate 判定规则标注对应原则编号。

---

## 0. 全局 Gate 铁律（M2）

1. **输出强制完整 `gate_output` JSON**：`{decision, reason, eval_score, problem_points}`，**禁止仅自然语言**——否则 Router 无法读 `decision` 与 `problem_points`。
2. **信息隔离（抗自审包庇）**：Gate 只接收**产出 Agent 的最终结构化产出 + 校验标准**；**不接收**其 CoT / scratchpad / 私有推理草稿，避免被上游论证锚定。
3. **eval 降级**：`eval_score` 失败时置 `null`，主流程不阻断，仅执行业务规则评审。
4. **不判轮次**：Gate 不读 `max_rounds`、不判断轮次超限；仅识别业务致命问题输出 `decision=escalate`；轮次超限一律 Router 处理（DESIGN §2 职责边界澄清）。
5. **异基座模型**：Gate 优先分配与产出 Agent **不同基座**的模型（`config/model_mapping.yaml`），缓解同源自我确认偏差（DESIGN §10 / §11-1）。
6. **业务规则优先于 eval**：rework 由业务规则驱动，`eval_score` 仅辅助、不做硬阈值（阈值待 M5 跑通后校准写入配置）。
7. **代码硬校验通过 ⇒ 强制放行**（M5 新增）：当机器校验确认关键项达标（如 GateC 引用章节完整性），
   返回 `advance` 并**强制生效**，不允许 LLM 主观 `rework` 覆盖。理由：引用章节由 `doc_export`
   确定性重建，属**代码所有项**（非 LLM 职责），代码判定即终审。
8. **闸 LLM 不可用 ⇒ 降级放行 + 审计**（M5 新增）：Gate LLM 最多解析 3 次仍失败时，
   若代码硬校验**已通过** ⇒ `decision=advance`，`reason` 标注"降级放行"，
   并写入 `engine_events.gate_llm_unavailable_degraded_advance`。
   若代码校验判 `rework`/`escalate` ⇒ 照代码结论（不放行）。
   理由：LLM 闸是**建议性**质量判断，代码硬校验才是权威；把瞬断抖动升级成整任务失败是假阴性，误杀。

> **优先级总序**：代码 `escalate` > 代码 `rework` > 代码 `advance` > LLM 判定。
> 即：代码判致命/可修/达标，均以代码为准；代码无结论（`None`）时，才采信 LLM。

---

## 1. 通用 Gate Prompt 骨架（编排器填充 `{gate_name}` / `{agent_output}` / `{check_standard}`）

```
你是 {gate_name} 审核员。只依据以下结构化产出做判定，不要猜测未提供的推理过程。
【待审产出】
{agent_output}
【校验标准】
{check_standard}

请只输出以下 JSON（禁止多余文字）：
{
  "decision": "advance" | "rework" | "escalate",
  "reason": "可读评审理由",
  "eval_score": 0.0-1.0 或 null（eval 失败时）,
  "problem_points": ["具体问题点，无则填['无']"]
}
规则：
- decision=advance：产出满足全部校验标准。
- decision=rework：存在可修正问题；problem_points 必须具体，将透传 Agent 定向修正。
- decision=escalate：仅当出现业务致命问题（如检索全空、编造引用、报告完全不可用）。
- 不判断轮次 / max_rounds；轮次超限由 Router 处理。
- eval_score 失败（429 / 超时）置 null，不影响上述 decision。
```

---

## 2. GateA（调研 → 分析）【Constitution: II, IV】

- **位置**：Researcher 后
- **信息隔离输入**：`retrieval_records` + `user_task`（**不收** Researcher 思考过程）
- **Constitution 对齐**：原则 II（提交时三道闸——GateA 是第一闸校验）+ 原则 IV（持续对齐——检测 scope 覆盖缺口）
- **校验标准**：
  - 覆盖度（**Constitution IV**）：`retrieval_records` 逐条回应 `user_task.scope` 每个子问题；缺子问题 ⇒ gap_task 类型 `coverage`
  - 来源可信度（**Constitution II**）：无 `credibility=low` 且未说明；`url` 真实
  - 偏题：素材围绕 `topic`
- **rework 条件**：子问题未覆盖 / 来源不可信未标注 / 偏离任务 ⇒ `decision=rework`，`problem_points` 点名缺哪个子问题；同时生成 gap_tasks
- **escalate 条件**：业务严重（检索完全无有效素材，`retrieval_records` 空或全 `low` 且无说明）

---

## 3. GateB（分析 → 撰稿）【含引用链机器校验】【Constitution: II, IV】

- **位置**：Analyst 后
- **信息隔离输入**：`analysis_conclusions` + `retrieval_records`（后者仅用于 id 存在性校验，**不收** Analyst CoT）
- **Constitution 对齐**：原则 II（提交时三道闸——GateB 是第二闸自审）+ 原则 IV（持续对齐——检测引用断裂、无据结论）
- **校验标准**：
  - 结论支撑（**Constitution II**）：每条 `analysis_conclusions.source_ids` **非空**
  - **机器可校验（代码层硬校验，不靠 LLM 主观）**（**Constitution II**）：每个 `source_ids` 中的 id 必须存在于 `retrieval_records` 的 id 集合；不存在 ⇒ 直接 `rework`，gap_task 类型 `broken_reference`
  - 逻辑自洽：结论间不矛盾
  - 无外部断言：不得出现 `retrieval_records` 外的来源
- **rework 条件**：出现无据结论 / 逻辑断裂 / `source_ids` 缺失或指向不存在 id；同时生成 gap_tasks
- **escalate 条件**：业务严重（大量编造引用，`source_ids` 普遍造假）

---

## 4. GateC（终稿）

- **位置**：Writer 后
- **信息隔离输入**：`draft_segments` + `analysis_conclusions` + `report_markdown`（**不收** Writer CoT）
  - M5 修订：必须含 `report_markdown`。此前只喂 `draft_segments`（结构化段本无引用章节），
    导致 Gate LLM 看不到 `doc_export` 已确定性重建的引用章节，反复误判"缺引用 / 来源章节"。
- **校验标准**：
  - 整体一致性：`draft_segments` 与各 conclusion 不冲突
  - 格式：含独立"引用 / 来源"章节
  - 引用完整：**机器可校验** `draft_segments.conclusion_ids` 非空且存在于 `analysis_conclusions`
  - **引用章节完整性（M5 代码硬校验）**：`report_markdown` 须含"引用 / 来源"章节，
    且覆盖 `retrieval_records` 全部 id **与其 url**（仅裸 id 不算完整，须由 `doc_export` 重建）
- **rework 条件**：引用缺失 / 与上游结论冲突 / 格式缺引用章节 / 引用章节未覆盖全部记录
- **escalate 条件**：业务严重（报告完全不可用）

---

## 5. 引用链机器校验伪代码（M3 引擎实现；Gate prompt 声明要求）

```python
def gateB_check(retrieval_records, analysis_conclusions):
    rec_ids = {r["id"] for r in retrieval_records}
    for c in analysis_conclusions:
        if not c.get("source_ids"):
            return ("rework", f"结论 {c['id']} 缺 source_ids")
        for sid in c["source_ids"]:
            if sid not in rec_ids:
                return ("rework", f"结论 {c['id']} 引用 {sid} 不存在")
    return ("pass", None)

# GateC 同理：draft_segments.conclusion_ids ⊆ analysis_conclusions.id

def gateC_citation_check(report_markdown, retrieval_records):
    """M5 新增：引用章节完整性（doc_export 确定性重建后方可通过）。"""
    if not re.search(r"^\s*##\s*(引用|参考|来源)", report_markdown, re.M):
        return ("rework", "缺『引用 / 来源』章节")
    missing = [rid for rid, r in [(r["id"], r) for r in retrieval_records]
               if rid not in report_markdown
               or (r.get("url") and r["url"] not in report_markdown)]
    if missing:
        return ("rework", f"引用章节未覆盖全部记录: {missing}")
    return ("advance", "引用章节覆盖全部检索记录，代码硬校验通过")
```

> 代码层校验优先于 LLM 主观判断；即使 LLM 觉得"看起来有引用"，id 不存在即 `rework`。

**判定优先级实现**（`orchestrator.py::make_gate`，M5 定稿）：

```python
if code_dec == "escalate":                        # ① 代码判致命 → escalate（LLM 不得放行）
    decision = "escalate"
elif code_dec == "rework" and llm_dec != "escalate":  # ② 代码判可修 → rework
    decision = "rework"
elif code_dec == "advance":                       # ③ 代码判达标 → 强制放行（LLM 不得误杀）
    decision = "advance"
else:                                             # ④ 代码无结论 → 采信 LLM
    decision = llm_dec
```

**闸 LLM 不可用兜底**（M5）：解析最多重试 3 次；仍失败时按 `code_dec` 分支——
`rework`/`escalate` 照代码结论，`None`（代码已通过）则降级 `advance` 并记
`engine_events.gate_llm_unavailable_degraded_advance`（见 §0 铁律 8）。

---

## 6. eval 降级实现注

Gate prompt 调用 eval 模型打分；捕获 429 / Timeout ⇒ `eval_score=null`，`decision` 仍由业务规则给出。`eval_score=null` 时 `problem_points` 仍须基于业务规则填写，不得空白。

---

## 7. 冒烟测试（M2 铁律 7）

`tests/test_gates.md`：
- GateA：`retrieval_records` 缺 scope 子问题 ⇒ 断言 `decision=rework` 且 `problem_points` 点名缺失子问题
- GateB：`analysis_conclusions` 含 `source_ids=["rec-99"]`（不存在）⇒ 断言 `decision=rework`
- GateC：`draft_segments` 无 `conclusion_ids` ⇒ 断言 `decision=rework`
- 全部 Gate：模拟 eval 超时 ⇒ 断言 `eval_score=null` 但 `gate_output` 四字段齐全、`decision` 正常输出
