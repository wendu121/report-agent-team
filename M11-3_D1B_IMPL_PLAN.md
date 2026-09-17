# M11-3 实施计划（D1-B 全 function-calling）· 实测 file:line 锚点

- 日期：2026-09-12
- 依赖：`DESIGN_M11_autonomous.md` §4（D1-B 已锁定）、M11-1（kb_store/web_search 已就绪）
- 目标：让 Researcher/Analyst **自主决定调用工具**（含 MCP 外部工具），而非引擎替它跑固定管道。
- 范围澄清：联网搜索能力本就存在（`build_search_tool`），D1-B 让其由 LLM 自主触发；MCP 是新增外部工具接入通道。

## 1. 当前调用流（实测，须改的点）

- `orchestrator.py:993-1031` Researcher：引擎调用 `tools.web_search.search_many(queries)`（queries 由 `plan_search_queries` 出），结果注入 `extra_context`，LLM 只从结果筛选。→ **D1-B：改为 LLM 自己发 `web_search` tool_call**。
- `orchestrator.py:1086-1098` Analyst：`tool_requests` 经 `tools.data_proc.run_requests`（AST 白名单求值）二次生成。→ **D1-B：统一走 `dispatch_tool` function-calling**（data_proc + mcp）。
- `LLMClient.complete`（`orchestrator.py:100`/`334`）只返回 `str(content)`；OpenAI SDK 在传 `tools=` 时返回 `message.tool_calls`。→ **新增 `complete_with_tools`**。
- `StubLLMClient.complete`（`orchestrator.py:166`）：须模拟 tool_calls 以驱动测试。
- `tools/__init__.py:23-90` `ToolBundle`（web_search/data_proc/doc_export）+ `build_tools`。→ 加 `mcp: MCPClient | None`。

## 2. 新增文件

### 2.1 `tools/mcp_client.py`
- `class MCPClient`：读 `config/mcp_servers.yaml`（id/name/transport[sse|http|stdio]/url/headers/enabled/tools_whitelist）。
- `list_tools() -> list[{server, name, description, input_schema}]`：对 enabled 且可达的 server 取工具；不可达 → `[]`（降级，不抛）。
- `call_tool(server_id, name, args) -> {ok, result|error}`：经对应 transport 调用；失败 → `ok=False`（诚实边界，不伪造）。
- transport 实现：SSE/HTTP 用 `mcp` SDK 的 `ClientSession`（`streamablehttp_client`/`sse_client`）；stdio 仅作容器内置预留（M11-3 先不支持 stdio 跨容器，文档标注）。
- 连接懒加载 + 缓存；并发安全从简（单任务串行足够）。

### 2.2 `config/mcp_servers.yaml`（新）
- 空 enabled 列表 + 1 个注释示例（SSE 型），含 `tools_whitelist` 说明。

## 3. `orchestrator.py` 改动

1. **`LLMClient.complete_with_tools(model, system, user, tools, **kw) -> dict`**（`orchestrator.py:100` 基类 + `:334` NewApiLLMClient + `:166` Stub）：
   - NewApi：`client.chat.completions.create(..., tools=tools, tool_choice="auto")`，返回 `{"content": msg.content, "tool_calls": [tc.model_dump() for tc in msg.tool_calls] or []}`。
   - Stub：依 `shape` 模拟——首轮返回 1 个 `web_search` tool_call（queries 从 user 抽），次轮返回最终 JSON（content 非空、tool_calls=[]）。
   - 基类默认：`raise NotImplementedError`（真实生产只走 NewApi/Stub）。

2. **`dispatch_tool(name, args, bundle, mcp) -> {ok, result|error, source}`**（新增，靠近 `make_agent`）：
   - `name == "web_search"` → `bundle.web_search.search_many(args["queries"], agent=role)`（沿用 M4 聚合）。
   - `name == "data_proc"` → `bundle.data_proc.run_requests(args["requests"], agent=role)`。
   - `name.startswith("mcp:")` → `server, tool = name[4:].split(":",1)`；`mcp.call_tool(server, tool, args)`；`source=f"mcp:{server}:{tool}"`。
   - 任意异常 → `{ok:False, error:...}`（不击穿图）。

