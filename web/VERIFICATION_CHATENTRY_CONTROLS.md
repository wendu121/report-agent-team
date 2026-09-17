# ChatEntry 对齐 Accio 全量重构 · 自审（Gate ②）

> 日期：2026-09-10
> 范围：ChatEntry 三控制项全量实现（插件多选 / 智能体切换 / 模型接口下拉——真·通用接口）
> 前置：boss 2026-09-10 明确「不怕改动大，模型接口做真·通用接口」，撤销原分阶段方案。

---

## 一、校验记录（Gate ①）

| 校验 | 命令 | 结果 |
|------|------|------|
| 前端类型检查 | `npm run typecheck`（vue-tsc --noEmit） | EXIT=0 |
| 前端构建 | `npm run build`（vue-tsc -b + vite build） | EXIT=0，1749 modules，13.46s |
| 后端语法 | `py_compile`（orchestrator/api/engine_client/engine_runner/admin/tools.__init__） | 6/6 OK |
| 逻辑自检 | model 归一 / plugin 过滤 / models.yaml 接口读取 | 全 PASS |

---

## 二、改动清单（真·控制器，非纯 UI）

### 前端
1. **ChatEntry.vue 重构**：
   - 顶部 bot 名片右侧新增「切换智能体」按钮（头像组 R/A/W + 数量），点击弹出多选，与 `selectedAgents` 绑定。
   - composer 左侧「插件」按钮 → 弹出可勾选数据源列表（来自 `GET /plugins`），已选数量 badge 显示。
   - composer 右侧新增「模型接口」下拉（来自 `GET /models`），默认「自动」，可选通用接口（auto-chat/auto-reasoning/auto-fast/auto-vision）与具体模型（LongCat-2.0）。
   - 提交时把 `plugins` + `model` 写入 payload。
2. **utils/chatEntry.ts**：`buildUserTask` 增加 `plugins`/`model` 出参；`model==='auto'` 不写入。
3. **types/api.ts**：`CreateTaskRequest`/`TaskResponse`/`CreateTaskResponse` 增 `plugins`/`model`。

### 后端
4. **config/models.yaml**（新）：可选模型接口列表（auto + 4 universal + 具体 LongCat-2.0），热加载。
5. **server/admin.py**：新增 `_load_model_interfaces()` + 公开 `GET /models`。
6. **server/api.py**：`CreateTaskRequest`/`TaskResponse`/`CreateTaskResponse` 扩展；创建任务时校验 plugins（未知/coming_soon → 400）与 model（不在 models.yaml → 400）；透传至 engine。
7. **server/engine_client.py**：`EngineProcess`/`launch_engine` 增 `plugins`/`model`，写入 input.json。
8. **server/engine_runner.py**：读取并透传 `plugins`/`model` 至 `run_report`。
9. **orchestrator.py**：`run_report(plugins=, model=)` → `build_tools(plugin_filter=)` 过滤数据源；`build_graph(model_override=)` → `make_agent(model_override=)`，**仅覆盖 Agent LLM 调用**。
10. **tools/__init__.py**：`build_tools(plugin_filter=)` 按所选插件 id 过滤数据源（None=全部 enabled）。

---

## 三、诚实边界核对

- ✅ **模型接口为真控制器**：用户所选写入 payload `model`，引擎 `make_agent` 用 `model_override or models["roles"][role]["model"]`，真实作用于 Agent 调用。
- ✅ **Gate 异基座隔离保持**：`make_gate` 仍用 `models["gates"][gate_name]["model"]`，不随用户所选改变（防包庇）。这是设计权衡，非"假配置"——Gate 刻意不受用户覆盖，已注释说明。
- ✅ **插件为真控制器**：`build_tools(plugin_filter=)` 过滤 `specs`，仅所选数据源进入 `SearchTool`。
- ✅ **无假配置**：unknown/coming_soon 插件与未知模型接口提交时 API 返回 400，杜绝传入不存在的名字冒充可用。
- ✅ **disconnected 插件可选但诚实**：UI 标「未连接」，引擎降级 MockProvider（tool_status 留痕）。

---

## 四、未覆盖项（诚实声明）

- **未跑真实 LLM e2e**：当前 429 配额规避（new-api 通道限流），引擎逻辑靠离线 py_compile + 逻辑自检覆盖。真实 LLM 跑 `model_override` 生效与否待配额恢复后补测。
- **未浏览器实测窄视口**：ChatEntry 工具条 4 个控件（插件/模型/发送）在超窄屏可能换行，留待独立 REVIEW 判断。
- **`/models` 端点未经 curl 实测**：容器尚未重建，待 Docker 重建后 curl 验证。

---

## 五、结论

Gate ① 与逻辑自检全绿；三项控制均为真控制器；诚实边界保留。待独立子代理 REVIEW（Gate ③）裁决。