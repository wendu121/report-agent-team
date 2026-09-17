# REVIEW · M12-diag 独立审议（含第 2 轮复核）

> **本文含三轮。**
> - **最终（第 3 轮）总判定：PASS_WITH_NITS（BLOCKER 0 / MAJOR 0）** —— 详见文末「第 3 轮复核（`3efd377` · 追加）」。
> - 小节按「时间倒序」阅读更顺：第 3 轮（最新，文末追加）→ 第 2 轮 → 第 1 轮（存档）。

---

# 第 2 轮复核（d6ce1f2 + 51c553b）

- 复核对象：`d6ce1f2`（修第 1 轮两条 MAJOR）、`51c553b`（自修 `d6ce1f2` 引入的「裸字『度』」回归）。**当前 HEAD = `51c553b`**，非 `d6ce1f2`。
- 方式：读码 + `git show` 比对 + 本地实测（Python 3.13.12，monkeypatch 打桩，未改业务代码）+ 全量 pytest。
- 全量测试：**123 passed / 4.78s**（沙箱缺 `asyncpg`/`mcp`，已按约定 `--ignore=tests/test_api_boundary.py`）。

## 第 2 轮结论

**总判定：MAJOR**（BLOCKER 0 / **MAJOR 2 条** / NIT 5 条）

- 第 1 轮两条 MAJOR **均已实质修好并验证通过**（详见下方「第 2 轮已验证」）。`d6ce1f2` 引入的「裸字『度』把『印度』切成『印』」回归，已被 `51c553b` 自修并经我实测确认（含 3 条 `印度` 反例 + `深度学习最新进展 → None`）。
- 但本轮实测**又抓到 2 条 MAJOR**：
  - **M-3**：`987048f` 引入、**至今仍在**的「日期单位误伤地名」——`日本天气 → 本`、`周口天气 → 口`、`日照天气 → 照`、`日喀则天气 → 喀则`。`d6ce1f2` 把这段从「左侧清洗」搬到「整句清洗」，问题原样保留。
  - **M-4**：`987048f` 的 zh→en geocoding 回退把 `/chat` 天气链路最坏耗时从 ~124s 抬到 **~185s**（已越过 boss 反馈的 2 分钟线）。这是 RSS 修好后 `/chat` 最坏耗时的**头号贡献者**。

## 第 2 轮发现清单

### M-3【MAJOR】日期单位清洗误伤含「日 / 周」的合法地名（`987048f` 引入，两轮修复均未触及）

- 位置：`report-agent-team/tools/data_sources.py:1019`
  ```python
  q = re.sub(r"(年|月|日|号|时|点|分|周|星期)", "", q)
  ```
- 问题：无条件删除**裸字**日期单位，而 `日`/`周` 是常见地名首字。实测（直接调 `OpenMeteoProvider._extract_location`）：

  | query | 抽取结果 | 期望 | 说明 |
  |---|---|---|---|
  | `日本天气` | `本` | `日本` | 国家 |
  | `日本明天天气` | `本` | `日本` | |
  | `周口天气` | `口` | `周口` | 河南地级市 |
  | `日照天气` | `照` | `日照` | 山东地级市 |
  | `日喀则天气` | `喀则` | `日喀则` | 西藏地级市 |

  后果与 M-2 完全相同：geocoding 拿 `本`/`口`/`照` 去查 → 落空 → `search()` 返回 `[]` → 天气静默消失 + 模型误报「未能获取天气」。
- 归因为你比我上轮描述的还多一层：上轮我只说「噪声表漏否定/疑问词」，没抓到这条；`d6ce1f2` 新增的 20 条参数化用例**也没有覆盖 `日/周` 开头的地名**，所以搬移时原样带过来了。
- 建议改法（日期单位只在**紧跟数字**时剥离，不再无条件删裸字）：
  ```python
  q = re.sub(r"\d+\s*(?:年|月|日|号|时|点|分|周|星期)", "", q)   # 先吃「带数字的日期单位」
  q = re.sub(r"[\d]+", "", q)                                    # 再吃裸数字
  ```
  验证：`2025年3月厦门天气` → 两步后为 `厦门天气` ✓；`3月15号天气` → `天气` ✓；`日照天气` → 不变 ✓；`日本天气` → 不变 ✓。
- 建议同步把这 5 条补进 `tests/test_m12_review_fixes.py:23-46` 的 `LOCATION_CASES`，否则下次还会搬丢。

### M-4【MAJOR】zh→en geocoding 回退使 `/chat` 天气链路最坏耗时 ~185s（越过 2 分钟线）

- 位置：`report-agent-team/tools/data_sources.py:949-964`（`for lang in ("zh","en")` + `:951` `_http_get_json_retry(..., self.timeout)`）、`:979`（forecast 同样 `attempts=3`）
- 关键：`data_sources.py:955-958` 的 `except ToolError: ... continue` —— **网络类失败也会继续跑到 en 分支**，于是「网络不通」这一最常见故障场景反而是最慢的。
- 静态推算（`_http_get_json_retry` 默认 `attempts=3`、`backoff=0.6` → 每次 20s、退避 0.6+1.2s；`timeout` 由 `build_search_tool` 传入 = 20）：

  | 阶段 | 最坏耗时 |
  |---|---|
  | geocoding zh（3 次全超时） | 61.8s |
  | geocoding en（3 次全超时） | 61.8s |
  | forecast（3 次全超时） | 61.8s |
  | **合计** | **≈ 185.4s** |

  `987048f` 之前无 en 回退，合计 ≈ 123.6s；即**本轮审的 5 个提交之一把最坏耗时抬高了 ~62s 并越过 120s**。
