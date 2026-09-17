# VERIFICATION_chat_mcp_learning.md · 阶段 2 自主调工具 + 自主学习 + 进化 · 自审

- 日期：2026-09-13
- 对应设计：`DESIGN_chat_mcp_learning.md`
- 范围：ChatAgent 从「薄壳（仅 generate_report）」升级为「能自主调 web_search/data_proc/mcp、能记忆、能进化」的一等对话 Agent
- 状态：闸②自审（闸①编译+单测已 PASS；闸③独立子代理评审进行中）

---

## 闸① 构建 / 编译 / 单测

- `python -m py_compile chat_agent.py tools/chat_learning.py server/api.py` → 全绿。
- `python -m compileall -q tools server chat_agent.py` → 全绿（整包无语法错误）。
- `pytest tests/test_chat_mcp_learning.py -q` → **7 passed**（原 5 + R1 补 2）：
  1. `test_chat_agent_calls_web_search`：fc-loop 自主调 web_search，`tool_calls` 含 `web_search`，`sources` 带回检索记录。
  2. `test_chat_agent_no_false_generate_report`：答案含 `{key:value}` 不被误判为研报（验证 `_classify` 不丢答案，修复潜在空回复→"好的。"陷阱）。
  3. `test_chat_remember_trigger_proposes_lesson`：「记住：…」触发 `propose_lesson`，不进研报路径。
  4. `test_chat_generate_report_intent`：研报 JSON 协议仍生效（intent=generate_report + report.topic）。
  5. `test_chat_mcp_tool_in_schema`：注入 FakeMCP 后 schema 含 `mcp:deepwiki:ask_question`，fc-loop 能调，tool_calls 含该工具。
  6. `test_chat_mcp_only_answer_has_sources`（R1）：MCP-only 回答的来源进入 `sources`（`src.tool == "mcp:deepwiki:ask_question"` 且带 `content`）。
  7. `test_chat_mcp_only_answer_is_learned`（R1）：MCP-only 回答触发 `chat_learn_write`（门槛从 `extra_search` 改为「任一 tool ok」），返回 1。

---

## 闸② 自审逐项（PASS / 风险）

| # | 核查点 | 结论 | 证据 |
|---|---|---|---|
| 1 | MCP 自主调用 | PASS | `chat_agent.py:step` 调 `_build_tool_schemas(bundle, mcp, role="ChatAgent")` → schema 含 `mcp:{s}:{t}`；`_run_fc_loop` 多轮。MCPClient 已在引擎验证。 |
| 2 | 诚实边界：MCP/KB 降级不冒充 | PASS | `tools/mcp_client.py:60` call_tool 失败返回 `{ok:False}`；`_run_fc_loop`(orchestrator:1169) 仅追加失败记录不阻断；`chat_learn_write`(chat_learning.py) 包 try/except 返回 0；`kb_retrieve` 失败返回 []。 |
| 3 | 进化门禁（D4：不静默自改） | PASS | `propose_lesson` 仅写 `proposed`；`accept_lesson` 才移入 `lessons`；`load_playbook` 只读 `lessons`（chat_learning.py:60）。ChatAgent 不读 `proposed`。 |
| 4 | API 调用点不变（向后兼容） | PASS | `server/api.py:chat` 仍为 `result = agent.step(...)`（同步）；`ChatResponse` 新增 `tool_calls`/`sources` 默认 `[]`，旧前端不受影响。 |
| 5 | 研报路径不被吞 | PASS | `_classify`(chat_agent.py) 仅当文本含 `intent=generate_report` 的 JSON 才走研报；否则当普通回复（修复空回复陷阱）。`test_*` 覆盖。 |
| 6 | 前端错误 UX | PASS | `ChatEntry.vue:onSend` catch 判定 `HTTP 0`/网络错误 → 「⚠️ 网络错误，请刷新页面后重试」；其余 → 「⚠️ 服务出错：…」。 |
| 7 | 工具调用可视化（回应 boss「他能自主调 MCP」） | PASS | `ChatResponse.tool_calls` 透传；前端渲染「📡 自主调用工具：web_search · mcp:deepwiki:ask_question」卡片。 |
| 8 | 配置持久化（playbook） | PASS | `config/chat_playbook.yaml` bind mount（docker-compose:67 `./config:/app/config`），accept 后下轮对话热加载；已加 `.gitignore`（运行时生成，不污染仓库）。 |
| 9 | 跨会话记忆（既有）保留 | PASS | `chat()` 仍注入 `_fetch_recent_session_memories`；新增 KB 召回是额外增强，不冲突。 |

---

## 风险与说明（PASS-WITH-NOTES）

