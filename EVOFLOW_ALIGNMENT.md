# EVOFLOW_ALIGNMENT.md · EvoFlow 开源仓库参考分析 + 与 report-agent-team 对齐报告

- 日期：2026-09-12
- 状态：参考分析（供 boss 决策，**不写代码**；遵循铁律：先评审后实现）
- 输入：boss 给出 `https://github.com/EvovexAI/EvoFlow.git` 并说"我目前就是想做这样的"
- 研究方法：WebFetch/WebSearch 读 EvoFlow 仓库 + 文档；直接读本仓源码（file:line 实测）

---

## 0. 执行摘要（1 行）

boss 想做的"自主进化 agent 框架"，实质 = EvoFlow **产品**的真实能力（持久记忆 + 反思笔记 + 人工门禁经验），而 report-agent-team 已用更稳健架构（pgvector + 真·function-calling + MCP 桥）实现其中 **~80%**；唯一代码缺口是 **M11-2 反思回写闭环**（设计已就绪，待 boss 拍板 4 项决策即可编码）。

---

## 1. ⚠️ 先澄清：两个同名 "EvoFlow"（重要，避免被名字误导）

| | EvoFlow（产品） | EvoFlow（论文） |
|---|---|---|
| 出处 | `github.com/EvovexAI/EvoFlow` | arXiv 2502.07373（同济/CUHK/SHA Lab） |
| 实质 | LangGraph 原生 Agent Runtime + 控制面板（Tauri 桌面端） | 用生态进化算法（niching EA）进化 agentic workflow **种群** |
| 代码 | 本仓库 | `anonymous.4open.science/r/EvoFlow`（独立） |
| 首次提交 | 2026-09-12（极新） | 2025-02 |

**关键事实：产品仓库没有实现论文的进化算法。** 它的"自我进化"是营销标签，内核 = **Asset Center**（经验沉淀 + 反思日志 + 本地文件记忆）。

> 结论：若你要的是"真·自主进化"（种群/变异/交叉/适应度），应看**论文**而非此仓库；若你要的是"有记忆、能复盘、能积累经验、可干预进化"的 agent，本仓已对齐且更强。

---

## 2. EvoFlow（产品）能力速览（实测读源码/文档）

四层架构：`EvoPanel(GUI)` → `Gateway(FastAPI)` → `LangGraph runtime` → `Harness(evoflow.* 框架，与产品壳解耦)`。

- **记忆**：JSON 文件 `memory.json`（每自定义角色/线程隔离），结构 `workContext/personalContext/topOfMind/facts[]/history{}`；注入 system prompt 的 `<memory>` 标签，上限 **15 事实 / 2000 token**；`MemoryMiddleware` → `UpdateQueue`（30s 防抖、按线程去重）→ `MemoryUpdater`（LLM 抽事实，原子 temp+rename 写）。
- **知识库/RAG**：`sqlite-vec`（本地虚拟表余弦召回）+ `sentence-transformers`(BGE-Small) 嵌入（首次运行下载）+ `jieba` 中文分词；可选外部 memory provider。
- **反思**：**被动笔记**——用户说"记住…""把这套做法沉淀成经验""记一下今天的反思"，AI 写本地文件（Asset Center 的 Profile/Memory/Experience/Reflection 标签页）。**非自动化自评估、无指标、无变异、无人值守搜索。**
- **工具/MCP**：一等公民，`langchain-mcp-adapters`，懒加载（mtime 缓存失效）；ACP（Agent Client Protocol）、子 agent（`task` 工具，限 **3 并发**）、Skills(`SKILL.md`)、IM 渠道（飞书/微信/Slack/Telegram）、沙箱。
- **编排范式**：Lead Agent + 13 中间件；Plan 模式（可改计划、执行前确认）+ Goal 模式（长程后台自驱）+ 员工编排（值班 agent + 人工门禁审批）。
- **治理**：成本账本（token/$ 按模型/日）、可观测面板、安全中心（沙箱/终端策略/审计）、多账号/SSO。
- **许可**：**PolyForm Noncommercial 1.0.0**（商用需 `cloud@evovexai.com` 书面授权）；NOTICE 确认派生自 ByteDance **DeerFlow(MIT)**，部分文件仍 MIT。

