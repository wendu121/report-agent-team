# VERIFICATION · M10-P2 keyless 数据源扩展（第二闸 / 自审）

**日期**：2026-09-10
**范围**：`tools/data_sources.py`、`config/plugins.yaml`、`DESIGN_PLUGIN_EXPANSION.md`（§2 Phase 2 方向修正）
**驱动**：boss 诉求「我放手，你给我弄好」+「不想一个一个去拿 key」→ 在**用户零 key** 前提下把引擎数据源层堆到自洽。

---

## 1. 目标与边界

| 项 | 内容 |
|---|---|
| 目标 | 新增 5 个 keyless 真源，扩大引擎零密钥检索覆盖（学术/百科/行情/新闻） |
| 硬约束 | 用户零 key；不伪造数据；失败源诚实标记不冒充；单源失败不阻塞任务 |
| 明确不做 | 不硬啃需授权的源（企查查/Wind/Bloomberg）；不做爬虫版源；不新增第三方依赖（stdlib urllib only） |
| 与 P2 原设计的差异 | 原 P2 为 alpha_vantage/finnhub/sec_edgar（需 key）→ **改为纯 keyless 源**，对齐 boss「零 key」诉求 |

---

## 2. 变更清单

### 2.1 `tools/data_sources.py`（+5 Provider）
| Provider id | 源 | 端点 | 解析 |
|---|---|---|---|
| `openalex` | OpenAlex 开放学术图谱 | `api.openalex.org/works` | JSON，`abstract_inverted_index` 反构摘要 |
| `europe_pmc` | Europe PMC（EBI） | `ebi.ac.uk/europepmc/.../search` | JSON，doi/pmid → URL |
| `wikidata` | Wikidata | `www.wikidata.org/w/api.php` | `wbsearchentities`，中文优先 |
| `tencent_quote` | 腾讯证券行情 | `smartbox.gtimg.cn/s3/` | `v_hint` 文本协议，`^`/`~` 分隔 |
| `gdelt` | GDELT 全球新闻 | `api.gdeltproject.org/api/v2/doc/doc` | JSON，ArtList |

均注册进 `KEYLESS_PROVIDERS`。`build_search_tool` 既有分支（`prov in KEYLESS_PROVIDERS` → 真 Provider，`has_real=True`）**零改动**即可命中——复用 M10-P1 已建立的扩展点。

### 2.2 `config/plugins.yaml`（+5 条目）
5 条目均 `auth_type: none` / `provider: <id>` / `enabled: true` / `builtin: true`。
`_recompute_connected` 对 `auth_type=none` 直接返回 `connected` → 引擎运行时视为已连接、真实取数。

---

## 3. 实测证据（本次真实执行）

### 3.1 端点连通性预探（写入代码前）
| 端点 | 结果 |
|---|---|
| openalex | ✅ 200，61KB，topkeys=[meta,results,group_by] |
| europe_pmc | ✅ 200，1.8KB，resultList |
| wikidata | ✅ 200（重试后；首次为代理 TLS 瞬时抖动） |
| gdelt | ✅ 200，articles |
| eastmoney search | ❌ 404（两路径均失效）→ **弃用** |
| tencent smartbox | ✅ 多匹配：`market~code~name~pinyin~type`，`^` 分隔 |

### 3.2 修复的真实 bug（非外部因素）
| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | OpenAlex HTTP 400 | `select` 含非法字段 `title_display`（OpenAlex 不暴露该字段供 select） | 去掉 `title_display`，取标题改用 `display_name`；二分定位确认（`select=display_name,title_display`→400） |

### 3.3 聚合端到端冒烟（build_search_tool 真实取数）
```
TOTAL 10  PER-SOURCE {tavily:2, arxiv:2, crossref:2, openalex:2, wikidata:2}
OK   sources: [arxiv, crossref, openalex, tavily, wikidata]
FAIL sources: [europe_pmc, gdelt, semantic_scholar, tencent_quote, wikipedia]
```
- 失败 5 源归类：`semantic_scholar`/`gdelt` = HTTP 429 限流；`wikipedia` = 代理 TLS 握手超时；`europe_pmc`/`tencent_quote` = 该 query 空结果。**均为外部因素或合理的空匹配，非代码缺陷**。
- 引擎 `search_many` 对单源异常已 `except ToolError → status.ok=False → continue`，不阻塞其余源（本次即验证：5 源 ok、5 源 fail，整体仍产出 10 条）。

### 3.4 单源定向复测
- `openalex.search("贵州茅台")` → 2 条，摘要反构成功（content len=136/115）。
- `tencent_quote.search("贵州茅台")` → 命中 sh600519 个股页。

### 3.5 全量回归
```
python3 -m pytest tests/ -q  →  41 passed, 0 failed, 2.09s
```
新增源未破坏任何既有测试（生产代码扩展点复用，测试文件零改动）。

---

## 4. 诚实边界（未验证/已知限制）

| 项 | 状态 |
|---|---|
| 容器内真实取数 | 待 Docker 重建 api 后复验（沙箱代理与容器出口不同，P1 经验：容器内更稳） |
| semantic_scholar 匿名池 | 100 req/5min，高频任务会 429；属源方限流，非缺陷 |
| wikipedia / wikidata / gdelt | 沙箱代理对其 TLS 不稳定；引擎容错已覆盖，容器内预期正常 |
| tencent_quote 仅检索不取行情数值 | 本 Provider 只做「名称→代码/个股页」定位；实时 K 线未接（可作 P2-b） |
| europe_pmc 英文医学为主 | 中文财经 query 常空匹配，属正常 |

---

## 5. 结论