- 与 M-1 的口径一致性：M-1（RSS ~240s 最坏）已被判 MAJOR 并修掉；现在 `/chat` 的最坏耗时头号贡献者换成 open_meteo（~185s），且同样落在 boss 投诉的天气路径上。按同一把尺子应同为 MAJOR。
- 触发条件如实说明：需要各次尝试**均打到 20s 超时**（黑洞/丢包网络）。容器当前表现是 SSL EOF（秒级失败），故这是**条件性**风险，不是必现故障。若团队判定「仅最坏场景」不配 MAJOR，请连同 M-1 一并下调口径——我不主张两把尺子。
- 建议改法（三选二即可，改动约 5 行）：
  1. `:955-958` 改成「网络类 `ToolError` 直接 `return []`，仅在**请求成功但无 results** 时才回退 en」→ 立即砍掉 61.8s；
  2. `:951` geocoding 单独压 `timeout=8`（geocoding 是极小 JSON，20s 无意义）→ 两语言合计 2×(3×8+1.8)=51.6s；
  3. `:979` forecast `attempts=2` → 41.8s。1+2+3 后合计 ≈ 93s，回到 2 分钟内。
  - 另：`data_sources.py:1420` 的 `fut.result()` 仍无 timeout（第 1 轮 N-6 提过），修复后 RSS 已降到 ~8.6s，但 open_meteo 仍可把整次 `search_many` 拖到分钟级，建议一并发 `timeout=`。

### N-8【NIT】RSS 并行耗时断言无法锁定 `FEED_TIMEOUT`，且 docstring 与代码阈值不一致

- 位置：`report-agent-team/tests/test_m12_review_fixes.py:64-80`（打桩 `slow_fail` 忽略 `timeout` 参数），`:62`（docstring 写「断言 < 2.5s」）vs `:79`（代码 `assert elapsed < 1.8`）
- 实测结论（本地，6 feed × 0.4s × 2 次重试）：
  - 并行（现实现）**0.80s**、串行基线（顺次调 `_fetch_feed`）**4.81s**；阈值 1.8s → 并行余量 **2.24x**，串行必红 ✅ **这条断言确实能抓住"回退到串行"**。
  - 但把它改成 `FEED_TIMEOUT = 20` 再跑，耗时仍是 0.81s、断言仍绿 ⚠️ —— **它没有锁住"单 feed 超时预算"这一半修复**。
  - 阈值 1.8s 会不会在慢 CI 假红？打桩用的是 `time.sleep`（释放 GIL），6 线程并发睡不受 CPU 饥饿影响，2.24x 余量我认为够；但 docstring 与代码不一致会误导后人。
- 建议（按优先级）：
  1. 打桩里记录 `timeout`，追加确定性断言 `max(timeouts) <= 6`——不依赖 wall clock，直接锁死 `FEED_TIMEOUT`；
  2. 把 wall-clock 断言改成**并发度断言**：打桩记录每次调用的 `(进入时刻, 退出时刻)`，断言 `最大并发数 >= 2`（并行必为 6，串行必为 1）。比绝对墙钟更抗机器差异；
  3. 若保留墙钟断言，把阈值提到 2.5s（与 docstring 对齐），仍远低于串行 4.8s。

### N-9【NIT】`test_rss_single_feed_failure_degrades_only_that_feed` 实际会打真网，与文件头声明不符

- 位置：`report-agent-team/tests/test_m12_review_fixes.py:85`（`real = ds._http_get_retry`）、`:92`（`return real(url, min(timeout, 4), attempts=1, **kw)`）vs `:3`（「全部离线，不打真网」）
- 问题：该用例对 `people.com.cn` 走真实 `_http_get_retry`，会发起真实出网请求（最多 4s）。沙箱/无网 CI 下会失败并走降级路径，用例仍能过，但**耗时随网络波动**，且违背文件头承诺。
- 建议：给 `people.com.cn` 也打桩返回一段静态 RSS 字节串，断言「xinhuanet 失败但另一 feed 的条目仍出现在结果里」——那样才是真正在验「只降级该 feed」，现在这条只断言了「没抛异常 + 调用过」。

### N-10【NIT】`_fetch_feed` 遍历 `root.iter("item")` 的异常会退化成无 feed 名的通用日志

- 位置：`report-agent-team/tools/data_sources.py:1117-1130`（`for it in root.iter("item")` 无 try），异常冒泡到 `:1156-1161` 的 `except Exception`
- 实测：**兜底是全的**——我分别让 `_http_get_retry` 抛 `ToolError` / `RuntimeError` / `ValueError`、让 `_parse_xml` 抛 `ToolError` / `RuntimeError`、让 `root.iter()` 抛 `RuntimeError`，6 种情况全部返回 `[]` 且未向上抛。
- 但只有前两类带 `feed_name`（`:1106`/`:1111`/`:1114`），第三类日志是 `[rss_news] feed 任务异常: RuntimeError: ...`（`:1160`），**丢失是哪个 feed**，排查时不好定位。
- 建议：`_fetch_feed` 整体再包一层 `try/except Exception`，内部 `logger.warning("[rss_news] feed %s 处理异常: %s: %s", feed_name, ...)` 后 `return []`。

### N-11【NIT】`会`/`将` 仍在噪声表，合法地名被吃掉（第 1 轮已提，未修）

- 位置：`report-agent-team/tools/data_sources.py:1051`
- 实测：`会理天气 → 理`、`将乐县天气 → 乐县`（会理市/将乐县均为真地名）。
- 建议：`会|将|有|在` 这类可能构成地名的字，改为「仅当地名整体长度 > 2 时剥离」，或直接移出噪声表（`会不会`/`有没有` 已有独立长词条目覆盖主要场景）。

### N-12【NIT】`FEED_TIMEOUT` 只作用于 RSS，`search_many` 仍无全局 deadline

- 位置：`report-agent-team/tools/data_sources.py:1420`（`raw = fut.result()` 无 timeout）
- 说明：RSS 已降到 ~8.6s（4s × 2 + 退避 0.6s），本条风险级别已从「MAJOR 成因」降为 NIT，但作为防御纵深仍建议补 `timeout=`（超时 future 降级为 status 失败项，与现有单源降级语义一致）。