---

## 3. 能力对齐表：EvoFlow vs report-agent-team（实测）

| 能力维度 | EvoFlow（产品） | report-agent-team（实测 file:line） | 对齐度 |
|---|---|---|---|
| 持久记忆（跨会话） | JSON 文件 `memory.json` | M11-1 `kb.entries` pgvector（`tools/kb_store.py`；`orchestrator.py:1780` `node_kb_retrieve` / `:1799` `node_kb_write`） | ✅ 更稳（服务端向量库 vs 文件） |
| 语义召回 | sqlite-vec + BGE-Small 本地 | new-api `/v1/embeddings`(BAAI/bge-m3,1024d) + pgvector HNSW cosine（`kb_store.py:41` `embed` / `:80` `kb_retrieve`） | ✅ 等价/更强 |
| 工具自主调用 | function-calling（Lead/子 agent） | M11-3 `_run_fc_loop`（`orchestrator.py:1129`）+ `_build_tool_schemas`（`:1083`）+ `build_tools`（`tools/__init__.py:32`） | ✅ 真·function-calling |
| 外部工具/MCP | 一等公民，懒加载 | M11-3 `MCPClient`（`tools/mcp_client.py:41`，`list_tools`/`call_tool`）+ `config/mcp_servers.yaml`（已注册 DeepWiki 实测 ok） | ✅ 等价 |
| 经验/技能沉淀 | Asset Center Experience/Reflection（本地文件，人工可编辑） | ChatAgent `propose_lesson`/`accept_lesson`（`tools/chat_learning.py:74`/`:108`）+ `config/chat_playbook.yaml`（热加载注入 system prompt，`chat_agent.py:252`） | ✅ 同"人工门禁才生效"范式 |
| 对话入口自动学习 | 闲时自动整理成 asset | ChatAgent `chat_learn_write`（`chat_learning.py:137`）→ chat_fact 落 kb（**任一工具 ok 即沉淀，MCP-only 也学**，R1 修复） | ✅ 更自动 |
| 反思/改进闭环 | 被动笔记（无自动应用） | **M11-2 设计就绪未编码**（`DESIGN_M11-2_reflection.md`：确定性规则引擎 + 人工 accept 回写白名单） | ⚠️ 设计就绪，缺实现 |
| 多 agent 编排 | Lead + 子 agent + 员工 | Router/Researcher/GateA/Analyst/GateB/Writer/GateC（LangGraph `StateGraph`，`DESIGN.md v2.4` SoT） | ✅ 我们更专精（研报） |
| 控制面板/UI | Tauri 桌面 EvoPanel | Vue3 WebUI（`web/`，chat 入口 + 设置/经验面板待补） | 🔶 我们偏 Web，功能面更窄 |
| 渠道/IM 集成 | 飞书/微信/Slack/Telegram | 无（仅 WebUI + `/chat` REST） | ❌ 未做（非研报核心） |
| 治理/成本账本 | 内建 | 部分（审计 `.audit/`、admin 端点） | 🔶 弱 |
| 自主变异 prompt/策略 | ❌ 无（产品不实装） | ❌ 无（诚实：仅人工 accept 改 yaml） | ✅ 一致（都不自改） |

---

## 4. 我们已有 vs 缺失（gap）

**已有（已 e2e / 容器内实测）**：M11-1 知识层、M11-3 MCP 桥、ChatAgent Stage 2（工具调用 + kb 召回 + chat 学习 + 经验草稿）、三道闸治理。

**缺失 / 未编码**：
1. **M11-2 反思回写闭环**（D1–D4 待 boss 拍板）——这是"进化"的诚实形态，目前只有设计。
2. **反思/经验可视化面板**（WebUI「反思建议」「经验」列表）——让"进化"可见、可干预（对齐 EvoFlow 卖点）。
3. 成本账本 / IM 渠道（非核心，可后置，避免过度工程）。

