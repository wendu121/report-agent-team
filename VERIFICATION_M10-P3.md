# VERIFICATION · M10-P3 电商 / 电商竞品分析数据源（第二闸 / 自审）

**日期**：2026-09-10
**范围**：`tools/data_sources.py`、`config/plugins.yaml`、`tests/test_tools.py`
**设计源**：`DESIGN_M10-P3.md`（§6b.5 建议的 4 个电商真源）
**驱动**：boss「你抓一批电商类，和电商竞品分析类的上去」+「你接好的插件，要记得分类」→ 在**用户零 key** 前提下补齐电商域检索覆盖。

---

## 1. 目标与边界

| 项 | 内容 |
|---|---|
| 目标 | 新增 4 个 keyless 电商真源，补齐「电商 / 电商竞品分析」域覆盖 |
| 硬约束 | 用户零 key；不伪造数据；失败源诚实标记不冒充；单源失败不阻塞任务；**必须归入 `电商` 分类** |
| 明确不做 | 不啃需授权的电商源（1688/拼多多开放平台）；不做爬虫；不新增第三方依赖（stdlib urllib only） |
| 粒度约定 | 3 个 suggest 源给的是**竞品关键词趋势**（候选词 + 热度），非价格/销量 —— 已与 boss 在设计阶段对齐（DESIGN §7 Q3） |
| 与设计差异 | 无。严格按 §6b.5 表格实现 4 个源，端点契约**先探测后编码**（§3.1） |

---

## 2. 变更清单

### 2.1 `tools/data_sources.py`（+4 Provider，KEYLESS 9 → 13）
| Provider id | 类 | 源 | 端点 | 解析要点 |
|---|---|---|---|---|
| `taobao_suggest` | `TaobaoSuggestProvider` | 淘宝 | `suggest.taobao.com/sug` | `{"result":[[词,热度],...]}`，`code=utf-8` 保中文 |
| `amazon_suggest` | `AmazonSuggestProvider` | Amazon US | `completion.amazon.com/api/2017/suggestions` | `{"suggestions":[{"value":..}]}`，mid=ATVPDKIKX0DER |
| `ebay_suggest` | `EbaySuggestProvider` | eBay | `autosug.ebay.com/autosug` | **裸数组** `["echo",[sug...]]`，非该 shape 安全返空 |
| `accio_tariff` | `AccioTariffProvider` | Accio/阿里 | `POST www.accio.com/api/turtle/classify` | **三层嵌套** `data.data`；query 支持 `商品\|目的国` |

均注册进 `KEYLESS_PROVIDERS`。`build_search_tool` 既有分支（`prov in KEYLESS_PROVIDERS` → 真 Provider，`has_real=True`）**零改动**命中——继续复用 M10-P1 建立的扩展点。

**`accio_tariff` 特别说明**：唯一 POST 源，自建 `urllib.request.Request`（既有 `_http_get` 只支持 GET）；异常全部包成 `ToolError`（HTTPError / URLError / 超时 / 非 JSON），对齐 M10-P2 独立审议确立的容错契约。未识别出 HS 码时**返回空列表**（诚实，不编造税率）。

### 2.2 `config/plugins.yaml`（+4 条目，全部 `category: 电商`）
4 条目均 `auth_type: none` / `provider: <id>` / `enabled: true` / `builtin: true` / `status: connected`。
`_recompute_connected` 对 `auth_type=none` 直接返回 `connected` → 引擎运行时视为已连接、真实取数。

### 2.3 `tests/test_tools.py`（+6 离线单测）
M10-P1/P2 只有联网 smoke、无 committed 单测，M10-P2 独立审议正是因此抓到「只验 URL 没验 title」的盲点。本里程碑补上**离线解析器契约测试**（monkeypatch，不发网络）：