## 第 2 轮已验证（确认修好的部分）

1. **M-1（RSS 串行 240s）确实修好**：
   - `data_sources.py:1151-1161` 改为 feed 级 `ThreadPoolExecutor(max_workers=min(8, len(FEEDS)))`；`:1156` 按**提交顺序**收 `f.result()` → feed 顺序确定，`:1176` 的稳定排序语义未被破坏。
   - 实测并行 0.80s vs 串行 4.81s（6 feed × 0.4s × 2 次重试打桩）。
   - 最坏墙钟由 `6 × 20s × 2 ≈ 240s` 降到 `FEED_TIMEOUT(4) × FEED_ATTEMPTS(2) + 退避 ≈ 8.6s`（`:1088-1089`）。
2. **M-2（地名抽取）主体确实修好**：`test_m12_review_fixes.py:23-46` 的 20 条参数化用例我逐条复跑，**20/20 通过**（含我上轮报的 `北京明天气温→北京`、`厦门会不会下雨→厦门`、`青岛没下雨吗→青岛`）。
   - 「先整句去时间词再定位关键词」的顺序修正（`data_sources.py:1010-1021`）是对的；`:1015-1016` 的注释与 `:1040` 的 `re.sub(r"\s+","", q[:m.start()])` 一起，把「整句阶段不去空格、只在取左侧时去空格」这条约束落实了——实测 `weather in Guangzhou → Guangzhou`、`Guangzhou temperature → Guangzhou`、`厦门 天气 → 厦门` 全部通过，**没有留下你担心的空格回归**。
3. **「裸字『度』」回归确已修掉**：`data_sources.py:1023-1025` 显式注释 + 关键词表只保留 `多少度|几度`（`:1026-1027`）。实测 `印度天气/印度今天天气/今天印度多少度 → 印度` 全对；`深度学习最新进展/季度报告/高精度传感器 → None`（不再误触发天气意图与无谓 geocoding）。`51c553b` 补的 3 条反例有效。
4. **`_fetch_feed` 异常兜底覆盖全路径**（见 N-10 的实测，6 种注入异常全部不击穿）。
5. **全量 123 passed / 4.78s**，无新增失败、无新增 warning 类别。

## 第 2 轮未验证

- 容器内真实网络下 6 个 feed 的可达性与实际耗时（沙箱无 `docker compose`，且按要求不打真网）；M-1 的 8.6s 是按常量静态推算。
- M-4 的 185s 同为静态推算，未在真实黑洞网络下实测。
- `日照/周口/日本/日喀则` 的 geocoding 实际是否落空，未联网实测（只验证了「抽取结果已错」这一层）。

---

# 第 1 轮（存档）

- 仓库：`E:\第二电脑`（项目子目录 `report-agent-team/`）
- 待审提交：`e970fa9`（RSS 新闻源）→ `987048f`（weather 抗噪）→ `21951ae`（logging + 探针）→ `2e21ea9`（dispatch_tool 真凶）（另附看 `7a8d660`）
- 审议人：independent reviewer（general-purpose-2-ae0c）
- 方式：读代码 + `git show` 逐条比对 + 本地实测（Python 3.13.12）+ 全量 pytest（99 passed / 3.37s）
- 未修改任何业务代码。

## 结论（第 1 轮）

**总判定：MAJOR**

- BLOCKER 0 条 / **MAJOR 2 条** / NIT 7 条
- 核心修复（A：`dispatch_tool` 丢弃真实检索结果；B：root logger 无 handler）**经独立复核成立且实现正确**，我实测确认「回退修复 → 新回归测试变红」，是本次真正的价值所在。
- 但本次同时引入的 `RssNewsProvider` 存在**串行拉取 6 个 feed、无整体 deadline** 的时延风险（最坏 ~240s），会直接重演 boss 反馈的「两分钟不返回」；`_extract_location` 的抗噪改动未覆盖「X 明天气温 / X 会不会下雨」这类高频句式，天气仍会静默落空。
- **状态更新**：MAJOR-1 已由 `d6ce1f2` 修复并经第 2 轮验证 ✅；MAJOR-2 主体已修复 ✅，但同类问题仍有残留（见第 2 轮 M-3 / N-11）。第 1 轮 7 条 NIT 均未处理，其中 N-7（open_meteo 双语回退）在第 2 轮**升级为 M-4**。
- 建议：M-1 修完即可合入；M-2 可与 M-1 同批处理，或单独立项（非本次引入，属同根因遗留）。

---

## 发现清单

### M-1【MAJOR · 已修复 ✅】RssNewsProvider 串行拉取 6 个 feed，最坏 ~240s，抵消检索并行化收益

- 位置：`report-agent-team/tools/data_sources.py:1093-1107`（feed 循环）、`:1095`（`_http_get_retry(url, self.timeout, attempts=2)`）、`:1069`（`timeout: int = 20`）
- 关联：`report-agent-team/tools/data_sources.py:1356-1369`（`SearchTool.search_many` 的 `ThreadPoolExecutor` + `fut.result()` **无 timeout**）
- 问题：
  - `search_many` 只在 **provider 粒度**并行（`data_sources.py:1359-1361` 按 `provider × query` 提交），`rss_news` 是单个 provider，其内部 6 个 feed 完全串行。
  - 单 feed 最坏耗时 = `attempts(2) × timeout(20s) + backoff(0.6s)` ≈ 41s；6 个 feed ≈ **240s+**。`ThreadPoolExecutor` 的 `with` 块会等所有 future，故整次 `search_many` 被最慢 provider 拖到 4 分钟。
  - 该路径就在 `/chat` 关键链路上：`chat_agent.py:322` → `orchestrator._run_fc_loop` → `dispatch_tool` → `search_many`。而 `2975c68` 刚做过「检索并行化」专项修复「/chat 两分钟不返回」，此改动是同根因的回退风险。
  - 触发条件不算苛刻：只要 query 命中 `NEWS_RE`（`data_sources.py:1062-1063`，含「消息/动态/报道/资讯」），6 个 feed 全量拉取；容器直连 NAT 下任一 feed 黑洞即命中。
