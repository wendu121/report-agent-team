# DESIGN_chat_mcp_learning.md · 对话入口「自主调 MCP + 自主学习 + 进化」

- 日期：2026-09-13
- 依赖：
  - `DESIGN_chat_first.md`（对话入口，阶段 1 已交付：薄壳 ChatAgent 仅 `generate_report`）
  - `DESIGN_M11_autonomous.md` M11-1（pgvector 持久知识层 ✅ 已落地）/ M11-3（MCP 消费桥 D1-B ✅ 已落地）/ M11-2（反思回写闭环，引擎侧 pending）
  - `orchestrator.py` 的 `_run_fc_loop` / `dispatch_tool` / `_build_tool_schemas`（引擎已验证的 function-calling 循环）
  - `tools/mcp_client.py` `MCPClient`（同步 `list_tools()` / `call_tool()`，已被引擎消费）
  - `tools/kb_store.py` `kb_retrieve` / `kb_write`（pgvector 读写）
- 状态：草案，待 boss 审阅
- 作者：（主代理）

---

## 0. 背景与动机（来自 boss 实测反馈）

boss 在 WebUI 聊天页实测发现：
1. 问「研报助手」是否能自主调 MCP / 调 skill / 自主学习 → 后端如实回答「暂不支持直接自主连接或调用任意外部 MCP 服务」。
2. boss 诉求：**「要改，而且我希望他能自主学习，进化」**。

现状事实（实测）：
- `chat_agent.py` 的 `ChatAgent` 是**薄壳**：只有 `generate_report` 一个意图工具；闲聊/问答走 `llm.complete(model="auto-chat")` 单轮，**无 web_search、无 data_proc、无 MCP、无记忆、无学习**。
- 引擎侧 `_run_fc_loop`（orchestrator.py:1129）已是成熟、经 e2e 验证的 function-calling 循环，能自主调 `web_search` / `data_proc` / `mcp:{server}:{tool}`。
- 知识层 `kb_store` 已就绪（M11-1）：`kb_retrieve` 语义召回、`kb_write` 按 `topic_hash` 去重 upsert。
- 跨会话记忆 `ChatSessionMemory` 已就绪（M10），但**仅用户手动 set**，非 agent 自主沉淀。

→ 本设计把引擎已验证的「工具循环 + 知识层」**复用**到 ChatAgent，使其从薄壳升级为「能自主调工具、能记忆、能进化」的一等对话 Agent。**不重写引擎、不造第二套工具循环**（避免过度工程）。

---

## 1. 目标与三能力映射

| boss 诉求 | 本设计能力 | 实现方式 | 复用/新增 |
|---|---|---|---|
| 自主调 MCP | **ChatAgent 工具升级** | 复用 `_run_fc_loop` + `build_tools()` + `MCPClient`，注入 `web_search`/`data_proc`/`mcp:*` 工具 schema | 复用引擎基础设施 |
| 自主学习 | **对话知识沉淀 + 召回** | 每轮先 `kb_retrieve` 注入相关历史结论；工具支撑的实质性回答 `kb_write` 为 `chat_fact` | 复用 `kb_store` |
| 进化 | **经验手册（playbook）+ 人工 accept 门禁** | 用户/agent 触发「存为经验」→ `chat_lesson`(proposed) → 管理员 accept → 写入 `config/chat_playbook.yaml` 注入 system prompt | 新增（对齐 M11-2 D4） |

> 诚实边界（铁律）：agent **绝不静默自改 prompt/代码**；任何「经验」须人工 accept 才生效（与 M11-2 D4 一致）。无 embedding 时知识层退化为全文召回，绝不伪造。

---

## 2. 架构

```
用户 ──POST /api/v1/chat──▶ ChatAgent.step()
                              │
        ┌─────────────────────┴─────────────────────┐
        │ 1) 召回：kb_retrieve(user_msg, k=3)         │  ← 自主学习·读
        │    → 历史 chat_fact / 已 accept chat_lesson │
        │ 2) 系统提示（+ playbook 注入 + KB 上下文）    │
        │ 3) _run_fc_loop(web_search+data_proc+mcp:*)  │  ← 自主调 MCP（复用引擎）
        │    → tool_calls 自主多轮，直到终态文本        │
        │ 4) _safe_parse(raw) → intent                │
        │    chat → 回复；generate_report → 触发引擎    │
        │ 5) 沉淀：kb_write(chat_fact)（工具支撑时）    │  ← 自主学习·写
        └─────────────────────────────────────────────┘
                              │
                   ChatResponse{reply, intent, tool_calls, task_id}
                              │
                   WS/前端渲染「🔌 调用了 N 个工具」
```

