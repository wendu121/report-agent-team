# VERIFICATION · M10-P4（方案 B：Accio 电商 Agent / Skill 移植 + 方案 A 遗留收口）

> 里程碑：M10-P4（承接 M10-P3 电商数据源）
> 范围：**A** = 抄 Accio 公开计算器里「我们真正用得上」的部分；**B** = 把 Accio 的电商
> Agent / Skill 架构移植成本引擎的一等公民。
> 自审人：主代理（**非**独立审议；独立审议见 `REVIEW_M10-P4.md`）
> 日期：2026-09-11

---

## 0. 结论速览

| 项 | 结果 |
|----|------|
| ① 构建/测试/前端 | ✅ `py_compile` OK · `pytest tests/` **79 passed / 0 failed** · `vue-tsc --noEmit` EXIT=0 · `npm run build` EXIT=0 |
| ② 容器端到端 | ✅ 自定义电商子集链 `MarketScout→EcomAnalyst→Writer` / `MarketScout→SourcingAdvisor→Writer` 均走通，`status=done`，三闸全 `advance`，序列化告警 0 |
| ③ 附带修复 | ✅ 6 项实测缺陷（见 §4），均有回归测试钉死 |
| ④ 独立审议回填 | ✅ `REVIEW_M10-P4.md` 的 M1–M5 + N1 全部闭环（见 §9），且审议促使发现并修掉第 6 项缺陷（§4.6） |
| 遗留 | 4 项（见 §7），均**不影响**本里程碑验收 |

> ⚠️ 本节为**独立审议后回填**后的最新数字。审议前为 `73 passed`；审议后补测 + 修 §4.6 后为 `79 passed`。

---

## 1. 方案 A：Accio 计算器资产盘点与取舍

### 1.1 实测结论（容器内直连，非纸面推断）

对 Accio 公开页面暴露的 9 个「计算器」逐一探测，结论：

| # | 计算器 | 实现形态 | 是否可抄 |
|---|--------|----------|----------|
| 1-7 | 利润/运费/汇率/CPC 等 | **纯前端**（无后端 API，公式写死在 JS） | ❌ 无可抄端点 |
| 8 | HS 码查询 | **真 API** `accio-wow-api.vercel.app/api/hs-code` | ✅ 已抄（M10-P4 新增 `hs_code_tariff`） |
| 9 | 关税试算 | 依赖 #8 | ✅ 经 #8 覆盖 |

**取舍理由（诚实边界）**：7 个纯前端计算器没有可复用的服务端点；把它们用 Python
重写等于「自己造一个 Accio 的公式副本」，既无数据源价值，又会引入「公式对不对无法
验证」的隐患。故**只抄有真端点的 #8**，其余明确放弃而非假装抄到。

### 1.2 新增零密钥数据源：`hs_code_tariff`

- **端点**：`https://accio-wow-api.vercel.app/api/hs-code/<code>`（GET，无需 key）
- **两源分工**（避免职责重叠）：
  - `accio_tariff`（M10-P3）：**商品名 → HS 码候选**（模糊归类）
  - `hs_code_tariff`（本次）：**HS 码 → 6 国关税 + 301/AD 风险**（精确查表）
- **关键设计决策（实测驱动）**：Accio 的关键词搜索**极不可靠**——
  `"ceramic mug"` → 返回铀矿（2844.10）、`"steel water bottle"` → 返回活鱼（0301.11）。
  若把这种结果喂给 Researcher，等于**污染研报素材**。
  故 `HsCodeTariffProvider` **只做精确 `/<code>` 查表**：输入非 HS 码时返回**空结果**，
  而非退回模糊搜索。宁可空手，不给错数据。

**实测（容器内，真实网络）**：

```
$ build_search_tool([hs_code_tariff]).search_many(["691110|US,EU,GB"])
source=hs_code_tariff cred=high
title: HS 6911.10：Porcelain dinnerware, ceramic tableware（美国 6%、欧盟 7.5%、英国 0%）
body : HS 6911.10 (HTS 6911.10.8010)｜关税 美国 6%、欧盟 7.5%、英国 0%｜风险 ad｜AD from China may apply (EU)
```

