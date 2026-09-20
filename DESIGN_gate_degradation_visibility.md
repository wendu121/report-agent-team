# DESIGN · 闸降级必须对用户可见（「假质量闸」消除）

> 状态：**待 boss 评审**（先设计后写码，评审通过才动手）
> 前置：`DESIGN_model_mapping_uf.md`（本轮的起因是它的验证过程）
> 关联宪法条文：**禁「假配置」** / **UI 必须 controller 非 viewer** / **诚实边界——不可用能力必须标出来，绝不挂 connected 冒充可用**

---

## 1. 实测证据（全部真机复现，非推断）

### 1.1 现象：用户看到的是「审核通过 · 评分 0.85」

上一轮为了验证「模型映射改为接口 id」是否真生效，用**子账号**（`af28b675-…`）跑了一次真实任务 `d483d809`。
报告正常产出、任务 `status=done`。结果页「评审留痕」卡片显示：

| 闸 | `decision` | `eval_score` | `reason` |
|---|---|---|---|
| GateA | advance | `null` | 审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题 |
| GateB | advance | **`0.85`** | **同上** |
| GateC | advance | **`0.85`** | **同上** |

即：**三道闸一道都没真审**，但用户在界面上看到的是绿色「放行」+ 一个 `0.85` 的分数。

### 1.2 根因链（四层，逐层实测确认）

**第 1 层 · 闸 LLM 本身不可用（模型能力问题）**

子账号配置（`tenants/af28b675-…/config/model_mapping.yaml`）里：

```
roles.Researcher/Analyst/Writer  → custom:agent:agnes-3.0-flash
gates.GateA/B/C                  → custom:discovery:deepseek-v4-flash-vision
eval                             → custom:discovery:deepseek-v4-flash-vision   ← 同一个模型
```

两个模型都**反复返回无法解析的 JSON**，引擎靠确定性合成兜底（`researcher_records_synthesized` / `writer_citation_regenerated`）才产出报告。

**第 2 层 · 引擎按设计降级放行（这一步本身合理）**

`orchestrator.py:2288-2303`：闸 LLM 两轮都解析失败且代码硬校验通过（`code_dec is None`）→
`decision="advance"`、`reason="审核 LLM 不可用（降级放行）…"`、`eval_score=None`，
并追加审计事件 `gate_llm_unavailable_degraded_advance`。

> 注：代码硬校验优先于 LLM 是**既定设计**（`gates/review.md §5`），本设计**不动**这条优先级。

**第 3 层 · ⚠️ 核心缺陷：降级之后又打了一次分（`orchestrator.py:2323-2328`）**

```python
# 3) eval 降级（DESIGN §10 / gates/review.md §6）
if eval_score is None:
    try:
        eval_score = call_eval(llm, models, state, gate_name, reg, role=role)
    except Exception:
        eval_score = None
```

`call_eval`（`orchestrator.py:1404-1416`）的实现：

```python
eval_model = models["eval"]["model"]        # ← 与闸侧同一个模型
raw = llm.complete(eval_model, "你是评分器，只返回一个 0-1 的小数。", user, role="Eval")
m = re.search(r"0?\.?\d+", raw)              # ← 从自由文本里「抠」第一个像数字的东西
return float(m.group(0)) if m else None
```

于是：**一个刚刚被证明连 JSON 都出不了的模型，换一句 prompt、用一条几乎零校验的正则，从自由文本里抠出 `0.85`**，
这个数字被写回 `eval_score`，与「闸未审核」这个事实**一起**进入同一条记录。

`eval_score` 一个字段因此承载了**两种完全不同的语义**：

| 路径 | `eval_score` 的真实来源 | 含义 |
|---|---|---|
| 闸 LLM 正常出 JSON | 闸 LLM 自评 | 「审核模型认为 0.85」 |
| 闸 LLM 降级 | `call_eval` 独立评分器 | 「另一个模型认为 0.85」 |
| 闸 LLM 正常但没给分 | `call_eval` 独立评分器 | 同上 |
| 两条都失败 | `None` | 无分 |

**第 4 层 · UI 把分数当成了认证**

