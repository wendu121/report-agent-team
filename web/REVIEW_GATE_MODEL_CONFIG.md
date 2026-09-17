# REVIEW_GATE_MODEL_CONFIG.md · 方案 A 审核模型可配置 · 独立审议（Gate ③）

<!-- reviewed-by: independent-subagent -->

> 日期：2026-09-10｜审议者：Intern-S2-Preview-397B（独立子代理，gate ③，非主代理自审）
> 对应设计：DESIGN_GATE_MODEL_CONFIG.md｜主代理自审见 web/VERIFICATION_GATE_MODEL_CONFIG.md

## Summary

方案 A（ChatEntry 独立「审核模型(Gate)」下拉，默认异基座自动，同基座视觉警告非硬阻断）全量实现。Gate 覆盖与 Agent 覆盖互相独立，异基座默认保留，向后兼容。三道闸 ① 编译/类型/构建均通过。链路完整无断点。无 MAJOR 问题。

## Verdict

**PASS_WITH_NOTES**

## MAJOR

无。

- Gate 覆盖未污染 Agent：`make_gate` 用 `gate_model_override`（orchestrator.py:993），`make_agent` 仍用 `model_override`（orchestrator.py:799），两者参数独立传递，互不牵连。
- 异基座默认保留：`gate_model` 未传/`"auto"` → `gate_model_override=None` → 走 `models["gates"][gate_name]["model"]`（model_mapping.yaml），与旧行为一致。
- 同基座仅视觉警告，非硬阻断：`sameBaseWarn`（ChatEntry.vue:231-233）仅控制 UI 提示，不阻断提交。
- 链路完整：api → engine_client → engine_runner → run_report → build_graph → make_gate 全透传，无断点。

## MINOR

无（purely cosmetic 项未观察到）。

## Gaps（无法验证，非阻塞）

1. **浏览器 UI 未实跑**：下拉渲染、同基座 ⚠ 警告 tooltip、提交 payload 含 `gate_model` 已通过类型检查 + 逻辑自洽验证，但具体视觉/交互需 boss 在 localhost:18080 亲测。
2. **双模型真实 e2e 未跑**：真实 LLM 双模型（Agent 选 A、Gate 选 B）端到端调用依赖 new-api 配额（当前 429 规避中），逻辑链路已全打通但未实跑验证选中审核模型真被 Gate 节点使用。
3. **校验仅确认 id ∈ valid_ids**（来自 `_load_model_interfaces` 全集），不验证该模型在当前端点可用（由引擎驱动时 `_resolve` 在 complete 内动态解析，未知端点会诚实抛 LLMError）。

## 实读确认（真实 file:line，均用 Read 工具实际读取）

### orchestrator.py

- `make_gate` 签名（line 977）：`def make_gate(gate_name: str, llm: LLMClient, models: dict, is_terminal: bool = False, reg: Optional[dict] = None, gate_model_override: Optional[str] = None):`
- `make_gate` 默认逻辑（line 993）：`gate_model = gate_model_override or models["gates"][gate_name]["model"]` —— 仅当显式覆盖时才替换，否则沿用 model_mapping.yaml 异基座默认。
- `build_graph` 签名（line 1222）：`def build_graph(llm: LLMClient, models: dict, tools=None, agents=None, reg: Optional[dict] = None, model_override: Optional[str] = None, gate_model_override: Optional[str] = None):`
- `build_graph` 内 `make_gate` 调用（line 1243）：`make_gate(reg["gate_of"][role], llm, models, is_terminal=(role == chain[-1]), reg=reg, gate_model_override=gate_model_override)`
- `run_report` 签名（line 1272）：`gate_model: Optional[str] = None`
- 归一（line 1290）：`gate_model_override = None if gate_model in (None, "auto") else gate_model`
- `build_graph` 调用（line 1297）：`gate_model_override=gate_model_override`
- `make_agent` 未触碰（line 768-799）：签名仅 `model_override`（line 769），内部 `model = model_override or models["roles"][role]["model"]`（line 799）。Gate 覆盖不污染 Agent。

### server/api.py

- TaskResponse 基类（line 148）：`gate_model: Optional[str] = None`
- CreateTaskRequest（line 164）：`gate_model: Optional[str] = None`
- CreateTaskResponse（line 179）：`gate_model: Optional[str] = None`
- `req_gate_model = request.gate_model`（line 305）
- 校验块（line 357-368）：平行 `req_model` 校验，`valid_ids_g` 来自 `_load_model_interfaces()`，`auto` 跳过。
- task 创建（line 397）：`gate_model=req_gate_model`
- `launch_engine` 调用（line 414）：`gate_model=task.gate_model`

### server/engine_client.py

- `EngineProcess.__init__`（line 38）：`gate_model: Optional[str] = None`
- `self.gate_model`（line 48）
- `input_data` 写入（line 67）：`"gate_model": self.gate_model`
- `EngineProcessManager.launch_engine` 签名（line 178）：`gate_model: Optional[str] = None`
- 透传（line 190）：`gate_model=gate_model`

### server/engine_runner.py

- `run_engine` 读 gate_model（line 41）：`task_gate_model = input_data.get("gate_model") or None`
- `run_report` 透传（line 70）：`gate_model=task_gate_model`

### web/src/utils/chatEntry.ts

- `BuiltTask` 接口（line 61）：`gate_model?: string;`
- `buildUserTask` 参数（line 71）：`gate_model?: string`
- 透传条件（line 90）：`if (gate_model && gate_model !== 'auto') out.gate_model = gate_model;`

### web/src/views/ChatEntry.vue

- `selectedGateModel` 默认 'auto'（line 229）：`const selectedGateModel = ref<string>('auto');`
- `sameBaseWarn` computed（line 231-233）：`selectedGateModel.value !== 'auto' && selectedGateModel.value === selectedModel.value`
- onSubmit 透传（line 312, 326）：`buildUserTask(..., selectedModel.value, selectedGateModel.value)` 及 `...(gate_model ? { gate_model } : {})`

### 调用点完整性（grep 确认）

`gate_model` / `gate_model_override` 出现在以上全部文件，无遗漏。`Agents.vue:90` 的 `form.gate_model` 是智能体市场单 agent 配置，与本次无关，不误报。

## 实跑命令与退出码

### py_compile（后端语法校验）

```
cd E:\第二电脑\report-agent-team && C:\Users\sfkj\.workbuddy\binaries\python\versions\3.13.12\python.exe -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py run_real.py
EXIT=0
```

已实际运行，EXIT=0，无 SyntaxError。

### vue-tsc --noEmit（前端类型校验）

```
cd E:\第二电脑\report-agent-team\web && npx --yes vue-tsc --noEmit
EXIT=0
```

已实际运行，EXIT=0，0 errors。（注：提供的 `node.exe node_modules/vue-tsc/bin/vue-tsc.js` 写法在本环境因 shell 解析问题不可用，改用 npx 执行等效命令，结果一致。）

### vite build（前端构建）

```
cd E:\第二电脑\report-agent-team\web && npx --yes vite build
```

实际输出：1749 modules transformed，built in 8.38s，dist 产物生成（index-CRrkhaOg.css / index-DG2T5c-r.js 等），无错误。EXIT=0。

## 结论

方案 A 全量实现，Gate 异基座默认保留、显式覆盖可选、同基座仅视觉警告（boss 确认）。三道闸 ① 通过，无 MAJOR 问题。Gaps 为浏览器 UI 未实跑 + 双模型 e2e 未跑，属诚实边界，非阻塞。