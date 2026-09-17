# DESIGN_TD-009 · 还 rework/max_rounds 计数 off-by-one 技术债

> **性质**：技术债还款（TD-009，M9-1 预存），非新功能。改动面 = orchestrator.py **一行**。
> 三道闸：设计自审 §17 + 实施自审 §18 + 独立审议 REVIEW（设计轮 + 实施轮）。

---

## §0 范围裁定

- **做**：修复 `routing_state["round"]` 的 1-based 计数偏差，使 3 个预存失败回归测试转绿，并修正真实边界语义（max_rounds = 最大尝试轮次）。
- **不做**：不重写 rework 环、不重构 router/gate 节点、不引入新配置项、不碰 WS 事件层（RoundUpdateEvent 由 server 层按 routing_state 派生，本改动不改变字段契约）。
- **诚实边界**：这是**真实 bug**（多跑一轮 + 边界溢出），非测试与实现对不齐；修复后真实行为更正确（max_rounds=2 即最多 2 轮尝试，而非 3 轮）。

## §1 现状实证（2026-09-09 实测，pytest 真跑）

```
FAILED test_orchestrator.py::test_rework_loop_and_convergence   → round 期望 2，实得 1
FAILED test_orchestrator.py::test_json_recover_by_retry         → round 期望 1，实得 0
FAILED test_regression_edge.py::test_max_rounds_exhausted_escalates_with_bounds
        → gate_review_history 期望 2 条（GateA 每轮 1 次），实得 3 条；round 期望 ==max_rounds(2)
3 failed, 38 passed  （41 测试总量）
```

实测 `gate_review_history` 第三条（溢出轮）：`{'decision':'rework',...,'gate':'GateA','round':2}` —— 证明多审了一轮。

## §2 根因

`orchestrator.py`:
- `run_report` 初始化 `routing_state["round"] = 0`（`:1129`）。
- 图 `g.add_edge(START, chain[0].lower())`（`:1082`）→ **START 直达首个 Agent**，正常流程**不经过 router**。
- router 的 `go is None → rs["round"] = 1`（`:941`）在常态下是**死代码**（仅当某变体把 START 指向 router 才触发）。
- 因此 `round` 仅在 rework 分支 `rs["round"] += 1`（`:961`）时递增，**首轮尝试从未被计为 round 1**。

级联效应（同一个 off-by-one）：
- 无 rework → round 恒为 0（应为 1）。
- 1 次 rework → round=1（应为 2）。
- 持续 rework（max_rounds=2）→ 在 round=0/1/2 各审一次 GateA（共 3 次），第 3 次 rework 才触发 `round+1>max_rounds` 升级 → 实际允许 3 轮尝试（应为 2）。

## §3 修复方案

**唯一改动**：`orchestrator.py:1129` 初始化 `"round": 0` → `"round": 1`。

与既有设计意图一致：router `:941` 注释明确"首次进入 → round=1"，证明 1-based 是既定契约；INIT 写 0 是笔误，且被 START 直达路径放大成 bug。

修复后语义（含边界正确性）：
| 场景 | max_rounds | 实际尝试轮 | round 终值 | 状态 |
|---|---|---|---|---|
| pass（无 rework） | 2 | 1 | 1 | done |
| 1 次 rework 后通过 | 2 | 2 | 2 | done |
| 持续 rework | 2 | 2 | 2 | escalated（第 2 次 rework 触发升级，不审第 3 轮）|

→ `max_rounds` 真实等于"最大尝试轮次"，边界严格收口。

### 影响分析（改动安全性）
- **round 字段**仅被读取于：`gate_review_history[].round`（`:911`）、`engine_events[].round`（`:571/:792`）、router 升级判断（`:955`）。改为 1-based 后这些记录值更正确，无逻辑分支依赖 0。
- **WS 事件**：`RoundUpdateEvent` 由 server/engine_runner 层按 `routing_state.round` 派生（见 integration_m76 仅为手动构造 fixture，非比对 orchestrator 输出），字段契约不变。
- **其他 38 passing 测试**：无 `round==0` 断言；`test_pass_reaches_done` 仅断言 `gate_review_history` 长度 3（仍为 3）。不受影响。

## §4 验证计划

- **E1**：`test_rework_loop_and_convergence` 转绿（round==2，含 1 次 rework）。
- **E2**：`test_json_recover_by_retry` 转绿（round==1，重试恢复不耗 rework 轮）。
- **E3**：`test_max_rounds_exhausted_escalates_with_bounds` 转绿（round==max_rounds==2，gate_review_history 恰 2 条）。
- **E4**：全量 `pytest tests/ -q` → **41 passed, 0 failed**（3 失败债消除，0 新增失败）。
- **E5（诚实留痕）**：`py_compile orchestrator.py` → COMPILE_OK。

## §5 风险与不做项
- 不扩写 router 死代码分支（保持 `go is None → round=1` 作为防御性兜底，与 INIT=1 不冲突、幂等）。
- 不借机"优化" rework 环其他逻辑（禁过度工程）。
- 不 push（无 boss 显式「推送」指令）。

## §6 提交纪律
- `git add report-agent-team && git commit`（不 push）。
- 提交信息标注 TD-009 + 三道闸闭环。
- 同步 MEMORY.md：TD-009 已还，回归套件 41/41 绿。
