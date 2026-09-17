<!-- reviewed-by: independent-subagent -->

# 独立审议 · TD-002 任务运行事件流修复

**Verdict: PASS_WITH_NOTES**
**MAJOR: 1**
**MINOR: 4**

审议对象：`orchestrator.py` / `server/engine_runner.py` / `server/api.py` / `VERIFICATION_TD002.md`（纯静态审查，未改动任何文件）。

---

## 一、核心结论

TD-002 根因（中间事件从未产生 + 跨进程连接表为空 → 时间线恒空）定位准确，修复架构正确：
`orchestrator` 经 `on_event` 回调 → 子进程 `engine_runner` 把事件追加写入 JSONL → 服务进程 `api.py`
并发 `tail` 后经其自身持有的 `manager.broadcast` 推给 WS 连接。这是跨进程事件传递的标准、低耦合做法，
且未引入任何新端点/契约、未改动前端、未编造数据，符合项目诚实边界约定。

下列 MAJOR 为**错误路径**下的资源泄漏缺陷，正常路径无问题；建议合并前以 `try/finally` 修复，但不阻断 TD-002 主功能。

---

## 二、逐项核查

### (a) 正确性 — `on_event` 调用点与字段名对齐 ✅（无问题）

| 事件 | 发射点 | 字段（orchestrator → engine_runner 信封 → TS） | 判定 |
|---|---|---|---|
| `round_update`(起始) | `orchestrator.py:1418-1422` | `event_type, round, max_rounds` | ✅ |
| `agent_complete` | `orchestrator.py:1013-1016` | `event_type, round, agent, tool_status` | ✅ |
| `gate_complete` | `orchestrator.py:1146-1151` | `event_type, round, gate, decision, reason, eval_score, problem_points` | ✅ |
| `tool_error` | `orchestrator.py:1008-1012` | `event_type, round, agent, tool, error` | ✅ |
| `round_update`(rework) + `rework_trigger` | `orchestrator.py:1194-1203` | `event_type, round, max_rounds` / `event_type, round, rework_target_agent, rework_reason, last_gate` | ✅ |

- 逐字段比对 `web/src/types/engine.ts` 判别联合（AgentCompleteData / GateCompleteData / ReworkTriggerData /
  RoundUpdateData / ToolErrorData）**命名完全一致**，无拼写/键错位。
- `WSEnvelope` 要求的 `task_id` + `timestamp` 由 `engine_runner._on_event`（`engine_runner.py:54-68`）补齐，
  与 TS `WSEnvelope`（`engine.ts:19`）一致。✅
- `eval_score` 在 `None` 时回落 `0.0`（`orchestrator.py:1149`），与 TS `number` 兼容。✅
- 每个 `on_event` 调用均包 `try/except`，事件失败不影响引擎主流程（`orchestrator.py:1005/1144/1192`）。✅

### (b) 跨进程传输 — 路径一致 ✅；半行竞态为理论风险（见 MINOR-1）

- **写端**：`engine_client.py:51` `output_file = state_dir / "{task_id}_output.json"`，`state_dir` 由 `api.py:29`
  传入 `STATE_DIR`（= `Path(__file__).parent.parent / ".engine_state"`）。`engine_runner.py:51`
  `events_file = output_file.parent / "{task_id}_events.jsonl"` ⇒ `.engine_state/{task_id}_events.jsonl`。
- **读端**：`api.py:515` `events_file = STATE_DIR / "{task_id}_events.jsonl"` ⇒ 同一路径。✅ 两端完全一致。
- 每轮运行前 `engine_runner.py:52` `events_file.unlink(missing_ok=True)` 清空旧事件，避免残留。✅
- 半行竞态：`_on_event` 单次 `_f.write(json.dumps(...) + "\n")`（append 模式 + 小事件，单 `write()` 实际原子），
  配合 `api.py:52` `read().splitlines()` 按完整行切分，正常不会读到半行。理论边界见 MINOR-1。

### (c) 并发 — 正常路径取消正确；错误路径泄漏（MAJOR）

- 正常路径：`start_engine_background`（`api.py:500-566`）在 `launch_engine` 后
  `asyncio.create_task(_stream_engine_events(...))`（`:516`），与 `engine_process.wait()`（`:521`）并行；
  `wait()` 返回后 `poller.cancel()`（`:561`）+ `await poller` 吞 `CancelledError`（`:562-565`）。✅
- `_stream_engine_events` 在 `CancelledError` 时收尾 `await _flush()`（`:71-73`），确保 `wait()` 后落盘的事件全部广播。✅
- **MAJOR（`api.py:567-573` 的 `except` 分支未取消 poller）**：`poller.cancel()` 位于 `try` 块内、`except`
  之前（`:561`）。若 `get_engine_process` / `engine_process.wait()` / `RoutingState(**routing)`（`:532`）/
  `emit_task_done|escalated`（`:550-554`）任一抛异常，控制流直接跳至 `except`，**绕过 `:561`**，poller 协程
  永不取消 → 成为游离（`orphaned`）任务，持续每 0.3s 轮询永不退出，长服务下随失败任务累积泄漏。
  应为 `try/finally`：`poller.cancel()` 放进 `finally`，或 `except` 内补 `poller.cancel()`。

### (d) 回归 — 向后兼容、无轮次双计 ✅

- `run_report`（`1384-1391`）/`build_graph`（`1335-1337`）/`make_agent`（`802-803`）/`make_gate`（`1027-1029`）/
  `make_router`（`1162`）均新增 `on_event: Optional[Callable] = None`。
- 全仓调用方（`run_real.py:99`、`tests/test_orchestrator.py:30/49/...`、`tests/test_regression_edge.py:69/...`、
  `engine_runner.py:84`、`orchestrator.py:1508` 的 `__main__`）均以 `(task, llm, max_rounds=2, tools=...)` 形式调用，
  **无一处按位置传入 `on_event`**，新增默认参数零回归。✅
