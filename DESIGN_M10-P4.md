# DESIGN_M10-P4 · 电商数据源补强（A）+ 电商智能体/技能（B）

**日期**：2026-09-11
**驱动**：boss「AB都要」→「A还是把你认为有用的抄了，再推进 B」
**前序**：`DESIGN_M10-P3.md`（4 个电商词源）、`REVIEW_M10-P3.md`（M3 指出通用 REST 桥未交付）

---

## 0. 一句话结论

- **A**：Accio 9 个公开计算器里，**7 个是纯前端公式（无后端可抄）**，只有「HS 码查询」背后有真 API → **已实现为 `hs_code_tariff` 源**（6 国关税 + 301/反倾销风险，零密钥）。
- **B**：把「电商竞品分析」拆成 **3 个新 Agent + 3 个新技能**，走引擎既有 `agents_library.yaml` / `skills.yaml` 契约，**零代码改动**（纯配置注入），UI 可见可选。

---

## 1. A：Accio 公开计算器探源结论（实测）

### 1.1 逐个探测（13 repo → 9 计算器）

| 计算器 | 页面有无后端调用 | 判定 |
|---|---|---|
| CBM 体积重 | 无 `fetch`/`api` 痕迹 | ❌ 纯前端公式 |
| 进口关税 | 无 | ❌ 纯前端公式 |
| Incoterms | 无 | ❌ 纯前端公式 |
| MOQ | 无 | ❌ 纯前端公式 |
| 利润计算 | 无 | ❌ 纯前端公式 |
| 运费估算 | 无 | ❌ 纯前端公式 |
| 采购成本分析 | 无 | ❌ 纯前端公式 |
| 中美/中英关税 | 无 | ❌ 纯前端公式 |
| **HS 码查询** | **`fetch` ×3 + `/api/` ×2** | ✅ **真 API** |

**证据方法**：`curl accio.com/wow/tool-*.html` → `grep -cE "fetch\(|XMLHttpRequest|axios"` 与 `grep -coE "/api/|/turtle/|graphql"`。除 HS 码查询页（3/2）外，其余均为 0-1/0。

### 1.2 挖出的真 API（零密钥）

源码行 `958-1142` 暴露：
```
API_BASE = 'https://accio-wow-api.vercel.app/api/hs-code'
  GET /categories                    → 11 分类 + 5618 产品总数
  GET /suggest?q=&limit=             → 模糊搜索（⚠️ 质量差，见 1.3）
  GET /search?q=&limit=&category=    → 模糊搜索（⚠️ 同上）
  GET /<code>                        → ✅ 精确码查询（可靠）
  POST /batch-csv                    → 批量 CSV
```

**精确码查询实测**（容器内，HTTP 200，连查 3 次稳定）：

| 码 | 描述 | AU | CA | DE | EU | GB | US | 风险 |
|---|---|---|---|---|---|---|---|---|
| 6109.10 | 棉 T 恤 | 10% | 18% | 12% | 12% | 0% | **34%** | section301 |
| 8518.30 | 无线耳机 | 0% | 0% | 0% | 0% | 0% | **10%** | section301 |
| 6911.10 | 陶瓷餐具 | 5% | 3% | 4% | 7.5% | 0% | 6% | **ad**（反倾销） |
| 8517.13 | 智能手机 | 0% | 0% | 0% | 0% | 0% | 0% | — |

字段：`hs_code` / `hts_code`（10 位 HTS）/ `description` / `category` / `tariff_risk` / `notes` / `tariffs{6国}`。

### 1.3 ⚠️ 关键诚实边界：**只抄精确查码，不抄模糊搜索**

模糊搜索实测**不可靠**（返回语义无关结果）：

| 查询 | 返回 | 判定 |
|---|---|---|
| `cotton t-shirt` | 6109.10 棉T恤 | ✅ |
| `bluetooth earphone` | 8518.90 耳机 | ✅ |
| `ceramic mug` | **2844.10 铀矿** | ❌ |
| `steel water bottle` | **0301.11 活鱼** | ❌ |

**根因**：该端点是 HS 库上的**模糊文本匹配**，非语义分类器。
**设计对策**：`HsCodeTariffProvider` **只暴露 `/<code>` 精确查询**，`_normalize_code` 把输入归一为 `XXXX.XX`；非码输入（如「陶瓷咖啡杯」）→ **返回空**，绝不返回模糊匹配的错码。

**两源分工（写入代码注释，防后人混淆）**：
- `accio_tariff`（阿里语义分类器）＝「**商品名 → HS 码**」（实测「陶瓷咖啡杯」→ 69111045 准）
- `hs_code_tariff`（本源）＝「**HS 码 → 六国关税 + 风险**」
- 组合 = 完整链路「商品名 → HS 码 → 各国关税」

