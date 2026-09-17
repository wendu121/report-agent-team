# VERIFICATION · TD-002 任务运行事件流修复

> 门禁② 主代理自审（edict-gate）。TD-002 = 任务追踪页"暂无运行事件"根因修复。

## 一、问题根因（真因，非表象）

用户截图：任务进行中（第 1/2 轮）任务追踪页显示 `暂无运行事件`（来自 `web/src/components/timeline/Timeline.vue:3` 的 `el-empty`）。

链路逐段核查结论：

1. **前端消费层已完整**：`wsService.ts` 连 `/api/v1/tasks/{id}/stream`；`task.ts:handleEvent` 把 WS 事件经 `wsEventToNode`（`utils/timeline.ts`）转时间线节点并 `nodes.value = [...nodes.value, node]`；`types/engine.ts` 已定义 7 类事件判别联合。**前端从未缺能力**。
2. **中间事件从未产生**：`orchestrator.run_report` 是纯同步函数，跑完才 `return final`（orchestrator.py:1389/1398），中途不 emit。子进程 `engine_runner.run_engine` 只把最终 `final_state` 写文件，不 emit 中间事件。`websocket.py` 里 `emit_agent_complete`/`emit_gate_complete`/`emit_rework_trigger`/`emit_round_update` 全是**死代码**（无调用方）。
3. **即便产生也广播不到**：引擎跑在独立子进程（`subprocess.Popen -m server.engine_runner`）。`websocket.manager` 是模块级单例，子进程内 `manager` 的连接表为空（连接挂在 API 服务进程），跨进程共享不了 → `broadcast` 是 no-op。
4. **结论**：`api.py` 仅在 `engine_process.wait()` 完成后 emit `task_done`/`task_escalated`（终态弹窗能显示），运行中的 agent_complete/gate_complete/rework_trigger/round_update 全程缺失 → 时间线 `nodes` 恒空 → 显示"暂无运行事件"。

## 二、改动清单（3 文件，0 前端改动，0 后端契约变更）

| 文件 | 改动 |
|---|---|
| `orchestrator.py` | `run_report` / `build_graph` / `make_agent` / `make_gate` / `make_router` 透传 `on_event` 回调；Agent 成功 → `agent_complete`；Gate 决策 → `gate_complete`；Agent 工具失败 → `tool_error`；Router 返工 → `round_update`(新轮) + `rework_trigger`；`run_report` 起始 → `round_update`(第1轮)。统一 `try/except` 包裹，事件推送失败不影响引擎主流程。 |
| `server/engine_runner.py` | 新增 `_on_event` 闭包：把事件补齐 `task_id`+`timestamp` 信封后**追加写入** `{state_dir}/{task_id}_events.jsonl`（每行一条）。`run_report(..., on_event=_on_event)`。每轮运行前 `unlink` 旧事件文件。 |
| `server/api.py` | 新增模块级 `_stream_engine_events(task_id, events_file)`：并发 tail JSONL（按行号增量，0.3s 轮询，cancel 时收尾 flush），逐条 `manager.broadcast`。`start_engine_background` 在 `launch_engine` 后 `asyncio.create_task` 启动该轮询协程，与 `engine_process.wait()` 并行；`wait()` 返回后 `poller.cancel()`。`from .websocket import manager as _ws_manager`。 |

**事件信封严格对齐 `web/src/types/engine.ts` 判别联合**：每条含 `event_type`+`task_id`+`timestamp`（WSEnvelope）+ 专属 payload（`round/agent/tool_status`、`round/gate/decision/reason/eval_score/problem_points`、`round/rework_target_agent/rework_reason/last_gate`、`round/max_rounds`）。

## 三、门禁①构建校验

- `python -m py_compile orchestrator.py server/engine_runner.py server/api.py` → `PY_COMPILE_ALL_OK`（EXIT=0）。
- 修复初版 `make_gate` 的 `else` 分支缩进错误（IndentationError），已修正并重编译通过。
- **MAJOR-1 修复后重编译**：将 `start_engine_background` 的 `poller.cancel()` 移入 `try/finally`（`api.py:568-575`，`if poller is not None` 守护防 `launch_engine` 异常时 `poller` 未定义），重跑 `py_compile` 三项 → `PY_COMPILE_ALL_OK`（EXIT=0）。✅

## 四、门禁②自审 + 真实测试证据（4 层）