`web/src/views/TaskResult.vue:66-74`：

```html
<strong>{{ r.gate }}</strong>
<el-tag size="small" :type="decisionTag(r.decision)">{{ decisionLabel(r.decision) }}</el-tag>
<span class="score">评分 {{ r.eval_score }}</span>
```

- 只有 `decision`（放行/打回/升级）有颜色语义，**「闸是否真的审过」没有任何视觉表达**；
- 降级信息只藏在 `reason` 那段小字里，而 `评分 0.85` 反而是显眼的正数字；
- 用户读到的是：`GateB · 放行 · 评分 0.85` → 合理推断为「AI 审核通过，0.85 分」。

### 1.3 派生缺陷（同一根因，本次一并处理）

| # | 缺陷 | 证据 | 危害 |
|---|---|---|---|
| **D1** | **`eval_score=None` 会击穿整个任务投影** | 容器内实测：`RoutingState(**raw)` 传 `eval_score=None` → `ValidationError: Input should be a valid number`（`server/api.py:139` `eval_score: float` 必填非空） | **闸降级 + eval 也失败 → 任务详情页整个 500**。当前靠 `call_eval` 侥幸有值而没暴露；子账号环境下随时可触发 |
| **D2** | 独立评分器**无范围/格式校验** | `re.search(r"0?\.?\d+", raw)` 取**第一个**匹配。模型返回「内容完整度 1. 结构 0.85」→ 抠出 `1`；返回「85%」→ 抠出 `85.0`（越界） | 分数可以完全失真，且越界值会流到前端与审计包 |
| **D3** | 前端事件类型**漏一种** | 后端 `server/api.py:121-125` 声明 4 种事件；前端 `web/src/types/api.ts:28-32` 只声明 3 种，**缺 `researcher_records_synthesized`** | `timeline.ts:180` 的 `titleMap` 查不到键 → 该事件渲染成**标题为空**的时间线节点（实跑中它真的出现过） |
| **D4** | 降级事件与闸记录**未关联** | 事件里有 `gate` + `round`（`orchestrator.py:2296-2299`），但只被 `timeline.ts` 平铺到时间线；`TaskResult.vue` 的闸卡片不读 `engine_events` | 用户得自己对时间线，才能猜出哪道闸没审 |
| **D5** | 前端类型同样谎报 | `web/src/types/api.ts:85` `eval_score: number`（非空） | 后端一旦按 D1 改为可空，前端类型不同步则类型层面继续骗人 |
| **D6** | 审计留痕只在文件里 | 全仓 `grep "GateReview("` 命中项**只有类定义与测试**——`server/models.py:116` 的 **DB 模型从未被实例化/落库**（`api.py:135` 的同名 Pydantic 模型仅用于响应，`tests/` 里确有构造）。故 `gate_reviews` 表空转，闸记录只活在 `.engine_state/*_output.json` 与审计 ZIP 里 | 属「任务持久化」另一条线；**本档不修**，但在 §10 点名 |

---

## 2. 设计目标 / 非目标

### 目标

- **G1**｜「这道闸到底审没审」在**数据契约层**就是一个显式、不可歧义的事实，而不是靠解析 `reason` 文本。
- **G2**｜降级在 UI 上**显眼可见**，且**任务级有汇总**——用户不该靠逐条读小字才发现质量结论没被审核。
- **G3**｜`eval_score` 的**来源与可信度**可区分；不可信的分数不得以「认证」姿态出现。
- **G4**｜降级不再让任务投影崩（D1），前端类型与后端契约一致（D3/D5）。
- **G5**｜**正常路径零回归**：主账号（模型都正常）的闸记录语义与显示与今天完全一致。

### 非目标（明确划线，见 §10）

- 不做「降级即阻断任务」的 strict 模式。
- 不做「闸模型失败时自动换模型重试」。
- 不修 `gate_reviews` 表落库 / 任务持久化（D6）。
- 不改代码硬校验 > LLM 判定的既定优先级。
- 不动主账号 `model_mapping.yaml`。

---

## 3. 数据契约（本设计的地基，先定这个）

### 3.1 闸记录新增显式字段