### 1.4 A 交付物

| 文件 | 变更 |
|---|---|
| `tools/data_sources.py` | `+HsCodeTariffProvider`（含 `_normalize_code` / `MARKET_LABEL` / market 过滤），注册 `KEYLESS_PROVIDERS`（13 → 14） |
| `config/plugins.yaml` | `+hs_code_tariff` 条目，`category: 电商`（电商类 4 → 5） |
| `tests/test_tools.py` | 新增解析器离线单测 |

**query 约定**：直接传 HS 码（`6109.10` / `610910` / `691110` 均可）；可选 `|US,DE` 限定市场。

---

## 2. B：电商竞品分析 Agent + Skill（零代码改动）

### 2.0 ⚠️ 前置修复：`reviews` 反查表在「多 Agent 共用一闸」时**静默解析错角色**（必修）

**实测证据**（模拟 4 角色、EcomAnalyst 与 Analyst 共用 GateB）：
```
reviews (gate->role) 反查: {'GateA': 'Researcher', 'GateB': 'EcomAnalyst', 'GateC': 'Writer'}
  GateB 解析为: EcomAnalyst   <-- 期望 Analyst，实际被覆盖
```

**根因**：`load_agent_registry` 的 `reviews = {a["gate"]: a["id"]}` 是 **1:1 反查**，
多个 Agent 共用同一闸时**后者覆盖前者**（dict 键碰撞，静默无告警）。
5 个调用点受影响（`build_gate_user` / `call_eval` / `make_gate` / 两处 router），
后果 = **GateB 会去审 EcomAnalyst 的产出而不是 Analyst 的**（审错对象）。

**修法（最小侵入，不改数据模型）**：
- `make_gate(gate_name, ..., role=None)`：新增可选 `role` 参数，**由图构建处直接传入被审角色**
  （`build_graph` 本就持有 `for role in chain`，天然知道谁被审）。
- `build_gate_user(gate_name, state, reg, role=None)` / `call_eval(..., role=None)` 同步新增可选参。
- 传了 `role` 就用它；未传则**回落 `reg["reviews"][gate_name]`**（向后兼容既有调用/测试）。
- `build_graph` 调用点改为 `make_gate(..., role=role)`。

**为何不选「给每个电商 Agent 发独立闸」**：会新增 GateD/GateE，牵动 gate_model 异基座校验、
前端闸列表、`default_chain` 语义，属过度工程；且**根因是反查表设计，不是闸不够用**。

> 本条属**实现 B 的必要前置**（不修则多 Agent 共用闸必然审错），非范围蔓延。

### 2.1 为什么能（近乎）零代码改动

引擎既有契约（实测源码）：
- `agents_library.yaml` = Agent SoT；`load_agent_registry()` 热加载（每 run 重读）
- `build_agent_system(role)` 读 **`agents/<role.lower()>.md`** 作为 system prompt
- `load_agent_registry` 派生 `PROD_KEY/TOOL/GATE_NAME/GATE_REVIEWS/gate_shape`
- `skills.yaml` + `build_skill_context(role)` 按 `target_roles` 把技能片段注入 system prompt
- **`shape ∈ {researcher, analyst, writer}`** 三选一，决定工具分发 + 产出键 + 闸校验形态

→ **新增电商角色 = 加 3 条 yaml + 写 3 个 md + 加技能**，无需碰 Python。

### 2.2 三个新 Agent（挂在既有 shape 上，不造新 shape）

| id | name | shape | tool | gate | 定位 |
|---|---|---|---|---|---|
| `MarketScout` | 市场侦察 | researcher | web_search | GateA | 电商市场/竞品关键词检索与事实核证（消费 Researcher 上游） |
| `EcomAnalyst` | 电商分析师 | analyst | data_proc | GateB | 竞品对标 + 关税/成本结构 + 利润空间测算 |
| `SourcingAdvisor` | 选品顾问 | analyst | data_proc | GateB | 选品决策：需求×竞争×成本×合规 四维评估 |

> **为何 SourcingAdvisor 也用 analyst**：shape 是「产出形态」而非「角色语义」；选品结论本质是 `analysis_conclusions`（带 source_ids 引用链），与 Analyst 同形态。**不新造 shape**（否则要改 gate 校验/工具分发/前端，违背「不过度工程」）。
>
> **Gate 复用**：新 Agent 复用 GateA/GateB。Gate 的 prompt 由 `gates/<gate_name>.md` 决定；内置 GateA/B/C 无独立文件 → 回落 `gates/review.md`，**新角色自动被既有闸覆盖**，零改动。