| 测试 | 锁定的协议 |
|---|---|
| `test_taobao_suggest_parses_pairs` | 二维数组取值 + 空数组容错 + URL 编码 |
| `test_amazon_suggest_parses_values` | `value` 取值 + 空白串跳过 |
| `test_ebay_suggest_parses_bare_array` | 裸数组 + **异常 shape 返空不崩** |
| `test_accio_tariff_parses_nested_and_splits_dest` | 三层嵌套 + `\|目的国` 解析 + 缺省 US 回落 + 无 HS 码返空 |
| `test_ecom_providers_registered_keyless` | 4 源必在 KEYLESS_PROVIDERS + auth/category 断言 |
| `test_ecom_sources_reach_aggregation` | 端到端接缝：经 build_search_tool 装载真 Provider 且结果带 source |

---

## 3. 实测证据（本次真实执行）

### 3.1 端点预探测（编码前，避免基于死端点编码）
沙箱出口（`HTTPS_PROXY=http://127.0.0.1:8048`），纯 stdlib urllib：

| 源 | 结果 | 证据片段 |
|---|---|---|
| taobao | **HTTP 200** | `{"result":[["蓝牙耳机政府补贴","100"],["蓝牙耳机2026新款","100"],...]}` |
| amazon | **HTTP 200** | `{"suggestions":[{"suggType":"KeywordSuggestion","type":"KEYWORD","value":"wireless earbuds bluetooth",...}]}` |
| ebay | **HTTP 200** | `["wireless earbuds",["wireless earbuds bluetooth","wireless earbuds noise cancelling",...]]` |
| accio | **HTTP 200** | `{"data":{"data":{"hscodeInfo":{"hscode":"69111045",...},"tariffRate":26.50,"tariffFormula":"一般关税[14%] + 附加关税[12.5%]",...}}}` |

4 端点契约与设计文档 §6b.5 **完全一致**，无偏差。

### 3.2 编译 + 单测
```
python -m py_compile tools/data_sources.py     → EXIT 0
python -m pytest tests/ -q                     → 47 passed, 0 failed, 2.63s
python -m pytest tests/test_tools.py -q        → 23 passed（17 既有 + 6 新增）
```

### 3.3 前端构建
```
cd web && npm run build  → ✓ built in 11.89s（EXIT 0）
```

### 3.4 容器重建 + 容器内真实验收（金标准）
```
docker-compose up -d --build api   → Successfully built，report-api Recreated，healthy
```
> 说明：`data_sources.py` 是 `COPY . .` 进镜像的（非 bind mount），**必须重建**才生效；`config/plugins.yaml` 是 bind mount，重建后依旧持久。

**容器内装载检查**：
```
KEYLESS total: 13
plugins total: 18
电商源: [('taobao_suggest','connected','taobao_suggest'), ('amazon_suggest','connected','amazon_suggest'),
        ('ebay_suggest','connected','ebay_suggest'), ('accio_tariff','connected','accio_tariff')]
```

**容器内真实网络取数**（3 query × 4 源 → **27 条真实结果**，与沙箱结果一致，无代理分叉）：
```
providers: [TaobaoSuggestProvider, AmazonSuggestProvider, EbaySuggestProvider, AccioTariffProvider]
using_mock: False
results: 27
分布: {'taobao_suggest': 6, 'amazon_suggest': 10, 'ebay_suggest': 10, 'accio_tariff': 1}

  [taobao_suggest] 蓝牙耳机政府补贴 | 淘宝联想词｜热度分 100
  [taobao_suggest] 蓝牙耳机2026新款 | 淘宝联想词｜热度分 100
  [amazon_suggest] 蓝牙耳机 | Amazon(US) 搜索补全｜类型 KEYWORD
  [ebay_suggest]   wireless earbuds bluetooth | eBay 搜索联想词
```

**Accio 关税多品类别实测（容器内）**：
| 查询 | HS 码 | 关税 | 构成 |
|---|---|---|---|
| `陶瓷咖啡杯\|US` | 69111080 | 40.8% | 一般[20.8%] + 附加[20%] |
| `ceramic coffee mugs\|US` | 69111045 | 26.5% | 一般[14%] + 附加[12.5%] |
| `蓝牙耳机\|US` | 85183020 | 12.5% | 一般[Free] + 附加[12.5%] |
| `不锈钢保温杯\|DE` | — | — | **返回空**（税率明细仅 CN→US，未编造）✅ |

