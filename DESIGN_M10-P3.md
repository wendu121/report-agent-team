# DESIGN · M10-P3 通用 REST 插件框架（引擎接入外部数据源的桥）

**日期**：2026-09-10
**状态**：设计待评审（**不写代码**，先过设计）
**驱动**：boss 要求「抓一批电商类和电商竞品分析类上去」+ 选择「两者都要：先连连接器，再建桥」+ 海内外全覆盖。
**上游**：`DESIGN_PLUGIN_EXPANSION.md` §2 Phase 3（骨架已立，本文件细化）

---

## 1. 要解决的问题

### 1.1 架构墙（核心约束）
- report-agent-team 引擎是**独立 Docker 服务**，其检索能力由 `tools/data_sources.py` 的 `KEYLESS_PROVIDERS` 注册表驱动。
- WorkBuddy 连接器市场（OpenBoost/出海匠/LinkFox 等）是 **WorkBuddy 宿主层**能力，**引擎无法直调**。
- ⇒ **连接器 ≠ 引擎数据源**。要两全，必须建一座桥。

### 1.2 电商数据获取的现实困难
| 路线 | 可行性 | 说明 |
|---|---|---|
| 电商平台官方 API（Amazon SP-API / TikTok Shop Open API） | ❌ 需商家授权 + 资质审核 | 引擎侧不可行 |
| 第三方连接器（OpenBoost/出海匠） | ⚠️ 仅 WorkBuddy 层可调 | 需桥接，且多数不暴露可直调 HTTP 端点 |
| 公开零密钥接口 | ⚠️ 电商领域极少 | 需实测筛选 |
| **HTTP 桥接（本设计）** | ✅ 通用 | 任何暴露 HTTP 的数据源都能接 |

**关键诚实边界**：本设计交付的是**桥的能力**，不是"一定能拿到电商数据"的承诺。桥建成后，能否取到数据取决于**是否有一个可直调的 HTTP 端点**。若连接器不暴露端点，桥也无能为力——届时需另寻公开 API 或降级为 coming_soon。

---

## 2. 设计目标与边界

| 项 | 内容 |
|---|---|
| 目标 | 引擎支持"配置即接入"任意 HTTP 数据源（`config/plugins.d/<id>.yaml`），无需改代码 |
| 复用 | 完全复用 M10-P2 的 `KEYLESS_PROVIDERS`/`build_search_tool`/`DataSourcePlugin` 契约 |
| UI | **本期不做**（P3-b 独立里程碑，先支持手放 yaml 即用） |
| 明确不做 | 不硬啃需授权的平台 API；不写爬虫；不降低 TLS 校验 |

---

## 3. 核心设计

### 3.1 配置形态（`config/plugins.d/<id>.yaml`）
```yaml
id: my_ecom
name: 我的电商源
category: 电商
auth_type: none                    # none | api_key
provider: generic_rest             # 新 Provider 类型
enabled: true
base_url: https://api.example.com/v1/search
method: GET                        # GET | POST
timeout: 20
query_template:                    # {topic} 为检索词占位；${ENV} 走 _expand_env_vars
  q: "{topic}"
  limit: 10
  api_key: "${DS_MYECOM_API_KEY}"  # auth_type=api_key 时注入，密钥不落盘
result_path: data.items            # JSON 取数路径（点号分隔，支持数组下标）
field_map:                         # 归一为 SearchRecord
  title: name
  snippet: desc
  url: link
  score: relevance                 # 可选；缺失用默认 0.6
```

### 3.2 `GenericRestProvider` 实现要点
| 组件 | 设计 |
|---|---|
| 载入 | `plugins.d/*.yaml` 与 `plugins.yaml` 合并；同 id 时 `plugins.yaml` 优先（显式注册胜出） |
| 请求 | stdlib `urllib`（零依赖）；`method`/`headers`/`query_template` 渲染后发请求 |
| 取数 | `result_path` 按点号逐级 `dict.get`；命中 list 则逐项归一；越界/缺失 → `ToolError`（诚实失败） |
| 映射 | `field_map` 把源字段映射为 `{title,url,content,score}`；`url` 缺失的项跳过 |
| 密钥 | `query_template` 的 `${DS_X_API_KEY}` 由 `build_search_tool` 从 `load_secrets()` 注入；**yaml 内永不写明文** |
| 容错 | 沿用 M10-P2：解析异常 → `ToolError`；`search_many` 的 `except Exception` 兜底保聚合不击穿 |

