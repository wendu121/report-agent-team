# VERIFICATION_TD-009 · 实施自审

## §17 设计自审（编码前）

### §17.1 一致性
- 修复方向（INIT round 0→1）与 router `:941` 注释"首次进入 → round=1"**完全一致**，证实 1-based 是既定契约，INIT=0 属笔误，非设计分歧。
- 与 M9-1~M9-5 同"最小改动、真修复"范式一致，未引入新抽象/配置。

### §17.2 代码实证（非印象，已 Read 真实代码）
- `orchestrator.py:1129` `"round": 0` —— 待改点（唯一）。
- `orchestrator.py:1082` `g.add_edge(START, chain[0].lower())` —— 证明 START 直达 Agent，router `go is None` 分支（`:941`）常态下不可达。
- `orchestrator.py:955` `if rs["round"] + 1 > rs["max_rounds"]` —— 升级判断依赖 round，1-based 后边界收口正确。
- `orchestrator.py:961` `rs["round"] += 1` —— 唯一其他 round 写入点，rework 时递增，与 INIT=1 衔接自然。

### §17.3 诚实边界（R1-R3）
- **R1（真 bug 非对齐）**：已实测 max_rounds=2 允许 3 轮尝试 + 多审一轮 GateA（见 §1 capture 第三条 round:2），确属真实边界溢出，修复后行为更正确。
- **R2（改动最小）**：单行改动，不改 gate 逻辑、不新增配置、不动 WS 契约。
- **R3（无副作用断言）**：全量 41 测试中除 3 个目标测试外，无 `round==0` 断言；WS RoundUpdateEvent 由 server 层派生且 integration fixture 为手动构造，不受影响。

### §17.4 控制器效应
- 不适用（非 UI/配置控制器变更，属引擎内部计数修复）。

### §17.5 复合写 / 原子性
- 不适用（单文件单行编辑，无 yaml/配置复合写）。

### §17.6 向后兼容
- `test_pass_reaches_done`（gate_review_history 长度 3）等 38 个既过测试不受影响（§3 影响分析已证）。
- UserTask / engine_events schema 不变。

### §17.7 风险
- 低。唯一风险点：若未来某变体将 START 指向 router，则 `:941` 兜底会再设 round=1（幂等，与 INIT=1 不冲突）。已注明保留该防御分支。

### §17.8 结论
设计自审 **PASS**，达到编码条件。建议实施轮独立审议确认。

---

## §18 实施自审（编码后填）

### §18.1 实现落点对照
- `orchestrator.py:1128-1130`：`"round": 0` → `"round": 1`（单行，附 TD-009 注释说明 START 直达首 Agent 不经 router 兜底）。
- `orchestrator.py:940-945`：router `go is None` 分支补防御性注释（与 INIT=1 幂等，非行为变更）。
- `tests/test_orchestrator.py:37`：新增 `assert rs["round"] == 1`（pass 场景回归加固）。
- `tests/test_regression_edge.py:85`：`degraded[0]["round"] is not None` → `>= 1`（事件轮次回归加固）。

### §18.2 E1-E5 实证（pytest 真跑，2026-09-09）
- **E1** ✅ `test_rework_loop_and_convergence` 转绿：`round==2`（1 次 rework 后通过）。
- **E2** ✅ `test_json_recover_by_retry` 转绿：`round==1`（重试恢复不耗 rework 轮）。
- **E3** ✅ `test_max_rounds_exhausted_escalates_with_bounds` 转绿：`round==max_rounds==2` 且 `gate_review_history` 恰 2 条（不再多审第 3 轮）。
- **E4** ✅ 全量 `pytest tests/ -q` → **41 passed, 0 failed**（3 失败债消除，0 新增失败）。
- **E5** ✅ `python -m py_compile orchestrator.py` → COMPILE_OK。
- 附：唯一 stderr 为 `datetime.utcnow` DeprecationWarning（`:36`，预存，与本改动无关，非失败）。

### §18.3 关键实测发现
- 修复后 `max_rounds=2` 真实 = 最多 2 轮 GateA 评审后严格升级（边界收口正确），与设计 §3 推演一致。
- `gate_review_history` 条目 `round` 字段现为 1-based，审计可读性提升（原首轮记 0 易误读）。

### §18.4 诚实边界 R1-R3
- **R1（真 bug 非对齐）**：实测 `gate_review_history` 第三条 `{round:2}` 溢出已消除；修复后边界语义正确，确属真实 bug 还款。
- **R2（改动最小）**：核心单行 + 2 处注释/断言加固，未碰 gate/router 逻辑、未新增配置、未动 WS 契约。
- **R3（无副作用）**：41 测试全绿，含 `test_pass_reaches_done`（新增 round==1 断言通过）、engine_events 加固断言通过。

### §18.5 回归
- 见 §18.2 E4：**41 passed / 0 failed**（原 38 passed + 3 failed → 全绿）。TD-009 预存债彻底消除。

### §18.6 结论
实施自审 **PASS**。3 个预存失败测试全部转绿，全量回归 41/41 绿，改动面最小且可审计。达到独立审议（实施轮）条件。
