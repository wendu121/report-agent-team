# DESIGN_GATE_MODEL_CONFIG.md · ChatEntry 审核模型(Gate)可配置化（方案 A）

> 状态：SoT（单一事实源）｜日期：2026-09-10｜依赖：M9-5 / DESIGN_OPENAI_ENDPOINTS.md

## 1. 背景与问题

当前 `make_gate`（`orchestrator.py:985`）**写死** `gate_model = models["gates"][gate_name]["model"]`，
Gate（审核方）始终走 `config/model_mapping.yaml` 的默认映射，用户**无法在 ChatEntry 改 Gate 模型**。

这是出于「异基座隔离」的安全考虑：审核方与被审方用不同模型，防止 **Gate 包庇 Agent**（同源模型易对同款错误视而不见）。
但 boss 要求「想改就改」，故改为**可选 + 默认保留异基座**。

## 2. 目标（boss 确认：方案 A + 视觉提示）

- ChatEntry 新增一个**独立的「审核模型(Gate)」下拉**，与现有「模型接口（Agent）」下拉并列。
- 默认 = `auto`（=「异基座自动」，沿用 `model_mapping.yaml`，保留审阅独立性）。
- 用户可显式选一个模型接口覆盖 Gate 的异基座默认。
- **同基座视觉警告（非硬阻断）**：当所选 Gate 模型 == 所选 Agent 模型时，UI 弹红色 ⚠ 提示「审核独立性下降」，但**允许提交**（决定权在 boss，不偷偷剥夺）。

## 3. 数据流（端到端链路）

```
ChatEntry.vue
  selectedModel (Agent) ─┐
  selectedGateModel(Gate)┴─► buildUserTask(..., model, gate_model)
       │
       ▼  POST /tasks  { model, gate_model }
api.py CreateTaskRequest{ model, gate_model }
       → TaskResponse{ model, gate_model }
       → engine_manager.launch_engine(model=, gate_model=)
       │
       ▼
engine_client.py EngineProcess{ model, gate_model } → input.json{ model, gate_model }
       │
       ▼
engine_runner.py run_engine() → run_report(model=, gate_model=)
       │
       ▼
orchestrator.py run_report(model=None, gate_model=None)
       → model_override / gate_model_override 归一（None/"auto"=不覆盖）
       → build_graph(model_override=, gate_model_override=)
            ├─ make_agent(..., model_override=)        # Agent 调用
            └─ make_gate(..., gate_model_override=)    # Gate 调用
```

## 4. 契约变更

### 4.1 `server/api.py`
- `CreateTaskRequest` 新增：`gate_model: Optional[str] = None`
- `CreateTaskResponse` 新增：`gate_model: Optional[str] = None`
- 校验：对 `req_gate_model` 做与 `req_model` 平行的校验（来自 `_load_model_interfaces()` 的 valid_ids；`auto`/None 跳过）。
- `task = TaskResponse(...)` 加 `gate_model=req_gate_model`。
- `launch_engine(...)` 调用加 `gate_model=task.gate_model`。

### 4.2 `server/engine_client.py`
- `EngineProcess.__init__` 新增 `gate_model: Optional[str] = None` + `self.gate_model`。
- `input_data` 加 `"gate_model": self.gate_model`。
- `EngineProcessManager.launch_engine` 新增 `gate_model` 参数并透传。

### 4.3 `server/engine_runner.py`
- `run_engine()` 读 `task_gate_model = input_data.get("gate_model") or None`。
- `run_report(...)` 调用加 `gate_model=task_gate_model`。

### 4.4 `orchestrator.py`
- `run_report(user_task, ..., model=None, gate_model=None)`：
  - `gate_model_override = None if gate_model in (None, "auto") else gate_model`
  - 传给 `build_graph(gate_model_override=gate_model_override)`。
- `build_graph(..., model_override=None, gate_model_override=None)`：
  - `make_gate(reg["gate_of"][role], ..., gate_model_override=gate_model_override)`。
- `make_gate(gate_name, ..., gate_model_override: Optional[str] = None)`：
  - `gate_model = gate_model_override or models["gates"][gate_name]["model"]`（**仅此处改**，其余不变）。

## 5. 前端改造（`web/src`）

### 5.1 `utils/chatEntry.ts`
- `BuiltTask` 加 `gate_model?: string`。
- `buildUserTask(input, presetId?, agents?, plugins?, model?, gate_model?)`：
  - `if (gate_model && gate_model !== 'auto') out.gate_model = gate_model;`

### 5.2 `views/ChatEntry.vue`
- 新增 `selectedGateModel = ref<string>('auto')`。
- 复用 `/api/v1/models` 的同一 `models` 列表，新增第二个 `el-select`（带「异基座自动」置顶项 + 分组复用）。
- `sameBaseWarn` computed：`selectedGateModel !== 'auto' && selectedGateModel === selectedModel` → 显示红色 ⚠ 提示。
- `onSubmit` 把 `selectedGateModel` 透传给 `buildUserTask(..., selectedModel.value, selectedGateModel.value)`。

## 6. 默认行为与诚实边界

- `gate_model` 未传 / `=auto`：完全等同于当前行为（Gate 走 `model_mapping.yaml`，异基座隔离默认生效）→ **向后兼容**。
- `gate_model` 显式指定：覆盖 Gate 异基座默认，仅作用于 Gate 节点。
- Agent 模型（`model`）仍只覆盖 Agent 节点，Gate 与 Agent 的覆盖互相独立。
- **不硬阻断同基座**：仅视觉警告，体现「自由 + 透明」。

## 7. 风险与回退

- 风险：用户误设 Gate=Agent 同模型 → 审阅独立性下降。缓解：视觉警告（已在 5.2）。
- 回退：删除 `gate_model` 透传或前端隐藏该下拉，即恢复写死异基座（1 处 `make_gate`）。
- 不影响 Gate 异基座隔离的**代码正确性**：默认路径仍走 `model_mapping.yaml`。

## 8. 三道闸要求

- ① typecheck / build / py_compile 全 EXIT=0。
- ② `web/VERIFICATION_GATE_MODEL_CONFIG.md` 主代理自审。
- ③ `web/REVIEW_GATE_MODEL_CONFIG.md` 独立子代理审议（Intern-S2-Preview-397B，主代理未自签）。
