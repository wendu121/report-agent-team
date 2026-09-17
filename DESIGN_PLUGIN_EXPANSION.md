# DESIGN_PLUGIN_EXPANSION.md — 插件市场扩展（M10 数据源扩展层）

> 状态：**待 boss 评审**（先设计后代码，治理铁律）
> 日期：2026-09-10 · 依赖：M9-2 数据源插件架构（DataSourcePlugin Protocol + build_search_tool 多源聚合，已就绪 ✅）
> boss 决策：四方向全做（免费学术源 / 金融免费源 / 通用 OpenAPI 框架 / 中文源）

## 1. 背景与领域定位

Accio Work 插件市场（boss 截图）有 13+ 插件：Accio 采购工具箱 / 国际站生意助手 / OKKI CRM / 1688 / Shopify / SHOPLINE / 跨境电商设计 / AI建站 / 语雀 / 飞书 / FastMoss / MuseSelect / DSers。其本质 = **电商 SaaS 官方授权对接**（阿里系自家+生态伙伴），由商业团队维护。

我方领域 = **研报数据源**（9-9 立约：抄架构、换领域；直接搬电商插件=方向性错误）。当前 6 插件仅 Tavily 为真，其余 5 个诚实标 `coming_soon`。

扩展原则：**只接真实存在、可核验的公开 API；每个 provider 上线前跑真实连通性冒烟；不可用的一律 `coming_soon` 不假装。**

## 2. 四阶段路线图

### Phase 1 — 免费学术/百科源（零 key，立刻做）✅ 推荐 first
| id | API | 认证 | 限流 | 解析 |
|---|---|---|---|---|
| `arxiv` | `https://export.arxiv.org/api/query` | 无需 key | 建议 1 req/3s | Atom XML → title/summary/link |
| `semantic_scholar` | `https://api.semanticscholar.org/graph/v1/paper/search` | 无 key | 100 req/5min（匿名池） | JSON → title/abstract/year |
| `crossref` | `https://api.crossref.org/works` | 无 key（polite pool：`mailto` 参数提限） | ~50 req/s polite | JSON → title/DOI/container |
| `wikipedia` | `https://zh.wikipedia.org/w/api.php`（中文优先） | 无 key | 宽松 | MediaWiki JSON → extract |

- 改动面：`tools/data_sources.py` +4 个 Provider 类；`config/plugins.yaml` 对应条目 `provider` 字段 `coming_soon → 真 provider 名`；`enabled: true`。
- 冒烟标准：每源 curl 实测 200 + 字段解析非空（留痕进 VERIFICATION）。

### Phase 2 — 金融免费源（provider 先写好，key 投放后即亮灯）
| id | API | 认证 | 免费档 |
|---|---|---|---|
| `alpha_vantage` | `https://www.alphavantage.co/query` | `DS_ALPHAVANTAGE_API_KEY`（env） | 25 req/日 |
| `finnhub` | `https://finnhub.io/api/v1/quote` | `DS_FINNHUB_API_KEY`（env） | 60 req/分 |
| `sec_edgar` | `https://www.sec.gov/cgi-bin/browse-edgar` | 无 key，但 UA 必须带邮箱 | 宽松（10 req/s） |

- 状态机沿用现有诚实边界：无 env key → `status: disconnected`（UI 灰显，不假装可用）；boss 投放 key 后热加载即 `connected`。
- **待 boss 提供各 key**（Alpha Vantage/Finnhub 免费注册即得）。

### Phase 3 — 通用 OpenAPI 插件框架（新功能，本设计先立骨架）
目标：boss 在 UI 贴「REST API + 参数模板 + 字段映射」→ 动态成为可用插件（Accio 生态的本质架构）。
- 配置形态（`config/plugins.d/<id>.yaml`，bind mount 已有）：
  ```yaml
  id: my_api
  name: 我的自定义源
  provider: generic_rest        # 新 Provider 类型：读配置动态实现
  base_url: https://api.example.com/v1/search
  method: GET
  query_template: {q: "{topic}", api_key: "${DS_MYAPI_KEY}", limit: 5}
  result_path: data.items       # JSON 取数路径
  field_map: {title: name, snippet: desc, url: link}
  enabled: true
  ```
- 引擎侧：`GenericRestProvider(spec)` 读 spec 动态发请求 + `result_path` 取数 + `field_map` 归一为统一 SearchRecord。
- 安全边界：query_template 里的 `${ENV}` 走现有 `_expand_env_vars`，密钥不落盘；SSRF 防护仅允许 http/https。
- **UI（Plugins 页）新增「自定义插件」表单**是独立小里程碑（P3-b），可后置——先支持手放 yaml 即用。

### Phase 4 — 中文源（风险已知，boss 知情选择）
| id | 路线 | 风险声明（诚实边界） |
|---|---|---|
| `cninfo` 巨潮 | 官方公开搜索接口（`http://www.cninfo.com.cn/new/hisAnnouncement/query`），无需 key | 接口无合同保障，可能限流/变更；仅公告标题+PDF 链接 |
| `xueqiu` 雪球 | 非官方接口需 cookie，**不稳定** | 随时失效；降级策略=失败即标记 disconnected 不阻塞任务 |
| `qcc` 企查查 | **不做非官方硬啃**（需企业授权 + 法律风险） | 保持 `coming_soon`，等 boss 拿到官方 key |

- 巨潮先做（接口公开度最高）；雪球做实验性 provider（`experimental: true` 标注）；企查查维持现状。

## 3. 实施顺序与验收

| 阶段 | 内容 | 验收门禁 |
|---|---|---|
| P1 | arxiv + semantic_scholar + crossref + wikipedia 4 真源 | 三道闸 + 每源 curl 冒烟留痕 |
| P4a | cninfo 官方接口 + xueqiu 实验性 | 同上 + 失败降级测试 |
| P2 | alpha_vantage / finnhub / sec_edgar provider（key 后补亮灯） | py_compile + 无 key 时 disconnected 态测试 |
| P3 | GenericRestProvider + plugins.d 加载链 | 三道闸 + 用一个真实公开 API（如 hn）做端到端样例 |
| P3-b | Plugins 页「自定义插件」表单 | 单独设计评审后再做 |

## 4. 明确不做（诚实边界）
- 不伪造任何 provider 的返回数据；Mock 仅限测试。
- 企查查/Wind/Bloomberg 等授权源不硬啃。
- 不做网页爬虫版数据源（反爬维护成本不可控），除非接口公开稳定。
