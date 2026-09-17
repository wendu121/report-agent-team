# REVIEW_M9-2 · 独立审议（设计阶段）

> 审议者：独立子代理（edict-gate 门禁③）
> 日期：2026-09-09
> 被审：DESIGN_M9-2.md
> 依据代码：`tools/__init__.py`、`tools/web_search.py`、`orchestrator.py`(:605/:655/:1101/:1107)、`server/admin.py`(:166/:172/:755+/:1049+/:1220)、`server/engine_runner.py`(:54-59)、`DESIGN_M9-1.md`、`DESIGN_PLATFORM_FUNCTIONS.md`

## 结论：PASS_WITH_NOTES

设计整体扎实、范式承接 M9-1 严谨、诚实边界与控制器效应立得稳。存在 2 处必须在写码前补明的 **[MAJOR]**（默认场景可实施性 + 破坏既有测试契约），以及数处 [MINOR] 措辞/落点补全。无违反 boss 铁律的根本性错误，故非 BLOCKED。

---

## 核查清单

| 要点 | 结论 | 说明 |
|---|---|---|
| A 领域替换 | PASS | 数据源全为研报向（Tavily/arXiv/雪球/巨潮/企查查/Google Scholar），无电商残留；§0.2 明令 coming_soon 不冒充。与 DESIGN_PLATFORM_FUNCTIONS §2.2 替换表一致。 |
| B 控制器效应 | PASS* | `engine_runner.py:59` 每任务 `tools=None` → `build_tools` 每任务重读 → `build_search_tool` 仅聚合 `enabled and provider!=coming_soon`；禁用即 `search_results` 不再含该源，GateA 判 escalate。链路闭环成立（* 但见 MAJOR-1 默认场景缺口）。 |
| C 诚实边界 | PASS* | coming_soon 在 `build_search_tool` 过滤 + `PUT enabled=true` 拒绝双重保险；密钥走 `.secrets/plugins.env` 不进 `plugins.yaml`；MockProvider `[MOCK]` 前缀清晰。*唯一缺口：keyless 真源降级规则未写（MAJOR-1）。 |
| D 与M9-1一致 | PASS | plugins CRUD 复用 `ruamel` round-trip(`admin.py:1220`)、`_atomic_write(:166)`、`_write_audit` subdir `:172`、`agents-library` CRUD 范式(:1049+)。无重复造轮。 |
| E 可实施性 | PASS_WITH_NOTES | 改动落点清晰、引用行号真实（如 `orchestrator.py:655` 确为 `search_many` 调用）。但 MAJOR-1/MAJOR-2 为真实可实施性缺陷。 |
| F 过度工程 | PASS | N1-N6 合理，严格复用 M9-1 范式，无范围蔓延；动态 import / OAuth / 爬虫均标 coming_soon 外置。 |
| G 内部矛盾 | MINOR | G3 称「无 enabled 真源时回落 legacy tools.yaml」，但 §3.3 代码 legacy 回落仅限 `plugins.yaml` 缺失；无 enabled 真源时应走 `build_search_tool` 的 MockProvider 分支。代码正确、prose 措辞易误导。 |

---

## 问题清单（按严重度）

### [MAJOR-1] `build_search_tool` 对「enabled 但无密钥的真源」缺乏降级规则，默认 E1 场景不可实施
设计 §3.2 伪代码只写「逐个 `provider.search` → 补 `source` → 无可用源返回 MockProvider 聚合」，未说明**如何决定某 enabled 真源是否「可用」**。
- 事实：`TavilyProvider.__init__` 在 `api_key` 为空时**直接抛 `ToolError`**（`tools/web_search.py:51-52`）。
- `load_secrets()` 当 `DS_TAVILY_API_KEY` 缺失时，若 `build_search_tool` 仍按 `provider=="tavily"` 去实例化 `TavilyProvider(api_key="")`，会在构造期崩溃，**整个 `build_tools` 失败**，进而 `engine_runner` 跑不动任务。
- 而 E1 期望「默认 `plugins.yaml`（Tavily **mock** enabled）→ `retrieval_records` 带 `source=tavily`」、E5 称「429 态 Tavily 仍走 mock」——即默认（无 key）场景必须让 **tavily 以 MockProvider 运行但标记 `source=tavily`**。
- 结论：设计未写明「`provider=="tavily"` 且 key 缺失 → 用 `MockProvider(source_id, source_name)` 但保留 `source=tavily`，`using_mock_search=True`」这一关键分支。此为默认路径，按字面实现会崩溃 → 必须在 §3.2 补明确规则后再写码。