### 2.3 三个新 Skill

| id | name | target_roles | 内容 |
|---|---|---|---|
| `ecom_competitor_matrix` | 电商竞品矩阵 | [analyst] | 竞品四维对标（卖点/价格带/评论痛点/流量来源） |
| `tariff_cost_model` | 关税成本模型 | [analyst] | 落地成本 = 货值 + 关税 + 运费 + 平台佣金；**强调 hs_code_tariff 查 6 国税率、section301/AD 风险必标** |
| `product_selection` | 选品四维评估 | [analyst] | 需求×竞争×成本×合规 打分框架 |

> 复用既有 `competitor_compare`（通用竞品对比）作底座，新增的是**电商特有维度**（关税/平台佣金/评论痛点）。

### 2.4 与既有链的关系（关键：不破坏默认行为）

- **`default_chain(reg)` 必须保持默认 3 角色**（Researcher/Analyst/Writer），否则所有既有任务行为改变、Gate 数变化 → 破坏回归。
- 新 Agent 通过 **子集编排（M8-5 机制）** 使用：任务提交时选 `agents=[MarketScout, EcomAnalyst, Writer]` 等组合。
- 实现上：新条目 `visibility: public` 但 **`builtin: false`**（可被 UI 增删），且**不进 `default_chain` 白名单**（需实测 `default_chain` 逻辑，见 §3 风险）。

### 2.5 B 交付物

| 文件 | 变更 |
|---|---|
| `config/agents_library.yaml` | +3 Agent 条目 |
| `agents/marketscout.md` | 新建（市场侦察 prompt） |
| `agents/ecomanalyst.md` | 新建（电商分析师 prompt） |
| `agents/sourcingadvisor.md` | 新建（选品顾问 prompt） |
| `config/skills.yaml` | +3 Skill 条目 |
| `skills/ecom_competitor_matrix.md` 等 | 新建 3 个技能片段 |

> ⚠️ **`config/skills.yaml` 是本项目历史踩坑文件**（ruamel round-trip 会改缩进风格，曾被误 commit）。本次改动须单独确认 diff、commit 后 `git diff-tree` 验证真实树。

---

## 3. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | 新 Agent 进 `default_chain` → 默认任务行为改变、既有回归崩 | 先读 `default_chain` 实现，确认它是按 `builtin` 还是按 yaml 顺序取；**必须保证默认链仍是 3 角色**。实测 `pytest tests/` 全绿为证 |
| R2 | 新 Agent 的 `gate.base` 异基座硬约束（防自审） | 新 Agent 复用 GateA/B，不新增 gate → 沿用既有异基座校验，无新增约束 |
| R3 | `agents/<role>.md` 文件名须 = `role.lower()` | 命名严格对齐：`MarketScout → marketscout.md` |
| R4 | 技能未 `installed`/`enabled` → 静默不生效 | `build_skill_context` 过滤链：enabled + (installed|builtin) + target_roles + 片段非空；yaml 全置 true 并实测注入 |
| R5 | `config/skills.yaml` 缩进风格被 ruamel 改写 | commit 前看 diff；若仅有风格变更需与 boss 确认单开（历史约定） |
| R6 | `hs_code_tariff` 模糊输入返错码 | 已设计为**只走精确码查询**，非码输入返空（§1.3） |
| R7 | vercel.app 端点稳定性（第三方免费托管） | 失败即 `ToolError` → 单源降级；已在 `_http_get_json` 统一容错 |

---

## 4. 验收标准（三道闸）

1. **闸①**：`py_compile` EXIT 0 + `pytest tests/` 全绿 + `web npm run build` OK
2. **闸②**：`VERIFICATION_M10-P4.md` 自审（含容器内真实取数证据 + Agent 注入实测）
3. **闸③**：独立子代理 `REVIEW_M10-P4.md`，未自签前不 commit

**容器验收**：重建后 5 个电商源全 connected；`hs_code_tariff` 真查 6 国关税；新 Agent 可被子集编排调用且默认链不变（3 角色）。

---

## 5. 明确不做

- ❌ 不抄 7 个纯前端计算器（无后端，抄来是重复造轮子）
- ❌ 不把 `hs_code_tariff` 的模糊搜索当检索源（质量差，会污染研究者素材）
- ❌ 不为电商新造 shape（不新造 gate、不改 gate 校验/工具分发）
- ❌ 不破坏默认 3 角色链（新 Agent 走子集编排）
- ❌ 不做 `GenericRestProvider` 通用桥（M10-P3 已记，仍待 boss 单独决策）
