# DESIGN_M9-2 · 数据源插件市场（DataSource Plugins，真·控制器）

> 元信息：起草 2026-09-09（M9-1 真·智能体市场已交付 `7a7762a` 后，M9 第二里程碑）
> 范围裁定：源自 [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.2（数据源插件市场，**领域内容已替换**，非电商插件）
> 状态：**待评审**（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议，全 PASS 才动代码）
> 关联：
> - 承接 M9-1 的「registry + 复合写 + UI 市场 + 三道闸」范式（[DESIGN_M9-1.md](./DESIGN_M9-1.md)）
> - 复用 M8 的 ruamel round-trip / 原子写 / 审计 / 热加载硬约束
> - 视觉沿用 [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §6（白底 + 绿 `#10B981`）

---

## 0. 范围裁定与诚实边界

### 0.1 方向（抄平台架构、换领域内容）
Accio 图4「插件市场」是电商插件（Shopify/Alibaba/TikTok）。研报系统**必须替换领域内容**（DESIGN_PLATFORM_FUNCTIONS §2.2）：
- ✅ 数据源插件 = Tavily / arXiv / 雪球 / 巨潮资讯 / 企查查 / Google Scholar
- ❌ 直接实现电商插件 = 方向性错误（评审须核查）

### 0.2 核心裁定：插件必须是"控制器"，不是"装饰卡片"
boss 立约（UI 必须是 controller，不能假配置）。因此 M9-2 不只是市场 UI，更要让**插件的启停/连接状态真实驱动引擎检索**：
- 启用 Tavily → 检索结果出现 `source=Tavily`；禁用 → 引擎不再取它，检索可能为空交由 GateA 判 escalate。
- `coming_soon` 源（arXiv/雪球/巨潮/企查查 等）**永不进入引擎**，即使被"安装"，UI 显示「即将推出」，引擎跳过 —— 不冒充可用。

### 0.3 不做（避免范围蔓延 / 过度工程）
- 不为每个国内源现写爬虫/反爬（雪球/巨潮/企查查 标 `coming_soon`，等 boss 后续指定再接）。
- 不做 OAuth 真流程（雪球 Cookie 之类标 `coming_soon`）。
- 不做插件"运行时动态加载 Python 模块"（超出 M9-2；provider 代码随引擎发版，registry 仅声明启用/配置）。
- 不重复造 Agent 市场的轮子：复用 M9-1 的 registry + 复合写 + 市场页范式，但**数据模型与引擎消费点不同**（插件驱动检索，Agent 驱动流水线）。

---

## 1. 当前检索真实状态（代码实证，非凭记忆）

### 1.1 `config/tools.yaml`（现状）
单 provider 选择：`web_search.provider = tavily|mock` + `max_results` + `timeout` + `fallback_to_mock`。密钥从 `TAVILY_API_KEY` 环境变量读（注释明写"不落配置文件"）。

### 1.2 `tools/web_search.py`（现状）
- `WebSearchProvider` Protocol：`search(query, max_results) -> list[{title,url,content,score}]`
- `TavilyProvider`（真，需 `TAVILY_API_KEY`）、`MockProvider`（占位，`[MOCK]` 前缀）、`WebSearchTool.search_many(queries, agent)` → `(results, status)`，结果字段 `{id,url,title,snippet,credibility,_query}`。

### 1.3 `tools/__init__.py build_tools`（现状）
读 `config/tools.yaml`，按 `provider` 选 Tavily/Mock，`using_mock_search` 标记。**每次调用重读 yaml**（天然热加载，无需 watchfiles）。返回 `ToolBundle(web_search=WebSearchTool, ...)`。

### 1.4 orchestrator 消费点（现状）
- `orchestrator.py:655` `tools.web_search.search_many(queries, agent=role)` → `fetched_new` → `merge_search_results` → `search_results` → 注入 Researcher `extra_context`。
- `retrieval_records` 由下游 LLM 从 `search_results` 结构化产出（`_fmt_retrieval_records` 透传 dict）。
- 结果 dict 当前**无 `source` 字段** → 无法区分来源。M9-2 补 `source`。

### 1.5 诚实约束（决定设计边界）
- **C1 单 provider**：`build_tools` 只认一个 provider，无法多源聚合。须改为"按 enabled 插件列表聚合"。
- **C2 无来源标记**：`retrieval_records` 无 `source`，无法审计"这条来自哪个数据源"。须补。
- **C3 密钥落盘风险**：现有密钥走 env（安全）。M9-2 新增插件的密钥**仍走 env/密钥文件，不进 `plugins.yaml`**（避免提交泄露）。
- **C4 热加载已有**：`build_tools` 每次重读 yaml → 改 `plugins.yaml` 下一个任务即生效，**零重启**（延续 M9-1 克制立约）。

---

## 2. 设计目标（Goals / Non-Goals）

### 2.1 必须做到（Goals）
- G1：`config/plugins.yaml` 成为数据源插件 SoT（id/name/icon/category/auth_type/enabled/status/provider）。替代 `tools.yaml` 的 `web_search.provider` 单选。
- G2：`tools/data_sources.py` 新增 `DataSourcePlugin` Protocol + `TavilyProvider`(复用 web_search) + `MockProvider`(按源 `[MOCK] <name>` 标记) + `ComingSoonProvider`(raise，绝不冒充) + `load_data_sources()`(mirror load_model_mapping) + `build_search_tool(enabled)`(多源聚合，结果带 `source`)。
- G3：`build_tools` 消费 enabled 插件 → `tools.web_search` 为 `build_search_tool` 产物；无 enabled 真源时回落 legacy `tools.yaml`（向后兼容）。`using_mock_search` 反映"无任何真源"。
- G4：orchestrator `retrieval_records` 带 `source` 字段（来自聚合结果），GateA 上下文可显示来源分布（可选）。
- G5：UI `/plugins` 市场页（Accio 图4：市场/我的 tabs + 搜索筛选 + 卡片 + 安装/连接/启停）。
- G6：后端 `GET /api/v1/plugins`(公开) + `GET/POST/PUT/DELETE /api/v1/admin/plugins`(复合写：plugins.yaml + 密钥文件 + 审计；ruamel round-trip)。
- G7：`coming_soon` 源引擎永不取数（status 永远非 connected，build_search_tool 跳过）。
- G8：密钥走 `.secrets/plugins.env`（`KEY=VALUE`，bind mount，`gitignore`），engine 读 `DS_<ID>_API_KEY`；tavily 兼容旧 `TAVILY_API_KEY`。
- G9：市场列表公开 GET（仿 `/templates` / `/agents-library`）；新增/删除 admin CRUD（仿 M9-1）。

### 2.2 不做（Non-Goals）
- N1：arXiv/雪球/巨潮/企查查/Google Scholar 真实爬虫（标 `coming_soon`）。
- N2：OAuth 真授权流（标 `coming_soon`）。
- N3：插件运行时动态 import 模块（provider 随引擎发版）。
- N4：M9-3 技能库 / M9-4 渠道 / M9-5 对话入口（本里程碑只做数据源插件）。
- N5：多用户/RBAC（沿用单 admin token）。
- N6：输出格式 pptx/docx/pdf（推 M9 后续 / 已有 TD）。

---

## 3. 架构方案

### 3.1 `config/plugins.yaml`（新 SoT）
```yaml
# 数据源插件注册表 · 接管 config/tools.yaml 的 web_search.provider 单选
# 热加载：build_tools 每次重读（mirror model_mapping/agents_library），改完下一个任务即生效
plugins:
  - id: tavily
    name: Tavily 搜索
    icon: ""                            # 空=前端渲染首字母圆/默认图标
    category: 数据源
    description: 实时网络检索，支持学术/新闻/财经源
    auth_type: api_key                  # none | api_key | oauth
    provider: tavily                    # 对应 tools/data_sources.py 的 Provider 实现键
    enabled: true                       # 控制器开关：false → 引擎不取此源
    status: connected                   # connected | disconnected | coming_soon（运行时可由引擎重算，此处为初值）
    builtin: true                       # 内置不可删
  - id: arxiv
    name: arXiv 学术
    icon: ""
    category: 学术
    description: 学术论文预印本检索
    auth_type: none
    provider: coming_soon               # 未接入：引擎永远跳过
    enabled: false
    status: coming_soon
    builtin: true
  - id: xueqiu
    name: 雪球
    category: 行情
    description: A股/港股行情与讨论
    auth_type: oauth                    # Cookie/OAuth，暂未接
    provider: coming_soon
    enabled: false
    status: coming_soon
    builtin: true
  # 巨潮/企查查/Google Scholar 同理 coming_soon（builtin:true, enabled:false）
```
**派生**：`connected` 实际由引擎 `load_data_sources` 重算 —— `auth_type==none` → connected；`auth_type==api_key` 且 `DS_<ID>_API_KEY` 存在于 env/`.secrets` → connected，否则 disconnected；`provider==coming_soon` → 永远 coming_soon（无视 enabled）。UI 展示以重算值为准。

### 3.2 `tools/data_sources.py`（新）
```python
class DataSourcePlugin(Protocol):
    id: str; name: str; category: str; auth_type: str
    def search(self, query: str, max_results: int) -> list[dict]: ...

class TavilyProvider:        # 从 web_search.TavilyProvider 复用，key 改读 DS_TAVILY_API_KEY（兼容 TAVILY_API_KEY）
    ...
class MockProvider:          # 按 source 标记：[MOCK] <name> ...
    def __init__(self, source_id, source_name): ...
class ComingSoonProvider:    # search() → raise ToolError("数据源尚未接入")
    ...

PROVIDER_REGISTRY = {        # provider 键 → 类（新增真源在此登记，不动态 import）
    "tavily": TavilyProvider,
    "mock": MockProvider,
    "coming_soon": ComingSoonProvider,
}

def load_data_sources(path=None) -> list[dict]:
    # 读 plugins.yaml；缺省回落 tools.yaml（backward-compat）：若 plugins.yaml 不存在，
    # 返回 [legacy tavily/mock 单源 spec]
    # 重算每个插件 connected 状态（见 §3.1 派生规则）

def build_search_tool(specs, secrets: dict, max_results=5, timeout=20):
    """聚合 enabled 且非 coming_soon 的插件，返回带 .search_many 的对象。"""
    # 仅保留 spec.enabled and spec.provider != "coming_soon"
    # 逐个 provider.search → 结果补 source=spec.id → 按 url 去重
    # 返回 SearchTool(results, status)；无可用源 → 返回 MockProvider 聚合（using_mock=True）
```

`SearchTool.search_many(queries, agent)` 签名**对齐** `WebSearchTool.search_many` → orchestrator `tools.web_search.search_many` 调用**零改动**。
- 结果 dict 新增 `source` 字段（= plugin id）。
- `tool_status` 条目 `tool` 字段写 `f"search:{source_id}"`（便于审计区分来源）。

### 3.3 `tools/__init__.py build_tools` 适配（C1 解决）
```python
def build_tools(config_path=None):
    specs = load_data_sources()                     # 读 plugins.yaml（热加载）
    secrets = load_secrets()                        # 读 .secrets/plugins.env + env
    if specs:                                       # 有新 registry
        web = build_search_tool(specs, secrets, ...)
        using_mock = web.using_mock_search
    else:                                           # 向后兼容：legacy tools.yaml
        web = _legacy_build(config_path)            # 原 Tavily/Mock 逻辑
        using_mock = web.using_mock_search
    return ToolBundle(web_search=web, data_proc=..., doc_export=..., using_mock_search=using_mock)
```
`tools.yaml` 保留 `data_proc`/`doc_export`（不变，仍由 build_tools 读）；`web_search` 块在 plugins.yaml 接管后可标记 deprecated 或保留作 fallback（向后兼容，回应 MINOR-2）。

### 3.4 orchestrator 适配（C2 解决）
- `retrieval_records` 自动带 `source`（`search_results`/`merge_search_results` 透传 dict，补 `source` 即可）。
- `search_many` 返回已带 `source` → `fetched_new` 带 `source` → `search_results` 带 `source` → LLM 结构化产出 `retrieval_records` 时应保留 `source`（**Researcher prompt 须显式指示"每条保留其 source 字段"**，回应 MINOR-1）。
- **tool_status 兼容性（回应 MAJOR-2）**：`search_many` 产出的 `tool_status` 条目**保留 `tool="web_search"`**（既有 `tests/test_orchestrator.py:180`、`tests/test_regression_edge.py:146` 硬断言不可破坏），新增独立 `source` 字段标识来源；不改成 `search:{source_id}`。
- GateA `_fmt_retrieval_records` 可加一行来源分布统计（可选，不强制）。

### 3.5 密钥管理（C3 解决，G8）
- `load_secrets()`：读 `.secrets/plugins.env`（`KEY=VALUE`）+ `os.environ`；返回 `{DS_TAVILY_API_KEY, DS_ARXIV_API_KEY, ...}`（统一前缀 `DS_<ID大写>`）。
- `tavily` 额外兼容旧 `TAVILY_API_KEY`（缺失 `DS_TAVILY_API_KEY` 时回落）。
- `.secrets/` 目录 bind mount（compose 加 `./.secrets:/app/.secrets`），`.gitignore` 加 `.secrets/`。
- admin `POST /admin/plugins/{id}/connect` 收 key → 追加/覆盖 `.secrets/plugins.env` 的 `DS_<ID>_API_KEY`（原子写）→ 审计 `.audit/plugins`。**不进 `plugins.yaml`**。

---

## 4. 后端 admin API（server/admin.py 扩展）

### 4.1 市场读取（公开，仿 `/templates` / `/agents-library`）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/plugins` | 公开列表（市场页用）。返回 plugins 数组（id/name/icon/category/description/auth_type/enabled/status/builtin，status 以引擎重算值为准）+ meta（registry 文件、connected 含义） |

### 4.2 管理 CRUD（admin token）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/plugins` | 同公开列表（带校验状态） |
| POST | `/api/v1/admin/plugins` | 复合新增自定义插件（见 §4.3） |
| PUT | `/api/v1/admin/plugins/{id}` | 更新（enabled 启停 / status / description / category）。**控制器入口** |
| DELETE | `/api/v1/admin/plugins/{id}` | 删除自定义插件（builtin 拒删；清理 registry + 审计） |
| POST | `/api/v1/admin/plugins/{id}/connect` | 写密钥到 `.secrets/plugins.env`（auth_type=api_key） |

### 4.3 `POST /admin/plugins` 复合写
请求体 `PluginPost`：`id`（正则，大写约定）、`name`、`category`、`description`、`auth_type`(none|api_key|oauth 枚举)、`provider`(须在 PROVIDER_REGISTRY 或 coming_soon)。
执行：
1. 校验：`id` 唯一（409）；`auth_type`/`provider` 枚举（400）；`provider != coming_soon` 须是真实现（否则 400，防把 coming_soon 当真源新增）。
2. 追加 `plugins.yaml` 条目（ruamel round-trip，保留注释）。
3. 审计 `.audit/plugins`。
4. 失败回滚 + 400 + rejected 审计。

### 4.4 `PUT /admin/plugins/{id}`（控制器核心）
- `enabled` 翻转 → 改 `plugins.yaml` → 下一个任务引擎据此聚合/跳过 → **真改检索行为**。
- `auth_type=api_key` 且 `enabled=true` 但无 key → status 重算为 disconnected（UI 提示去连接）。
- `coming_soon` 插件拒绝 PUT enabled=true（400：「该数据源尚未接入」）。

### 4.5 `POST /admin/plugins/{id}/connect`（G8）
- `auth_type != api_key` → 400。
- 写 `DS_<ID>_API_KEY=<key>` 到 `.secrets/plugins.env`（原子写 + 审计）；不回显 key（响应只返 status=connected）。

---

## 5. 前端市场页（web/src/views/Plugins.vue 新建 + 路由）

### 5.1 路由（web/src/router/index.ts）
```ts
import PluginsMarket from '@/views/Plugins.vue';
{ path: '/plugins', name: 'PluginsMarket', component: PluginsMarket, meta: { title: '数据源插件' } },
```
侧边栏（DefaultLayout.vue）加「数据源」入口（与「智能体」「设置」并列）。

### 5.2 `/plugins` 市场页（Accio 图4，视觉见 §6）
- 顶部：标题 + 描述 + Tabs（市场 / 已安装）。
- 搜索框 + 分类筛选（数据源/学术/行情/财报/新闻/社交）。
- 卡片网格：图标 + 名称 + 分类 + 描述 + 状态标签（已连接绿 / 未连接灰 / 即将推出黄）+ 动作：
  - **安装/启用** → `PUT /admin/plugins/{id}` `enabled=true`（builtin 源）。
  - **连接** → 弹窗填 API Key → `POST /admin/plugins/{id}/connect`（api_key 类型）。
  - **停用** → `PUT enabled=false`。
  - **删除** → `DELETE`（仅自定义，builtin 禁）。
- 数据：`GET /api/v1/plugins`（公开）。

### 5.3 视觉（勿另起炉灶）
复用 DESIGN_PLATFORM_FUNCTIONS §6 色板：卡片白底 `#FFFFFF` + 圆角 8-12px + 1px `#E5E7EB`；主色 `#10B981`；底 `#F9FAFB`。

---

## 6. 数据流（端到端）
```
市场页 /plugins
  ├─ 浏览插件（GET /plugins，公开；status 引擎重算）
  ├─ 启用 Tavily → PUT /admin/plugins/tavily {enabled:true}
  │      └─ build_tools 读 plugins.yaml → build_search_tool 含 tavily → 检索结果带 source=tavily
  ├─ 连接 → POST /admin/plugins/tavily/connect {key} → .secrets/plugins.env 写 DS_TAVILY_API_KEY
  ├─ 停用 → PUT {enabled:false} → 引擎跳过 tavily → retrieval_records 无此源
  └─ 自定义新增 → POST /admin/plugins → plugins.yaml 追加 → 审计
        └─ 引擎 build_search_tool 自动接纳（真驱动闭环）
```

---

## 7. 校验规则（后端，写盘前置）
- `id` 匹配 `^[A-Za-z0-9_-]+$`，唯一（409）。
- `auth_type ∈ {none, api_key, oauth}`（枚举 400）。
- `provider` 须在 PROVIDER_REGISTRY（含 coming_soon）否则 400。
- 新增真源（`provider != coming_soon`）须有对应实现键（防把未实现当真源）。
- `coming_soon` 插件拒绝 `PUT enabled=true`（400）。
- 复合写任一步失败 → 回滚 + 400 + rejected 审计。
- 密钥文件写用原子写 + `.gitignore` 已含 `.secrets/`。

---

## 8. 热加载 / 原子写 / 审计（复用现有）
- 热加载：`build_tools` 每次重读 `plugins.yaml`（天然，无需 watchfiles）。
- 原子写：复用 `_atomic_write`（admin.py）。
- 审计：复用 `_write_audit`，subdir `.audit/plugins`。
- yaml 保留注释：plugins.yaml 用 ruamel round-trip（初建也 round-trip）。

---

## 9. 验证计划（三道闸证据）

| 项 | 方法 | 预期 |
|---|---|---|
| ① 校验 | `py_compile` orchestrator.py / tools/data_sources.py / tools/__init__.py / server/admin.py；`npm run build`（前端 typecheck） | 0 错 |
| ② 自审 | VERIFICATION_M9-2.md §17：设计偏差 + 诚实边界 + 复合写回滚论证 | 见下 |
| ③ 独立审议 | REVIEW_M9-2.md（派独立子代理，严禁主代理自签） | PASS/NOTES |
| **E1 全量回归** | 默认 plugins.yaml（Tavily mock enabled）→ stub run `mode=pass` | status=done，retrieval_records 每条带 `source=tavily` |
| **E2 控制器效应** | 改 plugins.yaml `tavily.enabled=false` → 重跑 | 无 enabled 真源 → retrieval_records 空 → GateA escalate（证明启停真改引擎）；复原 |
| **E3 自定义源接入** | `POST /admin/plugins` 加 arxiv（provider=mock）→ `GET /plugins` 出现 → 重跑 | retrieval_records 含 `source=arxiv`（build_search_tool 自动接纳） |
| **E4 coming_soon 守卫** | arxiv 即便 enabled=true 但 provider=coming_soon → 引擎跳过 | 无 `source=arxiv` 假结果；UI 显示即将推出 |
| **E5 密钥持久化** | `POST /admin/plugins/tavily/connect {key}` → `.secrets/plugins.env` 含 `DS_TAVILY_API_KEY` → 引擎 `load_secrets` 读到 | （429 态 Tavily 仍走 mock，但文件写/读机制真实，using_mock_search 反映） |
| **E6 删除清理** | `DELETE /admin/plugins/arxiv` → plugins.yaml 条目移除 + 审计 → 重跑排除 | 无 `source=arxiv` |
| **E7 多源聚合** | 启用 tavily(mock)+arxiv(mock) → 重跑 | retrieval_records 去重 + 两源 `source` 均出现 |

> 全部离线（stub LLM，不触 new-api 配额，429 规避态）。E5 真实密钥机制可独立单测（文件写/读）。

---

## 10. 诚实边界 / 风险

- **R1 coming_soon 不冒充**：arXiv/雪球/巨潮/企查查/Google Scholar 一律 `provider=coming_soon`，引擎 `build_search_tool` 跳过，UI 标「即将推出」。绝不返回假检索。
- **R2 仅 Tavily 真联网**：当前 429 规避态下 Tavily 仍走 MockProvider（清晰 `[MOCK]` 标记），不冒充真实检索。`using_mock_search` 审计可见。
- **R3 密钥不落 config**：`.secrets/plugins.env` bind mount + gitignore，env 读取，plugins.yaml 无 key 字段。
- **R4 backward-compat**：plugins.yaml 缺失时 `build_tools` 回落 legacy `tools.yaml` 单 provider，现有行为不变（防回归）。
- **R5 复合写回滚**：plugins.yaml 写失败须回滚 + 审计 rejected（E3/E6 实证）。
- **R6 无任务列表页缺口**：不在本里程碑（推 M9-5）。

---

## 11. 不做清单（存档备查）
见 §2.2 N1-N6。国内源真爬虫、OAuth、动态 import、M9-3~5、RBAC、输出格式渲染——均不在本里程碑。

---

## 12. 提交纪律
- 本地 `git commit --only report-agent-team`，**不 push**（boss 指令）。
- 改动集（预估）：`tools/data_sources.py`(新) / `tools/__init__.py`(build_tools 接 plugins) / `tools/web_search.py`(TavilyProvider 导出复用) / `config/plugins.yaml`(新) / `orchestrator.py`(retrieval_records 带 source，最小改动) / `server/admin.py`(plugins CRUD + connect + 密钥文件) / `web/src/router/index.ts` + `web/src/views/Plugins.vue` + `DefaultLayout.vue`(侧边栏) / `.gitignore`(+`.secrets/`) / `.audit/`(新建) / `VERIFICATION_M9-2.md` / `REVIEW_M9-2.md`。
- 三道闸全 PASS 才 commit；独立审议 `reviewed-by` 严禁主代理自签。
- 文档漂移回改：本设计落地后回改 `DESIGN_PLATFORM_FUNCTIONS.md` §3.2（细化"真控制器"实现，消除"仅市场卡片"漂移）与 §7，随本里程碑入库。

---

## 13. 评审修订记录

| 轮次 | 日期 | 状态 | 评审者 | 主要修订 |
|---|---|---|---|---|
| 草案 | 2026-09-09 | 待评审 | （主代理） | 初版：plugins.yaml SoT + data_sources 抽象 + build_search_tool 聚合 + 密钥文件 + 市场 UI + 控制器效应 |