`gate_review_history[]` 每条新增：

| 字段 | 类型 | 语义 |
|---|---|---|
| `review_status` | `Literal["llm_reviewed", "code_verified", "degraded_unavailable"]` | **本次闸决策的实际依据来源** |
| `eval_score_source` | `Optional[Literal["gate_llm", "independent_scorer"]]` | 分数从哪来；`eval_score` 为 null 时为 null |

`review_status` 三态定义（与 `orchestrator.py:2288-2308` 现有分支一一对应，不新增逻辑分支）：

| 取值 | 触发条件（现有代码位置） | UI 语义 |
|---|---|---|
| `llm_reviewed` | `parsed is not None`（闸 LLM 出了可解析结论），`orchestrator.py:2305+` | ✅ 已由审核模型评审 |
| `code_verified` | `parsed is None` 且 `code_dec in ("rework","escalate","advance")`，`2289-2295` | ⚙️ 由代码硬校验裁决（LLM 未参与） |
| `degraded_unavailable` | `parsed is None` 且 `code_dec is None`，`2294-2303` | ⚠️ **审核模型不可用，降级放行** |

> **为什么用枚举而不是 `degraded: bool`**：`2289-2295` 那两条分支今天同样把 LLM 的缺席藏进了 `decision`——
> 一个 bool 只能表达「降级」，表达不了「这次其实是代码判的」。三态与代码分支一一对应，**零额外判断逻辑**，
> 前端只需要一个 `switch`。这是最小充分建模，不是过度工程。

### 3.2 `eval_score` 改为可空

```diff
  class GateReview(BaseModel):
      decision: Literal["advance", "rework", "escalate"]
      reason: str
-     eval_score: float
+     eval_score: Optional[float] = None      # D1：降级且独立评分器也失败时为 null
+     review_status: Literal["llm_reviewed", "code_verified", "degraded_unavailable"]
+     eval_score_source: Optional[Literal["gate_llm", "independent_scorer"]] = None
      problem_points: List[str]
```

同步 `web/src/types/api.ts`（D5）：`eval_score: number | null`，并补 `review_status` / `eval_score_source`。

**兼容性**：新增字段均为**可选**（`Optional` + 默认值），历史 `.engine_state/*_output.json`（无这两个字段）仍可投影——
历史记录会得到 `review_status` 缺失。为让历史数据也能正确显示，UI 的兜底判定为：

```
review_status 缺失 且 reason 含「降级放行」  →  按 degraded_unavailable 显示
review_status 缺失 且 无该文案              →  按 llm_reviewed 显示（与今天一致）
```

（后端可选择在投影时按 `reason` 文本**回填** `review_status`，把兼容逻辑收敛到一处；见 §5.2 决策点。）

---

## 4. 引擎侧改造（`orchestrator.py`）

### 4.1 写入 `review_status` / `eval_score_source`

在三处赋值点补字段（**只加字段，不改判定逻辑**）：

- `2289`（`code_dec == "rework"`）与 `2291`（`code_dec == "escalate"`）→ `review_status="code_verified"`
- `2294-2303`（降级分支）→ `review_status="degraded_unavailable"`
- `2305+`（`parsed is not None` 分支）→ `review_status="llm_reviewed"`；
  注意 `2323-2328` 的 eval 兜底：若走了 `call_eval`，则 `eval_score_source="independent_scorer"`，
  否则 `"gate_llm"`；`eval_score is None` 时两者皆 `null`。

### 4.2 `call_eval` 加可信性守卫（D2）

> **实现期修正（2026-09-19）**：初稿只写了"范围守卫"，但写测试时发现范围守卫**不够**——
> `"评分：1. 内容完整度 0.85"` 里首个匹配是 `1.` → `1.0`，**落在 [0,1] 内**，仍会通过。
> 这是"在散文里找数字"这一做法的结构性缺陷，不只是越界问题。
> 故实际实现升级为**整条回复匹配**（`re.fullmatch`，只允许结尾一个可选的"分"）：
> prompt 已明确要求"只返回一个 0-1 的小数"，不合规就诚实地判为"未评分"。
> 已由 `tests/test_gate_degradation_visibility.py` 的 9 个参数化用例锁死。