**修订建议**：§3.2 `build_search_tool` 增加判定——对每个 enabled 非 coming_soon 的 spec：
```
if provider == "tavily":
    key = secrets.get("DS_TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY")
    provider_inst = TavilyProvider(api_key=key) if key else MockProvider(spec.id, spec.name)
elif provider == "mock":
    provider_inst = MockProvider(spec.id, spec.name)
# coming_soon 已被前置过滤，不会到此处
```
并明确 `using_mock_search =`「所有 enabled 真源均无真实 key」。

### [MAJOR-2] `tool_status.tool` 改 `f"search:{source_id}"` 会破坏既有回归测试
设计 §3.2：「`tool_status` 条目 `tool` 字段写 `f"search:{source_id}"`」。
- 既有测试硬断言 `t["tool"] == "web_search"`：
  - `tests/test_orchestrator.py:180` → `assert ts and all(not t["ok"] for t in ts if t["tool"] == "web_search")`
  - `tests/test_regression_edge.py:146` → 同款断言
- `web_search.py:131/134/137` 当前也写 `"tool": "web_search"`。若新 `SearchTool` 改为 `search:tavily`，上述两测试将**失败**，且搜索失败审计/监控若依赖 `tool=="web_search"` 一并受影响。
- 这不是「控制器效应」必须项（`source` 已能从结果 dict 区分来源），改名只为审计观感，却破坏既有契约。

**修订建议**：保留 `tool="web_search"`（兼容既有状态过滤/测试），在 `tool_status` 条目与 `search_results` 结果 dict 中**新增独立 `source` 字段**（= plugin id）。既满足审计溯源，又不破 `test_orchestrator.py:180` / `test_regression_edge.py:146`。实施时同步更新两处测试为 `t["source"]` 校验或在断言中兼容。

### [MINOR-1] `retrieval_records` 真正带 `source` 需改 Researcher prompt JSON schema
§3.4 称「`search_results` 带 `source` → LLM 结构化产出 `retrieval_records` 时应保留 `source`（prompt 指示）」。但 `retrieval_records` 是 **LLM 产出**，当前 `search_results`(orchestrator:662)→`retrieval_records` 之间靠 prompt 抽取。设计未指明要在 Researcher 系统 prompt / JSON 输出 schema 中**显式增加 `source` 字段**。仅写「prompt 指示」LLM 未必可靠保留。
**修订建议**：在 Researcher prompt（`build_agent_system` 或 `agents/researcher.md`）的 JSON schema 中正式加入 `source`，并作为必填回写；否则 E1「`retrieval_records` 每条带 `source`」难保证。

### [MINOR-2] §3.3 `build_tools` 新路径需仍读 `tools.yaml` 的 `data_proc/doc_export`
当前 `build_tools`(`tools/__init__.py:74-83`) 从 `tools.yaml` 读 `doc_export.export_dir`、`data_proc.enabled`。设计 §3.3 伪代码只展示 `web = build_search_tool(...)` 并说「`tools.yaml` 保留 `data_proc`/`doc_export`（不变）」，但**未展示如何从 `tools.yaml` 重建 `DataProcTool`/`DocExportTool`**。若新路径只构造 `web` 而漏掉这两项配置读取，行为会漂移。
**修订建议**：在 §3.3 伪代码补全 `data_proc`/`doc_export` 仍读 `tools.yaml`（与 legacy 同逻辑），`web_search` 块可标 deprecated。

### [MINOR-3] G3 prose 与 §3.3 代码措辞不一致（内部）
G3：「无 enabled 真源时回落 legacy `tools.yaml`（向后兼容）」；但 §3.3 代码 `if specs: build_search_tool else: _legacy_build` —— legacy 回落**仅当 `plugins.yaml` 缺失**，而非「无 enabled 真源」。无 enabled 真源时走 `build_search_tool` 的 MockProvider 分支（MAJOR-1 补完即可）。代码正确，prose 易误导实施者。
**修订建议**：将 G3 改为「`plugins.yaml` 缺失时回落 legacy `tools.yaml`；存在但无 enabled 真源时由 `build_search_tool` 返回 MockProvider 聚合」。

