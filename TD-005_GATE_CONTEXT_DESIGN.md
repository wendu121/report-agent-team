# TD-005 设计文档 · Gate 审核上下文补充 `retrieval_records`

> 按 boss「先设计后编码」纪律（选 B 方案），本文件为**待评审稿**。评审通过后方可编码。
> 关联：TECH_DEBT.md TD-005、VERIFICATION.md §6.7、e2e 第四轮报告 `tests/e2e_real_run_report.json`。

## 0. 元信息

| 项 | 值 |
|---|---|
| 状态 | **已评审通过 → 已编码 → e2e 验证中**（boss 2026-09-06 批复「按推荐实现」） |
| 起草时间 | 2026-09-06 |
| 起草人 | 主代理 |
| 评审人 | boss |
| 触发证据 | e2e 第四轮（任务 `d152e81a…`，3m20s）`status=escalated`、`last_gate=GateB` |
| 改动面 | **单函数** `orchestrator.py:364-382 build_gate_user`（+ 新增 1 个纯函数） |
| 门禁影响 | e2e 当前 RED；本设计为解除阻断的**前置步骤** |

---

## 1. 问题陈述

### 1.1 现象

e2e 第四轮 GateB 连续两轮判 `rework`，`max_rounds=2` 打满 → `escalated`，真实引擎**无法抵达 `task_done`**。

### 1.2 铁证（两条互相独立）

**证据 A — 被判"不存在"的记录实际全部存在**
`tests/e2e_real_run_report.json:105-148` 的 `final_state.retrieval_records` 含 **6 条完整记录**：`rec-6 / rec-7 / rec-11 / rec-12 / rec-16 / rec-17`（id、url、title、snippet、credibility 齐全）。
GateB 却判：*"source_ids 中的 rec-6、rec-7、rec-11、rec-12、rec-16、rec-17 在 retrieval_records 中不存在"*。

**证据 B — LLM 自陈上下文缺失**
第二轮 rework 理由原文：
> *"结论 source_ids 中的 id 需要在 retrieval_records 中存在对应的记录，但**当前没有提供 retrieval_records 进行校验**"*

### 1.3 性质判定

**引擎 prompt 构建层缺陷**，与以下因素**均无关**：

- ❌ 非模型问题 —— 换任何模型都同样读不到未提交的上下文；
- ❌ 非配额/429 —— 第四轮 429 已消失（TD-004 方案 A 生效）；
- ❌ 非 MOCK 检索 —— 记录对象真实存在，仅内容为占位；
- ❌ 非 TD-002（中间事件不推）—— 正交。

---

## 2. 根因分析（精确）

### 2.1 缺陷点

`orchestrator.py:364-382` `build_gate_user()` 组装给 Gate 的审核材料仅三项：

| # | 内容 | 代码 |
|---|---|---|
| ① | `Gate: {gate_name}` | 368 |
| ② | 待审角色产出（结构化段 `prod_key`） | 369-370 |
| ③ | `user_task` | 380 |

**仅 `GateC` 额外附带 `report_markdown`**（375-379，M5 修复时补）。**所有 Gate 均不附带 `retrieval_records`**。

### 2.2 关键不对称（设计层面的根本原因）

| 侧 | 是否持有 `retrieval_records` | 证据 |
|---|---|---|
| **Agent 生产侧** | ✅ 有 | `orchestrator.py:595-598` 向 Analyst 注入 *"【上游调研素材 retrieval_records｜你唯一可引用的来源集合】"* |
| **Gate 审核侧** | ❌ 无 | `build_gate_user` 未注入 |

→ 生产者知道可引用集合，审核者不知道，却要审核引用是否合法 —— **必然无法校验**。

### 2.3 代码硬校验早已示范"正确做法"

`orchestrator.py:415-431`（GateB 硬校验）**本身**就用 `state["retrieval_records"]` 校验 `source_ids`，且其 rework 消息模板已自带可用集合：

```python
f"结论 {c.get('id')} 引用 {sid} 不存在于检索记录；"
f"当前可用记录 id 集合为 {sorted(rec_ids)}，请只从中选取"
```

即：**代码层知道该把 id 集合给审核方，LLM 审核层却没给**。本次修复正是把这一信息补齐到 LLM 侧。

### 2.4 为何代码校验通过、LLM 仍 rework

`rec_ids` 在代码层（416-417）取自真实 state，含 rec-6/7/11/… → 硬校验**通过** → 流程继续到 LLM 审核 → LLM 无上下文 → 臆断"不存在" → rework。二者结论冲突，根源即上下文缺失。

---

## 3. 目标与非目标

### 3.1 目标

- **G1**：Gate 审核时可基于真实记录集合校验引用链（`source_ids` ↔ `rec_ids`）；
- **G2**：不改变现有闸决策语义（代码硬校验优先级不变，`gates/review.md` 规则不变）；
- **G3**：token 成本可控，不因记录数量膨胀而失控。