```python
def call_eval(...) -> Optional[float]:
    ...
    raw = llm.complete(eval_model, "你是评分器，只返回一个 0-1 的小数。", user, role="Eval")
    # 只接受"整条回复基本就是一个小数"的形态，不在散文里抠数字
    m = re.fullmatch(r"(0?\.?\d+)\s*分?", (raw or "").strip())
    if not m:
        return None
    try:
        v = float(m.group(1))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= v <= 1.0):     # 越界（如 "85%" → 85）→ 视为不可信
        return None
    return v
```

**行为影响（真实、非零）**：`call_eval` 是闸 LLM 没给分时的兜底路径。收紧后，
"闸 LLM 没给分 + 评分器返回散文"的场景会从"一个有数字的分数"变成 `None` →
UI 显示「未评分」。这是**有意的行为变更**：宁可显示未评分，也不显示误读出来的数字。
主账号的常见路径（闸 LLM 自己在 JSON 里给 `eval_score`）**不受影响**（那条不走 `call_eval`）。

### 4.3 审计事件保持

`gate_llm_unavailable_degraded_advance` 事件**保留原样**（它已是可审计痕迹），
新增 `review_status` 是给**用户可见层**用的结构化事实，两者不互相替代。

---

## 5. API 侧改造（`server/api.py`）

### 5.1 消灭 D1 崩溃

- `GateReview.eval_score` 改 `Optional[float] = None`（§3.2）。
- `RoutingState(**routing)`（`api.py:573` / `:744`）外层加**显式容错**：
  投影失败时不得让整个任务详情 500，而应降级为「可读的最小状态 + 明确错误标注」。
  具体做法（择一，见决策点）：投影失败时保留上一份合法状态并追加 `projection_error` 提示，或按字段逐条容错。

### 5.2 决策点：历史数据兼容放哪
- **(甲)** 后端投影时按 `reason` 文本回填 `review_status` → 兼容逻辑集中一处，前端零判断。
- **(乙)** 前端兜底判断 → 后端保持纯粹，但逻辑分散。
- **推荐 (甲)**：一处收敛，且审计导出（`server/audit_export.py` 的 `_GATE_FIELDS`）也能同步受益。

`server/audit_export.py:22` 的 `_GATE_FIELDS` 需加入 `review_status` / `eval_score_source`，
否则审计 ZIP 里的 `gate_review_history.json` 会漏掉这两个最关键的新字段。

---

## 6. UI 侧改造（`web/src/`）

### 6.1 闸卡片：降级必须一眼看见

`web/src/views/TaskResult.vue` 的评审留痕卡片：

| 现在 | 改后 |
|---|---|
| `GateB` · `放行` · `评分 0.85` | `GateB` · `放行` · **`⚠️ 未经 AI 审核（降级放行）`** |
| （无） | 分数主视图显示 **`未评分`** 或 **`—`**；独立评分器分数降为次要标注：`独立评分器 0.85（非审核结论）` |
| （无） | `code_verified` 闸显示 `⚙️ 代码硬校验裁决` |

关键原则：**在降级闸上，`0.85` 这个数字绝不能以主视觉呈现**——它是「另一个模型随口给的数」，
给它同等视觉权重 = 继续骗人。

### 6.2 任务级汇总横幅（G2 的核心）

`TaskResult.vue` 顶部（报告正文之上）增加一条**仅在存在降级时出现**的警示：

```
⚠️ 本次报告有 2 / 3 道审核闸未真正执行（审核模型不可用，已降级放行）。
   质量结论未经 AI 审核，请谨慎采信。 [查看详情]
```

计数来源 = `gate_review_history.filter(r => r.review_status === 'degraded_unavailable').length`。
**无降级时完全不渲染**（正常任务零视觉变化，兑现 G5）。

### 6.3 事件与闸卡片关联（D4）

把 `engine_events` 里 `gate_llm_unavailable_degraded_advance` 按 `gate` + `round` 挂到对应闸卡片下方，
作为「为什么降级」的解释行；时间线上的平铺节点保留（两个入口，不互斥）。

