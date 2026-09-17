# VERIFICATION · T45 聊天体只读抓取原语 fetch_url（「抓上网」）

> 门禁① py_compile + 门禁② 自审（本文件）。③ 独立审议见 `REVIEW_T45.md`（由独立子代理产出，主代理不自签）。

## 1. 需求对齐（boss 原话）
> 「他还是抓不到，为什么？……我需要得是他能抓上网，要不然怎么学习进步？」

**根因（生产日志 `docker compose logs api` 证实）**：chat agent 此前**没有「按 URL 抓正文」的原语**：
- `web_search` 是**多源关键词搜索**，对 `https://github.com/msitarzewski/agency-agents.git` 这类链接只返回不相关的学术命中（crossref/openalex），甚至 tavily 403、arxiv/semantic_scholar 429；
- `install_skill` 语义是「装 skill」，模型不会为「分析仓库」去调它；
- `run_shell` 的 `curl` 被沙箱危险命令拦截（`curl` 在 `_DANGEROUS` 列表），fetch 不到任何东西；
- `deepwiki` MCP server 容器里断开（`RemoteProtocolError: Server disconnected`），其 `read_wiki_structure`/`ask_question` 全失败降级。

→ 结论：agent **抓不到任意链接的正文**，只能「搜」不能「抓」。

## 2. 本次变更清单

| 文件 | 改动 | 性质 |
|------|------|------|
| `tools/web_fetch.py` | **新增**模块 `fetch_url(url, max_chars=120000)`：只读抓取任意 http/https 正文，不落盘不执行 | 新增能力 |
| `orchestrator.py` | `dispatch_tool` 新增 `fetch_url` 分支（调 `web_fetch.fetch_url`）；`_build_tool_schemas` 的 `ChatAgent` 块新增 `fetch_url` schema | 接线 |
| `chat_agent.py` | `CHAT_SYSTEM_PROMPT` 新增「抓上网」能力 + 工具列表 `fetch_url` + 强制动作②（给具体链接优先 `fetch_url` 而非 `web_search`/`curl`）；`_build_sources` 新增 `fetch_url` 结果来源派生 | 接线 + 提示词 |
| `tests/test_tools.py` | 新增 7 个 `test_web_fetch_*` + 更新 `test_chat_agent_only_schemas_exposed_to_chatagent` 含 `fetch_url` | 回归 |

## 3. 设计要点 / 诚实边界

- **与 `skill_importer.fetch_source` 的边界**：后者受 GitHub 域名白名单约束且强制 CLASSIFY/INSTALL 闸门（只服务「装 skill」）。`fetch_url` 是**只读研究抓取**，host 更宽松（任意可信 http/https），复用 `skill_importer.extract_main_text` 做 HTML 正文提炼；二者职责不混。
- **GitHub 仓库智能定位**：仓库根链接自动按 `HEAD/main/master` 尝试 `README.md`/`readme.md`/`README.rst`/`docs/README.md`；`blob/tree/<branch>/<path>` 直链定位 raw 文件（`.md/.txt/.rst`）；全失败回退抓仓库主页 HTML 提炼。→ 直接命中 boss 失败的 `agency-agents.git` 场景（取 README）。
- **SSRF 防护（best-effort，诚实声明）**：仅 http/https；`file://` 等拒绝；私有/环回/链路本地/保留地址（`127/8`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`、云元数据 `169.254.169.254`、`::1`、`fc00::/7`、`fe80::/10`、`localhost`、`*.local/*.internal`）经 IP 字面量 + `getaddrinfo` 解析双检拒绝；**重定向逐跳校验**——自定义 `_SSRFRedirectHandler` 对 3xx 跳转目标 host 重新校验，防止「初始 host 安全、重定向跳到内网/元数据」的绕过（独立审议 关注点-1 已修复）。**已知边界**：不承诺关闭 DNS-rebinding 窗口（`getaddrinfo` 解析失败放行，真实请求会自然失败）——已在模块 docstring 明示，非 OS 硬隔离。
- **失败诚实**：HTTP 错误/超时/SSRF 拒绝均返回 `{ok:False, error:"明确原因"}`，不静默吞错、不谎称成功；`_build_sources` 也会把 `final_url` 作为来源展示。
- **零回归**：引擎研究检索（Researcher/Analyst）**不暴露** `fetch_url`（仅 `ChatAgent` schema 含），与 T42 既有约束一致。

## 4. 验证证据

- 门禁① `py_compile`：`tools/web_fetch.py` `orchestrator.py` `chat_agent.py` `tests/test_tools.py` 全部通过（COMPILE_OK）。
- 门禁③ 前全量 suite：`223 passed`（较 T44 的 216 +7 个新 `fetch_url` 测试；schema 测试为更新非新增）。
- 单测覆盖：SSRF 拦截（4 种地址）、非 http 协议拒绝、缺参、GitHub 仓库 README 定位、GitHub blob 文件直链、网页正文提炼 + `<title>` 抽取、HTTP 错误明确原因。

## 5. 待 boss 侧真实验证（与 T40/T41 天气同协议）
本沙箱无出站网络/SSRF 约束，无法跑真实 `fetch_url("https://github.com/msitarzewski/agency-agents.git")`。
boss 重建容器（`docker compose up -d --build api`）后，WebUI 发「分析这个仓库 https://github.com/msitarzewski/agency-agents.git」即可真·取到 README 正文（日志应出现 `fetch_url` 调用、`source_type=github_readme`、非零 `reply_len`）。

## 6. 提交状态
- 已 commit（**未 push**，遵循「无 boss 显式推送指令不 push」铁律）。commit 待 `git log` 确认。
- 受影响文件：`tools/web_fetch.py`（新增）、`orchestrator.py`、`chat_agent.py`、`tests/test_tools.py`、本 VERIFICATION、REVIEW。