---

## 5. 关键差异与取舍（架构层）

- **存储**：我们 pgvector（服务端、并发安全、HNSW）vs EvoFlow JSON 文件 + sqlite-vec（轻量但分布式弱）→ **我们更适生产**。
- **进化本质**：两者都是"记忆 + 笔记 + 人工门禁"，**都非真进化算法**。EvoFlow 名不副实，我们不冒充 → 诚实一致，无落差。
- **范围**：EvoFlow 是"通用 Agent 操作系统"（聊天/计划/目标/员工/工作流/知识/MCP/IM/沙箱/治理全包，重）；我们是"研报专精多 Agent + 对话入口"，更聚焦、SoT 更硬、不贪大。
- **许可风险**：EvoFlow PolyForm 非商用；我们自研 MIT 友好栈，仅借鉴设计、不抄代码，无授权顾虑。

---

## 6. 落地建议（对照 boss 诉求"他能自主调 MCP + 自主学习 + 进化"）

1. **立即可用**：ChatAgent 已满足"自主调 MCP + 学习"——只差真实 LLM e2e 验证（被 new-api 网关 429/400 挡住，见 §9）。
2. **落地 M11-2**：把"进化"从设计变代码——`tools/reflection.py` + `node_reflect` + `/admin/reflections` 端点 + 前端面板。这是诚实的"反思→人工采纳→配置改进"闭环。
3. **补 Asset Center 可视化**：WebUI「经验/反思」合并面板，让进化可见、可干预（对齐 EvoFlow 核心卖点）。
4. **不碰**：真·进化算法（论文）、IM 渠道、成本账本（除非 boss 要），避免过度工程。

---

## 7. 待 boss 拍板的决策

来自 M11-2 设计（`DESIGN_M11-2_reflection.md` §12），我建议全部按设计采纳：

| # | 决策点 | 我的建议 |
|---|---|---|
| D1 | 生成器：确定性规则 vs LLM Reflector | **v1 确定性**（可验证/零幻觉/零成本）；LLM 版留 M11-2c |
| D2 | reflection 是否落 `kb.entries` | **不落**（防污染 Researcher 来源集，理由见设计 §5） |
| D3 | accept 白名单范围 | **仅 `set_enabled` 机械执行**；技能/prompt 全部"仅提示+人工" |
| D4 | 前端面板（M11-2b） | **做**（否则闭环不可见） |

基于 EvoFlow 对齐**新增**两项：

| # | 决策点 | 我的建议 |
|---|---|---|
| A1 | ChatAgent 经验草稿（`chat_playbook.yaml`）与 M11-2 reflections 是否合并到同一"Asset Center"UI 面板？ | **合并**，避免两个经验入口割裂 |
| A2 | 是否现在就编码 M11-2？ | 等 boss 确认 D1–D4 + A1 后我按设计编码（铁律：先评审） |

---

## 8. 实施顺序（设计已就绪）

1. boss 拍板 D1–D4 + A1/A2。
2. 编码 M11-2：`orchestrator.py` ~40 行（`node_reflect`）+ `tools/reflection.py` 新 + `admin.py` ~120 行 + 前端一屏（M11-2b）。
3. 三道闸：`py_compile` + `pytest`（94+8）+ `vue-tsc`/`vite build`；`VERIFICATION_M11-2.md`；独立审议（Intern-S2-Preview-397B）。
4. 合并 Asset Center 面板（A1）。
5. 待 new-api 网关 429（2026-09-13 14:32:10 UTC+8 重置）/400 解决后，跑通真实 LLM 工具调用 e2e（ChatAgent 调 MCP→`tool_calls`+`sources`+沉淀；记忆指令→proposed lesson；跨会话召回）。
6. **不 push**（铁律，等 boss 显式指令）。

---

## 9. 已知阻塞 / 风险（与 EvoFlow 无关，纯本仓）