**自审 PASS**。5 个 keyless 源代码就位、编译通过、单源复测通过、聚合冒烟 5 源真实产出、全量回归 41 passed。唯一代码级缺陷（OpenAlex select）已修并复验。剩余失败均为外部限流/网络因素，引擎容错机制已覆盖。

待第三闸（独立审议）通过后方可 commit。

---

## 6. 第三闸（独立审议）发现与修复（2026-09-10 补记）

独立子代理（`reviewed-by: independent-subagent`）产出 `REVIEW_M10-P2.md`，初判 **BLOCK**（BLOCK 1 / MAJOR 2 / MINOR 4 / NOTE 1）。**主代理自审存在漏检，独立审议找出真因**。修复如下：

| # | 严重度 | 发现 | 修复 | 复验 |
|---|---|---|---|---|
| 1 | **BLOCK** | 腾讯行情 `v_hint` 的 name 是 `\uXXXX` **字面量**，直接取用致 title 全量乱码（自审只核 URL 未核 title，漏检） | 新增 `TencentQuoteProvider._unescape_js()`，转义字面量→真字符；解码失败退回原文不抛错 | ✅ `search("贵州茅台")[0].title` = `贵州茅台 (sh600519)`（原为 `\u8d35\u5dde\u8305\u53f0`） |
| 2 | **MAJOR** | `search_many` 仅捕 `ToolError`，但 `json.loads`/`ET.fromstring` 抛原生异常 → 任一源 200 却非 JSON 时**击穿整个聚合**，全源结果丢失 | `_http_get_json` 包 `ToolError`；新增 `_parse_xml` 同语义；`ArxivProvider` 改用之 | ✅ 对 baidu.com（HTML）调 `_http_get_json` → 正确抛 `ToolError`（原 `JSONDecodeError` 击穿） |
| 3 | MAJOR→不实 | 自审"容错已覆盖"结论不成立（见 #2） | 同上，容错语义现真实成立 | ✅ |
| 4 | MINOR | OpenAlex `title_display` 死回退 + `relevance_score` 被 select 排除致 score 恒读 0.7 死代码 | 回退去 `title_display`；score 直接常量 0.7（去掉死读） | ✅ compile |
| 5 | MINOR | EuropePMC 摘要未去 HTML 标签 | 加 `re.sub(r"<[^>]+>", ...)` | ✅ compile |
| 6 | MINOR | `server/admin.py` `VALID_PROVIDERS` 未登记新源（P1 4 源亦遗漏）→ UI 配置新源报非法 | 改为从 `KEYLESS_PROVIDERS` 派生 + try/except 退化 | ✅ grep 确认 1366-1369 |
| 7 | NOTE | 注释称无匹配返 `v_pv_none_match`，实际为 `v_hint="N"` | 注释更正 | ✅ |

**未处置（诚实留痕）**：`PROVIDER_REGISTRY`（data_sources 的）为 M9-2 遗留死代码，非本次引入，未扩大变更面处理，记入待办。

**修复后复验汇总**：
- `py_compile` tools/data_sources.py + server/admin.py → OK
- 腾讯中文名解码 → ✅ PASS
- 非 JSON 容错 → ✅ ToolError 不击穿
- 全量 `pytest tests/ -q` → **41 passed**（修复未破坏既有测试）
- `VALID_PROVIDERS` 已含 5 新源

**结论：BLOCK 已解除**，待独立复审确认后 commit。

---

## 7. 容器端到端验收（2026-09-10 补记，commit 6dced2b + 31e854c）

api 镜像重建后在**容器内**实测（真实网络出口，比沙箱代理可靠）：

| 项 | 结果 |
|---|---|
| 插件状态 | 9 源 `connected`（tavily + 8 keyless 真源）/ `gdelt` `disconnected` / 4 源 `coming_soon` |
| 聚合实测 | 3 query（large language model / 贵州茅台 / 量子计算）→ **35 条真实结果**，**8 个源全部 ok** |
| 单源分布 | tavily 6 / crossref 6 / openalex 6 / wikidata 6 / wikipedia 4 / tencent_quote 3 / arxiv 2 / europe_pmc 2 |
| 腾讯中文名 | ✅ `贵州茅台 (sh600519)`，美股 ETF 亦可检索（`量子计算etf...`） |
| pytest | 41 passed |
| 热加载 | config bind mount，`enabled` 开关改后下一任务即生效（零重启） |

### 容器实测驱动的额外修正（commit 31e854c）
| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 8 | MAJOR | **GDELT 容器内 TLS handshake failure**（本地代理链干扰该域；沙箱内可通）→ 跑不通却挂 connected = 假配置 | 降级 `enabled: false` → status `coming_soon` |
| 9 | MINOR | `_recompute_connected` 未检查 `enabled`：关闭的源仍显示 `connected`，UI 误导 | `enabled=False → disconnected`（UI 显示"未连接"，语义诚实） |
| 10 | 防御纵深 | `search_many` 仅捕 `ToolError`，复审 NOTE-R1 指出裸异常仍可能击穿 | 增 `except Exception` 兜底 |

### 最终状态（诚实边界）
- **可用真源 9 个**（零 key）：tavily* + arxiv + semantic_scholar + crossref + wikipedia + openalex + europe_pmc + wikidata + tencent_quote
  （*tavily 需 key；其余 8 个真·零密钥）
- **`coming_soon` 5 个**：xueqiu（OAuth）/ cninfo / qcc（需授权）/ scholar / gdelt（本机网络不可达）
- **遗留技术债**：`PROVIDER_REGISTRY`（data_sources）死代码，M9-2 遗留，待清理


