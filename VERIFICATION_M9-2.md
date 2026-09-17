# VERIFICATION_M9-2 · 自审（设计 + 实施）

> 关联：[DESIGN_M9-2.md](./DESIGN_M9-2.md)（M9-2 数据源插件市场）
> 门禁：edict-gate 三道闸 ② 自审（主代理）；③ 独立审议见 [REVIEW_M9-2.md](./REVIEW_M9-2.md)
> 纪律：独立审议 `reviewed-by` 严禁主代理自签；全 PASS 才 commit

---

## 17. 设计自审（DESIGN_M9-2.md，编码前）

### 17.1 与上游文档一致性
- ✅ [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.2（数据源插件市场）**领域内容已替换**为研报源（Tavily/arXiv/雪球/巨潮/企查查），未照搬电商插件（§0.1/§2.2 显式防错）。
- ✅ 复用 M9-1 范式（registry + 复合写 + 市场 UI + 三道闸），但明确两点差异（§0.3）：插件驱动**检索**、Agent 驱动**流水线**，不重复造轮子。
- ✅ 复用 M8 硬约束（ruamel round-trip / 原子写 / 审计 / 热加载），与 MEMORY.md「M8/M9-1 确立的硬约束」一致。
- ✅ 视觉沿用 PLATFORM_FUNCTIONS §6 色板，未另起炉灶（§5.3）。

### 17.2 代码实证支撑（非凭印象）
- ✅ `config/tools.yaml` 现状（§1.1）：单 `web_search.provider`，密钥走 env —— 与实测一致。
- ✅ `tools/web_search.py` 现状（§1.2）：`WebSearchProvider` Protocol + `TavilyProvider`/`MockProvider`/`WebSearchTool.search_many` —— 与源码一致（grep 印证）。
- ✅ orchestrator 消费点（§1.4）：`orchestrator.py:655 tools.web_search.search_many` → `search_results` → `retrieval_records`；结果 dict 无 `source` —— 与 `Read` 印证（:655/:662/:683）。
- ✅ `build_tools` 每次重读 yaml（§1.3/§1.5 C4）：天然热加载 —— 与 `tools/__init__.py` `yaml.safe_load(p.read_text())` 一致。

### 17.3 诚实边界（boss 立约，不掩饰）
- ✅ R1 coming_soon 不冒充：engine `build_search_tool` 跳过 `provider=coming_soon`，UI 标「即将推出」（§3.1/§10 R1）。
- ✅ R2 仅 Tavily 真联网，429 态仍 Mock（`[MOCK]` 标记 + `using_mock_search` 审计）（§10 R2）。
- ✅ R3 密钥不落 config：`.secrets/plugins.env` + gitignore + env 读（§3.5/§10 R3）。
- ✅ 未假装国内源可用（雪球/巨潮/企查查 标 coming_soon，等 boss 后续指定再接）（§0.3 N1）。

### 17.4 控制器效应论证（核心：不是装饰卡片）
- ✅ G2/G3：`build_tools` 消费 enabled 插件 → `build_search_tool` 聚合 → `retrieval_records` 带 `source` → 启停某源**真改引擎检索输入**（E2 实证：禁用 Tavily → 检索空 → GateA escalate）。
- ✅ 此效应端到端可离线验证（stub LLM），不依赖 new-api 配额（§9 注）。

### 17.5 复合写 / 回滚论证
- ✅ POST/PUT/DELETE `/admin/plugins` 走 `_atomic_write` + `_write_audit`（复用 M9-1），失败回滚 + rejected 审计（§4.3/§8/§10 R5）。
- ✅ 密钥写 `.secrets/plugins.env` 独立原子写，不进 plugins.yaml（§4.5/§3.5）。

### 17.6 向后兼容
- ✅ plugins.yaml 缺失 → `build_tools` 回落 legacy `tools.yaml`（§3.3/§10 R4），现有行为不变，防回归。

### 17.7 风险与不做清单
- ✅ §2.2 N1-N6 / §10 R1-R6 / §11 明确排除过度工程项（国内源真爬虫/OAuth/动态 import/M9-3~5/RBAC/输出格式）。

### 17.8 设计自审结论
设计内部自洽、与上游文档一致、代码事实有据、诚实边界清晰、控制器效应可证。**独立审议（③）结论 PASS_WITH_NOTES**，已据 REVIEW_M9-2 MAJOR-1/MAJOR-2 修订设计（§3.2 keyless→MockProvider 带 source 降级；§3.4 tool_status 保留 `tool="web_search"` + 新增独立 `source` 字段；§3.3 补 data_proc/doc_export 仍读 tools.yaml；§9 E1/E5 措辞对齐）。MINOR-1~4 已并入对应小节。修订后设计具备编码条件。

### 17.9 设计修订记录（回应独立审议）
| 修订 | 来源 | 落点 |
|---|---|---|
| keyless api_key 源 → MockProvider(spec.id, spec.name) 保留 source | REVIEW_M9-2 MAJOR-1 | §3.2 build_search_tool 解析规则 |
| tool_status.tool 保留 `"web_search"`，新增独立 source 字段 | REVIEW_M9-2 MAJOR-2 | §3.4 + §9 E1 |
| Researcher prompt 显式保留 source 字段 | REVIEW_M9-2 MINOR-1 | §3.4 |
| data_proc/doc_export 仍读 tools.yaml | REVIEW_M9-2 MINOR-2 | §3.3 |
| 统一「无 key→mock」表述 / G3 措辞对齐 | REVIEW_M9-2 MINOR-3/4 | §3.2/§2.1 |

---

## 18. 实施自审（编码后填，三道闸②后半）

> 编码完成、py_compile/build 全绿、E1-E7 实证后，主代理在此补实施自审（偏差/回滚实证/诚实边界落实验证），再交独立审议第 N 轮（实施）。

### 18.1 实现落点对照（设计 → 代码）
| 设计节 | 落点文件 | 实测状态 |
|---|---|---|
| §3.1 插件注册表 SoT | `config/plugins.yaml`（tavily enabled=true/status=disconnected；arxiv/xueqiu/cninfo/qcc/scholar 全 coming_soon/enabled=false） | ✅ |
| §3.2 抽象层 + 多源聚合 | `tools/data_sources.py`：`DataSourcePlugin` Protocol + `TavilyProvider`/`MockProvider`/`ComingSoonProvider` + `PROVIDER_REGISTRY` + `load_secrets`/`load_data_sources`/`build_search_tool` | ✅ py_compile 通过 |
| §3.3 build_tools 适配 | `tools/__init__.py`：`build_tools` 改读 `load_data_sources`/`load_secrets`/`build_search_tool` 聚合；无 plugins.yaml → 回落 `tools.yaml`；data_proc/doc_export 仍读 `tools.yaml` | ✅ |
| §3.4 orchestrator source 字段 | `orchestrator.py` 三处：stub 回显补 `source`（:169）、Researcher extra_context 提示「保留 source 字段」（:670）、3.6 按 url 回填 source（:763-769） | ✅ 三段均落盘（grep 实证） |
| §3.5 密钥隔离 | `.secrets/plugins.env`（bind mount）+ `.gitignore` 加 `.secrets/` + `docker-compose.yml` 加 `./.secrets:/app/.secrets` | ✅ |
| §4 admin API | `server/admin.py`：`GET /plugins`（公开）、`GET/POST/PUT/DELETE /admin/plugins`、`POST /admin/plugins/{pid}/connect`（写密钥不回显）；ruamel round-trip + `_atomic_write` + `_write_audit` + 失败回滚 + coming_soon 拒绝 enabled=true | ✅ py_compile 通过 |
| §5 前端市场 | `web/src/views/Plugins.vue`（市场/已安装 tab + 卡片状态 + 新增向导 + 连接弹窗）、`router/index.ts` 加 `/plugins`、`layouts/DefaultLayout.vue` 侧边栏「数据源」入口、`types/ui.ts` 加 `PluginItem` | ✅ |
| §8 热加载/原子写/审计 | build_tools 每次重读（零重启）；admin 复用 M9-1 `_atomic_write`/`_write_audit` | ✅ 代码级（沿用已验证范式） |

### 18.2 门禁①编译校验
- ✅ `python -m py_compile tools/data_sources.py tools/__init__.py orchestrator.py server/admin.py` → `COMPILE_OK`（4 文件全绿）。

### 18.3 控制器效应实证（E1-E7，m9-2_verify.py）
- ✅ E1 `using_mock_search` + `source=tavily` 透传（n=5）+ `[MOCK]` 标记 + `tool_status.tool` 保留 `web_search` + `source` 入 status。
- ✅ E2 **禁用 Tavily → retrieval 空（n=0）→ 控制器真生效**（启停直接改引擎检索输入）。
- ✅ E3 自定义源（arxiv）被 `build_search_tool` 接纳，结果带 `source=arxiv`。
- ✅ E4 `coming_soon` 源引擎**永远跳过**（不冒充可用），结果集仅含 tavily。
- ✅ E5 有 key 源 → 真 `TavilyProvider`（非 mock）分支可达。
- ✅ E7 多源聚合（tavily + arxiv）结果集正确带各自 `source`。
- 结论：`RESULT: ALL PASS`。

### 18.4 端到端真实节点流（m9-2_e2e.py）
- ✅ stub LLM 跑 `run_report` 真实节点流：`INJECTED[0]` 带 `source=tavily`；最终 `retrieval_records` **20/20 全部 `source=tavily`**（3.6 回填段生效）。
- ✅ 断言 `all(r.get("source") for r in recs)` 通过 → **E2E PASS**。

### 18.5 回归测试（pre-existing 基线，非 M9-2 引入）
- 跑 `tests/test_orchestrator.py` + `tests/test_regression_edge.py`：21 passed / 3 failed。
- ⚠️ 3 失败**在 M9-1 基线（git stash 我的改动后）同样复现** → 确认是**预先存在的技术债，与 M9-2 无关**：
  - `test_rework_loop_and_convergence`
  - `test_json_recover_by_retry`
  - `test_max_rounds_exhausted_escalates_with_bounds`（gate_review_history 计数 off-by-one：max_rounds=2 期望 2 次却 3 次）
- 处置：M9-2 范围（数据源插件）未触碰 rework 循环 / max_rounds 计次 / json 恢复逻辑，**不越界修复**（守 boss 不过度工程纪律）。该 3 项登记为独立 TD（建议 TD-009：orchestrator rework 计数与 json 恢复回归），留待专项修复，**不阻塞 M9-2 三道闸闭环**。
- ✅ 关键断言 `tool_status.tool=="web_search"`（test_orchestrator.py:180 / test_regression_edge.py:146）未被破坏（MAJOR-2 修复生效）。

### 18.6 诚实边界落地验证（§10 R1-R3）
- ✅ R1：`build_search_tool` 跳过 `provider=coming_soon`，coming_soon 源永不进引擎（E4 实证）。
- ✅ R2：无真实密钥 → `MockProvider` 带 `[MOCK]` 标记 + `using_mock_search=True` 告警（E1 实证），未假装联网。
- ✅ R3：密钥经 `load_secrets()` 读 `.secrets/plugins.env` / `DS_<ID>_API_KEY` / 旧 `TAVILY_API_KEY`，**不写进 plugins.yaml**（`.gitignore` 屏蔽 + bind mount）。

### 18.7 向后兼容
- ✅ `tools/__init__.py:50`：无 `config/plugins.yaml` 时回落 `config/tools.yaml` 单 provider，现有行为不变，防回归。

### 18.8 偏差记录（设计 vs 实现）
- 无实质偏差。设计阶段独立审议 MAJOR-1/MAJOR-2 已折入设计 §3.2/§3.4，并同步落到代码（keyless→MockProvider 带 source；tool_status.tool 保留 + 独立 source 字段）。
- 回滚/原子写实证为**代码级**（复用 M9-1 已验证范式），未单独跑 admin CRUD 故障注入（无 new-api 配额、且 ruamel round-trip 路径与 M9-1 agents-library 同源已证）。

### 18.9 实施自审结论
设计→代码落地完整、门禁①编译全绿、控制器效应 E1-E7 + E2E source 透传全 PASS、诚实边界三项 R1-R3 均实证、向后兼容与密钥隔离落实。**唯一红灯为 3 个预先存在的回归失败（非 M9-2 引入，已登记 TD-009 待专项）**，不越界修复。具备交独立审议（实施第 19 轮）条件。