### 3.3 安全边界（必须实现）
| 风险 | 措施 |
|---|---|
| SSRF | 仅允许 `http`/`https`；**禁止** `file://`/`ftp://`；可选黑名单内网段（10./172.16-31./192.168./127.） |
| 密钥泄露 | `${ENV}` 渲染只发生在请求时，不落盘、不回显、不入日志 |
| 注入 | `query_template` 值经 `urllib.parse.quote` 编码，不拼接原始字符串到 URL |
| 超时 | 强制 `timeout` 上限（如 ≤60s），防挂死 |
| 响应体积 | 上限（如 5MB）截断，防内存暴涨 |

### 3.4 与电商连接器的衔接（三段式）
```
① 连接器（WorkBuddy 层）
      ↓  boss 点击连接，获授权
② 若连接器暴露 HTTP 端点（或 boss 自备 API）
      ↓  写入 config/plugins.d/<id>.yaml
③ GenericRestProvider（引擎层）→ 引擎任务自动检索
```
**卡点**：第 ② 步需实测确认连接器是否暴露可直调端点。多数商业连接器**不暴露**（它们只对 WorkBuddy 宿主开放）。若不通，则本桥退化为「接 boss 自备的任意公开/自有 HTTP 数据源」——仍有价值。

---

## 4. 电商方向落地路径（海内外全覆盖）

### 4.1 优先实测的公开零密钥候选
| 方向 | 候选 | 待验 |
|---|---|---|
| 跨境电商趋势 | Google Trends（非官方端点）/ 公开商品聚合 | 连通性 + 数据可用性 |
| 国内电商 | 公开比价/商品搜索接口 | 同上 |
| 竞品情报 | 公开企业/商品舆情源 | 同上 |

⚠️ **不预设结论**：先探端点，通了才写 Provider（沿用 M10-P2 已验证的"探端点→写码"顺序）。

### 4.2 分期
| 期 | 内容 | 门禁 |
|---|---|---|
| **P3-a** | `GenericRestProvider` + `plugins.d/` 载入链 + 安全边界 | 三道闸 + 用**一个真实公开 API** 端到端样例 |
| **P3-b** | Plugins 页「自定义插件」表单 | 单独设计评审 |
| **P3-c** | 电商公开源实测接入（能拿多少算多少，拿不到就 coming_soon） | 同上 |

---

## 5. 验收标准（P3-a）
1. 手放一个 `plugins.d/demo.yaml` → 引擎 `load_data_sources()` 能读出该插件且 `status=connected`
2. `build_search_tool` 聚合时真实命中该源，结果带 `source=demo`
3. 安全：`file://` 被拒；`${ENV}` 未配置时 → 该源降级 mock 或 disconnect（不崩）
4. `result_path` 越界 → `ToolError`，不击穿聚合
5. 全量 pytest 不回归

---

## 6. 实测结论（2026-09-10，写码前探端点）

**重大发现：电商领域存在可直调的零密钥公开端点，不必等建桥即可落地！**

### 6.1 实测通过的电商端点
| 源 | 端点 | 返回 | 用途 |
|---|---|---|---|
| **淘宝 suggest** | `suggest.taobao.com/sug` | 干净 JSON `{"result":[["蓝牙耳机2026新款","100"],...]}` | 国内电商竞品关键词（2 次验证稳定） |
| **Amazon completion** | `completion.amazon.com/api/2017/suggestions` | JSON `{"suggestions":[{"value":"..."}]}` | 跨境竞品关键词 |
| **eBay autosug** | `autosug.ebay.com/autosug` | JSON `["kw",["sug1","sug2"]]` | 跨境竞品关键词 |

### 6.2 实测失败（诚实留痕，不再尝试）
| 源 | 结果 |
|---|---|
| 拼多多 suggest | 403 |
| 唯品会 / 抖音电商 | 502（代理不可达） |
| 阿里国际站 suggest | 502 |
| Google suggest / Etsy | 502 |
| Google Trends daily | 502 |
| 京东 dd.jd.com/suggest | 200 但为 HTML 落地页，无结构化数据 → 弃用 |

### 6.3 对设计的影响（**方案调整**）
原计划"先建桥再找数据"→ **实测后调整为「先直连真源（P3-a 简化版），建桥另计」**：
- **新增 3 个电商真源**（`taobao_suggest` / `amazon_suggest` / `ebay_suggest`），走 **M10-P2 同款 `KEYLESS_PROVIDERS` 架构**，零 key 直连，立刻可用。
- `GenericRestProvider`（原 P3-a）**降为可选**：仍值得做（让 boss 能接自备 API），但不再是"拿到电商数据"的前置条件。

### 6.4 诚实边界
- 检索建议给出的是**关键词/趋势信号**，**不是商品价格/销量**。要吃价格销量需商家授权 API，引擎侧不可行。
- 淘宝/Amazon/eBay suggest 均为**非官方公开接口**，可能限流或变更；按 M10-P2 惯例，失败即 `ToolError` 降级，不阻塞任务。

