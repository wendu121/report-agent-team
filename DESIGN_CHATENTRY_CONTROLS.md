# ChatEntry Richer Controls 设计文档

> 目标：让对话入口对齐 Accio 模板，支持「插件多选」「智能体切换」「模型接口选择」三项控制，且每项都是真控制器（非纯 UI 摆设）。
> 日期：2026-09-10
> 状态：已评审 · 全量实现（boss 2026-09-10 明确「不怕改动大，模型接口做真·通用接口」，模型接口不再分期）

---

## 0. 关键决议（boss 2026-09-10）

- 模型接口下拉**做真**，不再 disabled 占位。
- 「通用接口」= new-api universal alias 融合池（auto-chat/auto-reasoning/auto-fast/auto-vision）+ 具体模型。
- 用户所选模型**仅覆盖 Agent（Researcher/Analyst/Writer）LLM 调用**；Gate 始终走 model_mapping.yaml（维护「异基座隔离」硬约束，防 Gate 包庇）。
- 数据源接口列表来自 `config/models.yaml`（热加载，GET /models 每次读取）。

---

## 1. 当前问题

用户反馈当前 `ChatEntry` 过于单一：
- 插件按钮只是跳转到 `/plugins` 管理页，不能在 composer 内直接勾选本次任务要用的数据源。
- 智能体组合以 chip 形式放在输入区上方，Accio 的「切换智能体」在 bot 名片右侧、更紧凑。
- 没有模型接口选择器（Accio 右侧有「自动」下拉）。

---

## 2. 方案总览

| 控制项 | Accio 行为 | 本次实现 | 是否真控制器 | 备注 |
|--------|-----------|----------|--------------|------|
| 插件多选 | composer 左侧「插件」按钮 → 列出可用插件并勾选 | ✅ 本次实现 | 真 | 透传到引擎，`build_search_tool` 按所选插件过滤 |
| 智能体切换 | bot 名片右侧「切换智能体」→ 选择参与 Agent | ✅ 本次实现 | 真 | 已是真控制器，仅改 UI 位置与交互 |
| 模型接口 | composer 右侧「自动」下拉 → 切换模型/接口 | ✅ 全量实现 | 真（Agent 调用覆盖） | 见 §3.3；Gate 维持异基座隔离 |

---

## 3. 详细设计

### 3.1 插件多选（真控制器）

**前端交互**
- composer 工具条左侧保留「插件」按钮，点击展开 `el-popover`。
- popover 内展示当前 `enabled` 的数据源插件列表（从后端 GET `/plugins` 读取）。
- 每个插件显示：`emoji(category)` + `name` + `description` + 复选框。
- `connected` 插件默认勾选且可选；`disconnected` 可勾选但带「未连接」提示；`coming_soon` 禁用并标「即将推出」。
- 已选插件在按钮上以 badge 数字显示，例如「插件 · 2」。

**后端扩展**
- 新增公开只读端点 `GET /plugins`：返回 `config/plugins.yaml` 中 `enabled=true` 的插件（含 id/name/category/description/status），**无需 admin token**。
- 扩展 `CreateTaskRequest.plugins: Optional[List[str]]`。
- `TaskResponse` 回显 `plugins`。
- `EngineProcess` / `engine_runner` 透传 `plugins` 到引擎 input.json。

**引擎层**
- `run_report` 增加 `plugins: Optional[List[str]] = None` 参数。
- `tools.data_sources.build_search_tool()` 若收到 `plugins`，仅使用该列表内的数据源；否则沿用全部 `enabled` 数据源。
- 若所选插件含 `coming_soon`，创建任务时 API 返回 400（诚实拒绝，不冒充可用）。

### 3.2 智能体切换（UI 重构，已是真控制器）

**前端交互**
- bot 名片右侧新增「切换智能体」紧凑按钮，显示当前已选 Agent 头像/数量（如 `R/A/W` 或 `3`）。
- 点击后弹出下拉/抽屉，复选框选择 Researcher/Analyst/Writer，与现有 `selectedAgents` 绑定。
- 保留 `?agents=` URL 回跳覆盖逻辑。
- composer 工具条原「切换智能体」按钮移除或改为只保留插件按钮。

**后端**：无需改动，仍通过 `CreateTaskRequest.agents` 传递。

### 3.3 模型接口选择（全量实现，通用接口）

**数据源**：`config/models.yaml` 的 `interfaces` 列表（GET /models 每次读取，热加载）。
- `auto`（默认）：不覆盖，沿用 model_mapping.yaml。
- `universal`（auto-chat/auto-reasoning/auto-fast/auto-vision）：new-api 通用融合池，网关自动路由。
- `concrete`（LongCat-2.0 等）：具体已接入模型。

**前端交互**
- composer 右侧「模型接口」下拉，默认「自动」。
- 选项来自 GET /models；选择非 auto 时，值写入 payload `model`。