- 建议改法（三选一叠加）：
  1. feed 级并行：`ThreadPoolExecutor(max_workers=6)` 提交 6 个 `_http_get_retry`，用 `as_completed` 收集；
  2. 单 feed `timeout` 降到 6~8s（RSS 是轻量文本，20s 无意义），`attempts` 保持 2；
  3. 给 `search()` 加整体 deadline（如 `deadline = time.monotonic() + 10`），超时即跳出循环、返回已收条目——宁可少 feed，不可拖垮 `/chat`。
- 补充：`search_many` 的 `fut.result()`（`data_sources.py:1369`）建议统一加 `timeout=`，届时超时 future 降级为 status 失败项，与现有「单源降级」语义一致。

### M-2【MAJOR · 主体已修复 ✅，仍有残留 → 见第 2 轮 M-3 / N-11】`_extract_location` 抗噪不完整：时间词被天气关键词切断 + 缺「不/没/多少/怎样」等高频噪声

- 位置：`report-agent-team/tools/data_sources.py:1024-1039`（清洗逻辑），重点 `:1029-1036`
- 问题：噪声清洗只作用于 `query[:m.start()]`（`m` = 天气关键词位置）。当时间词与关键词**叠字相邻**时，时间词被切走一半，残字留在地名尾部；同时噪声表缺 `不 / 没`，而「会不会下雨」「不下雨吧」是极高频句式。
- 实测（Python 3.13.12，直接调用 `OpenMeteoProvider._extract_location`）：

  | query | 抽取结果 | 判定 |
  |---|---|---|
  | `北京明天气温` | `北京明` | ✗ 应为 `北京` |
  | `上海明天气温多少` | `上海明` | ✗ |
  | `北京今日气温` | `北京今` | ✗ |
  | `厦门会不会下雨` | `厦门不` | ✗ 应为 `厦门` |
  | `成都不下雨吧` | `成都不` | ✗ |
  | `青岛没下雨吗` | `青岛没` | ✗ |
  | `会理天气怎么样` | `理` | ✗ 合法地名被「会」吃掉 |
  | `将乐县天气` | `乐县` | ✗ 合法地名被「将」吃掉 |
  | `厦门今天天气` / `北京明天会下雪吗` / `帮我查一下深圳天气` / `2025年3月厦门天气` | 各自正确 | ✓（commit 声称的 4 例确实修好了） |

  `北京明` / `厦门不` 这类串基本拿不到 geocoding 结果 → `search()` 返回 `[]` → 天气静默消失 + 模型误报「未能获取天气」，**正是本次要根治的症状**。
- 归因说明（避免误判为回归）：我用旧算法逐例复算，`北京明天气温`、`厦门会不会下雨` 在 `987048f` **之前同样错误**（旧算法只做首尾剥离，「明天」被切成「明」后同样残留）。故这是「**本次修复未覆盖的同根因遗留**」，不是新引入缺陷——但它落在本次修复的承诺范围内，且命中率不低，按 MAJOR 提。
- 建议改法：
  1. **先在整句上去时间词，再定位关键词**：把 `今天|明天|昨天|后天|今日|昨日|大后天` 等在 `query` 上先 `re.sub` 掉，然后再 `re.search` 天气关键词——从根本上消除「明天|天气」切字问题；
  2. 噪声表补 `不|没|多少|几|怎样|怎么样|如何|是否`；
  3. 尾部清洗后追加一道「残字兜底」：`re.sub(r"[明今昨后不没]$", "", loc)`（对极短残留如 `理`/`乐县` 无效，故仍需 1、2）；
  4. 对 `会|将|有|在` 这类**可能构成合法地名**的字，改为「仅当地名整体长度 > 2 时剥离」或直接移出噪声表（`会理`、`将乐` 是真县名）。

### N-1【NIT】非法 `LOG_LEVEL` 会在 import 期抛 ValueError，导致 API 起不来

- 位置：`report-agent-team/server/main.py:26-30`
- 问题：`level = os.getenv("LOG_LEVEL", "INFO").upper()` 直接喂给 `logging.basicConfig`。实测 `logging.basicConfig(level="VERBOSE")` → `ValueError: Unknown level: 'VERBOSE'`。因为 `_configure_logging()` 在模块顶层调用（`server/main.py:35`），异常发生在 import 阶段 → uvicorn 启动即失败。容器 `HEALTHCHECK`（Dockerfile:27）会持续失败。
- 建议：`level = os.getenv("LOG_LEVEL", "INFO").upper()`；`if not isinstance(logging.getLevelName(level), int): level = "INFO"`（或 `logging.getLevelNamesMapping().get(level, logging.INFO)`）后再 `basicConfig`。

### N-2【NIT】root 设为 INFO 后会放行所有第三方库的 INFO 日志

- 位置：`report-agent-team/server/main.py:31-32`
- 问题：只压了 `httpx` / `httpcore`。`sqlalchemy` / `sqlmodel` / `watchfiles` / `urllib3` / `asyncio` 等在 root=INFO 下会开始吐 INFO，容器日志噪声上升，反而稀释刚加的 `[search_many] 源 X 命中 N 条` 这类关键诊断（与本次「可观测性」目标相悖）。
- 建议：把 `httpx/httpcore` 那两行扩成清单（`urllib3`、`sqlalchemy.engine`、`watchfiles`、`asyncio`、`multipart`），或直接用 `dictConfig` 显式声明 root handler + 逐 logger 级别。

### N-3【NIT】probe 脚本用法里的路径与实际镜像路径不符

