# REVIEW_TD-009 · rework/max_rounds off-by-one 还款 独立审议（设计轮）

> **立场声明**：本审议由独立子代理执行，与主代理**无串通**。结论基于独立 `Read` 真实代码 + 独立 `pytest` 复现取证，不采信设计/自审文档自述。
> 取证的真实文件：`orchestrator.py`、`tests/test_orchestrator.py`、`tests/test_regression_edge.py`、`tests/integration_m76.py`、`tests/e2e_real_run.py`，并独立运行 pytest 复现 3 failed。

---

## ① 根因是否成立（独立取证）

**设计主张**：`routing_state["round"]=0`（`:1129`）初始化，START 直达首个 Agent（`:1082`）致 router `go is None→round=1`（`:941`）为死代码，round 仅在 rework `:961` 递增 → 首轮未计为 1。

**实证**：
- `orchestrator.py:1082` `g.add_edge(START, chain[0].lower())` —— START 直连首 Agent，**确认不经过 router**。
- `orchestrator.py:940-941` `if go is None: rs["round"]=1` —— 查 `_make_route_after_agent`（`:1018`）与 `_make_route_after_gate`（`:1028`）：router 仅在 agent 失败（go=rework 信号）或 gate rework 时进入，此时 `go is not None`。START 不指向 router，**故 `go is None` 分支常态确不可达（死代码）**。
- `orchestrator.py:961` `rs["round"] += 1` 是唯一其他写入点，仅在 `d=="rework"`（`:954`）递增。
- 首轮 Agent→Gate 路径（advance）**从不写 round**，故 round 默认停留 init 值。
- 结论：**根因成立**。round 仅 rework 递增、首轮恒为 init 值是代码事实。

## ② 修复正确性（独立 pytest 复现 + 手动推演）

**复现输出（独立运行，与设计 §1 一致）**：
```
FAILED test_orchestrator.py::test_rework_loop_and_convergence  AssertionError (round 期望2 实得1)
FAILED test_orchestrator.py::test_json_recover_by_retry        AssertionError (round 期望1 实得0)
FAILED test_regression_edge.py::test_max_rounds_exhausted_escalates_with_bounds
   AssertionError: GateA 应恰好被评审 2 次 → gate_review_history 实得 3 条：
   [round:0, round:1, round:2]  （第三条 round:2 即溢出轮，证明确多审一轮）
3 failed, 38 passed（41 总量）
```

**手动推演（init: 0→1，max_rounds=2）**：
| 场景 | 改前 gate_review_history | 改前 round | 改后 history | 改后 round | 测试断言 |
|---|---|---|---|---|---|
| pass（无 rework） | 3 条(GateA/B/C) | 0 | 3 条 | 1 | 仅断言 len==3，过 |
| 1 rework 后过(test:48) | rework(0),advance | 1 | rework(1),advance(2) | 2 | round==2 ✓ |
| 持久 rework(test:175/176) | 3 条(round 0,1,2) | 2 | 2 条(round 1,2) | 2 | round==max_rounds & len==2 ✓ |

推演与复现失败点逐一对齐。**修复使 3 测试转绿且边界语义正确**：max_rounds=2 ⇒ 最多 2 轮 GateA 评审（现为 3），确为真多跑一轮。

## ③ 改动最小 / 无副作用

- **单行**：`:1129` `"round": 0`→`"round": 1`，未碰 gate 逻辑（`:917/:955`）、router（`:935`）、WS 契约（`:911/:571/:792` 仅读取，无分支依赖 0）。
- **RoundUpdateEvent**：`integration_m76.py:113-119` 为 `wsmod.RoundUpdateEvent(...round=2...)` **手工构造 fixture**，非比对 orchestrator 输出；`e2e_real_run.py` 仅在 `SCHEMA` 字段名(`:29-35`)与 `print`(`:127`) 引用 `round`，**无断言比对**。均不受 init 影响。
- **其余 round 断言**：`Grep tests/` 仅 `test_orchestrator.py:48/100`、`test_regression_edge.py:85/175` 涉 round；其中 `85` 为 `is not None`（0 或 1 均非 None，过），其余即 3 目标测试。无 `round==0` 硬依赖。

## ④ 诚实边界（真 bug vs 对齐）

捕获的 `gate_review_history` 第三条 `{round:2}`（test_regression_edge:176 实测）证明：max_rounds=2 下 GateA 被审 **3 次**。修复后严格 2 次。**确属真实边界溢出（多审一轮）+ 计数偏差，非测试与实现不对齐**。设计 §0 诚实边界成立。

## ⑤ 禁过度工程

设计 §5 明确「不重写 rework 环 / 不重构 router / 不引入配置 / 不动 WS」；router 死代码分支（`:941`）明确保留为幂等防御，**未借机改动**。符合最小改动纪律。

---

## 最终裁定

**PASS_WITH_NOTES**

- **MAJOR = NONE**（无破坏既有正确行为 / 无新增回归风险；单行改动 + 全量 38 passed 不受影响已实证，3 失败由修复预期消除）。
- 设计轮即 PASS；实施轮仍需独立子代理交叉运行 `pytest tests/ -q` 填 §18，确认 41 passed / 0 failed 与 `py_compile OK`。

## NOTES（非阻塞改进项）