**后端扩展**
- `CreateTaskRequest.model: Optional[str]`；提交时校验值 ∈ models.yaml 的接口 id（否则 400）。
- `EngineProcessManager.launch_engine` / `EngineProcess` / `engine_runner` / `run_report` 透传 `model`。
- `run_report(model=...)` → `build_graph(model_override=...)` → `make_agent` 内 `model = model_override or models["roles"][role]["model"]`，
  **仅作用于 Agent 调用**；Gate 节点 `gate_model = models["gates"][gate_name]["model"]` 不变（异基座隔离硬约束保持）。

**诚实边界**
- 用户自选模型只影响工作 Agent，不影响审计 Gate（避免 Gate 包庇）。如需让 Gate 也跟随，须先放松 model_mapping.yaml 硬约束 1（单独决策）。
- 所选 universal alias 的真实可用性由 new-api 网关决定；网关不可用将由 `NewApiLLMClient` 重试/escalate，不静默冒充。

---

## 4. API 变更

### 4.1 新增公开端点

```http
GET /plugins        # 已有（数据源列表，含 status）
GET /models         # 新增（模型接口列表，含 id/name/description/kind）
```
`GET /models` 响应：
```json
{
  "items": [
    {"id": "auto", "name": "自动", "description": "...", "kind": "auto", "default": true},
    {"id": "auto-chat", "name": "通用对话", "description": "...", "kind": "universal"}
  ],
  "total": 6
}
```

### 4.2 扩展任务创建

```json
POST /tasks
{
  "user_task": { ... },
  "template_id": "standard_research",
  "agents": ["Researcher", "Analyst", "Writer"],
  "plugins": ["tavily"],          // 新增：本次任务使用的数据源
  "model": "auto-chat"            // 新增：模型接口（auto=沿用 model_mapping.yaml）
}
```

### 4.3 引擎输入 JSON

```json
{
  "task_id": "...",
  "user_task": { ... },
  "max_rounds": 2,
  "agents": ["Researcher", "Analyst", "Writer"],
  "plugins": ["tavily"]
}
```

---

## 5. 数据流

```text
ChatEntry.vue
  ├─ 加载 GET /plugins → 渲染插件列表
  ├─ 用户勾选 plugins + agents
  └─ 提交 → buildUserTask(topic, presetId, agents, plugins)
        └─ task.createTask({ user_task, template_id, agents, plugins })
              └─ POST /tasks (api.py)
                    ├─ 校验 plugins 不存在/coming_soon → 400
                    ├─ 启动 engine_runner(input.json 含 plugins)
                    └─ run_report(..., plugins=...)
                          └─ build_search_tool(..., plugins=...) 过滤数据源
```

---

## 6. 诚实边界

- `coming_soon` 或未知插件不可选；提交时 API 拒绝。
- `disconnected` 插件可选，但引擎运行时降级为 MOCK/跳过，并在 `tool_status` 中留下不可用记录。
- 模型接口：用户所选仅覆盖 Agent LLM 调用；Gate 维持 model_mapping.yaml 异基座隔离（防包庇）。
- 所选模型值须经 `config/models.yaml` 校验（否则 400），杜绝传入不存在的模型名冒充可用。

---

## 7. 验收标准

- [ ] ChatEntry 顶部 bot 名片右侧出现「切换智能体」控件，点击可多选 Agent。
- [ ] composer 左侧「插件」按钮点击后展开可勾选插件，已选数量在按钮上 badge 显示。
- [ ] composer 右侧出现「模型接口」下拉，默认「自动」，其余项可选且写入 payload `model`。
- [ ] `npm run typecheck` 与 `npm run build` EXIT 0。
- [ ] Python 侧 `py_compile` 通过（api.py / engine_client.py / engine_runner.py / orchestrator.py / tools/__init__.py）。
- [ ] 提交含 `plugins` 的任务，引擎 `build_search_tool` 仅使用所选插件。
- [ ] 提交含 `model` 的任务，Agent LLM 调用使用该模型；Gate 仍走 model_mapping.yaml。
- [ ] 独立子代理 REVIEW PASS_WITH_NOTES 或 MAJOR=NONE。

---

## 8. 任务拆分

1. **前端**：ChatEntry.vue 重构（agent 控件置顶、插件 popover、模型下拉）。
2. **前端类型/store**：扩展 `buildUserTask`、`types/api.ts`。
3. **后端 API**：新增 `GET /models`、扩展 `CreateTaskRequest`/`TaskResponse`、插件/模型校验逻辑。
4. **引擎**：engine_runner / EngineProcess 透传 plugins + model；`build_search_tool` 支持插件过滤；`make_agent` 支持 model_override（仅 Agent）。
5. **三道闸**：typecheck/build/py_compile + VERIFICATION + 独立 REVIEW + Docker 重建探活。