ChatAgent 与引擎**共用** `build_tools()` / `MCPClient` / `_run_fc_loop` / `kb_store`，仅 prompt/role 不同（role=`"ChatAgent"`）。generate_report 仍走「意图 → api.py 触发 create_task」旧路径（不重写引擎）。

---

## 3. 后端改动

### 3.1 `chat_agent.py`（重写 ChatAgent）

- `__init__(self, llm)`：保持；但 `step` 内部按需构建工具：
  - `bundle = build_tools()`（ToolBundle：web_search+data_proc+mcp）
  - 复用引擎 `MCPClient`（已在 bundle.mcp）。
- `CHAT_SYSTEM_PROMPT` 改写：
  - 你是「研报助手」，能聊天、能调工具（联网检索 web_search、计算 data_proc、外部 MCP 工具）、能生成研报。
  - **回答事实/研究类问题**：先自主调用检索/MCP 工具取证，再综合回答；不得凭空编造数据。
  - **生成完整研报**：仅当用户明确要「一份/详细/完整报告」时，回复 JSON `{"intent":"generate_report",...}`；其余一律自然语言回答。
  - 结尾追加：`【你可调用的工具】web_search / data_proc / mcp:<server>:<tool> …`（schema 由 `_build_tool_schemas` 动态生成，prompt 仅示意）。
  - playbook 段（若有已 accept 经验）注入为「经验参考」。
- `step(history, user_msg, model)` 流程：
  1. `kb_ctx = _run_async(kb_retrieve(user_msg, k=3))` → 拼成上下文（仅注入 agent 输入，不改持久化原消息）。
  2. `tool_schemas = _build_tool_schemas(bundle, bundle.mcp, role="ChatAgent")`（不含 generate_report，避免长阻塞工具混入循环）。
  3. `raw, tool_entries, extra_search = _run_fc_loop(llm, chat_model, system+kb_ctx, user_blob, tool_schemas, bundle, bundle.mcp, role="ChatAgent", shape="researcher")`。
     - `shape="researcher"` 使 `complete_with_tools` 走 agent 提示模板；web_search 自动可用。
  4. `parsed = _safe_parse(raw)`：
     - `intent=="generate_report"` → 原样返回（api.py 触发引擎）。
     - 否则 `reply = parsed["reply"] or raw`；`intent="chat"`。
  5. **沉淀**：若 `extra_search` 非空（即工具产出真实素材）且回复非空 → `_run_async(kb_write([chat_fact_entry]))`：
     - `kind="chat_fact"`，`topic=user_msg[:200]`，`title=用户问题摘要`，`content=reply + "\n\n工具来源：" + sources`，`source="chat_agent"`，`metadata={credibility, tool_sources, session_ok}`。
     - 失败降级（kb_write 自带 try/except 返回 0），**绝不阻断回复**。
  6. 返回 `{intent, reply, tool_calls: [t["tool"] for t in tool_entries], sources: extra_search}`。
- 新增模块级 `_run_async` 导入（`from tools._async_util import run_async`）做同步→异步桥；api.py `chat()` 仍为 `result = agent.step(...)`（**不改成 await**，最小爆破面）。

### 3.2 `server/api.py`

- `ChatResponse` 增加字段 `tool_calls: List[str] = []`（前端展示自主工具调用），`sources: List[dict] = []`（可选，前端溯源）。
- `_get_chat_agent()` 不变（仍 `ChatAgent(llm=...)`；工具在 step 内懒构建，单例内缓存 bundle）。
- `chat()` 端点：把 `result.get("tool_calls", [])` / `result.get("sources", [])` 透传进 `ChatResponse`。跨会话记忆注入（现有 `_fetch_recent_session_memories`）保留。
- 新增 `POST /admin/chat-lessons` 列表 proposed + `POST /admin/chat-lessons/{id}/accept`（**复用 M11-2 reflection 范式**；若 M11-2 引擎侧尚未落地，本端点独立实现最小版：accept 即把 `chat_lesson` 写入 `config/chat_playbook.yaml`，ChatAgent 启动时热读）。

### 3.3 `tools/chat_learning.py`（新增，薄封装）

