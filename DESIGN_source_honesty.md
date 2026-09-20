# DESIGN · 检索源诚实降级（Source Honesty）

> 状态：**待 boss 评审（未写代码）** · 2026-09-19
> 触发：boss 用子账号跑「2026 中国外卖行业报告」，得到 8 条 `[MOCK] Tavily 搜索…` 假来源 + 一段
> 无法核验的数据。boss 问「我子账号的设置是否对」。
> 立约依据：`CONSTITUTION.md`「禁假配置 / 诚实边界：不可用能力必须显式标 coming_soon/[MOCK]，
> 绝不挂 connected 冒充可用」。

---

## 1. 真机证据（不是推断，全部实测）

### 1.1 子账号配置本身是对的

`wendy1` 租户（`af28b675-…`）`plugins.yaml`：**22 个源 / 16 个真源已启用 / 5 个停用 / 0 个 coming_soon**。

```
✅ 真检索(16): open_meteo duckduckgo rss_news arxiv semantic_scholar crossref wikipedia
              openalex europe_pmc wikidata tencent_quote taobao_suggest amazon_suggest
              ebay_suggest accio_tariff hs_code_tariff
⚠️ 降级为假(1): tavily          ← 唯一问题
⬜ 未启用(5):  gdelt xueqiu cninfo qcc scholar
```

`GET /api/v1/plugins` 对 `wendy1/tavily` 返回 `enabled=true, status=disconnected` —— **UI 是诚实的**，
它一直说「tavily 没连上」。问题不在 UI，也不在 boss 的配置。

### 1.2 真正的大坑：tavily 的 key 对**所有**账号都失效了

| 位置 | 结果 |
|---|---|
| legacy `/app/.secrets/plugins.env` | **有** `DS_TAVILY_API_KEY`（没人读） |
| `tenants/9910ebbc…(wendy)/.secrets/plugins.env` | 改造后从未创建 → `load_secrets()` 读不到 |
| `tenants/af28b675…(wendy1)/.secrets/plugins.env` | 同上 |

`tools/data_sources.py:1389 _secrets_file()` 在多租户改造（commit `0f8511a`，2026-09-17）后改为
`tenancy.secrets_path("plugins.env")` → `tenants/<account_id>/.secrets/plugins.env`，
但存量密钥**没有迁移**。→ **tavily 从 9-17 起对主账号、子账号一律降级为占位。**

已修（本轮）：`scripts/migrate_legacy_secrets_to_tenant.py`（幂等、dry-run 可选）把 legacy key
搬进主账号租户目录；实测 `wendy` 的 tavily 现已返回**真结果**（艾媒咨询数据页）。落在 bind mount
`./tenants:/app/tenants` 上，容器重建不丢。

### 1.3 P0 缺陷：缺 key 时**伪造结果**，并且一路冒充到底

`tools/data_sources.py:1599-1608`：

```python
if prov == "tavily":
    key = secrets.get(f"DS_{sid.upper()}_API_KEY")
    if key: ...真 Provider...
    providers.append((s, MockProvider(sid, s.get("name", sid))))   # ← 静默塞假源
```

于是每次检索都**照常返回 5 条 `[MOCK] Tavily 搜索：<query> · 结果 N`**，且：

| 环节 | 表现 | 位置 |
|---|---|---|
| 前端 | 显示「**来源（8）**」，列 8 条 mock | `web/src/views/ChatEntry.vue:160-163` |
| 日志 | `[chat] web_search 各源命中={'tavily': 8}` —— **假命中被记成命中** | `chat_agent.py:524-529` |
| 引擎 | `web_search=MOCK(占位)` 的警告**永远不打印** | `tools/__init__.py:70` |
| 代码注释 | 「不冒充真实检索」——注释与行为相反 | `tools/data_sources.py:1584` |

