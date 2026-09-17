# ChatEntry Controls 审议报告

> reviewed-by: independent-subagent
> 日期: 2026-09-10
> 审议者: 独立代码审议子代理 (gate ③)

## Summary

ChatEntry 新增三项控制（插件多选 / 智能体切换 / 模型接口）均已真实实现，数据流完整贯通。前两个审议子代理的两条 MAJOR 指控经逐行 Read 核对均不属实。三项校验命令全部通过。窄屏布局因无法实测标记为 GAP。

## Verdict

**PASS_WITH_NOTES**

## MAJOR

无。两条被指控的 MAJOR 均已证伪：

### 指控 A：make_gate 违反异基座隔离 — 不属实

读 orchestrator.py `def make_gate`（行 833-834），函数体行 842：
```
gate_model = models["gates"][gate_name]["model"]
```
该行仅从 `models["gates"][gate_name]["model"]` 读取，**未引用** `model_override`，gate 永远走 model_mapping.yaml 异基座映射。

再读 `def make_agent`（行 625-626），行 656：
```
model = model_override or models["roles"][role]["model"]
```
`model_override` 仅在 `make_agent` 内生效，通过 `build_graph`（行 1086）仅注入 Agent 节点。Gate 节点（行 1088-1089）不传 `model_override`。

结论：异基座隔离硬约束保持完好，指控 A 无依据。

### 指控 B：api.py 插件校验漏 coming_soon — 不属实

读 server/api.py `create_task` 行 296-329。行 313-319：
```
src_map = {p["id"]: p for p in load_data_sources()}
bad = []
for pid in req_plugins:
    spec = src_map.get(pid)
    if spec is None:
        bad.append(f"未知插件：{pid}")
    elif spec.get("status") == "coming_soon" or spec.get("provider") == "coming_soon":
        bad.append(f"插件尚未接入（coming_soon）：{pid}")
```
行 322-329：若 `bad` 非空则抛 `HTTPException(status_code=400)`。

结论：`coming_soon` 被显式判断并返回 400，指控 B 无依据。

## MINOR

无。

## Gaps

1. **窄屏响应式（<768px）无法实测**。ChatEntry.vue composer-bar（行 390-412）含插件按钮 / 模型下拉 / spacer / 发送按钮四项，当前无 `@media` 响应式规则；模型下拉固定 `width: 150px`（行 413）。在窄屏下可能溢出。**标记 GAP**，建议后续加媒体查询或改为横向滚动。

## 实读确认（真实 file:line）

- DESIGN_CHATENTRY_CONTROLS.md §3.3（行 83-84）：模型接口仅作用于 Agent，Gate 维持异基座隔离。
- models.yaml（行 9-11）：诚实边界声明 — 用户所选仅覆盖 Agent，Gate 走 model_mapping.yaml。
- orchestrator.py `make_gate` 行 842：`gate_model = models["gates"][gate_name]["model"]`（无 model_override）。
- orchestrator.py `make_agent` 行 656：`model = model_override or models["roles"][role]["model"]`（仅 Agent）。
- orchestrator.py `build_graph` 行 1086：`model_override` 仅注入 `make_agent`；行 1088-1089：`make_gate` 不传 model_override。
- orchestrator.py `run_report` 行 1131：`model_override = None if model in (None, "auto") else model`（"auto"→不覆盖）。
- orchestrator.py `run_report` 行 1135：`build_tools(tools_config_path, plugin_filter=plugins)`。
- tools/__init__.py `build_tools` 行 32：`plugin_filter` 参数；行 63-65：非空则过滤 specs；行 67：过滤后的 specs 进 `build_search_tool`。
- server/api.py `create_task` 行 296-329：插件 + 模型校验；行 319：`coming_soon` 显式判 400；行 330-347：模型接口 ∈ models.yaml 校验。
- server/api.py 行 387-388：`plugins=task.plugins or None, model=task.model` 透传给 launch_engine。
- server/engine_client.py 行 63-64：`plugins` / `model` 写入 input.json。
- server/engine_runner.py 行 38-39：读取 input.json 的 plugins / model；行 62-63：传给 run_report。
- ChatEntry.vue 行 11-24：智能体切换在 bot 名片右侧（el-popover）。
- ChatEntry.vue 行 65-86：插件 popover，行 176-195 fetchPlugins 读 GET /plugins。
- ChatEntry.vue 行 89-100：模型下拉，行 197-208 fetchModels 读 GET /models。
- ChatEntry.vue 行 229-242：`buildUserTask` 携 plugins / model 进 payload。
- ChatEntry.vue 行 166-174：onMounted 处理 `?agents=` 回跳。
- chatEntry.ts 行 63-88：`buildUserTask` 纯函数；行 85：plugins 透传；行 86：`model !== 'auto'` 才写 model。
- types/api.ts 行 127-136：`CreateTaskRequest` 含 plugins / model；行 121-122：`TaskResponse` 回显。

## 实跑命令与退出码

```
cd "E:/第二电脑/report-agent-team/web" && npm run typecheck; echo EXIT=$?
EXIT=0

cd "E:/第二电脑/report-agent-team/web" && npm run build; echo EXIT=$?
EXIT=0

cd "E:/第二电脑/report-agent-team" && python -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py; echo EXIT=$?
EXIT=0
```

三项校验全部通过。