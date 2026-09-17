<!-- reviewed-by: 独立子代理 general-purpose-1-68e4（Claude/CodeBuddy 系静态代码审查）· 2026-09-13 -->
<!-- R1 状态更新（2026-09-13 后续修复）：R1 已在 commit 闭环——orchestrator.py 的 tool_entries 补 result 字段，chat_agent.py 新增 _build_sources 且学习门槛改为「任一 tool ok」，tools/chat_learning.py 来源从 tool_entries 派生，前端 ChatEntry.vue 渲染 sources。复验 py_compile 全包绿 + pytest 7 passed（含 2 个 R1 回归测试）。详见 VERIFICATION_chat_mcp_learning.md「闸③ R1 回执闭环」。 -->
<!-- 诚实声明：本审查为静态代码核查，未运行任何实时 LLM；为验证「诚实边界 / 类型透传」断言，
     额外读取了 orchestrator.py(_run_fc_loop/_build_tool_schemas)、tools/mcp_client.py、tools/_async_util.py、
     web/api/client.ts、web/api/errors.ts 作为佐证。测试用例据任务说明已在本地通过，本审查未重新执行 pytest。 -->

# REVIEW_chat_mcp_learning.md · ChatAgent 阶段 2（自主调工具 + 自主学习 + 进化）

## 概述

对照 `DESIGN_chat_mcp_learning.md` 的 7 项核查点，对 6 个文件做静态审查（含对引擎基础设施
`orchestrator.py` / `tools/mcp_client.py` / `tools/_async_util.py` 的佐证性查阅）。结论：
**主体实现符合设计、诚实边界与爆破面控制到位**，但发现 **1 个与「自主调 MCP + 自主学习」主诉求直接相关的实质缺口**
（MCP 工具结果未进 `sources`、且 MCP-only 回答不会被学习沉淀），以及若干中低风险项。
整体评级：**APPROVED-WITH-NOTES**（建议合入前修复 R1，其余可作为后续增强）。

---

## 逐项核查

### ① 诚实边界：MCP/KB 降级不冒充 —— PASS

- `kb_retrieve`：`chat_agent.py:160-176` 整体 `try/except`，异常时 `kb_ctx=""` 静默降级，**不影响回复**。
- `kb_write`（学习沉淀）：`chat_agent.py:216-219` 同样 `try/except` 包裹，**绝不阻断对话**。
- `MCPClient.call_tool`：`tools/mcp_client.py:61-70`，未启用/不存在/异常均返回 `{ok:False, error:...}`，无伪造结果（诚实边界铁律成立）。
- `_run_fc_loop`：`orchestrator.py:1169-1170` 工具失败时**仅追加 `{ok:False}` 记录并把 error 回注 prompt**（`summaries.append({"tool":name,"error":...})` → 行 1171 拼回 `conv_user`），LLM 可见失败、可诚实作答，**不冒充**。
- 额外佐证：`tools/_async_util.py:20-40` 的 `run_async` 在「已有运行中 loop」时改用**独立线程 + 新 event loop** 兜底（`chat()` 是 async FastAPI 处理器，step 内同步调 `run_async` 不会触发 "Cannot run the event loop while another loop is running"）。爆破面安全。

### ② 进化门禁（M11-2 D4）：playbook 仅在 accept 后生效 —— PASS

- `propose_lesson`：`tools/chat_learning.py:84` 仅 `data["proposed"].append(...)`，**绝不写入 `lessons`**。
- `load_playbook`：`tools/chat_learning.py:58` 只读 `data.get("lessons")`，**完全不读 `proposed`** → proposed 永不自动注入 prompt。
- `accept_lesson`：`tools/chat_learning.py:108-131` 是**唯一**把 proposed→lessons 的路径（按 text 匹配）；全仓检索确认无其它代码把 proposed 注入 system prompt。
- `ChatAgent`「记住：」分支：`chat_agent.py:142-153` 调 `propose_lesson` 并返回诚实文案「待管理员在『设置 → 经验』中采纳后生效」，**无静默自改 prompt/代码**。
- 偏离（非阻断）：设计 §3.2 写的是 `POST /admin/chat-lessons/{id}/accept`，实现为 `POST /admin/chat-lessons/accept`（body `{text}`，按 text 采纳，`server/api.py:1821-1827`）。功能等价，仅接口形态与设计文档不一致，建议同步文档。

