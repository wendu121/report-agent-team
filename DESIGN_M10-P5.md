# DESIGN · M10-P5 插件市场 UI 改造（移除「暂未推出」死端）

> 单一事实源：本文件。配套交付 `VERIFICATION_M10-P5.md`（门禁②）、`REVIEW_M10-P5.md`（门禁③）。
> 触发：boss 在插件市场页看到 `coming_soon` → 界面显示「即将推出」死端，要求改为「需要 KEY 就给键盘输入 + 真实教程链接」，并把分类收敛到 6 个。

## 1. 问题澄清（boss 问「暂未推出是什么意思」）

`coming_soon` 是后端 `load_data_sources()` 对 `provider: coming_soon` 插件的固定状态——
表示「这是真实外部服务 / 真实数据源，但**引擎尚未接入可用的后端 Provider**，故引擎在聚合检索时直接跳过它，绝不冒充可用」。

当前 4 个 `coming_soon` 插件：
| id | 真实服务 | 卡点 |
|----|---------|------|
| `qcc` 企查查 | 工商数据，需 API Key | 后端无 Provider 实现（需 key 才能 e2e 验证） |
| `xueqiu` 雪球 | 行情/讨论，需 Cookie/OAuth | 后端无 Provider 实现（且非标准 key） |
| `cninfo` 巨潮资讯 | 财报公告，公开端点 | 后端无 Provider 实现 |
| `scholar` Google Scholar | 学术引用 | 无官方 API，后端无 Provider |

旧 UI 把它们渲染成 `<el-tag>即将推出</el-tag>` 死端 —— 既不能配 key，也没有去哪拿 key 的指引。
这是**诚实但无功能的占位**，boss 要求改为「可操作」。

## 2. 设计原则（对齐治理铁律）

- **控制器而非查看器**：UI 必须能真实驱动后端（配 key、启用/停用）。
- **诚实边界**：未接入后端的插件，UI 明示「后端未接入 / 待接入」，不假装可用、不伪造「已连接」。
- **不瞎编**：教程链接只用**真实官网根域**（tavily.com / qcc.com / xueqiu.com / cninfo.com.cn / scholar.google.com），不臆造 API 端点。
- **零破坏性**：引擎 `provider=coming_soon` 跳过逻辑**不改**（保持诚实跳过），仅前端重解释状态 + 暴露 key 输入 affordance。

## 3. 方案

### 3.1 分类收敛为 6（界面美观）
当前 8 类（数据源/学术/百科/行情/新闻/财报/工商/电商）→ 6 类：

| 新分类 | 归入插件 |
|--------|---------|
| 综合检索 | tavily, gdelt |
| 学术文献 | arxiv, semantic_scholar, crossref, openalex, europe_pmc, scholar |
| 百科知识 | wikipedia, wikidata |
| 财经行情 | tencent_quote, xueqiu, cninfo |
| 企业工商 | qcc |
| 电商数据 | taobao_suggest, amazon_suggest, ebay_suggest, accio_tariff, hs_code_tariff |

前端按 `category` 动态分组（已有逻辑），配置写 6 类即渲染 6 个 section。

### 3.2 插件配置新增元数据字段（config/plugins.yaml）
每个插件可带：
- `doc_url`：官网/文档（真实根域）
- `key_guide_url`：去哪拿 key 的教程/控制台（仅 api_key/oauth 插件）
- `key_env`：密钥对应的 env 名提示（如 `DS_QCC_API_KEY`）

后端的 `_plugin_item()` 透传这三个可选字段 → 前端用。

### 3.3 前端状态重解释（Plugins.vue）
不再出现「即将推出」。按 `(status, auth_type, provider)` 推导展示：

| 条件 | 状态标签 | 动作区 |
|------|---------|--------|
| `status=connected` | 已连接（绿） | 停用 |
| `auth=api_key` 且未连接 | 待配置密钥（橙） | **配置密钥**（开弹窗）+「获取密钥教程 ↗」链接 |
| `auth=oauth` 且未连接 | 待接入后端·需凭证（橙） | 配置凭证说明 + 官网/教程 ↗ |
| `provider=coming_soon` 且 `auth=none`（cninfo/scholar） | 后端未接入（灰） | 官网 ↗ + 注「后端待实现」 |
| `provider=coming_soon` 且 `auth=api_key`（qcc） | 后端未接入·可预配密钥（橙） | 配置密钥（+诚实注「后端接入后生效」）+ 教程 ↗ |

> qcc 的「配置密钥」写入 `.secrets/plugins.env`（`DS_QCC_API_KEY`），与现有 connect 流程一致；
> 因后端 Provider 暂未实现，引擎仍跳过——UI 明确标注「后端接入后生效」，非假可用。真实 Provider 实现列为 follow-up（需 boss key 做 e2e）。

### 3.4 连接弹窗增强
- 顶部显示「为 <名称> 配置 API Key」
- 显示 `key_env` 提示（`DS_<ID>_API_KEY`）
- 显示「🔗 如何获取密钥：<key_guide_url>」可点击链接（真实官网）
- 注：key 不入库、不回显（沿用既有）

## 4. 变更文件清单（预计）
- `config/plugins.yaml`（M）：分类 + 新字段
- `server/admin.py`（M）：`_plugin_item` 透传 3 字段
- `web/src/types/ui.ts`（M）：`PluginItem` +3 可选字段
- `web/src/views/Plugins.vue`（M）：状态重解释 + 教程链接 + 弹窗增强

## 5. 诚实边界 / 明确不做
- **不实现** qcc/xueqiu/cninfo/scholar 的真实后端 Provider（无 key 无法 e2e 验证，实现即「瞎编」）。UI 暴露 key 输入 + 教程属「控制器前瞻」，标签明示未接入。
- **不伪造**任何教程 URL；只用官方根域。
- 引擎跳过 `coming_soon` 逻辑不变。

## 6. 验收（门禁①）
- `py_compile` 后端 OK；`pytest` 全绿；`vue-tsc` EXIT=0；`vite build` EXIT=0。
- 容器内 `/plugins` 接口返回 19 项、6 类、含新字段。
- 前端 4 个原 `coming_soon` 插件不再显示「即将推出」，改为上述可操作状态 + 教程链接。