### [MINOR-4] 「429→mock」表述含糊
E5/R2 称「429 态 Tavily 仍走 mock」。事实：现有 `TavilyProvider` 遇 429 抛 `ToolError`（`web_search.py:71-77`），`search_many` 捕获后该 query `ok=false`、**不降级为 mock**。所谓「走 mock」实为**无 key → MockProvider**（MAJOR-1 分支）。表述应统一为「无密钥态走 MockProvider（清晰 `[MOCK]`），非真实 429 兜底」。

### [NOTE-1] 控制器链路透传已验证，`source` 可存活
`merge_search_results`(`orchestrator.py:613-619`) 对每条 `r` 整体 `out.append(r)` 再重编号，不剥离字段 → `search_results` 带 `source` 后透传进 `extra_context` 与上游注入均成立。控制器效应（启停真改引擎）链路通畅。

### [NOTE-2] 热加载真实（每任务生效）
`engine_runner.py:54-59` 调 `run_report(..., tools=None)`，`run_report:1095-1097` 每任务 `build_tools()` 重读 yaml → 改 `plugins.yaml` 下一任务即生效、零重启，与 M9-1 `load_agent_registry` 热加载同构。控制器效应**每任务**真实，非装饰卡片。

### [NOTE-3] `coming_soon` 守卫是双重保险
`build_search_tool` 前置过滤 `provider != coming_soon` + §4.4 `PUT enabled=true` 拒绝 + §7 校验拒绝，三层确保 coming_soon 永不进引擎、不冒充。即便绕过 API 直接改 yaml，引擎层仍过滤。诚实边界稳固。

---

## 修订建议（给主代理的具体修改指令）

1. **§3.2 `build_search_tool` 补「keyless 真源降级」分支**（MAJOR-1）：明确 `tavily` 无 key → `MockProvider(spec.id, spec.name)` 但保留 `source=tavily` 且 `using_mock_search=True`；并定义 `using_mock_search` 的全局判定语义。这是默认 E1/E5 可跑通的前提。
2. **§3.2 `tool_status` 保留 `tool="web_search"`，新增 `source` 字段**（MAJOR-2）：勿改 `tool` 标签以免破 `tests/test_orchestrator.py:180`、`tests/test_regression_edge.py:146`；结果 dict 与 status entry 同加 `source=spec.id`。实施时同步修两处测试断言兼容。
3. **Researcher prompt JSON schema 增加 `source` 必填**（MINOR-1），保证 `retrieval_records` 真正带 `source`（E1 证据）。
4. **§3.3 补 `data_proc`/`doc_export` 仍读 `tools.yaml`**（MINOR-2）。
5. **G3 措辞对齐 §3.3 代码**（MINOR-3）：legacy 回落仅限 `plugins.yaml` 缺失。
6. **统一「无 key→mock」表述**，去掉「429→mock」歧义（MINOR-4）。
7. 其余（域名替换、诚实边界、与 M9-1 范式一致、不过度工程、热加载）均合格，无需改。

> 三道闸纪律提示：本审议为门禁③之证据，非主代理自签。`reviewed-by` 字段由本独立子代理产出即生效，请勿由主代理代填。

<!-- reviewed-by: independent-subagent -->
<!-- review-stage: 实施独立审议（第 19 轮） -->
<!-- date: 2026-09-09 00:32 GMT+8 -->

---

## 实施独立审议（第 19 轮）

> 审议者：独立子代理（edict-gate 门禁③，实施阶段，与主代理无串通）
> 日期：2026-09-09 00:32 GMT+8
> 被审：M9-2 实现（编码后）
> 依据：`DESIGN_M9-2.md` / `VERIFICATION_M9-2.md` / 实际代码 + 交叉跑验证脚本 + `git diff`
> 落款：`independent-subagent`（严禁主代理代签）

### 0. 核查过程

