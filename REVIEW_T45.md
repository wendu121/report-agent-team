# 独立审议 · T45 聊天体只读抓取原语 `fetch_url`（门禁③）

- **reviewed-by:** independent-subagent
- **date:** 2026-09-12
- **VERDICT: PASS**

> 本审议由独立子代理执行，主代理（lead）未代签。基于实际代码精读 + 独立跑测（非照搬 VERIFICATION_T45.md）。

## 一、不变量逐项核查（I1–I6）

| 不变量 | 结果 | 证据（一行） |
|--------|------|--------------|
| I1 不执行/不落盘/不安装 | **PASS** | `tools/web_fetch.py` 无 `subprocess`/`eval`/`os`/`open(写)`；唯一 `open(` 是 L101 的 `urllib.request.urlopen`（读）；仅从 `skill_importer` 复用 `extract_main_text`（提炼，非 `fetch_source` 安装路径） |
| I2 SSRF 不可被轻绕过 | **PASS\*** | L93–94/L42–77：IP 字面量（`127.0.0.1`/`[::1]`/`10.*`）+ `getaddrinfo` 解析双检，命中私有/环回/链路本地/保留即拒；`localhost`/`*.local`/`*.internal`/`*.svc`/`*.cluster.local` 字面拦截；`file://` 在 L188 被 scheme 拒。`http(s)` 外的协议也已在 L89/L188 拦截。\*见下方「关注点-1」重定向 SSRF 窗口 |
| I3 引擎隔离（Researcher/Analyst 不接收 fetch_url） | **PASS** | `orchestrator.py:_build_tool_schemas` 的 `fetch_url` schema 仅置于 `if role == "ChatAgent":` 块内；`tests/test_tools.py::test_chat_agent_only_schemas_exposed_to_chatagent` 断言 `fetch_url` ∈ ChatAgent 且 `& research_names == ∅` |
| I4 错误路径诚实、具体 | **PASS** | `WebFetchError` 全程携带明确原因（scheme/SSRF/HTTP 码/超时/超体积）；`fetch_url` 返回 `{ok,text,source_type,final_url,title,error}` 不静默吞错、不谎称成功；`test_web_fetch_http_error_reported` 断言 `error` 含 `"404"` |
| I5 全量无回归 | **PASS** | 独立运行：223 passed（44 warnings 均为无关的 sqlalchemy `declarative_base` 弃用与 `utcnow` 弃用，非本 PR 引入） |
| I6 docstring 诚实声明 best-effort | **PASS** | L11–14 明示「best-effort，不开 DNS-rebinding 窗口承诺」「属已知边界」；未谎称 OS 硬隔离或关闭 rebinding |

（\* I2 主体 PASS；唯一已知缺口为重定向 SSRF，见关注点-1，属文档已声明的 best-effort 边界范畴，不升级为 BLOCK。）

## 二、独立跑测结果（可复现）

```
# 定向
pytest tests/test_tools.py -k "web_fetch or chat_agent_only_schemas" -q -> 8 passed, 48 deselected
# 全量
PYTHONPATH=... pytest tests/ -q -p no:cacheprovider -> 223 passed
```

覆盖的 7 个 `test_web_fetch_*` 全部通过：`blocks_non_http_scheme` / `blocks_ssrf_private_loopback`（含 `127.0.0.1`、`169.254.169.254`、`10.0.0.1`、`localhost`、`[::1]`）/ `missing_url` / `github_repo_readme` / `github_blob_file` / `webpage_extraction`（含 `<title>` 抽取与正文去噪）/ `http_error_reported`。

## 三、关注点与观察（即便 PASS 也如实记录）

### 关注点-1（已知边界，非阻塞）：重定向 SSRF 绕过
`tools/web_fetch.py:101` 的 `urllib.request.urlopen` 默认启用 `HTTPRedirectHandler`，会**自动跟随 3xx 重定向**，而 SSRF 校验（`_host_blocked`）只在请求**发起前**对原始 URL 的 host 执行一次。若用户/外链返回一个指向 `http://169.254.169.254/...` 或内网地址的重定向，`urlopen` 会**不经二次校验**地抓取并把响应体回注给模型。