> 最后一行是**诚实边界的正面证据**：非 CN→US 国别对无税率明细时返回空，而非伪造数字。

### 3.5 UI API 面（经 web 反代 `localhost:18080`）
```
GET /api/v1/admin/plugins  → 200
total plugins via API: 18
电商分类: 4
  taobao_suggest   淘宝联想词       status=connected  auth=none
  amazon_suggest   Amazon 联想词   status=connected  auth=none
  ebay_suggest     eBay 联想词     status=connected  auth=none
  accio_tariff     Accio 关税查询   status=connected  auth=none
分类分布: {'数据源':1,'学术':6,'百科':2,'行情':2,'新闻':1,'财报':1,'工商':1,'电商':4}
```
**`电商` 成为独立分类**（boss 明确要求「要记得分类」），4 源全部落入。

### 3.6 两条聚合路径验证
| 路径 | providers | 结果 | 电商源参与 |
|---|---|---|---|
| 全量（无 filter） | 13 | 42 条 / 7 源成功 | ✅ accio_tariff 命中 |
| `plugin_filter=[4 个电商源]`（= UI 插件多选） | 4 | 27 条 | ✅ 4 源全参与 |

### 3.7 真实引擎端到端（stub LLM，离线安全，规避 429）
```
task.topic = "蓝牙耳机"
→ status: done
→ retrieval_records: 11 条
→ 按源: {'taobao_suggest': 5, 'amazon_suggest': 5, 'accio_tariff': 1}
→ 源连通性: {taobao_suggest: True, amazon_suggest: True, ebay_suggest: False, accio_tariff: True}
```
数据穿过 Researcher → GateA → Analyst → GateB 全链路，**电商源真实进入研报检索记录**。
（ebay 该轮 False 系返回空结果；同一 provider 在 3.4 的英文 query 下稳定 OK，属 query 语种匹配问题，非连接故障。）

### 3.8 admin 白名单自动纳入（零改动确认）
`server/admin.py:1369` 的 `VALID_PROVIDERS` 由 `KEYLESS_PROVIDERS` 派生：
```python
VALID_PROVIDERS = ["tavily", "mock", "coming_soon"] + _KEYLESS_IDS
```
等价验证：4 个新 id 均在 `VALID_PROVIDERS`（total=16）。**新增源无需改 admin.py**，M10-P2 的派生改造在此直接受益。

---

## 4. 诚实边界声明

| 项 | 状态 |
|---|---|
| 3 个 suggest 源的数据粒度 | **竞品关键词趋势**（候选词 + 热度），**非价格/销量**。已在设计阶段与 boss 对齐 |
| `accio_tariff` 税率明细覆盖 | 实测**仅 CN→US 完整**；其它国别对 HS 分类可用但税率可能返空 |
| `accio_tariff` 稳定性 | 第三方公开 API，无合同/限流契约；失败即 `ToolError` → 单源降级，不阻塞任务 |
| 长 query 行为 | suggest 类 API 对超长输入（如整句 topic）返回空——属该 API 天然粒度，已在 3.7 注明 |
| 未做的 | 未接 1688/拼多多（需授权）；未做价格/销量抓取；未自研电商分析 skill（DESIGN §7 Q5 待 boss 决策） |
| 真实 LLM e2e | 本轮用 StubLLMClient（429 规避中）；真实 LLM 端到端待配额恢复补跑 |

---

## 5. 结论

4 个电商真源**已实现、已分类、已容器化、已端到端接缝验证**：
- 端点契约：编码前 4/4 实测 200，与设计零偏差
- 单测：47 passed / 0 failed（+6 离线契约测试，补 M10-P1/P2 无单测之缺）
- 容器：重建后 27 条真实结果 / 4 源全通，与沙箱结果一致
- UI：`电商` 独立分类，API 面 18 插件 / 4 电商，全 `connected`
- 引擎：两条聚合路径 + 真实 run_report 全链路命中
- 诚实：非 CN→US 返空不编造；粒度边界明示

**自审 PASS**，提请第三闸（独立子代理审议）。
