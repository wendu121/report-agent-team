# VERIFICATION_GATE_MODEL_CONFIG.md · 方案 A 审核模型可配置 · 主代理自审（Gate ②）

> 日期：2026-09-10｜对应设计：DESIGN_GATE_MODEL_CONFIG.md｜关联：M9-5 / DESIGN_OPENAI_ENDPOINTS.md
> 主代理自审（非独立审议；独立审议见 REVIEW_GATE_MODEL_CONFIG.md，由子代理落 `reviewed-by`）

## 1. 改造文件清单

| 文件 | 改动性质 |
|---|---|
| `orchestrator.py` | make_gate / build_graph / run_report 加 gate_model_override（仅 Gate 节点，与 Agent 覆盖独立） |
| `server/api.py` | CreateTaskRequest / TaskResponse / TaskResponse 基类 加 gate_model；校验；task 创建；launch_engine 透传 |
| `server/engine_client.py` | EngineProcess + launch_engine 加 gate_model 透传 |
| `server/engine_runner.py` | run_engine 读 gate_model → run_report 透传 |
| `web/src/utils/chatEntry.ts` | BuiltTask + buildUserTask 加 gate_model |
| `web/src/views/ChatEntry.vue` | 独立「审核模型」下拉 + 同基座视觉警告 + onSubmit 透传 |
| `DESIGN_GATE_MODEL_CONFIG.md` | 新增设计文档（SoT） |

## 2. 关键代码点（真实 file:line，已 Read 核对）

### 2.1 后端（异基座默认优先，显式覆盖仅作用于 Gate）
- `orchestrator.py:977` `make_gate(..., gate_model_override: Optional[str] = None)`
- `orchestrator.py:993` `gate_model = gate_model_override or models["gates"][gate_name]["model"]` —— **默认 None → 沿用 model_mapping.yaml（异基座保留）**
- `orchestrator.py:1222` `build_graph(..., gate_model_override: Optional[str] = None)`
- `orchestrator.py:1243` `make_gate(..., gate_model_override=gate_model_override)`
- `orchestrator.py:1272` `run_report(..., gate_model: Optional[str] = None)`
- `orchestrator.py:1290` `gate_model_override = None if gate_model in (None, "auto") else gate_model`
- `orchestrator.py:1297` `build_graph(..., gate_model_override=gate_model_override)`

### 2.2 契约 + 链路
- `api.py:148` TaskResponse 基类 `gate_model: Optional[str] = None`
- `api.py:164` CreateTaskRequest `gate_model: Optional[str] = None`
- `api.py:179` CreateTaskResponse `gate_model: Optional[str] = None`
- `api.py:305` `req_gate_model = request.gate_model`
- `api.py:357-368` 校验（平行 req_model，valid_ids 来自 `_load_model_interfaces()`；`auto` 跳过）
- `api.py:397` `TaskResponse(..., gate_model=req_gate_model)`
- `api.py:414` `launch_engine(..., gate_model=task.gate_model)`
- `engine_client.py:38/48/67` EngineProcess 加 gate_model + input.json 写入
- `engine_client.py:178/190` launch_engine 加 gate_model 参数并透传
- `engine_runner.py:41` `task_gate_model = input_data.get("gate_model") or None`
- `engine_runner.py:70` `run_report(..., gate_model=task_gate_model)`

### 2.3 前端
- `chatEntry.ts:61` BuiltTask 加 `gate_model?: string`
- `chatEntry.ts:71` buildUserTask 加 `gate_model?: string` 参数
- `chatEntry.ts:90` `if (gate_model && gate_model !== 'auto') out.gate_model = gate_model`
- `ChatEntry.vue:191-194` `selectedGateModel` + `sameBaseWarn` computed
- `ChatEntry.vue`（模板）独立「审核模型」el-select（置顶「异基座自动（默认）」+ 分组复用 models）
- `ChatEntry.vue:312/326` onSubmit 透传 `selectedGateModel.value` → `gate_model`

## 3. Gate 异基座隔离默认保留（核心安全属性）
- `make_gate` 改为 `gate_model = gate_model_override or models["gates"][gate_name]["model"]`。
- `gate_model` 未传 / `"auto"` → `gate_model_override=None` → **完全等同旧行为**（Gate 走 model_mapping.yaml 异基座默认）。
- 用户显式选审核模型 → 仅覆盖 Gate 节点；Agent 模型（model_override）与 Gate 覆盖**互相独立**，互不牵连。
- `make_agent` 未触碰（model_override 逻辑不变）。

## 4. 向后兼容
- 所有新参数默认值 `None`：`run_real.py`、`tests/test_*.py`、`orchestrator.run_report` 内部示例调用均未传 gate_model → 自动走异基座默认。
- `gate_model` 字段在 TaskResponse 回显；前端未选时 `auto` → payload 不含 gate_model（与旧契约一致）。

## 5. 三道闸 ① 结果（实测）
- `vue-tsc --noEmit`（typecheck）：EXIT=0，0 errors（静默通过）。
- `vite build`（build）：1749 modules transformed，built in 9.19s，dist 产物生成（index-CRrkhaOg.css / index-DG2T5c-r.js 等），无错误。
- `python -m py_compile`（6 文件：orchestrator/server.api/engine_client/engine_runner/admin/tools/__init__/run_real）：EXIT=0，无 SyntaxError。
- 临时日志已清理：`web/tsc_check.log`、`web/py_check.log`、`web/build_check.log`、`web/build_check2.log`、`web/vite_out.log`、`web/vite_err.log`、`report-agent-team/py_check.log`。

## 6. 诚实边界（GAP，非阻塞）
- **浏览器 UI 未实跑**：下拉渲染、同基座 ⚠ 警告 tooltip、提交 payload 含 gate_model 已通过类型检查 + 逻辑自洽验证，但具体视觉/交互需 boss 在 localhost:18080 亲测。
- **双模型真实 e2e 未跑**：真实 LLM 双模型（Agent 选 A、Gate 选 B）端到端调用依赖 new-api 配额（当前 429 规避中），逻辑链路已全打通但未实跑验证选中审核模型真被 Gate 节点使用。
- 校验仅确认 id ∈ valid_ids（来自 _load_model_interfaces 全集），不验证该模型在当前端点可用（由引擎驱动时 _resolve 在 complete 内动态解析，未知端点会诚实抛 LLMError）。

## 7. 自审结论
方案 A 全量实现，Gate 异基座默认保留、显式覆盖可选、同基座仅视觉警告（boss 确认）。三道闸 ① 通过，待 ③ 独立审议。
