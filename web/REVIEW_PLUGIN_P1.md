<!-- reviewed-by: independent-subagent -->

# REVIEW_PLUGIN_P1.md — P1 免费学术源扩展独立审议（门禁③）

日期：2026-09-10 · 改动范围：`tools/data_sources.py` + `config/plugins.yaml`
设计依据：DESIGN_PLUGIN_EXPANSION.md §2 Phase 1

---

## Summary

本次 P1 改动在 M9-2 数据源插件架构上新增 4 个 keyless 真源 provider（arxiv / semantic_scholar / crossref / wikipedia），把市场从 1 真源（tavily）扩到 5 真源。改动集中在 `tools/data_sources.py`（+4 Provider 类 + KEYLESS_PROVIDERS 注册表 + build_search_tool 新分支 + _recompute_connected 扩展 + 两个 urllib 辅助）和 `config/plugins.yaml`（arxiv 转真源 + 3 新条目，4 个 coming_soon 维持不动）。

## Verdict

**PASS_WITH_NOTES**

无 MAJOR 阻塞项。代码与 DESIGN §2 Phase 1 一致，返回结构、build_search_tool 分支、降级逻辑、连接状态重算均正确，无密钥硬编码、无第三方依赖引入。备注（非阻塞）见 MINOR 与 Gaps。

## MAJOR

无。

## MINOR

1. **`WikipediaProvider.__init__` 冗余 `lang` 参数**：构造时 `self.lang` 被赋值，但 `search()` 硬编码用 `self.lang` 拼接 URL（data_sources.py:229），实际语言由 `__init__` 参数决定。当前用法默认 zh，无 bug；但若未来配置化 `lang`，需确认 `ENDPOINT` 常量（line 213 硬编码 zh）与 `lang` 参数保持一致——目前 line 213 的 `ENDPOINT` 未被使用（search 重新拼接），属于死代码（MINOR 外观债）。

2. **`CrossrefProvider` 的 `mailto` 占位邮箱**（line 180）：`report-agent@example.com` 是占位值，polite pool 提限效果有限。DESIGN 已注明正式使用建议换真实邮箱——记为配置化待办，不阻塞 P1。

3. **`SemanticScholarProvider` 无重试/退避**：匿名池 100 req/5min 限流，当前 `_http_get_json` 遇 429 直接 ToolError。SearchTool 已有降级（ok:False），但频繁 429 会浪费检索周期。可在 provider 层加简单 `time.sleep` 重试（MINOR 健壮性债），或接受降级——DESIGN 已知情（VERIFICATION 行 31）。

## Gaps

1. **容器内连通性未测**：沙箱代理 13131 对 semanticscholar.org / zh.wikipedia.org 是黑洞（502）；本机代理 30808 可通 wikipedia 但 SSL 握手仍偶发超时。semantic_scholar 上游持续返回 429（本时段 3 次重试均失败）。**部署后必须在 report-api 容器内重冒烟 4 源**；若有源不通则该源回 `coming_soon` 或按需加代理配置，绝不假装可用。此 GAP 已知，与 VERIFICATION_PLUGIN_P1.md 行 26-28 一致。

2. **wikipedia SSL 握手超时**：本时段经 13131 和 30808 代理均超时，但直连 zh.wikipedia.org 成功（见实跑记录）。属网络环境差异，非代码问题。

## 实读确认（真实 file:line）

以下全部基于实际 Read 工具读取：

- **DataSourcePlugin Protocol**（data_sources.py:28-37）：`search(query, max_results) -> list[dict]`，要求 title/url/content/score。4 个 Provider 的 return 均满足——
  - ArxivProvider search (data_sources.py:115-137)：返回 `[{title, url, content: summary[:500], score: 0.8}]`，line 131-136。
  - SemanticScholarProvider search (data_sources.py:152-172)：返回 `[{title, url, content: year+abstract, score: 0.8}]`，line 166-171。
  - CrossrefProvider search (data_sources.py:188-207)：返回 `[{title, url, content: journal+abstract[:500] (JATS 去标签), score: 0.75}]`，line 206。
  - WikipediaProvider search (data_sources.py:223-243)：返回 `[{title, url, content: snippet (去 HTML 标签), score: 0.7}]`，line 237-242。
  所有 key 均存在，无缺失。