- 位置：`report-agent-team/scripts/probe_outbound.py:31`（`docker compose exec api python probe_outbound.py`）、`:34-36`
- 问题：`Dockerfile:16` 是 `COPY . .`（`WORKDIR /app`），脚本实际位于 `/app/scripts/probe_outbound.py`。按文档写的 `python probe_outbound.py` 会 `can't open file`。
- 建议：文档改为 `python scripts/probe_outbound.py`。

### N-4【NIT】probe 脚本关闭了证书校验，未标注「结果不能作为 TLS 可信性结论」

- 位置：`report-agent-team/scripts/probe_outbound.py:102-103`（`ctx.check_hostname = False` / `ctx.verify_mode = ssl.CERT_NONE`）
- 说明：作为区分「网络拦截 vs TLS 握手问题」的探针，这么做可以理解；但 `SSL: UNEXPECTED_EOF` 的判定口径在关闭校验后会被弱化。建议在 `verdict()`（`:123-131`）补一句「本探针不校验证书，OK 仅代表连通，不代表证书可信」。

### N-5【NIT】两条回归断言太弱，抓不住 A 类 bug（实测证据）

- 位置：`report-agent-team/tests/test_m11_mcp_fc.py:242-247`、`report-agent-team/tests/test_chat_mcp_learning.py:63`
- 我用 monkeypatch 复现「修复前」的 `dispatch_tool`（`_, entries = search_many(...)` / `result=entries`），在**不改任何业务代码**的前提下跑三条断言，结果：
  - `test_dispatch_web_search_returns_records_not_status`（`test_m11_mcp_fc.py:225-239`）→ **红**（`KeyError: 'url'`）✅ 真能抓住 A 类 bug；
  - `test_dispatch_web_search_accepts_bare_string_queries`（`:242-247`）→ **绿** ⚠️ 只断言 `r["ok"] is True`，修复前后都过；
  - `test_chat_agent_calls_web_search`（`test_chat_mcp_learning.py:63`，`len(out["sources"]) == 1`）→ **绿** ⚠️ 修复前后都过。实测「修复前」产出正是 `[{"source":"tavily","url":null,"title":null,"snippet":null,"tool":"web_search"}]`——即 boss 看到「引用全空」的现场，但因为 status 项也带 `source` 字段，`_build_sources`（`chat_agent.py:178`）仍能凑出 1 条。
- 建议：
  - 裸字符串用例：让 `FakeWebSearch` 记录 `self.queries = queries`，断言 `ws.queries == ["厦门天气"]`；
  - chat 用例：断言 `out["sources"][0]["url"] == "http://example.com"` 且 `title` 非空。
- 备注：`test_chat_mcp_learning.py:15-25` 把 FakeWebSearch 从 `([], [记录])` 归位为 `([记录], [status])` 这一步是**正确且必要**的（原写法确实在反向锁定缺陷）。

### N-6【NIT】`tool_status` 现在携带完整检索记录，state / WS 事件体积膨胀

- 位置：`report-agent-team/orchestrator.py:1290-1297`（`tool_entries` 带完整 `result`）、`:1577-1580`（写入 `state["tool_status"]`）、`:1597-1600`（`on_event` 里 `"tool_status": tool_entries`）
- 问题：修复前 `result` 是 status 项（每项 ~120 字符）；修复后是真实检索记录（22 源 × 5 条 ≈ 110 条，每条含 500 字符 snippet + url + title，`data_sources.py:1398-1406`）。研报路径下这些数据会进 `.engine_state` 快照（含 `prior_versions`，`orchestrator.py:1571`）并随 `agent_complete` 事件经 JSONL → WS 推给前端。
- 建议：`tool_status` 落盘/推送时剔除 `result`（或只留条数 + 源 id 统计），`result` 仅保留在内存里供 `_build_sources` / `chat_learn_write` 派生。`/chat` 路径不受影响（不落 state），故定 NIT。

### N-7【NIT → 第 2 轮已升级为 M-4】open_meteo 双语 geocoding 回退使最坏耗时翻倍

> **升级原因**：第 2 轮重算后确认最坏耗时 ≈ 185s（含 forecast），已越过 2 分钟线，且是 `/chat` 最坏耗时的头号贡献者。按与 M-1 同一把尺子处理，详见第 2 轮 **M-4**。

- 位置：`report-agent-team/tools/data_sources.py:948-963`（`for lang in ("zh", "en")` + 内层 `_http_get_json_retry(..., attempts=3)`）
- 问题：zh 失败 → 再走 en，每层 3 次重试 × 20s → 最坏 ~120s（原为 ~60s）。在「境外被拦」场景下失败很快，影响有限；但在**慢网/黑洞**场景下会显著拉长 `/chat`。
- 建议：仅当 zh 返回**成功但无 results** 时才回退 en（网络类 `ToolError` 直接返回 `[]`）；或回退层 `attempts=1`。

### 另：commit message 与事实的一处小偏差（不列为缺陷）

`21951ae` 描述「支持 `CHAT_PROXY` 走宿主代理」，但本次提交只新增了 `scripts/probe_outbound.py` 与 `server/main.py` 的日志配置；`CHAT_PROXY` 实际是 M11-2 已在 `docker-compose.yml:54-67` 落地的（含 `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy` 与 `NO_PROXY`）。探针脚本只是**复用了**该能力。建议 commit message 措辞收敛为「新增只读出网探针（可配合既有 CHAT_PROXY 验证）」，避免后续追溯时误判改动面。

---

## 已验证正确的修复

以下各项我**实际读码 +（能跑的）实测**确认，不是照抄 commit message：

1. **A 类根因成立且修对了**（`orchestrator.py:1131-1143`）
   - 修复前 `_, entries = search_many(...)` + `result=entries`，模型拿到的确实是 status 项（`{agent,tool,source,ok,error}`），无 `title/url/snippet`。
   - 修复后 `result=results`（检索记录）、`status=entries`（成功/失败明细）分列，语义清晰。
   - `queries` 为裸字符串时包装成 `[queries]`（`:1138-1140`），避免按字符迭代。