1. 通读设计（DESIGN_M9-2）、主代理自审（VERIFICATION 17/18）、设计阶段独立审议（REVIEW_M9-2，MAJOR-1/MAJOR-2/MINOR-1~4）。
2. 逐文件读实现：`tools/data_sources.py`、`tools/__init__.py`、`config/plugins.yaml`、`.gitignore`、`docker-compose.yml`、`orchestrator.py`（grep `source` + 三处 hunk 全文）、`server/admin.py`（plugins 全部端点）、`web/src/views/Plugins.vue`、`router/index.ts`、`DefaultLayout.vue`、`types/ui.ts`。
3. 交叉验证（独立跑，非主代理口径）：
   - `py_compile` 4 文件 → **COMPILE_OK**。
   - `m9-2_verify.py` → **RESULT: ALL PASS**（E1 using_mock / source=tavily n=5 / [MOCK] 标记 / tool_status.tool 保留 / status 含 source；E2 禁用→n=0 控制器生效；E3 arxiv 自动接纳；E4 coming_soon 跳过；E5 有 key→真 TavilyProvider；E7 多源聚合）。
   - `m9-2_e2e.py` → **E2E PASS**：INJECTED[0] 带 `source=tavily`，最终 retrieval_records 20/20 全部 `source=tavily`（3.6 回填段生效）。
4. 回归基线独立判定：`git diff HEAD -- orchestrator.py` 仅 **3 个 hunk**（约 :165 stub 补 source / :667 Researcher prompt 指示 / :760-769 3.6 url 回填），**未触碰** rework 计数 / max_rounds / json 恢复逻辑。故 3 个失败测试确为 M9-1 基线预先存在债（登记 TD-009），与 M9-2 无关，不阻塞闭环。

### 1. 逐项结论（A–F + 回归）

| 项 | 结论 | 证据 |
|---|---|---|
| **A 真·控制器效应** | **PASS** | `build_search_tool` 仅聚合 `enabled and provider!=coming_soon`；E2 禁用 Tavily→检索空→GateA escalate；E3/E7 自定义/mock 源被真实接纳并带各自 source。前端 PUT enabled 直接改 `plugins.yaml`，下一任务引擎热加载生效（每任务 `build_tools` 重读）。非装饰卡片，已实证。 |
| **B 诚实边界 R1-R3** | **PASS** | R1：`build_search_tool` 行 233-234 过滤 `coming_soon` + admin `PUT enabled=true` 行 1511-1515 拒绝 coming_soon（双保险）。R2：`MockProvider` 结果带 `[MOCK]` 前缀 + `using_mock_search=True` + 醒目 WARN（data_sources.py:34-59 / tools/__init__.py:61-69），未冒充真实检索。R3：`load_secrets` 只从 `.secrets/plugins.env`/env 读 `DS_<ID>_API_KEY`（兼容旧 `TAVILY_API_KEY`）；`plugins.yaml` 无 key 字段；`.gitignore` 含 `.secrets/`；`docker-compose.yml` 挂载 `./.secrets:/app/.secrets`。密钥不落 config 已落实。 |
| **C 设计→代码一致性（MAJOR-1/MAJOR-2）** | **PASS** | MAJOR-1：keyless 真源→`MockProvider(sid, name)` 保留 `source`（build_search_tool 行 237-245），旧 `TavilyProvider` 构造期缺 key 崩的逻辑被消除（tools/__init__.py 旧 `raise ToolError` 已改为降级）。MAJOR-2：`tool_status` 条目 `tool` 保留 `"web_search"`（orchestrator:172 / data_sources.py:191/195/198），独立 `source` 字段标识来源；E1 断言 `tools=['web_search']` 通过，既满足溯源又不破 `test_orchestrator.py:180`/`test_regression_edge.py:146`。MINOR-1 已落：Researcher prompt 显式指示保留 source + 3.6 url 回填兜底。MINOR-2/3/4 均已对应落地。 |
| **D 向后兼容** | **PASS** | `load_data_sources` 行 137-151：无 `plugins.yaml` → 读 `tools.yaml` 回落 legacy 单源 spec（provider 来自 tools.yaml），再经 `build_search_tool` 聚合（无 key→MockProvider，与原 mock 行为等价）；`data_proc`/`doc_export` 仍读 `tools.yaml`（build_tools:71-80）。现有行为不变，防回归成立。 |
| **E 不过度工程** | **PASS** | 无运行时动态 import（PROVIDER_REGISTRY 静态登记，data_sources.py:76-80）；无 OAuth 真流程（xueqiu/qcc 标 coming_soon）；无国内源真爬虫；未引入 M9-3~5/RBAC/输出格式渲染。前端新增向导仅暴露 `coming_soon`/`mock`（防把未实现当真源），真源须后端 PROVIDER_REGISTRY 登记（VALID_PROVIDERS 校验）。范围克制，与设计 §2.2/N1-N6 一致。 |
| **F 复合写安全** | **PASS** | admin 全部端点复用 M9-1 范式：`_load_plugins_rt`（ruamel round-trip 保注释）→ `_atomic_write` → `_write_audit`（subdir `plugins`）。create/update/delete 均有 `before` 快照 + 异常回滚 + rejected 审计（admin.py:1462-1488/1517-1538/1557-1568）。`connect` 写 `.secrets/plugins.env` 独立原子写、不回显 key（仅返 `status=connected`，admin.py:1574-1623）。coming_soon 拒绝 `enabled=true`（行 1511-1515）。密钥不进 plugins.yaml（R3 已证）。 |