`cred=high`（一手关税数据）与 `accio_tariff` 的模糊结果形成明确分层。

---

## 2. 方案 B：电商 Agent / Skill 移植

### 2.1 新增 3 个 Agent（`builtin: false`）

| id | name | shape | gate | output_key | 定位 |
|----|------|-------|------|------------|------|
| `MarketScout` | 市场侦察 | researcher | GateA | `retrieval_records` | 竞品价格带/关键词/评价/履约/合规取材 |
| `EcomAnalyst` | 电商分析师 | analyst | GateB | `analysis_conclusions` | 电商货架维度竞品矩阵 |
| `SourcingAdvisor` | 选品顾问 | analyst | GateB | `analysis_conclusions` | 六维加权打分 + 合规否决项 |

配套：`agents/marketscout.md` / `agents/ecomanalyst.md` / `agents/sourcingadvisor.md`
三份角色模板，均含**诚实红线**（关键词热度 ≠ 销量、关税 ≠ 落地成本、单市场 ≠ 全球、禁臆造）。

**关键隔离验证**（避免污染既有任务）：

```
$ default_chain(reg)
['Researcher', 'Analyst', 'Writer']        # ✅ 仍为 3 角色，新 Agent 未渗入
builtin agents: ['Researcher', 'Analyst', 'Writer']
```

→ 新 Agent 必须经 `agents=[...]` 显式指定才启用（M8-5 子集编排语义），既有一切任务行为不变。

### 2.2 新增 3 个 Skill

| id | target_roles | 内容要点 |
|----|--------------|----------|
| `ecom_competitor_matrix` | analyst | 价格带/关键词覆盖/评价/履约/合规五维对标矩阵 |
| `tariff_cost_model` | analyst | HS 归类 → 目的国税率 → 落地成本拆解 → 敏感性 |
| `product_selection` | analyst | 需求/供给/竞争/利润/合规/履约六维加权打分 |

**注入验证（容器 API 实测）**：

```
$ GET /api/v1/skills   → skills total: 8
  ecom_competitor_matrix  cat=电商选品  roles=['analyst']  status=active
  tariff_cost_model       cat=电商选品  roles=['analyst']  status=active
  product_selection       cat=电商选品  roles=['analyst']  status=active
```

`build_skill_context('analyst')` 长度 2061 / `build_skill_context('ANALYST')` 2061
（大小写归一均命中，符合 M9-3 既定契约）。

### 2.3 异基座硬约束校核（`model_mapping.yaml`）

| Agent | 其 Gate | role_base | gate_base | 结论 |
|-------|---------|-----------|-----------|------|
| MarketScout | GateA | custom | cloudflare | ✅ 异基座 |
| EcomAnalyst | GateB | custom | cloudflare | ✅ 异基座 |
| SourcingAdvisor | GateB | custom | cloudflare | ✅ 异基座 |

→ 满足 `model_mapping.yaml` 硬约束 1（Gate 必须异于所审 Agent 基座），
维持「自审包庇」防护不被新 Agent 稀释。

---

## 3. 容器端到端验收（金标准）

### 3.1 成功路径（最终一轮，`tid=832c5ddd`）

```
POST /api/v1/tasks
  topic   : 蓝牙耳机
  agents  : ["MarketScout","EcomAnalyst","Writer"]
  plugins : ["taobao_suggest","amazon_suggest","ebay_suggest"]
→ gates  : ["GateA","GateB","GateC"]      # 派生正确
```

结果：

```
status  : done          last_gate: GateC
闸历史  : [('GateA','advance',0.9), ('GateB','advance',0.9), ('GateC','advance',0.95)]
检索记录: 17   分析结论: 8   tool_status: 10
record.source: taobao_suggest | credibility: medium
confidence: high
report: 3476 字符
Pydantic 告警: 0        ← 修复前为 36
```