3. **`make_agent` researcher/analyst function-calling 循环**（改 `:993-1031` 与 `:1086-1098`）：
   - 收集工具 schema：researcher = `[web_search]` + mcp tools（role=researcher 作用域）；analyst = `[data_proc]` + mcp tools。
   - 循环（max `MCP_MAX_ROUNDS=4`）：调 `complete_with_tools`；若有 `tool_calls` → 逐个 `dispatch_tool` → 结果拼成 `【工具执行结果】` 回注 `user`；无 `tool_calls` 或达上限 → 退出循环取 `content` 作最终 JSON。
   - **向后兼容兜底**：若 LLM 全程不发 tool_calls（含 Stub 在旧测试中不发），则 `content` 即原 JSON → 走现有 schema 校验/溯源逻辑，**79 个旧测试不破**。
   - Researcher 溯源硬校验（`:1110-1116`）改为基于「本轮实际 dispatch 过的 web_search 结果集」生效（变量名 `search_results` 已由 dispatch 累积）。

4. **`build_graph` / `run_report`**：`MCPClient` 在 `build_tools` 内构建并塞入 `ToolBundle.mcp`；`make_agent` 节点通过 `tools.mcp` 取工具。**不新增图节点**（function-calling 在 Agent 节点内循环，非独立节点）。

5. **`tools/__init__.py`**：`ToolBundle` 加 `mcp: object = None`；`build_tools` 末尾 `MCPClient(config_path=...)`（读 `config/mcp_servers.yaml`）；`run_report` 传 `tools` 给 `build_graph`。

## 4. `server/api.py` 改动（镜像 `/admin/providers` `:1115-1182`）
- `GET/POST/DELETE /admin/mcp-servers` + `McpServerCreate` + 加载 `config/mcp_servers.yaml`（ruamel round-trip 原子写，M8-1 同款），热加载。
- 密钥进 `.secrets/mcp.env`（不入库）。

## 5. `requirements.txt` / `Dockerfile`
- `requirements.txt` 加 `mcp>=1.0`（SSE/HTTP client）；`Dockerfile` 已 `pip install -r requirements.txt`，无需改。

## 6. 诚实边界 / 降级
- MCP server 不可达 → 该 server 工具不出现在 schema、call 返回 `ok=False`（LLM 见失败可改策略，绝不伪造结果）。
- `complete_with_tools` 异常 → 走现有 `_agent_fail` rework 路径。
- stdio 型 MCP 不在 M11-3 容器外支持（文档标注，避免跨容器进程坑）。

## 7. 测试计划（gate①）
- 单测 `dispatch_tool`：web_search/data_proc/mcp 路由 + 异常降级（无真实 MCP 也能测，用 fake mcp 对象）。
- `run_report` stub e2e：StubLLMClient 模拟 researcher 发 web_search tool_call → 验证结果回流进最终 JSON + 溯源校验用 dispatch 结果集。
- 现有 79 测试须仍绿（旧 Stub 不发 tool_calls → 走兜底管道）。

## 8. 风险
- **R1** function-calling 循环 runaway → `MCP_MAX_ROUNDS` 上限 + 达上限强制取最后一次 content。
- **R2** 真实 LLM tool_calls JSON 参数解析 → `args` 已是 dict（OpenAI SDK 给出），但须容错非 dict。
- **R3** async MCP client 在同步 Agent 节点 → `dispatch_tool` 内用 `_run_async`（M11-1 已有）包 `mcp.call_tool` 协程。
- **R4** 79 旧测试破坏 → 兜底管道保证不破；新增测试覆盖 tool_calls 路径。