- **new-api 网关 429**（频率限制，2026-09-13 14:32:10 UTC+8 重置）+ **400**（`input length too long`）：真实 LLM e2e 被挡。
  - 400 **已修（2026-09-12 本会话）**：`chat_agent.py` 加三道注入预算闸——`kb_ctx`(≤1200字/单条300)、`playbook`(≤1500)、`history`(≤2400/单条350，预算耗尽丢更早轮)，覆盖 `kb_ctx`+`playbook`+16 轮历史叠加超 `LongCat-2.0` 上下文的主因。`_run_fc_loop` 内部工具结果累积超长属次要残留风险，若复现再在 fc-loop 层加截断。
  - **`str` object has no attribute 'choices'（已修）**：`orchestrator._normalize_completion` 把 ChatCompletion/dict/str 归一化；网关回非 JSON 体时 `openai>=1.30` 当 str 返回，旧代码直接 `.choices` 在 str 上崩。str 先 try json.loads，失败当直答文本返回（不再崩）。
  - **new-api HTML 登录页（根因已锁 + 已修，2026-09-13）**：boss 重测"今天杭州天气怎么样"，ChatAgent 不崩但把 `<!doctype html>…<title>New API</title>` 仪表盘 SPA 当答案返回。根因 = `NEWAPI_BASE_URL` 漏写 `/v1`（仓库无 `.env`，值由 boss `.env` 注入；默认 `host.docker.internal:3000/v1` 正确）。OpenAI SDK 在 base_url 后拼 `/chat/completions` → `host:3000/chat/completions` 无路由 → new-api 返回 SPA 兜底 HTML。修复：三处消费点统一 `/v1` 兜底归一化——① `orchestrator.NewApiLLMClient._get_client`（raw 不 endswith /v1 则补 /v1，空值抛 LLMError）；② `tools/kb_store.py EMBED_URL`（新增 `_normalize_newapi_base`，防记忆层嵌入静默失效）；③ `server/admin.py _fetch_gateway_models`（/models 前补 /v1，防网关模型下拉静默退化）。py_compile 五文件（orchestrator/chat_agent/api/admin/kb_store）全绿 + 归一化逻辑独立验证三输入全 OK。**生效需重建 api 镜像**：`docker compose up -d --build api`。
  - 429 仍需待重置后跑真实 LLM e2e 验证（归一化修复让"漏 /v1"不再是干扰项）。
- **WebUI `POST /api/v1/chat` 返 499 / 前端「网络错误 HTTP 0」（已修，2026-09-14）**：`/chat` 是同步阻塞端点（`server/api.py:1651` 直接 `agent.step` 跑 LLM 多轮工具循环，`MCP_MAX_ROUNDS=4` × `complete_with_tools(max_retries=2, timeout=120s)`），真实链路 20~60s+；而前端 `web/src/api/client.ts:10` 仅 `timeout:30000` → 浏览器 30s 掐断 → nginx 记 499。修复：① 前端 axios `30000 → 600000`（10min）+ `chatService.ts` 调用显式 `{timeout:600000}` 双保险；② `server/api.py _get_chat_agent` 给 ChatAgent 专用 `NewApiLLMClient(max_retries=1, timeout=90)`，new-api 真卡顿时 ~3min 内返 500 而非干等。**生效需重建 api+web 镜像**：`docker compose up -d --build api web`。
- **Shell 环境损坏**：本会话 bash 运行时 shim 异常（`dirname`/`cd` 失败），已改用 Read/Glob 直读源码；命令行诊断受限，不阻塞设计分析。
- **许可注意**：若 boss 项目商用，EvoFlow 的 PolyForm 许可禁止复用其代码；本分析仅借鉴设计，本仓栈自研、无依赖。

---

## 10. 一句话结论

我们不是在"从零对齐 EvoFlow"，而是**已经用更稳的栈实现了它真实的那部分**；下一步不是抄它，而是把已设计好的 M11-2 落地 + 把"经验/反思"做成可见可干预的 Asset Center 面板。真·进化算法（论文）按需另议，不急。