**内容真实性核验**：报告 100% 由检索记录驱动——每条结论带 `source_ids`
（如 `['rec-3','rec-7','rec-8','rec-10','rec-17']`），正文以 `[con-N]` / `(rec-N)`
回指真实记录；`tool_status` 显示 `ok=False` 的源头被如实保留（未隐藏失败）。
**无编造数据**。

### 3.2 共享闸端到端（离线 stub，`agent` 逐步捕获）

```
链1 ecom : Researcher → EcomAnalyst → Writer
   GateA r1 advance  被审角色=Researcher
   GateB r1 advance  被审角色=EcomAnalyst     ← 共享 GateB，角色正确
   GateC r1 advance  被审角色=Writer
链2 scout: MarketScout → SourcingAdvisor → Writer
   GateA r1 advance  被审角色=MarketScout     ← 共享 GateA，角色正确
   GateB r1 advance  被审角色=SourcingAdvisor ← 共享 GateB，角色正确
   GateC r1 advance  被审角色=Writer
```

两条链 `status=done`，**被审角色序列与链路完全一致**（共享闸不再串角色）。

### 3.3 诚实中止验证（非缺陷）

用超出现有数据源能力的题目（"无线蓝牙耳机跨境选品**与关税成本分析**"，
但只挂 suggest 类源）跑，结果 `status=escalated`、`last_gate=GateA`、
`reason="检索完全无有效素材"`。

**判定：这是引擎正确行为，非缺陷。** `agent_result="ok"`（Agent 本身成功），
是 GateA 的**覆盖度硬校验**发现素材不足以支撑题目要求，遂诚实升级而非
硬凑一份编造的研报。这正是设计要的「宁可交白卷，不出假报告」。

---

## 4. 附带修复的 4 项实测缺陷

移植 B 的过程中暴露了 4 个真实缺陷。因它们**恰好落在本里程碑的执行路径上**
（不修则 B 无法正确工作），故在本里程碑内修复，各自配回归测试。

### 4.1 共享闸角色串位（CRITICAL，M9-1 遗留）

- **根因**：`load_agent_registry` 构造 `reviews = {a["gate"]: a["id"]}` 是**1:1 反查表**。
  多个 Agent 共用同一闸（`Analyst` 与 `EcomAnalyst` 都用 GateB）时，后写者**静默覆盖**前者。
  实测：`reg["reviews"]["GateB"]` 解析为 `EcomAnalyst` 而非 `Analyst` → **审错角色**。
- **影响**：M9-1 起「自定义 Agent」名义上是一等公民，实则共用闸即串角色。
- **修复**：`make_gate` / `build_gate_user` / `call_eval` 增加显式 `role` 形参；
  `build_graph` 传入该闸**实际被审角色**；闸节点记录 `rs["last_gate_role"]`；
  两个 router 优先读 `last_gate_role`，缺失才回落反查（兼容历史状态）。
- **验证**：§3.2 两条共享闸链，被审角色序列 100% 正确。

### 4.2 API 传输层闭集硬编码 Agent/Gate 名（会崩任务）

- **根因**：`server/api.py` 的投影模型把 agent/gate 写成 `Literal` 闭集
  （`Literal["Researcher","Analyst","Writer"]` / `Literal["GateA","GateB","GateC"]`）。
- **实测复现**（修复前）：

  ```
  ToolStatus(agent="MarketScout")                  -> FAIL
  GateReview(gate="GateD")                         -> FAIL
  RoutingState(rework_target_agent="EcomAnalyst")  -> FAIL
  RoutingState(last_gate="GateD")                  -> FAIL
  ```

- **影响**：`task.routing_state = RoutingState(**routing)` 一旦抛 `ValidationError`，
  **整个任务投影崩溃**（任务卡 running、前端拿不到结果）。即 M9-1/M8-5 承诺的
  「自定义 Agent/闸是一等公民」在传输层被违反。