**警告为什么失效**：`using_mock_search = not has_real`（`data_sources.py:1619`）是**全局布尔**。
keyless 真源（duckduckgo/rss_news/…）让 `has_real=True` → 标志为 False → `tools/__init__.py:70`
的 `[WARN] 检索已降级为 MockProvider` 分支**一次都没进去过**。守卫被「有别的真源」中和了。

### 1.4 假源还把真源挤出了展示位

同一次检索（子账号、同 3 条 query）复现：

```
tavily 15(假) · crossref 14 · openalex 12 · wikipedia 10 · arxiv 6 · taobao_suggest 5
europe_pmc 3 · accio_tariff 1            合计 66 条真实结果
```

**真源取到了 66 条**，但 `chat_agent.py:521` 是 `_build_sources(tool_entries)[:8]`，而结果保序
（`_trim_web_results` 保序、tavily 在插件表里靠前）→ **前 8 条全是 mock，真源被截掉**。
boss 看到的「来源（8）」里，**一条真的都没有**。

### 1.5 关于那份「报告」

boss 那次 `intent=chat`（**没走研报引擎**），所以没有报告文件产出，只是聊天里贴了一段 markdown。
因为检索层给不出可信数据，模型**诚实地说了「未能获取到可靠实时数据」**，然后**用自己记忆里的数字
（7848.2 亿 / 5.95 亿用户 / 美团 54%…）补了一份带「智研咨询 / 艾媒咨询」署名的报告**。
→ 结构完整、看着有据，**但一个来源都无法核验**。这是最危险的形态：**假来源 + 真腔调**。

---

## 2. 设计目标

1. **缺 key 的源不得伪造数据**：要么真、要么明说不可用，没有第三种。
2. **降级必须可见**：用户、日志、审计三处都能看到「哪个源没起来、为什么、怎么修」。
3. **mock 只用于显式声明**：`provider: mock`（离线开发/链路验证）才允许占位，且占位**不得冒充来源**。
4. 不动质量闸、不动 `max_rounds`、不引入新框架/新服务。

---

## 3. 方案（4 处改动，全部在既有函数内）

### 3.1 `tools/data_sources.py` · `build_search_tool()`

- **删除** `providers.append(..., MockProvider(...))` 的静默兜底（当前 1608 行）。
  `api_key` 源缺 key → **不注册**，追加到 `degraded`：
  `{"id", "name", "reason": "missing_key", "fix": "在「设置→数据源」填写密钥"}`。
- `provider: mock` 的源**仍按配置注册**，但结果打 `is_mock: True`。
- `SearchTool.__init__` 新增 `degraded: list[dict] | None`；`using_mock_search` 语义改为
  **「本次是否装载了显式 mock 源」**（不再由「有没有真源」推导）。
- `search_many` 给每个结果原样带 `is_mock`（mock 源产出），真实源不带。

### 3.2 `tools/__init__.py`

- `ToolBundle` 增字段 `degraded: list[dict]`（透传，不改现有字段语义）。
- 警告分支 `if web.using_mock_search:` → `if web.degraded:`，文案改为**逐源**列出：
  `[WARN] 数据源 tavily 缺少密钥，本次不参与检索。修复：设置→数据源→Tavily 填写 API Key`。

### 3.3 `chat_agent.py` · `_build_sources()` + `/chat` 响应

- `_build_sources` **过滤 `is_mock` 结果**：占位不进「来源」（不冒充引用）。
- `/chat` 响应新增 `source_warnings: list[str]`（由 `degraded` 生成）与 `sources_are_real: bool`。
- `chat_agent.py:524` 的 `_ws_src` 统计**只统计真实源**，并在日志里另打
  `degraded=[...]`，杜绝「假命中记成命中」。
- ChatAgent 系统提示：当 `degraded` 非空且问题需要实时事实时，**明确告知模型
  「本次无实时检索数据」**，并要求它**不得编造带机构署名的数据**（可答、但须标「来自模型自身知识，未经检索验证」）。