2. **下游消费自洽**（逐点核对，无遗漏）
   - `_run_fc_loop`（`orchestrator.py:1298-1310`）：`entries = r.get("result")` → 现在是检索记录；`extra_search.extend(entries)`（`:1301`）与 `_trim_web_results(entries)`（`:1303`）拿到的形状都对。
   - `_trim_web_results`（`:1241-1260`）按 `e.get("source")` 限额：真实记录在 `data_sources.py:1404` 确实带 `source`（= plugin id），且 `_WEB_PER_SOURCE=3 / _WEB_MAX_TOTAL=20` 的裁剪语义在真实记录上比在 status 上更合理（此前按 status 裁剪纯属巧合能跑）。
   - `_build_sources`（`chat_agent.py:175-186`）依赖 `item["source"]/["url"]/["title"]/["snippet"]`，现在全部有值。
   - `chat_learn_write`（`tools/chat_learning.py:154-171`）从 `tool_entries[].result` 取 `source`，形状匹配。
   - **无遗留调用方**：全仓 `search_many(` 只有 `orchestrator.py:1141`、`:1365` 与 3 处 `tests/test_tools.py`，全部按 `(results, status)` 解包；`dispatch_tool` 只被 `orchestrator.py` 与测试引用，无其它消费点用旧形状。

3. **`_fmt_failures` 无空引用/类型隐患**（`orchestrator.py:1225-1238`）
   - `for s in status or []` 兜住 `None`；`isinstance(s, dict)` 兜住非 dict；`s.get("error") or "未知原因"` 兜住空 error；`limit` 有界。
   - 非 FC 的 researcher 分支（`:1387-1392`）传入的 `entries` 就是 status 列表，异常分支（`:1367-1370`）构造的条目无 `source` → 落 `"unknown"`，不崩。

4. **`_get_bundle` 注入保护正确，且生产热重载语义不受影响**（`chat_agent.py:211`、`:219-220`）
   - `self._bundle_injected = bundle is not None`，注入即原样返回。
   - 全仓 `ChatAgent(` 实例化点只有 `server/api.py:962`（`ChatAgent(llm=_chat_llm_client)`，不注入 bundle）与 8 处测试注入 → **生产热重载语义确实不变**，维护者这条声明成立。
   - 副作用正面：单测不再打真网。实测全量 `99 passed in 3.37s`（commit 声称 106s → 3.2s，我复现为 3.37s，量级一致）。

5. **B 类根因成立且修法安全**（`server/main.py:17-35`）
   - 全仓此前确无 `logging.basicConfig`（现已仅此一处）。
   - **与 uvicorn 无冲突**：实测本机 uvicorn 0.52.4，`Config.__init__` 在加载 app 前先 `configure_logging()`；其默认 `LOGGING_CONFIG` **不含 `root` 键**且 `disable_existing_loggers: False`，`dictConfig` 不会移除已有 root handler。无论 basicConfig 先跑还是后跑，root handler 都能存活。
   - **无重复打印**：`uvicorn` / `uvicorn.access` logger 均 `propagate: False`，`uvicorn.error` 向上传到 `uvicorn` 后终止，不会二次冒泡到 root。业务 logger（`orchestrator` / `tools.data_sources` / `chat_agent`）无 handler，正常冒泡到 root 输出一次。
   - `[search_many] 源 X 命中 N 条`（`data_sources.py:1390-1391`）与 open_meteo 新增的 `logger.info/warning`（`data_sources.py:945/956-957/963/981/985`）在 root=INFO 下确实会输出——B 的假设成立。

6. **`RssNewsProvider` 的意图判定方向正确**
   - `NEWS_RE`（`data_sources.py:1062-1063`）只认新闻名词，**不含**「今天/最新」——实测 `厦门今天天气怎么样` → `news? False`，不会污染天气检索（commit 里提到的初版 bug 确已修掉）。
   - 连续子串打分（`:1116`）避免了单字「天」误命中，方向正确。
   - `score` 0.5 / 0.9 经 `score_to_credibility`（`tools/web_search.py:23-33`，阈值 `_CRED_HIGH=0.7` / `_CRED_MEDIUM=0.4`）→ medium / high，**不存在可信度虚高**。
   - 单 feed 失败隔离（`:1096-1107`）与 `except Exception` 兜底（`:1104`）确实不击穿整体聚合。

7. **配置落地正确**
   - `config/plugins.yaml` 顺序实测为 `['open_meteo','duckduckgo','rss_news','tavily', ...]` 共 22 项，**无重复项**，`open_meteo`/`duckduckgo` 置顶生效（`7a8d660` 的搬运正确，无残留旧块）。
   - `rss_news` 已登记进 `KEYLESS_PROVIDERS`（`data_sources.py:1213`），`build_search_tool`（`:1442-1445`）走 `KEYLESS_PROVIDERS[prov](timeout=timeout)`，构造签名匹配。

8. **当前日期注入**（`chat_agent.py:296-301`）：拼在 system prompt 末尾，措辞仍要求「实时数据须调工具取证」，不会诱导模型凭记忆作答。无副作用。

9. **`_http_get_retry`（`data_sources.py:146-164`）**：与 `_http_get_json_retry` 同策略（仅重试 `ToolError`、指数退避、`raise last or ToolError`），不掩盖解析层错误；已实测 `_parse_xml`（`:167-172`）能正确解析 RSS `item/title/link`。

---

## 未验证项（如实标注）