- **修复**：5 个模型（`RoutingState` / `ToolStatus` / `GateReview` / `EngineEvent` /
  `ReviewRequest`）的 agent/gate 字段改为 `str` + 默认值。合法集合由引擎层 registry
  保证，传输层不做业务白名单。
- **验证**：`tests/test_api_boundary.py` 11 项，含「内置与自定义 Agent 一视同仁」参数化。

### 4.3 产出模型与引擎 shape 漂移（36 条序列化告警）

- **根因**：`api.py` 把引擎 raw dict **直接赋值**给已校验的 model 字段 → 绕过校验 →
  响应序列化阶段 Pydantic 逐字段报 `SerializationUnexpectedValue`。叠加三处字段声明漂移：
  1. `RetrievalRecord` 缺 `source`（M9-2 多源聚合新增，**M10-P3 就漏声明**）
  2. `ToolStatus` 缺 `source`，且 `error` 在成功时**不存在**（引擎不写该键）→ Required 告警
  3. `AnalysisConclusion.confidence` 声明 `float`，但 prompt 契约产出 `"high|medium|low"`
- **影响**：数据仍透传（不阻塞响应），但**污染日志**、前端 TS 类型拿不到字段，
  且「shape 漂移」这类真问题被降级成 warning 静默滑过。
- **修复**：
  - 补声明 `source` / `confidence: Optional[str]` / `error` 默认值；
  - 新增 `_validate_records()` 显式批量校验后再赋值 → shape 漂移 **fail loud**（抛错）而非静默告警。
- **`confidence` 的类型裁定**：三处不一致（prompt 说 string、API_SPEC 示例写 0.85、
  模型写 float）。**以 prompt 为准**——它是 LLM 运行时真实契约，且无任何代码消费该字段做数值计算。
  ⚠️ 已记入 §7 遗留项：`API_SPEC.md` 该示例待同步。
- **验证**：容器实测告警 **36 → 0**；`tests/test_api_boundary.py` 内含零告警断言。

### 4.4 句子式 query 击穿联想词源（本里程碑主功能缺陷）

- **根因**：引擎 `build_search_queries()` 下发的 query 是**主题整句**与**主题+scope 拼接**，
  例如 `"无线蓝牙耳机跨境选品与关税成本分析"`。而联想词接口（suggest.taobao /
  completion.amazon / autosug.ebay）**面向短语级输入**，整句 → **0 结果**。
- **实测**：整句 → 0 条；`"蓝牙耳机"` → 5 条。首轮 e2e 因此取回 0 素材而 escalate。
- **修复**：新增 `_extract_keyword()` 抽取器，**分语种**处理：
  - **拉丁**：空格是词内合法分隔（`"wireless earbuds"` 是一个短语），**不按空格切分**；
    剥掉分析类虚词后取前 2 词收敛到核心品类词。
  - **CJK**：先按连接词（与/和/及/、）切成并列片段（避免「耳机与关税」跨概念粘连），
    再在最长片段内剥离修饰性停用词。
  - **诚实标注**：发生截断时在结果正文注明「引擎整句已截取核心词「X」」，
    避免下游把「截断词的结果」误读为「整句的结果」。
  - **空查询不发请求**：抽取为空时提前返回，不做无意义 HTTP。
- **踩坑记录（修复过程中的两次自我纠正）**：
  1. 初版按空格切分 → 把 `"wireless earbuds"` 切成 `"wireless"`，**弄坏英文源**。已改为分语种。
  2. 初版只「剥离」停用词而不「切分」→ `"无线蓝牙耳机跨境选品与关税成本分析"`
     产出怪词 `"无线蓝牙耳机关税"`（"成本"被删后"关税"黏上）。已改为先按连接词切分。
- **验证**（容器内真实网络，整句输入）：

  ```
  taobao_suggest  : '无线蓝牙耳机跨境选品与关税成本分析'                -> 5 条
     无线蓝牙耳机2026新款 | 淘宝联想词｜热度分 100（引擎整句已截取核心词「无线蓝牙耳机」）
  amazon_suggest  : 'wireless earbuds cross-border selection and tariff analysis' -> 5 条
  ebay_suggest    : 'bluetooth speaker portable market analysis'         -> 5 条
  ```