### ③ API 向后兼容：`chat()` 调用点不变 —— PASS

- `chat()` 调用点：`server/api.py:1706` 仍为 `result = agent.step(history=..., user_msg=..., model=...)`，**同步、未改 await**，与阶段 1 一致。
- `ChatResponse` 新字段默认空：`server/api.py:929`（`tool_calls: List[str] = []`）、`:931`（`sources: List[dict] = []`）。旧前端忽略未知字段，不受影响。
- `step()` 在「记住：」分支返回的字典未含 `sources` 键，`api.py:1725` 以 `result.get("sources", []) or []` 兜底，OK。

### ④ 工具调用透传 + 类型 —— PASS-WITH-NOTES（tool_calls 通过；sources 存在实质缺口）

- **tool_calls（通过）**：`chat_agent.py:221` `[t.get("tool") for t in tool_entries]` → `List[str]`；`api.py:1724,1798` 透传；`chatService.ts:23` 类型 `tool_calls?: string[]`；`ChatEntry.vue:112-114` 渲染「📡 自主调用工具：…」。链路完整、类型一致。
- **sources（缺口）**：`chat_agent.py:226` `sources = extra_search[:8]`。但 `extra_search` 仅在 `orchestrator.py:1162-1163` 的 `if name == "web_search"` 分支被 `extend`；**`mcp:*` 与 `data_proc` 的结果在 `orchestrator.py:1165-1168` 的 else 分支只进 `summaries`（局部变量），不进 `extra_search`**。
  - 结果：MCP 工具调用时 `tool_calls` 会显示工具名（可见，满足「让 boss 直观看到他能自主调 MCP」），但 **`sources` 为空** —— 设计 `DESIGN_chat_mcp_learning.md §2` 明确写「sources：工具产出的真实素材（检索记录 / MCP 结果）」未被满足。
  - 连带影响（见 R1）：`kb_write` 的沉淀门槛 `chat_agent.py:215` `if intent == "chat" and extra_search and reply` 依赖 `extra_search`，**MCP-only 的回答永远不会被沉淀为 chat_fact** —— 直接削弱「自主学习」对 MCP 这一头条能力。
  - 类型本身正确（`List[dict]`，透传机制正确），缺口是**内容**而非类型。

### ⑤ generate_report 路径未被 fc-loop 吞掉 —— PASS

- `chat_agent.py:95-106` `_classify` 从 fc-loop 终态 `raw` 判意图：仅当文本为含 `intent=generate_report` 的 JSON 才走研报路径，否则当普通回复；`_safe_parse`（`chat_agent.py:78-91`）做 `topic>=4` 校验。
- `api.py:1728` 触发 `create_task`（异步建任务、引擎子进程后台跑），与 `chat_agent.py` 注释 §0「长阻塞任务不混入 fc-loop」一致。无回归。
- 注：`_run_fc_loop` 用 `shape="researcher"`（`chat_agent.py:204`），依赖 `NewApiLLMClient.complete_with_tools`（引擎已验证基础设施），不在本审查范围，仅记录。

### ⑥ 错误 UX：网络错误友好文案 —— PASS（含 1 处与设计文案偏差）

- 链路核实：`web/api/client.ts:24-28` 网络失败 → `ApiError(status=0, {})`；`web/api/errors.ts:10` 构造 `message = body.message || body.error || "HTTP ${status}"` → **"HTTP 0"**。
- `ChatEntry.vue:453-467`：`m.includes('HTTP 0')` 命中 → 显示「⚠️ 网络错误，请刷新页面后重试。」。**实测可达**（此前担心的 axios "Network Error" 文案问题不存在，因为 ApiError 统一输出 "HTTP 0"）。
- 偏差（非阻断）：设计 §4.1 要求 5xx →「服务暂时不可用，请稍后重试」；实现为 `⚠️ 服务出错：{m}`（`ChatEntry.vue:464`）。文案不同但已友好化，建议按设计统一。

### ⑦ 测试覆盖 —— PASS-WITH-NOTES