### 3.2 非目标（明确排除）

- **N1** 不做中间事件流式化（属 TD-002 新功能，另立设计）；
- **N2** 不改 `server/api.py` 模型契约（TD-003 已定其为权威，本次不触碰）；
- **N3** 不改前端任何代码/配置（M7 前端已冻结）；
- **N4** 不调整 `config/model_mapping.yaml`（TD-004 已解决）；
- **N5** 不改 `max_rounds` / rework 轮次 / Router 逻辑。

---

## 4. 方案设计

### 4.1 改动点（唯一）

仅 `orchestrator.py` 新增 1 个纯函数 + `build_gate_user` 内 1 处 `parts.append(...)`。**不触碰** prompt 文件、闸规则、模型映射、前端、server 模型。

### 4.2 决策项 D1 — 附带范围

| 方案 | 说明 | 评估 |
|---|---|---|
| S1 | 全部 Gate（A/B/C）统一附带 | 通用性好，但 GateA 产出即 `retrieval_records` 本身（`PROD_KEY["Researcher"]="retrieval_records"`），**重复注入纯属浪费**；且需回归 GateA/C |
| **S2（推荐）** | **仅 GateB** | 问题仅在 GateB 暴露；改动最小、回归面最小；GateC 已有 `report_markdown` + 代码硬校验（449-458）兜底；GateA 无需重复 |
| S3 | 自动判定（产出含 `source_ids`/`conclusion_ids` 时附带） | 最优雅，但改动面与回归面扩大，收益不抵风险 |

**推荐 S2**。若 boss 选 S1/S3，需额外回归 GateA/GateC 的 rework 行为。

### 4.3 决策项 D2 — 字段集

| 方案 | 字段 | 评估 |
|---|---|---|
| F1 | 全字段（id/url/title/snippet/credibility） | `snippet` 最长且对存在性校验无贡献，浪费 ~60% token |
| **F2（推荐）** | **id + title + credibility** | id→存在性校验；title→语义相关性；credibility→可信度评估（`gates/review.md` 要求评估来源可信度） |
| F3 | 仅 id 列表 | 最省，但 Gate 无法评估来源可信度 |

**推荐 F2**。

### 4.4 决策项 D3 — 条数上限与截断

真实检索可能数十至数百条。策略：

- **id 集合：始终全量附**（每条 ~5 token，成本极低，且是存在性校验的唯一依据，不可截断）；
- **明细（title/credibility）：截断至 200 条**，超出时标注"共 X 条，明细仅列前 200"。

**token 估算**

| 记录数 | id 全量 | 明细 | 合计（约） |
|---|---|---|---|
| 6（当前 MOCK） | ~30 | ~180 | **~210 token**（可忽略） |
| 100 | ~500 | ~3,000 | ~3.5k（可控） |
| 500（明细截断 200） | ~2,500 | ~6,000 | ~8.5k（可接受） |

### 4.5 决策项 D4 — GateC 是否同步附带

- **否（推荐）**：GateC 审 Writer，`doc_export` 已确定性重建引用章节，且 `build_gate_user` 已附 `report_markdown`、代码硬校验（449-458）覆盖 rec-id 完整性 → 无必要，保持最小改动。
- 是：可交叉验证引用章节与记录一致性，但引入 GateC 新 rework 风险。

### 4.6 伪代码（待评审确认后实现）

```python
GATE_CONTEXT_MAX_REC_DETAIL = 200   # 明细条数上限；id 集合始终全量

def _fmt_retrieval_records(state: dict) -> Optional[str]:
    """格式化检索记录为 Gate 审核上下文（仅存在性/可信度所需字段，控 token）。"""
    recs = state.get("retrieval_records", []) or []
    if not recs:
        return None
    ids = [r.get("id") for r in recs if r.get("id")]
    detail = [
        {"id": r.get("id"), "title": r.get("title"), "credibility": r.get("credibility")}
        for r in recs[:GATE_CONTEXT_MAX_REC_DETAIL]
    ]
    return (
        f"检索记录 retrieval_records（共 {len(recs)} 条｜被审产出的 source_ids 只能取自下列 id 集合）:\n"
        f"全部 id 集合: {ids}\n"
        f"记录明细（最多 {GATE_CONTEXT_MAX_REC_DETAIL} 条）:\n"
        f"{json.dumps(detail, ensure_ascii=False)}"
    )


def build_gate_user(gate_name: str, state: ReportState) -> str:
    ...
    # TD-005：GateB 审 Analyst 引用链，需提供检索记录上下文供存在性/可信度校验
    if gate_name == "GateB":
        ctx = _fmt_retrieval_records(state)
        if ctx:
            parts.append(ctx)
    ...
```