---

## 6b. Accio 调研结论（2026-09-10，boss 提供链接实地探访）

### 6b.1 探访过程（诚实留痕）
- boss 给 `accio-ai.com/work/app/plugins` 链接 → **登录墙拦截**（手机号/邮箱/Google/阿里），插件列表登录后才渲染。**主代理未尝试绕过登录**（边界）。
- 改从公开资料扒取：Accio 官方文档 + `github.com/AccioWork`（**User 非 Org**，13 个仓库真实存在）。

### 6b.2 Accio 插件清单（官方文档实锤）
分类：E-Commerce & Marketplaces / Productivity & Knowledge / Social Media / Communication / Developer Tools / Events
官方合作插件：Shopify / Amazon Seller Toolkit / eBay Selling / 1688 / Alibaba.com Logistics / Global Selling Agent / Semrush / Ahrefs / GEOly / Mailchimp / Klaviyo / Instantly / Apollo.io + Notion/Airtable/HubSpot/Netlify/Vercel 等。

### 6b.3 ⚠️ 重要修正：搜索摘要**夸大**了开源 skill 清单
- 搜索摘要称 AccioWork 开源了 9 个 skill（Product Selection / Market Insight / Review Summarizer 等）—— **实查 `agent-skills` 仓库仅有 1 个真 skill**，其余是 CSS 文件。摘要不可信，**已实测证伪**。
- 实际存在的仓库多为**免费计算器工具站**：cbm-calculator / hs-code-lookup / import-duty-calculator / incoterms-calculator / moq-calculator / profit-margin-calculator / shipping-cost-estimator / sourcing-cost-analyzer / uk-import-duty-calculator / china-to-uk-shipping-calculator。

### 6b.4 🎯 真正有价值的发现：零密钥关税 API（已实测 HTTP 200）
`AccioWork/agent-skills` 的 `accio-import-tariff-hs-code-calculator/SKILL.md` 暴露了一个**公开 API**：

```
POST https://www.accio.com/api/turtle/classify
Content-Type: application/json   （无需 API key，无需注册）
body: {source:"alibaba", originCountryCode:"CN", destinationCountryCode:"US",
       productName:"ceramic coffee mugs", digit:8}
```

**实测结果**（真实返回）：商品「陶瓷咖啡杯」→ HS 码 `69111045`，总关税 **26.5%**（一般关税 14% + 附加关税 12.5% Section 301），含完整税则明细 + 中英描述。

| 项 | 说明 |
|---|---|
| 覆盖 | 税率明细 **仅 CN→US**；HS 编码分类支持任意国别对（200+ 国家） |
| 数据 | HS 码 / 关税税率 / 关税构成（GEN 一般 + ADT 附加）/ 计税方式 |
| 价值 | **跨境电商竞品分析的"成本结构"维度**——可直接进引擎作 Provider |
| 风险 | 阿里的公开 API，无合同保障；可能限流/变更；按惯例失败即降级 |

### 6b.5 建议新增 4 个电商真源（P3-a'）
| id | 源 | 端点 | 用途 | 实测 |
|---|---|---|---|---|
| `taobao_suggest` | 淘宝 | `suggest.taobao.com/sug` | 国内电商竞品关键词 | ✅ 200 JSON |
| `amazon_suggest` | Amazon | `completion.amazon.com/api/2017/suggestions` | 跨境竞品关键词 | ✅ 200 JSON |
| `ebay_suggest` | eBay | `autosug.ebay.com/autosug` | 跨境竞品关键词 | ✅ 200 JSON |
| **`accio_tariff`** | **Accio/阿里** | `www.accio.com/api/turtle/classify` | **关税/HS码/落地成本** | ✅ 200 JSON |

全部走 M10-P2 同款 `KEYLESS_PROVIDERS` 架构，零 key 直连。

---

## 7. 待 boss 决策（已更新）
1. **是否批准先做 3 个电商真源**（淘宝/Amazon/eBay，零密钥，M10-P2 同款架构）——这是最快的"上去"路径
2. `GenericRestProvider` 建桥是否仍要做（做则 P3-b，能让 boss 接自备 API）
3. 电商数据粒度是否接受"关键词趋势"（而非价格/销量）
4. **【新增】是否采纳 Accio 关税 API**（`accio_tariff`，零密钥，补足"关税/落地成本"维度）
5. **【新增】电商 skill 方法论**：Accio 开源仅 1 个真 skill（关税计算），**原计划"移植 3 个电商 skill"因源不存在而不可行**——改为**自研**电商分析技能（如"竞品关键词 + 关税成本"组合分析法），还是先只做数据源、暂不做 skill？