### 6.4 类型补齐（D3/D5）

`web/src/types/api.ts`：
- `EngineEventType` 补 `'researcher_records_synthesized'`；
- `GateReview` 补 `review_status` / `eval_score_source`，`eval_score` 改 `number | null`；
- `timeline.ts:180` 的 `titleMap` 补 `researcher_records_synthesized: '🧩 检索记录由引擎合成'`。

---

## 7. 兼容性与风险

| 风险 | 评估 | 对策 |
|---|---|---|
| 主账号显示被改动（打破 G5） | 主账号三道闸均为 `llm_reviewed` → 横幅不渲染、卡片只多一个「已由审核模型评审」的淡标签 | 验收 A5 逐条比对改动前后的闸记录渲染 |
| 历史任务 `review_status` 缺失 | 前端兜底 or 后端回填（§5.2） | 决策点，二选一 |
| 后端加字段导致旧前端崩 | Pydantic 多余字段默认忽略；前端 TS 是编译期类型不参与运行时 | 无风险 |
| `eval_score` 可空后旧渲染逻辑 `评分 {{ null }}` | 会显示「评分 」空串——**必须**改 §6.1 的显示逻辑 | 验收 A3 覆盖 |
| `call_eval` 加范围守卫导致「本来有分现在没分」 | 正是目的：宁缺毋滥 | 属行为改善，非回归 |
| `RoutingState` 容错掩盖真实字段漂移 | 项目既有立场是 `fail loud`（见 `api.py:585-589` 注释） | 容错必须**同时**记 `projection_error` 到响应与日志，不许静默 |

---

## 8. 验收标准（全部须真机实测，不接受纸面）

| # | 标准 | 判据 |
|---|---|---|
| A1 | `eval_score=None` 不再击穿投影 | 容器内构造含 `eval_score: None` 的 routing → `RoutingState(**raw)` 成功；`GET /tasks/{id}` 返回 200 |
| A2 | 降级闸带结构化标记 | 复现降级（或构造）→ 该闸 `review_status == "degraded_unavailable"` |
| A3 | 降级闸 UI 不显示裸分数 | 闸卡片呈现「未经 AI 审核」且主视觉无 `0.85` |
| A4 | 任务级横幅计数正确 | 构造 2/3 降级 → 横幅显示 `2 / 3`；构造 0 降级 → **不渲染** |
| A5 | **正常路径零回归** | 主账号跑真实任务：三道闸 `review_status=="llm_reviewed"`，闸卡片显示与今日一致，无横幅 |
| A6 | 越界分数被拒 | 单测：`call_eval` 遇 `"85%"` / `"1."` 等 → 返回 `None` 而非 `85.0` |
| A7 | 双失败链路可活 | 闸 LLM + 独立评分器**同时**不可用 → 全链路 200，UI 显示「未评分」 |
| A8 | 前端类型一致 | `vue-tsc --noEmit` **exit=0**；`EngineEventType` 4 种齐全 |
| A9 | 审计导出含新字段 | `GET /tasks/{id}/audit` 的 `gate_review_history.json` 含 `review_status` |

全量 `pytest` 不得出现**新增**失败（已知基线：7 个失败全在 `test_doc_render.py`，因宿主缺 `docx`/`pptx`/`reportlab`）。

---

## 9. 交付分档（按优先级）

| 档 | 内容 | 为什么这个顺序 |
|---|---|---|
| **档 1 · 后端契约与容错** | §3 数据契约 + §4 引擎写字段 + §4.2 正则守卫 + §5 API（含 D1 崩溃、审计导出字段） | 契约不定，前端改了也白改；D1 是**会 500 的实缺**，优先 |
| **档 2 · 用户可见性** | §6 全部（闸卡片徽标 / 任务级横幅 / 事件关联 / 类型补齐） | 有了结构化事实才谈得上显示 |
| **档 3 · 可选（后续另议）** | strict 模式（降级即阻断或强制重跑）/ 闸模型失败自动换模型 | 会改变子账号可用性，需单独拍板 |

---

## 10. 不做的事（明确划线）