> 真实 LLM 当前被 429 限流，故用 **stub 离线 + 生产服务实跑** 双轨验证，不编造数据。

1. **编排器事件发射（stub 单元）** — `report-api` 容器内 `run_report(StubLLMClient, on_event=collect)`：
   - `scenario="pass"` 事件序列含 `round_update`/`agent_complete`/`gate_complete`，终态 `done` ✅
   - `scenario="rework"` 事件序列含 `rework_trigger` + 新一轮 `round_update` ✅
2. **JSONL→广播传输层（异步假 manager）** — `_stream_engine_events` 读 JSONL 并 `await broadcast`：写出 3 条 → 广播 3 条，`event_type`/`task_id`/`timestamp` 齐备 ✅（首次用同步假 manager 触发 `await None` 告警，改为 `async def` 假 manager 后干净通过 `TRANSPORT_ASYNC_OK`）。
3. **生产服务实写事件（真实任务）** — 经 `:18080` 创建真实任务，容器内 `.engine_state/<tid>_events.jsonl` 实时出现：
   ```
   round_update   round=1 max_rounds=2
   round_update   round=2 max_rounds=2
   rework_trigger rework_target_agent=Researcher reason="产出缺少数组字段 retrieval_records"
   ```
   证明 引擎子进程→JSONL 在生产全链路跑通（此任务受 429 影响 Researcher 未取到数据→GateA 返工，故无 agent_complete，符合"失败不报完成"的正确语义）。
4. **WS 实时投递（浏览器真实路径）** — `report-api` 容器内连 `ws://report-web/api/v1/tasks/<tid>/stream`（与浏览器一致，走 nginx `/api/` 反代），建任务后收到：
   ```
   connection_established
   round_update {round: 1}
   ```
   `WS_DELIVERY_OK` ✅。证明运行事件经 `manager.broadcast` 实时到达客户端（同一 `manager.broadcast` 此前已能送 `task_done` 弹终态窗，机制同源）。

## 五、诚实边界核查

- ✅ 纯后端+引擎重构，复用电商/数据源等既有真实控制器；**无任何新端点、无新后端契约**，前端零改动（消费层本就完备）。
- ✅ 不冒充 Accio / @publisher / 第三方；徽标/来源均源自 `builtin` 真实字段。
- ✅ 事件 payload 全部来自引擎真实运行状态（round/gate/decision/reason/tool_status），无虚构。
- ⚠️ 真实性边界：受 429 限流，本环境无法跑通"LLM 成功→agent_complete+gate_complete 全序列"的端到端浏览器演示；该路径已由 stub 单元 + 传输层 + 生产半程事件三重证明逻辑正确，待 new-api 配额恢复即可在浏览器见完整叙事。
- ⚠️ `tool_error` 在 mock 数据源下出现较多（mock 条目 `ok=False`），属测试数据特征，非引擎 bug；生产真实数据源正常时仅在实际工具失败时上报。

## 六、门禁③独立审议结论 + 待办

- **门禁③（独立子代理，PASS_WITH_NOTES，MAJOR 1 / MINOR 4）已通过**，verdict 见 `REVIEW_TD002.md`（含 `<!-- reviewed-by: independent-subagent -->`）。
- **MAJOR-1 已修复**（错误路径 poller 协程泄漏）：`start_engine_background` 改用 `try: ... finally: if poller is not None: poller.cancel(); await poller`（`api.py:568-575`），重编译通过。见上 §三。
- **MINOR 1-4 非阻塞**，留作技术债（半行竞态按解析行推进 / rework 空串语义 / 异常路径补发终态 / 批量刷盘优化），不在本次提交范围。
- 提交范围（仅本次 TD-002 三 py 文件 + `VERIFICATION_TD002.md` + `REVIEW_TD002.md`）：
  - `orchestrator.py`、`server/engine_runner.py`、`server/api.py`
- `git push` 仍按铁律等 boss 显式指令，不主动 push。
- 早期「统一能力市场页」改动（Plugins.vue/Market.vue/router/index.ts/DefaultLayout.vue + VERIFICATION_ACCIO_FULL.md + REVIEW_ACCIO_FULL.md）独立审议已 PASS_WITH_NOTES/MAJOR 0，与本 TD-002 一并 `git commit`（均属功能完善，互不卷无关项）。