---

### 4.5 前端 `confidence` 契约缺口（独立审议 M2 促使发现 → 已修）

- **问题**：后端把 `AnalysisConclusion.confidence` 从 `float` 改为 `str`（见 §4.3），
  但 `web/src/types/api.ts` 仍是 `confidence: number`，**前后端契约不一致**。
  实测 grep 全 `web/` 目录，当前无组件消费该字段，故**暂无运行时崩溃**，
  但属未闭环的契约缺口。
- **修复**：`api.ts` 的 `confidence` 改 `string | null`；同时补齐本里程碑新增的
  `RetrievalRecord.source` / `ToolStatus.source` / `ToolStatus.error` 字段，
  并把 `AgentRole` / `GateName` 由联合字面量放宽为 `BuiltinX | (string & {})`，
  使自定义 Agent / Gate 在前端类型层也合法（与后端 §4.2 的放开对称）。

### 4.6 注入素材正文含方括号 → stub 解析器误截断（本次审议回填时新发现，已修）

- **问题**：`StubLLMClient._extract_injected_records()` 用**朴素 depth 计数**配对中括号。
  而 M10-P3/P4 引入的检索素材正文天然含方括号——`accio_tariff` 的
  `一般关税[Free] + 附加关税[12.5%]`、Crossref 摘要的 `[2025]` 年份前缀。
  扫描器在**字符串内部**误增减 depth，把一个合法 JSON 数组**截断**成非法片段
  → 解析恒失败 → Researcher 按「检索失败」如实返回空数组 → **GateA 整任务 escalate**。
- **为何此前未暴露**：修复 §4.4（query 抽取）后，accio_tariff 与 crossref 才**真正开始**
  返回含方括号的素材；两者同时命中才触发。属「修好一个 bug 后暴露的隐藏 bug」。
- **实测取证**（容器内，真实 e2e 链路）：
  ```
  search_many() 返回 38 条；state["search_results"] = 38 条；
  但 state["retrieval_records"] = 0  → status=escalated
  stub parse: 0 / 38
  first_bad: (16512, 16518, '[12.5%]｜CN→US｜Microphones and stands...',
              "Expecting ',' delimiter: line 1 column 6 (char 5)")
  ```
- **修复**：解析器改为**字符串感知**扫描——进入 JSON 字符串字面量后跳过其中所有
  `[` / `]`（含 `\"` 转义处理），只在字符串外部计数 depth。
- **验证**：新增回归 `test_extract_injected_records_ignores_brackets_inside_strings`，
  覆盖「字符串内含方括号」「转义引号 + 方括号」「无数组」「空串」
  「合法数组但元素非 record」五类；修复后 e2e 双链回到 `status=done`。

  ```
  修复前：chain1 status=escalated  retrieval_records=0
  修复后：chain1 status=done       retrieval_records=38  sources=6 种
          chain2 status=done       retrieval_records=43  sources=7 种
  ```

---

## 5. 测试证据

```
$ pytest tests/ -q
79 passed, 40 warnings in 2.24s
```

（40 warnings 为既有 `datetime.utcnow()` DeprecationWarning，非本里程碑引入，见 §7）

**独立审议后新增的 6 项测试**（审议 M5 要求补边界、M3 要求补 quarantine、§4.6 补回归）：

| 测试 | 钉死的行为 |
|------|-----------|
| `test_extract_keyword_prefers_cjk_in_mixed` | 混合语种 `iPhone 16 Pro Max 手机壳` → 取 `手机壳`（不误靶 `iPhone`） |
| `test_extract_keyword_rejects_garbage` | `！！！` / `😀🔥` → 空，**不发无意义 HTTP** |
| `test_has_searchable_content` | 抽取结果可检索性判定 |
| `test_validate_records_quarantines_bad_records` | 单条 shape 漂移 → 隔离该条并告警，**不整任务 escalate**（审议 M3） |
| `test_validate_records_skips_non_dict` | 非 dict 元素安全跳过 |
| `test_extract_injected_records_ignores_brackets_inside_strings` | §4.6 字符串内方括号不破坏解析 |