---

## 5. 副作用分析

| 对象 | 预期影响（S2 + F2） | 风险 |
|---|---|---|
| **GateA** | 零（不附带） | 无 |
| **GateB** | 从"必然 rework"转为"可正确校验引用链" | 中：LLM 可能提出**新的** rework 理由（如 credibility=medium 不足）→ 属正确审核行为，需 e2e 观察再定 |
| **GateC** | 零（不附带） | 无 |
| **代码硬校验** | 不变，优先级不变（415-431 / 449-458） | 无 |
| **gate_review_history 结构** | 不变（`decision/reason/eval_score/problem_points/gate/round/timestamp`） | 无，不影响 TD-003 契约 |
| **前端 / WS 契约** | 零 | 无 |
| **token / 延迟** | 见 §4.4，最坏 ~8.5k | 低 |

---

## 6. 风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| GateB 拿到记录后提出新 rework 理由，仍不 advance | 中 | e2e 仍 RED | e2e 观察 rework 理由；若属"过度严苛"，再议是否调 `gates/review.md`（需二次评审，本设计不含） |
| 真实检索数百条导致 token 膨胀 | 低 | 成本/延迟 | id 全量 + 明细截断 200 |
| `retrieval_records` 为空时 prompt 异常 | 低 | 无 | `_fmt` 返回 `None` 时不 append |
| 改动引入回归 | 低 | 低 | 新增纯函数 + 1 处 append；`py_compile` + e2e 双覆盖 |

**回滚**：仓库尚未 commit，直接还原 `build_gate_user` 与新增函数即可，无数据/迁移影响。

---

## 7. 验证计划

1. `python -m py_compile orchestrator.py` 通过；
2. **重启 server**（按 boss 上轮指令"修复完成后重启服务再测试"），注入 `NEWAPI_API_KEY` 后健康检查 `/health`；
3. 重跑 `tests/e2e_real_run.py`，断言：
   - 抵达 **`task_done`**（`status=done`、`report_markdown` 非空）；
   - `gate_review_history` 中 GateB 的 rework 理由**不再**出现"不存在 / 未提供 retrieval_records"；
   - WS 8 事件判别联合契约校验全通过；
4. 若仍 `escalated` → **定位新根因，不强行 PASS**（boss 指令 #6）；
5. 通过后 → 回填 `VERIFICATION.md §6.7` → 派**独立子代理**跑 REVIEW.md 迭代 5（严禁主代理自签 `reviewed-by`）→ 三道闸全 PASS → `git add`/`commit` 入库。

---

## 8. 待评审决议项（请 boss 勾选）

| # | 决议项 | 选项 |
|---|---|---|
| **D1** | 附带范围 | □ S1 全 Gate　□ **S2 仅 GateB（推荐）**　□ S3 自动判定 |
| **D2** | 字段集 | □ F1 全字段　□ **F2 id+title+credibility（推荐）**　□ F3 仅 id |
| **D3** | 截断策略 | □ **id 全量 + 明细截断 200（推荐）**　□ 全量不截断　□ 整体截断 |
| **D4** | GateC 同步附带 | □ **否（推荐）**　□ 是 |
| **D5** | 是否允许因此修改 `gates/review.md` | □ **否（推荐，仅改代码）**　□ 是 |

> 若 boss 对 D1–D5 无异议，可直接回复「按推荐实现」，我将按 S2 + F2 + id 全量/明细截断 200 + 不含 GateC + 不改 prompt 实现，并按 §7 验证。

---

## 9. 评审结论与实现记录（2026-09-06 boss 批复「按推荐实现」）

**决议**：D1=**S2**（仅 GateB）、D2=**F2**（id+title+credibility）、D3=**id 全量 + 明细截断 200**、D4=**否**（不含 GateC）、D5=**否**（不改 `gates/review.md`）。

**实现落点（`orchestrator.py`）**：
- `370` `GATE_CONTEXT_MAX_REC_DETAIL = 200`（模块级常量）；
- `373-391` 新增纯函数 `_fmt_retrieval_records(state) -> Optional[str]`（空记录返回 `None`，调用方跳过追加）；
- `413-417` `build_gate_user` 内 `if gate_name == "GateB":` 追加上下文。

**冒烟实证（编码后即时）**：
- `py_compile` **OK**；
- 6 条记录 → 输出含「全部 id 集合」+ 明细，`snippet` 已剔除；
- 空 `retrieval_records` → 返回 `None`（不污染 prompt）；
- 260 条 → 明细截断为 **200** 条，id 集合**全量**保留（符合 D3）。

**e2e 验证**：后台任务 `vslV0X`（server 已重启加载新代码，`/health` = healthy）。结果按 §7 判定；**未 PASS 前不回填 §6.7 结论、不启动独立审议、不 commit**。