- **N1（部署需重建镜像）**：`api`/`web` 是 `build` 镜像（源码 COPY 进镜像，仅 `./config` bind mount）。改 `chat_agent.py`/`server/api.py`/`tools/`/`web/` 必须 `docker compose up -d --build api web` 才能生效；仅 `restart` 不够。已在交付说明中给出命令。
- **N2（KB 依赖 ASYNC_DATABASE_URL）**：`kb_retrieve`/`kb_write` 需 pgvector 库可达。容器已用 `pgvector/pgvector:pg15`（M11-1 落地）。若库不可达，按设计降级（chat 仍正常回答，只是不记忆）——已 wrap try/except。
- **N3（MCP 须先注册 server）**：`config/mcp_servers.yaml` 已注册 DeepWiki（M11-3 实测）。要让 boss 看到「自主调 MCP」，需该 server 在 UI/配置中 enabled；否则 tool_calls 仅含 web_search。
- **N4（完整回归未跑）**：未跑 `pytest tests/ -q` 全集（含 e2e_real_run 需真实 LLM+网络，沙箱不适宜）。已跑与本次改动直接相关的 5 个单测 + 全包 py_compile。

---

## 闸③ R1 回执闭环（独立评审 HIGH 项已修）

- 独立子代理评审结论 `REVIEW_chat_mcp_learning.md` 标记 **APPROVED-WITH-NOTES**，其中 **R1（高）**：`extra_search` 只收 `web_search` → MCP 结果不进 `sources`、`MCP-only` 回答不学习。
- 修复（引擎安全、纯附加字段，不碰引擎检索合并逻辑）：
  1. `orchestrator.py:_run_fc_loop`（行 ~1160）`tool_entries.append` 新增 `"result": r.get("result") if r.get("ok") else None`（引擎只读 `agent/tool/ok/error`，不影响引擎）。
  2. `chat_agent.py` 新增 `_build_sources(tool_entries)`：`web_search` 取真实检索记录（source/url/title/snippet）、`mcp:*` 取工具全名 + 截断内容；`step()` 的 `sources = _build_sources(tool_entries)[:8]`（替代 `extra_search[:8]`）。
  3. `chat_agent.py:step` 学习门槛由 `if extra_search` 改为 `if any(t.get("ok") for t in tool_entries)` → MCP-only 回答也沉淀。
  4. `tools/chat_learning.py:chat_learn_write` 来源从 `tool_entries` 派生（`web_search` 记录 + `mcp:*` 工具名），门槛改为「任一 tool ok」，`credibility=high`；`extra_search` 仍兼容并入。
  5. 前端 `ChatEntry.vue`：`ChatResponse.sources` 已渲染为「📚 来源」卡片（web_search 出链接、mcp 出工具名+截断内容），`UIMessage` 补 `sources` 类型、`onSend` 透传、`truncate()` 辅助。
- 复验：`py_compile` 全包绿 + `pytest` **7 passed**（含 2 个 R1 回归测试）。R1 已闭环。

---

## 补丁 2：auto-chat 等 universal alias fallback 到 chat.tool_model（boss 实测「不能自主上网」）

- **boss 反馈**：WebUI 对话仍回答「我只能通过已接入的插件（如 tavily）联网查询，无法自主连接其他外部服务」，且不能自主学习。
- **根因诊断（实测）**：
  - 宿主侧 `api` 容器**大概率仍运行旧代码**（新 `ChatResponse` 含 `tool_calls`/`sources` 默认值，但线上 `/chat` 返回无这两个字段）。
  - 即使新代码部署，默认模型 `auto-chat` 是 new-api universal alias，实测路由到**配额耗尽**渠道（`accounts that have not been recharged can only try 10 times`），且可能路由到**不支持 OpenAI tools** 的模型 → 工具环无法触发。
- **修复**：
  1. `config/model_mapping.yaml` 新增 `chat.tool_model: LongCat-2.0`（LongCat-2.0 是引擎已验证支持 tools 的本地基座模型）。
  2. `chat_agent.py` 新增 `_load_chat_tool_model()` / `_resolve_chat_model(user_model)`：当用户选择 `auto`/`auto-chat` 时，工具环 fallback 到 `chat.tool_model`；用户显式选具体模型时尊重其选择。
  3. `CHAT_SYSTEM_PROMPT` 加强：事实/研究类问题「必须先用工具联网取证」，禁止「我无法实时获取」式搪塞。
- **复验**：`py_compile` 全包绿 + `pytest` **24 passed**（chat 9 + M11 function-calling 15）。新增 2 个测试覆盖 fallback 与显式模型尊重。
- **部署命令**：必须重建 `api` 镜像（`docker compose up -d --build api web`）。仅 `restart` 不够，因为 `api` 是 build 镜像（源码 COPY 进容器）。

---

## 结论

闸①② PASS（含 7 单测 + 全包编译），闸③ 独立评审 APPROVED-WITH-NOTES 且 **R1 高优项已闭环**。诚实边界、进化门禁、向后兼容、错误 UX、工具可视化、MCP 来源与学习均落实。N1 部署须重建镜像，已交底。