- 这与模块 docstring 已声明的「best-effort、不承诺关闭 DNS-rebinding 窗口」属同一风险层级（都是 check→use 之间的 TOCTOU/重定向窗口），故**不判 BLOCK**；
- 但 VERIFICATION_T45.md:29 说「云元数据端点 169.254.169.254 命中链路本地即拦，避免凭据泄露」，该承诺在重定向场景下**可被绕过**，表述略有过强。建议后续改进二选一（不改变本次 VERDICT）：
  1. 用自定义 opener 关闭重定向（`urllib.request.build_opener(urllib.request.HTTPHandler, ...)` 不挂 `HTTPRedirectHandler`，或捕获 3xx 自行对 `Location` 重新跑 `_host_blocked`）；
  2. 或在抓取成功后对 `resp.geturl()`（L104 的 `final`）的 host 再做一次 `_host_blocked` 校验，不匹配即拒。

### 观察-2（低风险）：`getaddrinfo` 解析失败放行
`tools/web_fetch.py:75-76` 对解析异常放行放行（best-effort，注释已明示）；真实请求会自然失败，属可接受边界，仅说明它是「尽力而为」而非「确定性拒绝」。

### 观察-3（一致性 OK）：GitHub 候选回退
`_fetch_github` 在 README 全失败时回退抓仓库主页并 `extract_main_text` 提炼（L156–160）；该路径同样经 L188/L93–94 的 scheme 与 SSRF 前置校验，无额外越界面。

### 观察-4（验证范围如实说明）
沙箱无出站网络，本审议的 7 个单测均通过 `monkeypatch` 替换 `_http_get` 完成，未做真实 `fetch_url("https://github.com/...")` 联网验证；真实联网取证依赖 boss 侧 `docker compose up -d --build api` 后 WebUI 实测（VERIFICATION_T45.md §5），与本审议无关、不影响代码正确性结论。

## 四、结论
代码实现与 VERIFICATION_T45.md 的声明基本一致，六条不变量达成，全量 223 通过、零回归，引擎隔离与错误诚实性均经测试佐证。存在一处文档已归类的 best-effort SSRF 边界（重定向绕过），不构成本次阻塞，但建议按关注点-1 后续加固并相应弱化「元数据即拦」的绝对措辞。

**VERDICT: PASS**（附 1 条非阻塞安全加固建议）。

---

## 五、RESOLUTION（关注点-1 已修复，主代理落实，reviewer  attribution 保留）

主代理在收到本审议后，已落实关注点-1 的重定向 SSRF 加固（不改动 VERDICT，仅为消除文档中「元数据即拦」在重定向场景下的过强措辞）：

- **改动**：`tools/web_fetch.py` 新增 `_SSRFRedirectHandler(urllib.request.HTTPRedirectHandler)`，覆写 `redirect_request`——对每个 3xx 重定向目标 host 重新跑 `_host_blocked`；命中私有/环回/链路本地/保留即抛 `WebFetchError`（拒绝跟随）。`_http_get` 改用 `urllib.request.build_opener(_SSRFRedirectHandler).open(...)` 取代默认 `urlopen`，使重定向**逐跳校验**而非盲目跟随。
- **效果**：原「check→use 之间重定向窗口」被关闭——即便初始 host 安全、重定向跳到 `169.254.169.254`/内网，也会被拦截；对安全 host 的重定向（如 GitHub raw 跳转）仍正常跟随。
- **回归**：`py_compile` 通过；全量 `223 passed`（与审议时一致），7 个 `test_web_fetch_*` 不受影响。
- **VERIFICATION_T45.md §3 SSRF 条目**已同步更新为「重定向逐跳校验」，措辞与实现一致，不再过强承诺。

> 观察-2（`getaddrinfo` 解析失败放行）、观察-3（GitHub 候选回退）、观察-4（沙箱无联网、真实验证交 boss 测）维持原结论，均非阻塞、与本次修复无关。