- `chat_learn_write(user_msg, reply, tool_entries, extra_search) -> int`：组装 `chat_fact` entry 调 `kb_write`。
- `load_playbook() -> str`：读 `config/chat_playbook.yaml`（git-ignored）已 accept 经验，拼成 prompt 段；缺省返回 `""`。
- `propose_lesson(text) -> id`：写 `kb.entries` kind=`chat_lesson`, metadata `status=proposed`。
- `accept_lesson(id)`：置 `status=accepted` + 追加到 `config/chat_playbook.yaml`（ruamel round-trip，对齐 M8-1）。

### 3.4 `config/chat_playbook.yaml`（新增，git-ignored）

- 已 accept 经验列表（问答偏好 / 工具使用习惯 / 领域知识）；ChatAgent 热读注入 system prompt。

---

## 4. 前端改动（`web/src/views/ChatEntry.vue`）

1. **错误 UX 修复**：`messages.value[idx] = {role:'assistant', content:'抱歉，调用失败：'+error}` 改为友好文案：
   - `HTTP 0` / 网络级失败 → 「⚠️ 网络错误，请刷新页面后重试」（`ApiError` 已带 `status===0` 判定）。
   - 后端 5xx → 「服务暂时不可用，请稍后重试」。
2. **工具调用可视化**：`ChatResponse.tool_calls` 非空时，在 assistant 气泡上方渲染一行「📡 自主调用工具：web_search · mcp:deepwiki:ask_question（共 N 次）」，让 boss 直观看到「他能自主调 MCP」。
3. 接入 `config/mcp_servers.yaml` 已注册 server（如 DeepWiki）后，无需改前端即可看到对应工具被调用。

---

## 5. 诚实边界 / 风险

1. **ChatAgent.step 同步 + `_run_async`**：kb_retrieve/kb_write 经 `_async_util.run_async` 在同步上下文跑事件循环；引擎 `engine_runner` 无事件循环冲突（api 进程独立循环）。实测验证。
2. **MCP 不可达**：`MCPClient.call_tool` 已返回 `{ok:False}`，`_run_fc_loop` 仅追加失败记录不阻断 → 降级，不冒充。
3. **知识层降级**：无 embedding → `kb_retrieve` 全文召回；`kb_write` embedding=NULL 仍可存。绝不伪造。
4. **进化不静默自改**：playbook 仅 accept 后注入；proposed 永不自动生效（D4）。
5. **generate_report 仍走意图路径**：长阻塞引擎任务不混入 `_run_fc_loop`（避免同步卡死）；保持现有异步触发。
6. **爆破面控制**：api.py `chat()` 调用点不变（仍 `agent.step(...)`）；仅 `ChatResponse` 加字段（向后兼容前端旧版）。

---

## 6. 验证计划（三道闸）

- **闸① 构建/编译**：`python -m py_compile chat_agent.py tools/chat_learning.py server/api.py`；`pytest tests/ -q`（新增 `test_chat_mcp_learning.py`：stub LLM 走 `_run_fc_loop` 调 web_search → 返回 tool_calls + 沉淀 chat_fact）；`vue-tsc --noEmit` + `vite build`。
- **闸② VERIFICATION_chat_mcp_learning.md**：自审 PASS；e2e：注册 DeepWiki MCP → 聊天问「用 deepwiki 查 langgraph 架构」→ 回复含 MCP 工具结果 + `tool_calls` 含 `mcp:deepwiki:ask_question`；再问同类问题 → `kb_retrieve` 召回首轮沉淀；accept 一条 lesson → 下轮 prompt 含该经验。
- **闸③ 独立子代理 REVIEW_chat_mcp_learning.md**：重点查诚实边界（MCP/KB 降级不冒充）、playbook 人工门禁、爆破面（api 调用点不变）。

---

## 7. 实施顺序（每步独立 commit，不 push）

1. `chat_agent.py` 升级（工具循环 + KB 召回/沉淀）+ `tools/chat_learning.py` + `ChatResponse` 加字段。
2. `config/chat_playbook.yaml` + `tools/chat_learning.py` playbook/accept + `server/api.py` `/admin/chat-lessons` 端点。
3. 前端 `ChatEntry.vue` 错误 UX + 工具调用可视化。
4. 单测 + 三道闸 + 部署（api+web 重建）→ boss 测 WebUI。

---

## 8. 与既有设计的边界

- 不改 `DESIGN_chat_first.md` 阶段 3（持久化）路线图，本设计在其阶段 2（工具）上扩展。
- 不重做 M11-2 引擎反思；仅把「经验 accept 门禁」范式复用到 chat（最小独立实现）。
- 不引入流式 WS（阶段 2 原设计的流式先不做，保持同步 `/chat`，先把「自主调工具 + 学习」跑通；流式为后续增强）。