1. **router `:941` 死代码注释**：可补一行「仅当变体 START→router 时触发，常态由 INIT=1 覆盖」，避免后续维护者误以为 round 由 router 首入置 1。
2. **`test_pass_reaches_done` 无 round 断言**：建议增补 `round==1`，固化 1-based 不变量，防回归。
3. **engine_events round**（`:571/:792`）：修复后 agent 失败事件 round 由 0→1，属更正确记录；当前 `test_regression_edge.py:85` 仅判非 None，建议加 `>=1` 强化。

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 设计独立审议 · 独立子代理，主代理严禁自签 -->

---

## 实施独立审议（第 2 轮）

> **立场声明**：本审议由独立子代理执行，与编码主代理**无串通**。结论基于独立 `Read` 真实代码 + 独立 `py_compile`/`pytest` 交叉运行取证，不采信 VERIFICATION §18 自述。
> 取证文件：`orchestrator.py`、`tests/test_orchestrator.py`、`tests/test_regression_edge.py`；独立运行 `py_compile` 与 `pytest tests/ -q` 及 3 个目标测试定向复跑。

### ① 修复真实性（独立取证）

**实证（git diff orchestrator.py，独立核对）**：
- `orchestrator.py:1128-1132`：INIT `"round": 0` → `"round": 1`，并附 TD-009 注释（START 直达首 Agent、round 须 1-based）。`git diff` 显示删除 `- "round": 0,...`，新增 `+ "round": 1,...`，**确认单行核心改动落点为真**。
- 独立 `py_compile orchestrator.py` → 输出 `COMPILE_OK`（退出码 0，stderr 空）。
- 独立定向复跑 3 目标测试：
  ```
  tests/test_orchestrator.py::test_rework_loop_and_convergence PASSED
  tests/test_orchestrator.py::test_json_recover_by_retry PASSED
  tests/test_regression_edge.py::test_max_rounds_exhausted_escalates_with_bounds PASSED
  3 passed
  ```
- 独立全量：`pytest tests/ -q` → `41 passed, 0 failed`（退出码 0；stderr 仅 40 条 `datetime.utcnow` DeprecationWarning，见 ④）。
- **结论**：`round:1` 真改、3 目标测试真转绿、全量真 **41 passed**。E1/E2/E3/E4/E5 全部独立实证通过。

### ② 边界正确性（手动推演 + 实测断言）

max_rounds=2 + 持续 rework 路径推演（init:1，rework 仅 `:961` `rs["round"] += 1`）：
- 首轮：GateA 评审（round=1）→ rework → round 2。
- 次轮：GateA 评审（round=2）→ rework → 判 `rs["round"] + 1 > max_rounds` → `3 > 2` 触发 escalate。
- ⇒ GateA 恰被评审 **2 次**，升级，`gate_review_history` 恰 **2 条**（round 1、2）。
- 实测 `test_max_rounds_exhausted_escalates_with_bounds` 断言 `round == max_rounds == 2` 且 `gate_review_history` 恰 2 条 → PASSED。**与设计 §3 边界表逐行对齐，边界严格收口。**

### ③ 改动最小 / 无副作用

- **核心单行**：`:1129(现1128-1132)` 仅 `"round": 0→1` 值变，附注释。
- **router 兜底**：`:940-945` 仅新增注释（"防御性兜底/常态不经此分支/与 INIT 幂等"），`git diff` 证实 `rs["round"]=1` 行为**未变**。
- **rework/gate/router 逻辑**：`git diff` 中 orchestrator.py 仅 7 行变更（2 处注释 + 1 行值），无 gate(`:955/:917`)、router(`:948+`)、WS 契约(`:571/:792/:911` 仅读取)改动。
- **加固断言**：`test_orchestrator.py:38` `assert rs["round"] == 1`（pass 场景）PASS；`test_regression_edge.py:86` `assert degraded[0]["round"] >= 1` PASS——均含于 41 passed 内。
- **结论**：确为"单行核心 + 2 处注释/断言加固"，无副作用。

### ④ 诚实边界（真 bug 非对齐）

- 设计轮已实测捕获修复前 `gate_review_history` 第三条 `{round:2}`（max_rounds=2 下 GateA 被审 3 次）——确属真实边界溢出，非测试与实现对不齐。
- 修复后该溢出轮消除（§② 实测 2 条）。属**真实 bug 还款**，非测试对齐。
- `datetime.utcnow` DeprecationWarning 来自 `orchestrator.py:36`，预存告警，与 TD-009 改动（`:1129/:940`）无关；pytest 退出码 0 即通过，**不计入失败**。

### ⑤ 禁过度工程

- orchestrator.py `git diff` 不含 rework 环(`:961` 递增未改)、router 结构、配置项、WS 事件层。
- 设计 §5「不做项」（不重写 rework 环/不重构 router/不引配置/不动 WS）**遵守**；router `go is None` 死分支保留为幂等防御，未借机改动。

### 最终裁定

**PASS_WITH_NOTES**

- **MAJOR = NONE**：无破坏既有正确行为、无新增回归（原 38 passed 全保留，3 失败由修复预期消除）；独立复跑 41 passed / 0 failed 实证；边界语义正确性实测确认。
- 三道闸闭环：设计轮 PASS_WITH_NOTES + 本实施轮 PASS_WITH_NOTES ⇒ TD-009 达到交付条件。

### NOTES（非阻塞改进项）

1. （同设计轮 NOTE 1）router `:941` 死分支注释已补；可再标注"仅变体 START→router 时触发"，防误读。
2. （同设计轮 NOTE 2/3）`test_pass_reaches_done` 已加 `round==1` 加固、`test_regression_edge.py:85` 已改 `>=1`——NOTES 已落地，无需补做。

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 实施独立审议第 2 轮（独立子代理，主代理严禁自签） -->