- **实现期精化（评审时请一并看这条）**：`source_warnings` **仅在本轮真的发起过 `web_search` 时**
  才输出降级告警。否则闲聊（不检索）也会挂一条「某源未配置密钥」，用户很快会学会无视它 ——
  告警的价值在于稀有。`sources_are_real` 的定义随之精确为：
  本轮未发起检索 → `True`（无可质疑之处）；发起过检索 → 仅当「无降级源 且 无占位被剔除」为 `True`。

### 3.4 前端 `web/src/views/ChatEntry.vue`

- 收到 `source_warnings` → 在回复上方显示醒目 `el-alert`（type=warning）：
  「本次未取得实时检索数据（tavily 未配置密钥）→ 回答来自模型自身知识，**请勿直接引用**」。
- 「来源（N）」保持只显示真实来源；若 `sources_are_real=false` 则**整个来源区不显示**。

### 3.5 顺带（低风险，同一 PR）

- `tools/data_sources.py:1460` legacy 回退分支把 `DS_TAVILY_API_KEY` 硬编码 → 改为
  `DS_{prov.upper()}_API_KEY`（与 `_recompute_connected:1443` 一致）。

---

## 4. 明确不做（避免过度工程）

- ❌ 不动 GateA 判决、不动 `max_rounds=2`、不动检索源清单 —— 那是质量闸口径，属 boss 决策。
- ❌ 不引入「自动重试 / 自动切模型 / 多 key 池」——本轮只解决「诚实」。
- ❌ 不删 `MockProvider`（离线链路验证仍需要），只改它的**触发条件**（显式声明）。
- ❌ 不动 legacy `/app/.secrets/plugins.env`（保留作回滚参照），迁移由脚本单向完成。

---

## 5. 验收（待实现后填实测值）

| # | 断言 | 判据 |
|---|---|---|
| S1 | 缺 key 的 api_key 源**不产出任何结果** | `search_many` 结果里无该源 |
| S2 | `degraded` 精确报出该源 + reason + fix | 字段级比对 |
| S3 | `using_mock_search` 在「显式 mock 源」时才为 True | 两个用例（有/无显式 mock） |
| S4 | `/chat` 的 `sources` **不含** mock 条目 | 结果里 `is_mock` 恒缺席 |
| S5 | `[WARN]` 打印逐源、含修复指引 | 日志字符串断言 |
| S6 | `_ws_src` 只统计真源；mock 不计入「命中」 | 日志断言 |
| S7 | 主账号 tavily 真结果、子账号 degraded（现状复刻） | 双账号 e2e |
| S8 | 回归：`verify_subaccount_isolation.py` 13/13、`verify_chat_interrupt_edit.py` 9/9 不回归 | 原样跑通 |
| S9 | 回归：引擎 `e2e_report_flow.py wendy` 终态 `done` 且报告非空 | **看终态不看 exit code** |

---

## 6. 风险与边界（如实声明）

1. **移除静默 mock 会让「无任何真源」的账号检索直接空结果**。这是**故意的**：UI 已显示 disconnected，
   引擎不该编数据。但必须同时上 §3.3 的提示，否则用户从「假答案」变成「没答案」却不知为何。
2. **子账号 tavily key 由谁定**：本设计**不自动跨租户复制密钥**（违背「每账号自有资源」立约）。
   子账号要么 boss 自己填，要么显式授权复用主账号同一把。→ **待 boss 决策，见 §7**。
3. 移除 mock 后，若某次模型只拿到学术源（crossref/openalex），报告质量可能下降 —— 这是**真实质量的可见化**，
   不是新问题。
4. 本轮改动会让**历史 chat 记录里已有的 mock 来源**保持存在（不追溯清洗）。

---

## 7. 待 boss 决策

| # | 问题 | 选项 |
|---|---|---|
| D1 | 子账号 tavily key | (a) 复用主账号同一把（共享额度） (b) 一直留空，只用 16 个 keyless 真源 (c) boss 另给一把 |
| D2 | 是否按本设计实施 | 设计评审通过后进入实现 + 三道闸（编译 → 自审 VERIFICATION → 独立审议 REVIEW） |