**本里程碑新增测试文件与用量**：

| 文件 | 新增 | 覆盖 |
|------|------|------|
| `tests/test_tools.py` | +9 | `_extract_keyword` 分语种抽取、混合语种边界、垃圾输入、截断标注、空查询不发请求 |
| `tests/test_api_boundary.py` | +14（新文件） | 传输层闭集放开、产出模型 shape 对齐、零序列化告警、quarantine 隔离 |
| `tests/test_orchestrator.py` | +1 | §4.6 注入素材方括号回归 |

**回归测试设计原则**：全部**离线**（`monkeypatch` 拦截 `_http_get_json`），
不依赖联网/LLM 配额，可在 CI 稳定复现。

---

## 6. 变更文件清单

```
M  config/agents_library.yaml   (+39)  3 个电商 Agent（builtin: false）
M  config/model_mapping.yaml    (+12)  3 个新角色映射（custom 基座，异于各自 Gate）
M  config/plugins.yaml          (+11)  hs_code_tariff（category: 电商）
M  config/skills.yaml           (+33)  3 个电商技能
M  orchestrator.py              (+66)  共享闸角色显式传递（§4.1）+ gate_roles 1:N + _fallback_role（审议 M1）
                                       + _extract_injected_records 字符串感知（§4.6）
M  server/api.py                (+72)  闭集放开 + shape 对齐 + _validate_records quarantine（§4.2/4.3，审议 M3）
M  tests/test_tools.py          (+92)  §4.4 回归 + 混合语种/垃圾输入边界（审议 M5）
M  tests/test_orchestrator.py   (+27)  §4.6 方括号回归
M  tools/data_sources.py       (+193)  HsCodeTariffProvider + _extract_keyword + _has_searchable_content（§1.2/§4.4）
M  web/src/types/api.ts         (+12)  confidence→string + source/error 字段 + AgentRole/GateName 放开（审议 M2）
A  agents/marketscout.md               市场侦察角色模板
A  agents/ecomanalyst.md               电商分析师角色模板
M  agents/sourcingadvisor.md           选品顾问角色模板（审议 M4：四维统一为六维）
A  skills/ecom_competitor_matrix.md    电商竞品矩阵技能
A  skills/product_selection.md         选品评估技能
A  skills/tariff_cost_model.md         关税成本建模技能
A  tests/test_api_boundary.py          §4.2/4.3 回归（新文件）
A  DESIGN_M10-P4.md                   设计文档
A  VERIFICATION_M10-P4.md              自审（本文件）
A  REVIEW_M10-P4.md                    独立审议
```

**未提交**：`.git-backup-20260910/`（历史遗留 git 目录备份，非本次产物，保持 untracked）。

---

## 7. 遗留项（不影响本里程碑验收）

| # | 项 | 性质 | 处置 |
|---|----|------|------|
| L1 | `API_SPEC.md` §501 `confidence` 示例为 `0.85`，与实现（string 枚举）不符 | 文档滞后 | 待同步为 `"high"`；代码已从实现（prompt 契约），**不影响运行** |
| L2 | `server/websocket.py` 仍有约 20 处 `Literal` 闭集（agent/gate 名） | 同 §4.2 类问题 | 当前 WS 中间事件未广播（TD-002），**未触发**；待 TD-002 中间事件流式化时一并处理 |
| L3 | `datetime.utcnow()` DeprecationWarning（40 条） | 既有 | 非本里程碑引入，全局替换影响面大，另行排期 |
| L4 | 通用 REST 桥 `GenericRestProvider`（M10-P3 M3 发现） | 待决策 | 仍待 boss 决策，未实现 |

---

## 8. 诚实边界声明

