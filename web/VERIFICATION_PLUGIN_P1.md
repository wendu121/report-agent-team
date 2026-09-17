# VERIFICATION_PLUGIN_P1.md — P1 免费学术源自审（门禁②）

日期：2026-09-10 20:30 · 设计：DESIGN_PLUGIN_EXPANSION.md §2 Phase 1 · 改动：`tools/data_sources.py` + `config/plugins.yaml`

## 改动清单
1. `tools/data_sources.py`：
   - 新增 `_http_get/_http_get_json`（stdlib urllib，UA=report-agent-team/1.0，非 200 一律 ToolError）
   - 新增 4 个 keyless Provider：`ArxivProvider`（Atom XML→ET 解析）、`SemanticScholarProvider`、`CrossrefProvider`（JATS 去 XML 标签）、`WikipediaProvider`（zh MediaWiki）
   - 统一约定：`__init__(timeout=20)`；`search(query,max_results)` 返回 `[{title,url,content,score}]`；limit 上限 clamp 20
   - 新增 `KEYLESS_PROVIDERS` 注册表；`build_search_tool` 新增分支：`provider in KEYLESS_PROVIDERS` → 直连真源 + `has_real=True`
   - 语义不变式：coming_soon 跳过 / enabled=False 跳过 / tavily key 逻辑原样 / 未知 provider 跳过 —— 全部保留
2. `config/plugins.yaml`：arxiv 转 `provider: arxiv, enabled: true`；新增 semantic_scholar / crossref / wikipedia（均 auth_type=none, enabled）；xueqiu/cninfo/qcc/scholar 维持 coming_soon 不动

## 门禁①验证（实测留痕）
| 项 | 结果 |
|---|---|
| py_compile data_sources.py | EXIT=0 |
| yaml.safe_load plugins.yaml | 9 plugins 结构正确（5 真源 enabled + 4 coming_soon）|
| ArxivProvider 冒烟（'transformer attention'）| OK n=3「Dilated Neighborhood Attention Transformer」|
| CrossrefProvider 冒烟（'climate change economy'）| OK n=3「Climate Change Threatens $2 Trillion」|
| WikipediaProvider 冒烟（'量化交易'）| OK n=3「量化」（**须走代理**，见环境差异）|
| SemanticScholarProvider 冒烟 | 首测 429（上游匿名池瞬时限流）；复测 OK n=3「Efficient Memory Management for LLM Serving」|
| pytest tests/test_tools.py | **17 passed**（M9-2 分支逻辑无回归）|
| pytest 全量 | 30 passed + 11 failed——**11 失败为预存债**：全在 test_orchestrator/test_regression_edge（LLM retry），与 P1 文件零 import 依赖（grep 已证）；9-9 TD-009 时 41 全绿 → 失败源自 9-10 某 commit（疑 367c62d 多端点改造），**记入待还债，不阻塞 P1** |

## 环境差异诚实记录（重要）
- 本机沙箱注入 `HTTP(S)_PROXY=127.0.0.1:13131` 对 zh.wikipedia.org / semanticscholar 是黑洞（502）；经本机代理 30808 则通。
- **report-api 容器网络与沙箱不同**（无 13131 注入；Tavily 在容器内直连正常）。→ **部署后必须在容器内重冒烟 4 源**，若有源不通则该源回 `coming_soon` 或按需加代理配置，绝不假装可用。

## 已知限制（诚实边界）
- SemanticScholar 匿名池限流（429 常见）：ToolError → SearchTool 记 `ok:False` 优雅降级，不阻塞其它源。
- Wikipedia 走 `zh.wikipedia.org`：容器内如被网络策略拦截会显式失败（不静默）。
- Crossref `mailto` 用占位邮箱提限 polite pool；正式使用建议换成真实邮箱（可在后续配置化）。

## 🚨 环境事件（本时段重大插曲，详见会话报告）
`.git` 于 20:17/20:26 两度被删（pack+refs），已从 Windows 回收站 100% 原位恢复（fsck 0 错误），并快照备份至 `report-agent-team/.git-backup-20260910`。疑似 git stash/checkout 写操作触发沙箱 safe-delete 隔离。已规避 stash 类操作。