- **容器网络侧 RSS/境外可用性**：沙箱无 `docker compose`，`scripts/probe_outbound.py` 未实跑；6 个 feed 在容器网络下是否可达、耗时多少，未验证。M-1 的 ~240s 是按 `timeout × attempts × feed 数` 的**静态推算**，非实测。
- **真实 LLM / 真实 /chat e2e**：按要求未联网、未调 LLM。故「修复后 boss 的『抓不到新闻/天气』是否真的消失」只能从代码链路推断，未端到端验证。
- **geocoding 落空判定**：M-2 里 `北京明` / `厦门不` 是否真被 Open-Meteo 判为无匹配，未联网实测，属于「抽取结果已错」层面的结论。
- **`tests/test_api_boundary.py`**：沙箱缺 `asyncpg` / `mcp`，按要求整体跳过（未收集），其覆盖不受本次改动影响与否未验证。
- **`LOG_LEVEL` 未在任何 compose/env 中定义**：N-1 属于「若运维后续设置非法值」的前瞻风险，非当前故障。

---

# 第 3 轮复核（`3efd377` · 追加）

- 复核对象：`3efd377`（修第 2 轮 M-3 / M-4）。**当前 HEAD = `3efd377`**（提交链 `d6ce1f2` → `51c553b` → `3efd377`）。
- 方式：读码 + `git show` 比对 + 本地实测（Python 3.13.12，monkeypatch 打桩，未改业务代码）+ 全量 pytest。
- 全量测试：**131 passed / 5.01s**（与 commit 声称的 131 一致）。

## 最终总判定

**PASS_WITH_NITS**（BLOCKER 0 / **MAJOR 0** / 本轮新增 NIT 3 条）

- 第 2 轮的两条 MAJOR **M-3、M-4 均已修好并经我实测确认**，且未引入新的 MAJOR。
- 剩下的是覆盖面缺口与测试锁强度问题，均为 NIT，**不阻塞合入**。
- 三轮累计仍未处理的 NIT：第 1 轮 N-1~N-6（其中 N-7 已升 M-4 并已修）、第 2 轮 N-8~N-12、第 3 轮 N-13~N-15。它们都不改变总判定。

## 第 3 轮：M-3 / M-4 复核结论

### M-3（日/周地名被砍）——已修复 ✅

- 改法落点：`report-agent-team/tools/data_sources.py:1024-1026`
  ```python
  q = re.sub(r"\d+\s*(年|月|日|号|周|星期|时|点|分|秒钟?)", "", q)   # :1024
  q = re.sub(r"星期[一二三四五六日天]", "", q)                        # :1025
  q = re.sub(r"[\d]+", "", q)                                        # :1026
  ```
- **顺序约束成立**：`:1024`（去带数字的日期单位）确实排在 `:1026`（剥裸数字）之前，`2025年3月15日厦门天气 → 厦门` 实测通过（若反序则单位失去依靠，这一点我认可）。
- 实测 `LOCATION_CASES` 现为 **26 条，26/26 通过**，含新增的 `日本 / 周口 / 日照 / 日喀则 / 本溪 / 2025年3月15日厦门天气`。
- 我上一轮报的 4 个反例全部恢复：`日本天气→日本`、`周口天气→周口`、`日照天气→日照`、`日喀则天气→日喀则`。

### M-4（网络失败换语言重试）——已修复 ✅

- 改法落点：`report-agent-team/tools/data_sources.py:955-961`（`except ToolError:` 分支由 `continue` 改为 `return []`）；「成功但无匹配」仍走 `:962-964` 的 `if ... break` / 循环末尾换语言。
- 实测（打桩 `_http_get_json_retry`，数调用次数）：

  | 场景 | 调用次数 | 结果 |
  |---|---|---|
  | 网络 `ToolError` | **1** | `[]` ✅ |
  | 成功但返回 `{}` 无匹配 | **2**（zh → en） | `[]` ✅ 换语言语义保留 |
  | `HTTP 429` | **1** | `[]` ✅ |

- **关于「zh 被限流 429 但 en 可用」会不会被误伤——结论：不会**，理由三条：
  1. 429 是作用在**端点 / 出口 IP** 上的限流，`language` 只是 query 参数，换它不换配额；
  2. 到达 `:955` 之前，`_http_get_json_retry`（`data_sources.py:123`，默认 `attempts=3`、退避 0.6/1.2s）已经重试过 3 次，**瞬时抖动已被吃掉**，剩下的是持续性失败；
  3. 唯一理论上"与语言相关"的失败是 `_http_get_json` 的 `响应非 JSON`（`data_sources.py:113-120`），但该分支同样来自同一域名同一响应格式，换语言不会变 JSON。
- 可选的更稳判据（**不建议采用**）：按 `ToolError` 消息前缀区分 `HTTP nnn:` / `请求失败:`（网络类）与 `响应非 JSON:`（解析类），只让后者换语言。依赖字符串前缀很脆，收益≈0，当前写法更干净。

## 第 3 轮新增 NIT

### N-13【NIT】`(下|上|这|本)周 + 星期几/末` 未覆盖，会残留「三 / 一 / 末」

- 位置：`report-agent-team/tools/data_sources.py:1027-1028`（时间词表含 `本周|这周|上周|下周`，但不含其后的星期几/「末」）
- 实测：

  | query | 结果 | 期望 |
  |---|---|---|
  | `下周三北京天气` | `三北京` | `北京` |
  | `本周一南京天气` | `一南京` | `南京` |
  | `上周末北京天气` | `末北京` | `北京` |
  | `3周后成都天气` | `后成都` | `成都` |

- 说明：`3efd377` 之前这些用例同样是错的（旧版无条件删「周」得到 `下三北京` / `末北京`），**不是新引入的回归**，只是新补的 `本周|这周|上周|下周` 只吃掉了前两个字。
- 建议：在 `:1027-1028` 的表里，把 `(?:下|上|这|本)周[一二三四五六日天末]?` 排**在** `本周|这周|上周|下周` **之前**（分支按序匹配，长选项必须靠前）。`3周后` 这类可用 `\d+\s*周[后内]?` 顺带覆盖，或接受为噪声。