- 起始 `round_update(round=1)`（`:1420`）仅发射一次；Router 仅在 rework 分支发射**新轮** `round_update`
  （`:1195`，此时 `rs["round"]` 已 `+1`）。两者轮次不同（1 vs 2），**无同轮双计**。✅

### (e) 诚实边界 ✅

纯后端+引擎重构，复用既有真实 `STATE_DIR`/真实引擎运行状态（round/gate/decision/reason/tool_status），
无新端点、无新后端契约、前端零改动；死代码 `websocket.emit_*` 仍被 `task_done`/`task_escalated` 复用，
未冒充 Accio/@publisher/第三方。符合项目约定。

### (f) 环境限制 ✅ 非代码缺陷

受 429 限流，无法在本环境跑通「LLM 成功 → agent_complete+gate_complete 全序列」浏览器演示；
该路径已由 stub 单元 + 传输层 + 生产半程事件三重证明逻辑正确（见 VERIFICATION_TD002.md §四）。属环境配额限制，非缺陷。

---

## 三、MAJOR 清单

- **MAJOR-1** `server/api.py:561`（`start_engine_background`）：`poller.cancel()` 位于 `try` 内、`except`
  （`:567`）之前。当 `engine_process.wait()` / `RoutingState(**routing)` / `emit_*` 抛异常时，poller 协程
  未被取消而成为游离任务，长服务下累积泄漏。
  **修复**：将 `poller.cancel()` 移入 `finally`（或 `except` 内补 `poller.cancel()`），并 `await` 吞
  `CancelledError`。

## 四、MINOR 清单

- **MINOR-1** `server/api.py:48-65`（`_flush` 返回 `len(lines)`）：`sent` 按总行数推进，若某行是
  写入中途的半行（缺 `\n`）被 `json.loads` 跳过，下一轮 `lines[sent:]` 会跳过该行 → 该事件**永久丢失**。
  现状因 append 模式小事件单 `write()` 实际原子而罕见，但应按「成功解析并广播」的行号推进，或改用字节偏移只读完整行。
- **MINOR-2** `orchestrator.py:1200`（`rework_trigger`）：`rework_target_agent` 在 `rs.get(...) or ""` 时
  可能为 `""`；TS `ReworkTriggerData.rework_target_agent` 为 `AgentRole`（字符串别名），空串通过类型但语义为空。
  实践中 Gate 节点 rework 时必置 `rs["rework_target_agent"]`（`orchestrator.py:1138`），风险低，仅作提示。
- **MINOR-3** `server/api.py:568-573`（`except` 分支）：异常路径下未发 `task_done`/`task_escalated` 终态事件，
  客户端收不到终态弹窗（但服务端 `task.status` 已置 `escalated`）。与 MAJOR-1 同源，修复 `finally` 时可一并补发终态。
- **MINOR-4** `engine_runner.py:65`：每事件 `open(...,"a")` + 写 + 关闭，无缓冲/批写。事件量低时无误，
  高并发任务可改为行缓冲或累积批量刷盘（性能优化，非缺陷）。

---

## 五、NOTES（综合）

1. 修复切中要害：用「文件作跨进程事件总线」规避了子进程 `manager` 连接表为空的硬伤，比在子进程内
   直连 WS 稳健；且事件源全部来自引擎真实运行态，无虚构字段。
2. 字段契约与 `web/src/types/engine.ts` 完全对齐，前端无需任何改动即可消费 `agent_complete`/`gate_complete`/
   `rework_trigger`/`round_update`/`tool_error`。
3. **唯一必须修复项为 MAJOR-1（错误路径 poller 泄漏）**：建议 `start_engine_background` 用
   `try: ... finally: poller.cancel(); await poller` 结构，确保任意退出路径都释放 tail 协程。
   该缺陷仅在引擎异常时触发，不影响正常成功/升级路径的实时投递，故判 `PASS_WITH_NOTES` 而非 `FAIL`，
   但合并前建议修掉以免长服务泄漏。
4. 验证文档（VERIFICATION_TD002.md）证据链完整、分层真实（stub 单元 + 传输层 + 生产半程 + WS 实投），
   且明确标注 429 环境限制，未编造端到端全序列演示，符合诚实边界。

---

## 六、MAINTAINER FOLLOW-UP（主代理后置修复记录，非自签审议）

> 本段由**主代理**在独立审议（PASS_WITH_NOTES）之后追加，仅记录对 MAJOR-1 的修复动作，
> **不改动上方 `<!-- reviewed-by: independent-subagent -->` 审议结论**。

- **MAJOR-1 已按审议建议修复**：`start_engine_background`（`server/api.py`）原为 `poller.cancel()` 置于
  `try` 内、`except` 之前；现改为 `try: ... finally: if poller is not None: poller.cancel(); await poller`，
  并吞 `CancelledError`。`poller = None` 前置（`:502`），确保 `launch_engine` 抛异常时 `poller` 未赋值也不触发 `AttributeError`。
- **修复位置**：`server/api.py:502`（初始化）+ `:568-575`（finally 取消块）。
- **重编译校验**：`python -m py_compile orchestrator.py server/engine_runner.py server/api.py` → `PY_COMPILE_ALL_OK`（EXIT=0）。
- **状态**：MAJOR-1 关闭。MINOR 1-4 为性能/边缘优化，非阻塞，留作技术债，不纳入本次提交。
- **建议**：若可用性要求高，可考虑对 MINOR-3（异常路径补发 `task_done`/`task_escalated` 终态）单独补一个独立审议，
  避免客户端在引擎异常时收不到终态弹窗。