5 个单测覆盖：web_search 调用+sources（`test_chat_mcp_learning.py:51-58`）、非研报误判（`61-67`）、记住→propose（`70-82`）、generate_report 意图（`85-93`）、MCP schema/调用（`96-112`）。
**缺口**：
- KB 召回（`kb_retrieve`）路径仅「跑过」、未被断言（被 `try/except` 包住，依赖 DB/loop，降级无断言）。
- 「accept 经验 → 下轮 prompt 注入 playbook」**无测试**（load_playbook 与 accept_lesson 的端到端链路未覆盖）。
- **MCP sources 缺口未被任何测试捕获**（见④）——该测试 `test_chat_mcp_tool_in_schema` 只断言 `tool_calls` 含 mcp 名，未断言 `sources`，故④的缺口能溜过 CI。
- 两个 admin 端点（`/admin/chat-lessons`、`/accept`）无接口测试。
- 前端错误 UX 无单测。

---

## 风险与建议

### R1（高，建议合入前修）— MCP 结果不进 sources、MCP-only 回答不学习
- 根因：`orchestrator._run_fc_loop` 的 `extra_search` 仅收集 `web_search` 结果（`orchestrator.py:1162`），MCP/DataProc 结果只进局部 `summaries`。`ChatAgent` 拿不到 MCP 结果，既无法在 `sources` 展示，也因 `extra_search` 为空跳过了 `kb_write`（`chat_agent.py:215`）。
- 建议（二选一，尽量小爆破面）：
  1. 让 `_run_fc_loop` 在 MCP/data_proc `ok=True` 时**也把结果并入 `extra_search`**（注意：引擎 `make_agent` 也会消费 `extra_search` 做 `merge_search_results`，需确认不会重复/污染检索集——可能有波及，需评估）；**或**
  2. 更安全：`_run_fc_loop` 在 `tool_entries` 中补一个 `result` 字段（`{"agent","tool","ok","error","result"}`，`orchestrator.py:1156-1160`），`ChatAgent.step` 据此构造 `sources`（含 MCP 结果）并把 `kb_write` 门槛从 `extra_search` 改为「`tool_entries` 中任一 `ok=True`」（`chat_agent.py:215`）。方案 2 不碰引擎检索合并逻辑，推荐。
- 影响：boss 诉求「他能自主调 MCP + 能进化/学习」中，可见性（tool_calls 已有）OK，但「学习/溯源」对 MCP 暂未闭环。

### R2（低）— playbook yaml 原子写缺并发保护
`tools/chat_learning.py:30-49` `_write_yaml_atomic` 用固定 `.tmp` 后缀（无 PID/时间戳），与 `server/api.py:996-1013` 的 admin 版本不同。管理员并发 accept 可能竞争同一 `.tmp`。频率低，建议加 PID 后缀（对齐 admin 实现）。

### R3（低）— KB 召回按「记忆增强后的 user_msg」检索
`server/api.py:1704` `user_msg_for_agent = request.message + memory_prompt`，随后 `chat_agent.py:163` `kb_retrieve(user_text)` 用的是增强后文本。检索 query 含「【你可能感兴趣的上下文…】」噪声，略降召回质量。建议 `kb_retrieve` 用原始 `request.message`，或剥离 memory 段。

### R4（低）— load_playbook 每次请求读盘
`chat_agent.py:178` 每次 `step` 都 `load_playbook()`（读+解析 yaml）。chat 高频场景下有微小 I/O 开销，可接受；如需可加进程内缓存（注意 accept 后要失效）。

### R5（信息）— 「记住：」解析边界
`chat_agent.py:143` 双 split 对「记住：：：x」会得到含冒号残片，无害；功能正确。

---

## 结论

**APPROVED-WITH-NOTES**

- 诚实边界（①）、进化门禁（②）、API 兼容（③）、generate_report 路径（⑤）、网络错误 UX（⑥）均通过，爆破面控制到位，无静默自改 prompt/代码，MCP/KB 降级不冒充。
- 需关注：**R1（MCP 结果未进 sources、MCP-only 回答不沉淀）** 是与本特性主诉求直接相关的实质缺口，建议合入前以 R1 方案 2 修复；④ 的 sources 缺口与 R1 同源。
- 中低项 R2–R4 可后续增强；⑦ 测试建议补「accept→prompt 注入」「MCP sources」「admin 端点」三项以闭环覆盖（尤其需补一条断言把 R1 锁住，避免回归）。
- 总体可合入，但 R1 修复后会更贴合 boss 的「自主调 MCP + 学习 + 进化」预期。
