# VERIFICATION_T45_fix.md · T45 候选循环超时 + fetch_url 留痕修复 自审

> 日期：2026-09-15 ｜ 关联 commit：T45 (`c4b4f8b`) 的后续修复 ｜ 门禁：edict-gate 三道闸第②道（自审）

## 1. 触发（实测，boss 在宿主 docker logs 给出）

```
report-api | 2026-09-15 12:42:48,270 INFO  [server.api] ChatAgent step start: ... msg_len=4469 history_len=6
report-api | 2026-09-15 12:47:48,255 ERROR [server.api] ChatAgent step timeout after 300s: ...
```

step 在 **12:42:48 → 12:47:48 正好 300s 超时**，且 `findstr fetch_url` **无任何 fetch_url 行**。
即：「分析 GitHub 仓库」那次**没成功**，step 被某个工具调用挂满 300s 预算。

## 2. 根因（两层，均定位）

### 根因①（致命）：GitHub 候选循环无总预算 → 正好 300s
`tools/web_fetch.py:_fetch_github` 对仓库根构造 **15 个 raw 候选**
（HEAD/main/master × README.md/readme.md/README.rst/README.txt/docs/README.md），
每个 `_http_get(timeout=20)`。若 `raw.githubusercontent.com` 在容器网络下呈现
「连得上但不回包」（防火墙典型行为，非 RST），每个候选挂满 20s → **15×20s = 300s**，
**正好等于 ChatAgent step 的 300s 超时上限**。这是 12:42 超时最直接的机制。

### 根因②（排查黑洞）：fetch_url 成败均静默
`orchestrator.dispatch_tool` 的 `fetch_url` 分支**仅在该工具抛「意外异常」时 warning**；
而 `fetch_url` 自身把 `WebFetchError` 捕获后返回 `{ok:False, error}`，并不抛出。
于是「模型调到了 fetch_url 但失败」与「模型根本没调 fetch_url」在日志里**毫无区别**
——这正是 boss 用 `findstr fetch_url` 看不到任何痕迹的根因。

## 3. 修改（3 文件）

- **`tools/web_fetch.py`**
  - 新增 `_GITHUB_PER_TIMEOUT = 10`、`_GITHUB_TOTAL_BUDGET = 45.0`。
  - `_fetch_github`：每轮先查 `time.monotonic() > deadline` 提前 `break`；单候选超时压到 10s；
    全部候选失败后 `raise last_err`（保留最近一次真实错误原因），主页回退同样受预算约束。
  - 语义：即使 raw.githubusercontent 全挂，也 **45s 内失败返回**，不再逼近 step 300s。
- **`orchestrator.py`**（`dispatch_tool` 的 `fetch_url` 分支）
  - 成功 → `logger.info("fetch_url(%s) -> ok type=%s len=%d", ...)`；
    受控失败 → `logger.warning("fetch_url(%s) 返回失败: %s", ...)`。**成败都留痕**。
- **`tests/test_tools.py`**
  - 新增 `test_web_fetch_github_loop_bounded_by_budget`：monkeypatch 把 `_http_get` 改成「sleep(timeout) 后抛错」
    （模拟防火墙 accept 不回包）+ 把预算压到 1s、单候选 0.3s，断言 `fetch_url` 在 **<3s 返回 ok=False**，
    而非无限逼近 300s。

## 4. 校验（实测）

- `py_compile tools/web_fetch.py orchestrator.py` → **OK**。
- `pytest tests/test_tools.py -q` → **57 passed in 2.64s**（含 8 个 web_fetch/schema 用例，新增回归用例通过）。
- 新增用例确定性证明：挂起式 `_http_get` 下，fetch_url 受预算约束返回，耗时远低于旧 300s。

## 5. 诚实边界（非推断，明确标注）

- **沙箱无法真实复现 raw.githubusercontent 挂起**：该主机从本沙箱超时（`TimeoutError`），
  而 `github.com`/`example.com` 可达；故用 monkeypatch 模拟，逻辑正确性由单测覆盖。
- **容器内 raw.githubusercontent 真实 egress 未被原探针覆盖**：probe_outbound.py 只测了
  open_meteo/duckduckgo/wikipedia/arxiv/tavily + 境内源，**没测 raw.githubusercontent.com**。
  生产是否可达需宿主实测；但本修复保证「即使不可达也 45s 内失败返回，不再拖垮 step」。
- **12:42 那次究竟是 fetch_url 候选循环挂起，还是模型改调 deepwiki MCP 挂起，旧日志无法区分**
  （见根因②静默问题）。部署本修复后，若再超时，日志必出现 `fetch_url` 行或 `mcp:`/`deepwiki` 行，
  可一锤定音定位。

## 6. 待办（部署侧，需 boss 在宿主）

1. `docker compose -f E:/第二电脑/report-agent-team/docker-compose.yml up -d --build api` 重建。
2. WebUI 重发「分析 https://github.com/...」验证 `source_type=github_readme`、非零 reply。
3. 若仍超时：`docker compose ... logs --since <t> api | findstr /I "fetch_url mcp deepwiki"` 定位是 fetch 还是 MCP 挂起。