**回归基线判断**：M9-2 对 orchestrator.py 的改动仅 3 处 source 相关，rework 循环 / `max_rounds` 计次 / json 恢复逻辑均未被触碰。`test_rework_loop_and_convergence` / `test_json_recover_by_retry` / `test_max_rounds_exhausted_escalates_with_bounds` 三失败为 M9-1 基线预先存在（主代理已自证 git stash 复现），属 TD-009 技术债，与 M9-2 无关，**不阻塞闭环**。

### 2. 发现问题（独立分级）

**MAJOR：NONE**
**MINOR：NONE（致命/阻断类均无）**

非阻断性观察（不影响诚实边界/控制器效应/三道闸，仅后续可优化）：
- **NOTE-N1（信息性，非阻断）**：`tools/__init__.py:51` 的 `cfg = yaml.safe_load(p.read_text(...)) or {} if p.exists() else {}` 因运算符优先级，实际为 `X or ({} if p.exists() else {})`，`p.read_text()` 在 `p` 缺失时会先抛 `FileNotFoundError`，`if p.exists()` 守卫未真正保护读操作。但正常部署 `config/tools.yaml` 始终存在（M9-2 向后兼容场景为"plugins.yaml 缺失、tools.yaml 仍在"），故不触发；且该行为与原 legacy 旧代码一致（非 M9-2 引入的回归）。建议后续顺手改为 `cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}` 以消除隐患。
- **NOTE-N2（信息性，非阻断）**：`StubLLMClient`（orchestrator:172）researcher 分支的 `tool_status` 未带 `source` 字段（仅生产 `SearchTool.search_many` 带）；既有测试只断言 `tool=="web_search"`，不受影响。属 stub 模式小缺，不影响生产与 E1 实证。

### 3. 最终结论

**PASS_WITH_NOTES**

- 设计→代码落地完整、门禁①编译全绿、控制器效应 E1-E7 + E2E source 透传全 PASS、诚实边界 R1-R3 均实证、向后兼容与密钥隔离落实、复合写安全（ruamel 保注释 + 原子写 + 审计 + 回滚 + coming_soon 拒绝 + 密钥不回显）齐备、未越界引入过度工程。
- 设计阶段 MAJOR-1/MAJOR-2 修订**真落到代码**（keyless→MockProvider 带 source；tool_status.tool 保留 + 独立 source 字段），未被悄悄改回。
- 3 个回归失败为预先存在债（TD-009），非 M9-2 引入，不阻塞。

**commit 前必须修复项：NONE（无阻塞）**。
**可后续跟进项（非阻断，建议随本里程碑或 TD-009 一并处理）**：
1. NOTE-N1：`build_tools` 中 `tools.yaml` 缺失时的读取守卫修正（消除潜在 FileNotFoundError，纯健壮性）。
2. NOTE-N2：stub `tool_status` 补 `source`（一致性，不影响测试）。
3. TD-009：专项修复 rework 计数 / max_rounds / json 恢复 3 个预先存在回归（独立于 M9-2）。

**落款**：independent-subagent（独立子代理，实施第 19 轮，与主代理无串通、无自签）。M9-2 三道闸可闭环，准予 commit（本地不 push，遵循 boss 指令）。