- 本文件是**自审**，`reviewed-by` 标记由**独立子代理**在 `REVIEW_M10-P4.md` 中签署，
  主代理**未自签**。
- 方案 A 只抄了 1 个真端点（HS 码查询），**明确放弃** 7 个纯前端计算器，
  未用 Python 重写其公式——理由是重写无法验证且无数据源价值，属**主动放弃**而非遗漏。
- `hs_code_tariff` 仅做精确查表，非 HS 码输入返回空。**已知不可用场景已如实标注**，
  未用模糊搜索结果填充。
- §3.3 的 `escalated` 是**引擎正确行为**（素材不足诚实中止），非缺陷，未做「修好它」的粉饰。
- `hs_code_tariff` 的 `cred=high` 依据是「一手关税数据」；而联想词源的 `cred=medium`
  反映「关键词热度 ≠ 销量」的固有局限，**未虚高**。

---

## 9. 独立审议发现项闭环（`REVIEW_M10-P4.md`）

独立审议裁定 **PASS_WITH_NOTES，MAJOR=0**。其提出的 5 项 MINOR + 1 项 NIT
**已全部在本轮修复并回归**：

| 审议项 | 严重度 | 问题 | 处置 | 钉死证据 |
|--------|--------|------|------|----------|
| **M1** | MINOR | `reg["reviews"]` 是被覆盖的**有损 1:1 表**（`GateA→MarketScout` 错误），且作为 router 回落值保留 → 未来维护者陷阱 | 新增 1:N `gate_roles` + `_fallback_role()`（歧义时返回 None 并 console 告警）；`reviews` 降级为**显式弃用**兼容层并加注释 | 容器实测 `gate_roles={"GateA":["Researcher","MarketScout"],"GateB":["Analyst","EcomAnalyst","SourcingAdvisor"],"GateC":["Writer"]}`；三链闸→角色归属全部正确 |
| **M2** | MINOR | 前端 `api.ts` `confidence: number` 与后端 `str` 契约不一致 | 改 `string \| null`；同时补 `source`/`error` 字段、放开 `AgentRole`/`GateName` | §4.5；`vue-tsc --noEmit` EXIT=0 |
| **M3** | MINOR | `_validate_records` fail-loud 被宽泛 `except` 捕获 → **单条坏记录拖垮整任务 escalate** | 改为 **quarantine**：逐条 try/except，坏记录丢弃 + 告警，好记录保留 | `test_validate_records_quarantines_bad_records`（capsys 断言告警文案） |
| **M4** | MINOR | `sourcingadvisor.md` 用**四维**（3/3/3/1），而 `product_selection.md` 与 `skills.yaml` 用**六维** → 同 Agent 注入两套矛盾框架 | 统一为**六维**（需求/供给/竞争/利润/合规/履约） | 两文件维度口径一致 |
| **M5** | MINOR | 混合语种误靶向（`iPhone 16 Pro Max 手机壳`→`iPhone`）、垃圾输入发请求（`！！！`）**未被测试覆盖** | 抽取器改为**优先 CJK 片段**；新增 `_has_searchable_content()` 守卫 | `test_extract_keyword_prefers_cjk_in_mixed` / `test_extract_keyword_rejects_garbage` |
| **N1** | NIT | `data_sources.py` 修复说明注释重复两段 | 删除重复段 | — |
| **§4.6（审议外新发现）** | 阻塞级 | 注入素材含方括号 → stub 解析器截断 → 整任务 escalate | 解析器改字符串感知 | `test_extract_injected_records_ignores_brackets_inside_strings`；e2e 双链回到 `done` |

**审议纪律声明**：
- 主代理**未自签** `reviewed-by`；`REVIEW_M10-P4.md` 的该标记由**独立子代理**产出。
- 审议结论原文（PASS_WITH_NOTES / MAJOR=0）**未被改写**，本文件仅**追加**闭环事实。
- M2/M3 原为审议「建议 commit 前须消化」的项，**已消化完毕**；M1/M4/M5/N1 属「可后续排期」，
  亦已一并修完。