1. **不阻断**：降级仍放行。子账号当前**完全依赖降级**才能出报告，阻断=直接不可用。本档只做「可见 + 诚实」，把选择权留给用户。
2. **不自动换模型重试**：闸模型失败就换一个再试，会引入新的不可控成本与串角色风险（`reg["reviews"]` 1:1 投影问题尚未完全收敛）。
3. **不修 `gate_reviews` 表空转**：属「任务持久化」既有技术债（D6），单独立项。
4. **不改代码硬校验 > LLM 判定的优先级**：这是既定设计。
5. **不动主账号配置**：本轮起因是子账号，主账号行为必须逐条零变化。

---

## 11. 待 boss 拍板的两个决策点

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **K1** | 降级闸的 `eval_score` 怎么显示 | (甲) 主视图保留数字 + 标注来源　(乙) **主视图显示「未经 AI 审核 / 未评分」，原始分数降为次要标注** | **(乙)**。给一个已被证明不可靠的模型打出的数字同等视觉权重，就是继续骗人；审计要的原始值在详情/导出里仍可取 |
| **K2** | 历史任务 `review_status` 兼容逻辑放哪 | (甲) 后端投影时按 `reason` 回填　(乙) 前端兜底 | **(甲)**。一处收敛，审计导出同步受益 |

> **拍板结果（boss 授权自决，2026-09-19）**：K1 取 **(乙)**、K2 取 **(甲)**，均已实现并实测。

---

## 12.0 【根因追查】子账号三道闸 100% 降级的**真原因不是"可见性"，是配额耗尽**

boss 追问：「你做成这样，不就变成了**无审核直接放行**了？」——顺着这句追问挖到了本档
之外的一个真问题，记录在此。

### 12.0.1 先澄清：降级放行**不是本档引入的**

`git show HEAD:orchestrator.py` 第 2139-2156 行（**已提交版本**）就存在降级分支：

```python
# 代码校验已通过（code_dec=None）而闸 LLM 不可用 → 降级放行（可审计）
reason = "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题"
problem_points, eval_score = ["审核 LLM 不可用，已降级放行"], None
```

本档 `git diff orchestrator.py` 对决策逻辑**零改动**，只**新增**了
`review_status` / `eval_score_source` 的赋值（用于标记依据来源）。
**即：改前改后，放行判定完全一样；区别在于改前 UI 写「✅ 审核放行 0.85」，改后写「⚠️ 未经 AI 审核」。**

### 12.0.2 那为什么闸一次都没审？——**闸模型配额耗尽（429）**

新增探针 `scripts/probe_gate_model.py` / `scripts/probe_model_quota.py` 实测（子账号租户）：

| 用途 | 模型 | provider | 极小调用结果 |
|---|---|---|---|
| Researcher / Analyst / Writer | `custom:agent:agnes-3.0-flash` | `apihub.agnes-ai.com` | **OK**，能直接输出 `{"ok":true}` |
| GateA / GateB / GateC / eval | `custom:discovery:deepseek-v4-flash-vision` | `discovery-api.intern-ai.org.cn` | **❌ 429 `quota exceeded`** |

结论：**闸不是"笨到出不了 JSON"，而是"没钱了"**。引擎把 `LLMError`（含 429）统一归入
「审核 LLM 不可用」→ 降级放行 → 报告照出、分数照给（改前）。
所以 boss 的直觉是对的：在当前配置下三道闸**事实上等于没在工作** —— 但**这是我暴露出来的，不是我造成的**。

### 12.0.3 恢复真审的三条路（需 boss 拍板，我无法自行完成）

| 方案 | 做法 | 约束 |
|---|---|---|
| **(甲) 充值 / 换 key（推荐）** | 给 `discovery`（Intern-AI）续额度或换一个有效 key | 最省事；保持「异基座防自审包庇」 |
| **(乙) 引入第三个 provider** | 新增一个可用 endpoint+key 专供 gate/eval | 需 boss 提供凭据；同样保持异基座 |
| **(丙) 让 gate 复用 `agnes`** ⚠️ | 闸改用与 Agent 同基座的模型 | **违反设计约束** `gates.*.base != roles.<reviews>.base`，会形成自审包庇 —— **不建议** |