### N-14【NIT】关键词表裸字 `雨|雪|晴|阴` **有真实反例**，但影响面小 → 明确不报 MAJOR

- 位置：`report-agent-team/tools/data_sources.py:1033-1034`
- 你要的具体反例（均为真实中国区县名），实测：

  | query | 结果 | 说明 |
  |---|---|---|
  | `晴隆天气` | `None` | 贵州黔西南州晴隆县 |
  | `晴隆县天气` | `None` | |
  | `雨花区天气` | `None` | 长沙市雨花区 |
  | `雨湖区天气` | `None` | 湘潭市雨湖区 |
  | `雨山区天气` | `None` | 马鞍山市雨山区 |
  | `雨城区天气` | `None` | 雅安市雨城区 |
  | `长沙雨花区天气` | `长沙` | 有上级地名时降级为市，可用但不精确 |
  | `贵州晴隆天气` | `贵州` | 同上 |

  失效条件是「该地名位于 query 开头」——因为 `re.search` 取**最左**匹配，`雨/晴` 在 index 0 就命中，`before` 为空。
- 为什么定 NIT 而不是 MAJOR：受影响的都是**区县名**（非常用地级市/国家名），且必须位于 query 开头；带上级地名时会退化成可用结果（`长沙`/`贵州`）。与 M-3 的 `日本 / 周口 / 日照`（国家+地级市、且无条件失效）不是一个量级。
- 建议（按性价比）：
  1. 从关键词表去掉裸字 `晴|阴`——「天晴/阴天」类查询极少，而 `多云|天气预报` 已覆盖主要天气说法，收益 > 代价；
  2. `雨|雪` 建议保留（`上海有雨吗`、`北京下雪了吗` 依赖它们），接受上述区县名风险；
  3. 若想两者兼得：把 `re.search` 换成「取**最右**匹配」——我实测推演过，它能修好 `晴隆/雨花区/长沙雨花区`，**但会新引入 `厦门会不会下雨 → 厦门下`**（最右匹配选中 index 5 的 `雨` 而非 index 4 的 `下雨`）。所以单纯"取最右"不可行，需要更复杂的「最右且不与前一长词重叠」策略，复杂度不值。**推荐只做第 1 条。**

### N-15【NIT】`FEED_TIMEOUT` 仍未被任何断言锁住（复测确认）+ 关于取舍的判断

- 位置：`report-agent-team/tests/test_m12_review_fixes.py:64-80`（打桩 `slow_fail` 忽略 `timeout` 形参）
- 复测：把 `RssNewsProvider.FEED_TIMEOUT` 改成 20 后，该用例耗时仍是 **0.80s**、断言 `< 1.8` **仍绿** → 超时预算这一半修复确实没有回归锁。
- **关于你的取舍——我的判断：保留 wall-clock 断言是对的，但"确定性断言抓不到运行时行为"这个理由不成立。**
  并发度本身就是运行时行为，只是判定条件是离散的（重叠 or 不重叠），不依赖机器快慢。下面这个写法**完全不读时钟**，且在串行回退时**必然变红**（我已实测）：
  ```python
  def test_rss_feeds_are_fetched_concurrently(monkeypatch):
      lock = threading.Lock()
      cur = peak = 0

      def stub(url, timeout, attempts=1, **kw):
          nonlocal cur, peak
          with lock:
              cur += 1
              peak = max(peak, cur)          # 峰值并发计数
          try:
              time.sleep(0.05)               # 只为了让 6 个线程真正在飞
          finally:
              with lock:
                  cur -= 1
          raise ToolError("stub")

      monkeypatch.setattr(ds, "_http_get_retry", stub)
      RssNewsProvider().search("今天有什么新闻", max_results=5)
      assert peak >= 2, f"feed 未并发（峰值并发={peak}，串行回退时为 1）"
  ```
  实测结果：并行实现 **peak = 6**，模拟串行回退 **peak = 1**（必然红）。
- 所以我的建议是**叠加而非替换**：保留现有 wall-clock 断言（它确实能抓「并行改回串行」）＋ 增加上面这条并发度断言（抗机器差异）＋ 在打桩里记录 `timeout` 并断言 `max(timeouts) <= 6`（锁 `FEED_TIMEOUT`）。三条合计约 15 行，互斥覆盖「并行 / 并发 / 超时预算」三个维度。
- 另：`:62` docstring 写「断言 < 2.5s」而 `:79` 代码是 `assert elapsed < 1.8`，两者不一致（第 2 轮提过，仍未改），建议对齐以免后人误读。

## 第 3 轮已验证（无问题项）

1. `LOCATION_CASES` 26/26 通过；`pytest` 全量 **131 passed / 5.01s**，无新增失败。
2. M-3 的 4 个原反例 + 6 条新增反例全部通过；单位剥离顺序正确。
3. M-4 的三种失败场景调用次数符合设计（1 / 2 / 1），且 `test_open_meteo_retries_other_language_when_no_match` 这条对照用例能有效防止「把换语言重试整个删掉」，设计得好。
4. `3efd377` 未触碰 RSS 并行路径（`:1160-1165` 未变），M-1 的修复保持完好。
5. 未发现新的顺序/正则副作用：英文分支（`:1038-1046`）、空格处理（`:1047`）、「度」反例（`:1030-1032`）均未被本次改动影响。

## 第 3 轮未验证

- 容器内真实网络下 6 个 feed 的可达性与耗时（沙箱无 `docker compose`，按要求不打真网）。
- `晴隆 / 雨花区` 等地名的 geocoding 是否真落空，未联网实测（只验证到「抽取结果为 None」这一层）。
- 真实 LLM 生成的 query 分布，无法验证 `下周三…`、`晴隆…` 这类句式的实际出现频率（N-13/N-14 的严重度判断基于常识估计）。
