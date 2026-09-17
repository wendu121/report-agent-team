<!-- reviewed-by: 无 —— 本轮独立审议【未执行】，见下方 BLOCKED 说明；主代理未代签、不冒充已审。 -->
VERDICT: **BLOCKED** —— 独立审议未执行（API 429 限流）。**补审 PASS 前不 commit。**

> ⚠️ **本文件不是「通过证明」，而是「未闭环」的记录。**
>
> 独立审议子代理**三次**启动失败（**均未执行任何审议**，故本文件不含 reviewed-by 标记）：
> 1. 首次：`Failed to execute task ... Tool Agent not found in agent general-purpose`。
> 2. 重试（2026-09-16 00:33 前后）：**`429 您的使用量已超出频率限制`**，
>    **配额重置时间：2026-09-16 11:45:42（UTC+8）**。
> 3. boss 指示改用 **`Intern-S2-Preview-397B`** 模型重试（2026-09-16 00:40 前后）：
>    **`您已超过输入 tokens 配额`** —— 属同一配额窗口，尚未到重置时间。
>
> 三次失败均为**配额/工具层不可用**，与待审代码质量无关；**不得据此推定 PASS**。
>
> 依据治理硬约定（boss 2026-09-03 立约）：**门禁受阻时严禁主代理自审自签顶替**——
> 不得伪造 `<!-- reviewed-by: independent-subagent -->` 标记、不得改写结论冒充已审。
> 故本轮如实标记 BLOCKED；以下 3 个文件的改动**继续留在工作区，不提交**，待配额恢复后补审。

---

## 一、待审范围（未提交，工作区）

| 文件 | 增量 | 改动性质 |
|------|------|----------|
| `report-agent-team/tools/mcp_client.py` | +22 | 逻辑：MCP 操作加硬超时 |
| `report-agent-team/tools/skill_importer.py` | +5 | 逻辑/缺陷修复：`.git` 后缀剥离 |
| `report-agent-team/chat_agent.py` | +13 / −1 | **仅提示词**（无代码逻辑） |

合计 **3 files changed, 39 insertions(+), 1 deletion(-)**。
（`tools/web_fetch.py` + `orchestrator.py` 属上一批，已随 commit `2c6d658` 提交，
且彼时已由独立子代理出具 `REVIEW_T45_fix.md` = PASS_WITH_NITS，**不在本次待审范围**。）

### 各改动声称的意图（供审议者逐条证伪）
1. **mcp_client**：`list_tools`/`call_tool` 原先无任何超时，实测不可达的 deepwiki 干等 ~36s
   （13:46:37 step start → 13:47:13 列举失败），吃掉 ChatAgent step 300s 预算。
   新增 `_LIST_TIMEOUT`（env `MCP_LIST_TOOLS_TIMEOUT`，默认 15）/ `_CALL_TIMEOUT`（env `MCP_CALL_TIMEOUT`，默认 30），
   `_list_one`→`_list_one_inner`、`_call_one`→`_call_one_inner`，外层用 `asyncio.wait_for` 包裹。
2. **skill_importer**：`_github_raw_candidates` 原 `owner, repo = parts[0], parts[1]` 未剥 `.git`，
   致 `raw.githubusercontent.com/owner/repo.git/HEAD/SKILL.md` **必然 404**（boss 实测 3×404）。
   修复：仓库段去尾 `.git`。
3. **chat_agent（提示词）**：(a) `fetch_url` 描述由「GitHub 仓库自动取 README」改为「可抓**任意**
   http/https，含仓库内具体文件（blob/raw `.md` 直链）」——原措辞误导模型以为只能取 README；
   (b) 新增【诚实红线】：禁止谎称「无法访问 GitHub / 无法下载 / 架构限制」（它刚用 fetch_url
   抓完 README 却否认有此能力），并补充非标准 skill 仓库的装法（先 fetch_url 读具体 .md，
   再拿该文件 URL 装）。

---

## 二、补审待办（配额恢复后，交给独立子代理照此执行）

1. **核验 diff 与上述声明逐条一致**，确认无夹带、无回归。
2. **mcp_client**：
   - `asyncio.wait_for` 是否包在 async 方法内（而非包住同步的 `_run_async` 调用）；
   - Python 3.11+ 下 `asyncio.TimeoutError is TimeoutError`，确认 raise/catch 不会异常吞没；
   - 既有 300s 负缓存（TTL）是否仍生效，失败列举不会被每请求重试；
   - `_list_one_inner` / `_call_one_inner` 是否仍保留 `finally: await self._safe_close(session, ctx)`
     （成功与超时两条路径都要清理，防连接泄漏）。
3. **skill_importer**：`.git` 剥离**只作用于仓库段**，不得误伤路径/文件名中合法的 `.git`；
   至少验证三种 URL：`.git` 仓库根、普通仓库根、带 `.git` 的 `blob/<branch>/<path>.md`。
4. **chat_agent**：确认为纯提示词改动；新文案不得破坏「研报生成的 JSON 协议」，
   且不得与 SSRF/私有地址拒绝等既有诚实边界自相矛盾。
5. **跑测**：
   - `pytest tests/test_tools.py -q` → 期望 **57 passed**；
   - `pytest tests -k "mcp" -q --ignore=tests/test_api_boundary.py --ignore=tests/test_m12_admin_api.py` → 期望 **26 passed**；
   - 另有测试报 `ModuleNotFoundError: No module named 'ruamel'` / `'asyncpg'`
     ——**已确认为沙箱依赖缺失**（容器 requirements 里有），须由审议者复核并明示为环境问题、非回归。
6. **py_compile** 三个改动文件。

---

## 三、主代理已做的自审（**仅供参考，不构成通过、不计入门禁**）

- `py_compile`：3 个文件均 OK。
- `pytest tests/test_tools.py` → **57 passed**（零回归）。
- `pytest -k mcp`（排除上述两模块）→ **26 passed**。
- 手工验证 `_github_raw_candidates`：`agency-agents.git` → `.../agency-agents/HEAD/SKILL.md`（正确去 `.git`）；
  blob 直链亦能定位到具体文件。
- **5 条 skill 测试失败**已确认全为沙箱缺 `ruamel.yaml`（`import ruamel` 在本环境确实缺失），代码按设计 fail loud。

**结论：以上均为作者自证，未经独立审议，按约定不得作为过闸依据。**

---

## 四、残留风险（补审时应一并确认）

1. 单轮 LLM `timeout=90` + 1 次重试 ≈ 182s，而 `MCP_MAX_ROUNDS=4`，最坏仍可越过 300s step 上限——
   本次**未**改动此路径，待后续给整个 step 加「剩余预算」。
2. `_LIST_TIMEOUT=15` 较 LibreChat 的 MCP 默认 60s 更激进；若遇真实但极慢的 server 可能误判，
   且失败会进 300s 负缓存（该窗口内该 server 不可用）。已提供 env 覆盖，补审时请确认默认值是否合宜。
3. 容器对 `raw.githubusercontent.com` 的真实 egress 仍未被探针覆盖（原探针未测该主机）。