> 注：`strict` 模式（降级即阻断）**仍不建议**：当前 100% 降级，启用等于子账号直接出不了报告。

### 12.0.4 待拍板 **K3**：要不要把「配额耗尽」与「模型出不了 JSON」分开标注？

现状：两种性质完全不同的失败被归成同一句「审核 LLM 不可用（降级放行）」。

- **429 配额耗尽** → **可恢复的外部配置问题**（充值即可），用户应被告知"充了就好"；
- **JSON 解析失败** → 模型能力/prompt 问题，需换模型或改解析。

若只说「审核模型不可用」，用户会误以为"这个系统没有审核能力"，而实际上换个有额度的模型就能恢复。
建议新增 `degraded_reason: 'quota_exhausted' | 'unparseable' | 'other'`，UI 分别提示。
**本档未实现**，等 K3 拍板。

---

## 12. 目视验收增补（2026-09-19 夜）：同一语义存在**两条渲染路径**，只补一条 = 没修

> 本节记录目视验收阶段发现的**两处漏改**。它们都不在设计初稿范围内 —— 因为初稿把
> 「时间线」当成了一个函数，而实际上**同一个语义有两套渲染代码**。

### 12.1 背景：前端时间线有两套路径

| 路径 | 入口 | 数据来源 |
|---|---|---|
| **实时** | `utils/timeline.ts::wsEventToNode` | WS 增量（后端 `_stream_engine_events` 原样转发 `*_events.jsonl`） |
| **快照重建** | `engineEventToNode` + `gateReviewToNode`（`buildNodesFromTask`） | `GET /tasks/{id}` 的 `routing_state`（mount / WS 重连时全量兜底） |

**实测确认的事件走向**（扫全部 `*_events.jsonl`）：
`researcher_records_synthesized` **会**经 WS 广播；`writer_citation_regenerated` /
`gate_llm_unavailable_degraded_advance` **只**出现在 `engine_events`（快照路径）。

### 12.2 漏改 ①：引擎事件的实时路径

初版只把 `researcher_records_synthesized` 补进快照路径的 `titleMap`，实时时间线仍渲染
`未知事件 researcher_records_synthesized`（真机复现）。

**修法**：把引擎事件 → 标题/类型/状态 的映射提升为模块级唯一常量 `ENGINE_EVENT_NODES`，
两条路径**共用**；`WSEventType` 补全 3 种缺失类型 + `EngineEventData` payload。

### 12.3 漏改 ②：闸节点的快照路径（**更严重**）

初版只改了 `wsEventToNode` 的 `gate_complete` 分支，**`gateReviewToNode` 未改** ——
于是追踪页（快照路径）仍渲染：

```
✅ 审核放行 · GateA
审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题
🔓 闸 LLM 不可用·降级放行        ← 紧挨着的下一条（引擎事件）
```

同一张页面上「✅ 审核放行」与「🔓 降级放行」**自相矛盾** —— 把降级又包装成了审核结论，
正是本设计要消灭的东西，且比不显示更糟。修法：`gateReviewToNode` 同样走
`isDegraded` + `REVIEW_STATUS_TIMELINE`，与实时路径一致。

### 12.4 对既有语义的一处修正（明示，不藏在代码里）

原实现对除降级放行外的引擎事件一律给 `tool_error` + `error`（红）。但
`researcher_records_synthesized` 是**纯告知性**事件（模型层 JSON 缺失/无效/空时由引擎
从真实检索结果确定性合成，count=N），`writer_citation_regenerated` 是补救性动作。
把信息性事件渲染成红色错误与「把降级说成审核通过」同类失真，故改为 `info` / `warning`。

### 12.5 验证方式（不烧模型配额）

新增 `scripts/verify_timeline_nodes.js`：用 `typescript.transpileModule` 把
`timeline.ts`/`reviewStatus.ts` 转译后在 Node 里**真实执行**，对两条路径逐分支断言
（18 条断言，含「正常闸零变化」「历史数据 reason 兜底」「`review_status` 直采」）。
真机浏览器证据见 `VERIFICATION_*.md §7`。