- **build_search_tool 新分支**（data_sources.py:397-437）：
  - KEYLESS_PROVIDERS 命中（line 429-432）：`providers.append((s, KEYLESS_PROVIDERS[prov](timeout=timeout))); has_real = True` → `has_real=True` 正确。
  - coming_soon 跳过（line 414-415）、enabled=False 跳过（line 411-412）、未知 provider 跳过（line 433-435）——守卫未被破坏。
  - Tavily key 缺失 → MockProvider（line 417-426）原样保留。

- **_recompute_connected**（data_sources.py:296-311）：
  - `auth_type=none` → `return "connected"`（line 306-307）——与 DESIGN §3.1 一致，UI/引擎状态诚实。
  - coming_soon → coming_soon（line 303-304）；api_key 有 key → connected / 无 key → disconnected（line 308-309）；其它 → disconnected（line 310）。

- **SearchTool.search_many 降级**（data_sources.py:360-394）：
  - `except ToolError` 捕获单源异常 → `status.append({...ok:False...})` + `continue`（line 370-373），不影响其它源。
  - 空结果同样记 ok:False（line 374-377）。

- **无密钥硬编码、无第三方依赖**（data_sources.py:12-25）：import 仅为 stdlib（json, os, re, urllib.*, xml.etree.ElementTree, pathlib, typing）+ 同包 ToolError / web_search。密钥读取走 env + .secrets/plugins.env（line 271-293），config/plugins.yaml 无密钥（实读 plugins.yaml 全文确认）。

## 实跑命令与退出码

所有命令均在 `E:/第二电脑/report-agent-team` 目录执行，python 为 `C:/Users/sfkj/.workbuddy/binaries/python/versions/3.13.12/python.exe`。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m py_compile tools/data_sources.py` | 0 | 编译通过 |
| `python -c "yaml.safe_load + 9 plugins 断言"` | 0 | 9 plugins 结构正确：5 真源 enabled（arxiv/semantic_scholar/crossref/wikipedia + tavily），4 coming_soon（xueqiu/cninfo/qcc/scholar） |
| `python -m pytest tests/test_tools.py -v` | 0 | 17 passed（M9-2 分支逻辑无回归） |
| `python -c "ArxivProvider.search('transformer attention', 3)"` | 0 | n=3，title 真实：「Dilated Neighborhood Attention Transformer」等 |
| `python -c "WikipediaProvider.search('量化交易', 3)"`（直连） | 0 | n=3，title 真实：「量化」「高頻交易」「量化寬鬆」 |
| `python -c "CrossrefProvider.search('climate change economy', 3)"` | 0 | n=3，title 真实：「Climate Change Threatens $2 Trillion 'Blue Economy」等 |
| `python -c "SemanticScholarProvider.search(...)"` | 0（代码 OK）但上游返回 429 | 3 次重试均 HTTP 429——上游匿名池限流，非代码缺陷；SearchTool 降级路径已验证 |
| `python -c "_recompute_connected 全分支"` | 0 | none→connected, coming_soon→coming_soon, api_key+key→connected, api_key+nokey→disconnected, oauth→disconnected |
| `python -c "build_search_tool + KEYLESS_PROVIDERS + 降级"` | 0 | 5 providers 实例化（4 keyless + tavily mock），has_real=True；单源 ToolError 降级 ok:False |

---

**裁决：PASS_WITH_NOTES**（MINOR 3 项为外观/健壮性债，Gaps 2 项为环境差异待部署后验证）。