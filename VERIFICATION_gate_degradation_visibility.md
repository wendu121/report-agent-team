# VERIFICATION · 闸降级必须对用户可见（M13-gate-degradation）

> 里程碑：**M13-gate-degradation**
> 设计 SoT：`DESIGN_gate_degradation_visibility.md`（含 §4.2 实现期修正）
> HEAD：`75b263a`（本轮改动**未 commit**）
> 自审人：主代理（**非**独立审议；gate ③ 见 `REVIEW_gate_degradation_visibility.md`）
> 依据铁律：自审结论必须来自 **Edit 后立即 grep 复核 + 真机命令输出**，不得凭「我发过 Edit」下结论。

---

## 0. 一句话结论

档 1（后端契约与容错）+ 档 2（前端可见性）**已实现并实测通过**：容器内 31 项断言全绿、
宿主全量回归零新增失败、前端 `vue-tsc exit=0`、双镜像已重建且产物确认为新建。
**未做**：浏览器目视验收（我只做到数据面 + 产物字符串核对，见 §5）。

---

## 1. 交付范围（本轮**仅**这 11 个文件）

工作树里同时存在其他多个未提交里程碑的改动，故此处逐文件划清本轮范围。

### 1.1 后端（4 个文件改动 + 2 个新增）

| 文件 | 改动 |
|---|---|
| `orchestrator.py` | ① `call_eval` 加可信性守卫；② 闸节点 4 分支写 `review_status`；③ 第 3 步记 `eval_score_source`；④ `go` 字典与 WS 事件补两字段 |
| `server/api.py` | ① `GateReview` 加 `review_status`/`eval_score_source`、`eval_score` 改可空；② 新增 `_coerce_gate_reviews` / `_coerce_routing_state`；③ 实时路径 + 文件恢复路径改用容错投影 |
| `server/audit_export.py` | `_GATE_FIELDS` 与 `gate_review_history.json` 补两字段 |
| `server/websocket.py` | `GateCompleteEvent` 与 `emit_gate_complete` 补两可选字段 |
| `tests/test_gate_degradation_visibility.py` | **新增**（19 个用例：14 宿主实跑 + 5 容器实跑） |
| `scripts/verify_gate_degradation.py` | **新增**（容器内 31 项断言，补 pytest 缺失） |

### 1.2 前端（4 个文件改动 + 1 个新增）

| 文件 | 改动 |
|---|---|
| `web/src/types/api.ts` | `EngineEventType` 补第 4 种；新增 `GateReviewStatus`/`EvalScoreSource`；`GateReview.eval_score` 改 `number \| null` |
| `web/src/types/engine.ts` | `GateCompleteData` 补两个可选字段 |
| `web/src/utils/timeline.ts` | `titleMap` 补键；`gate_complete` 节点降级时改文案与状态 |
| `web/src/views/TaskResult.vue` | 任务级警示横幅 + 闸卡片状态 chip + 降级闸不显示裸分数 + 解释行 + 样式 |
| `web/src/utils/reviewStatus.ts` | **新增**（唯一一处兜底逻辑，时间线与结果页共用） |

---

## 2. 验收标准逐条核对（对齐设计 §8）

| # | 标准 | 结论 | 证据 |
|---|---|---|---|
| **A1** | `eval_score=None` 不再击穿投影 | ✅ PASS | 容器 `B1/B2/B3`；`I1`（文件恢复路径）；修复前实测抛 `ValidationError`（见 §4.1） |
| **A2** | 降级闸带结构化标记 | ✅ PASS | 容器 `F2` 实测 `['degraded_unavailable','degraded_unavailable','degraded_unavailable']` |
| **A3** | 降级闸 UI 不显示裸分数 | ⚠️ **代码面 PASS / 目视未验** | `TaskResult.vue:87-89` 条件分支已 grep 复核；web bundle 命中「非审核结论」「未经 AI 审核」；**渲染效果未亲眼看**（§5-1） |
| **A4** | 任务级横幅计数正确，0 降级不渲染 | ⚠️ **代码面 PASS / 目视未验** | 横幅 `v-if="degradedCount > 0"`；`degradedCount` 由 `normalizeReviewStatus` 过滤计数；bundle 命中「道审核闸未真正执行」 |
| **A5** | **正常路径零回归** | ✅ PASS | 容器 `H1/H2/H3`：正常路径三道闸全 `llm_reviewed`、来源全 `gate_llm`、任务 `done`；宿主全量回归零新增失败（§3） |
| **A6** | 越界/误读分数被拒 | ✅ PASS | 容器 `G` 段 9 例全对（`85%`→None、`评分：1. 内容完整度 0.85`→None、`2.5`→None …） |
| **A7** | 双失败链路可活 | ✅ PASS | 容器 `F1`–`F4`：闸+评分器同时不可用 → 任务 `done`、分数 `None`、投影成功 |
| **A8** | 前端类型一致 | ✅ PASS | `vue-tsc --noEmit` → `exit=0`（§3.2） |
| **A9** | 审计导出含新字段 | ✅ PASS | `audit_export._GATE_FIELDS` 已含两字段；宿主用例 `test_audit_fields_include_review_status` 通过 |

> **A3/A4 的诚实标注**：这两条是**纯视觉**标准。我做到的证据是「模板分支 + 类型检查 + 产物字符串命中」，
> **属于代码面证明，不等于「我看过渲染出来的界面」**。要闭环需 boss 打开一次页面（见 §5-1）。

---

## 3. 构建与测试（全部真机命令）

### 3.1 宿主全量回归 —— 零新增失败

```
$ pytest tests/ -q --ignore=tests/test_api_boundary.py \
    --ignore=tests/test_api_report_download.py --ignore=tests/test_m12_admin_api.py
7 failed, 264 passed, 5 skipped, 69 warnings in 18.66s
```

- **7 个失败全部是 `tests/test_doc_render.py`**，原因清一色是宿主缺 `docx`/`pptx`/`reportlab`
  —— **与改动前的基线完全一致**（改动前同一命令为 `7 failed / 250 passed`）。
- **净增 14 个通过**（= 本轮新增用例在宿主可跑的部分）。
- **5 skipped** = 需 `server.api`（依赖 asyncpg）的用例，宿主缺包 → 由 §3.3 的容器脚本覆盖。

### 3.2 前端类型检查

```
$ cd web && node_modules/.bin/vue-tsc --noEmit ; echo "vue-tsc exit=$?"
vue-tsc exit=0
```

### 3.3 容器内验证（补 pytest 缺失）—— 31/31

`report-api` 是生产镜像（**不含 pytest**），故用 `scripts/verify_gate_degradation.py` 直接调用被测函数：

```
$ docker compose exec -T api python /app/scripts/verify_gate_degradation.py
PASS 31 / FAIL 0
```

分组覆盖：`A` 契约、`B` 投影容错、`C` 历史回填、`D` 单条隔离、`E` 缺标量容错、
`F` 双挂端到端、`G` 评分守卫 9 例、`H` 正常路径不回归、`I` **文件恢复路径**。

其中 `I` 段尤为关键：它在 tmp 目录伪造一份**降级且无分**的 `{id}_output.json`，
验证 `_load_task_from_file` 能恢复出任务 —— 修复前该函数被外层 `except` 整体吞掉并返回 `None`，
表现为「**任务不存在**」（比 500 更难排查）。

### 3.4 双镜像与产物

```
$ docker compose build api  → Image report-agent-team-api Built
$ docker compose build web  → Image report-agent-team-web Built
$ docker compose ps         → api/postgres/redis/web 四服务在跑
```

web 产物核对（**新 bundle**，构建于 15:01，文件名由旧的 `index-BcWZ6SK-.js` 变为 `index-DMEyzeZx.js`）：

| 关键字 | 命中次数 |
|---|---|
| `未经 AI 审核` | 2 |
| `道审核闸未真正执行` | 1 |
| `researcher_records_synthesized` | 1 |
| `非审核结论` | 1 |

---

## 4. 自审中发现的问题与更正（诚实记录）

### 4.1 复核「这个缺陷是真的」——修复前实测

在写码**之前**，先在容器内把 `eval_score=None` 喂给当时的投影模型：

```
❌ 投影失败 -> ValidationError
1 validation error for RoutingState
gate_review_history.0.eval_score
  Input should be a valid number [type=float_type, input_value=None, input_type=NoneType]
```

→ 确认这是**真缺陷**而非推测。修复后同场景在 `B1`/`I1` 双双通过。

### 4.2 ⚠️ 设计 §4.2 在实现期被**修正**（我自己的测试抓到的）

设计初稿对 `call_eval` 只要求「范围守卫」（拒绝 `>1` / `<0`）。写测试时我发现**范围守卫不够**：

```
AssertionError: assert 1.0 == None
 +  where 1.0 = _eval_with('评分：1. 内容完整度 0.85')
```

`"评分：1. 内容完整度 0.85"` 的首个正则匹配是 `1.` → `1.0`，**落在 [0,1] 内**，
范围守卫放行 —— 但这是**彻底误读**。根因是「在散文里找数字」这个做法本身有结构性缺陷，
不只是越界问题。

**处置**：实现升级为**整条回复匹配**（`re.fullmatch(r"(0?\.?\d+)\s*分?")`），
理由是该 prompt 已明确要求「只返回一个 0-1 的小数」，不合规就诚实判「未评分」。
设计方案 §4.2 已同步改写并注明「实现期修正」。

**行为影响（真实、非零）**：`call_eval` 是「闸 LLM 没给分」时的兜底路径。
收紧后该场景会更常返回 `None`（UI 显示「未评分」）。这是**有意变更**：
宁可显示未评分，也不显示误读出来的数字。主账号常见路径（闸 LLM 在 JSON 里自带 `eval_score`）
**不受影响**（那条不走 `call_eval`），已由 `H2` 实测。

### 4.3 `review_status` 的语义边界（明确写下，避免后续误读）

`parsed is not None` 时一律记 `llm_reviewed`，**即使最终 `decision` 被代码硬校验覆盖**
（如 `code_dec == "escalate"` 强制升级）。理由：闸 LLM **确实产出了可解析结论**，
「依据来源」与「最终结论」是两件事。此定义已写入设计与代码注释。

### 4.4 一处措辞精确化（自审第二遍时发现）

设计文档 §1.3 原写「`grep "GateReview("` 全仓零实例化」——**不准确**：
`tests/` 里确有构造。已更正为「DB 模型 `server/models.py:116` 从未被实例化/落库；
`api.py:135` 的同名 Pydantic 模型仅用于响应」。

---

## 5. 未做 / 未验证的事（明确划线，不粉饰）

1. **界面未目视验收（A3/A4 的视觉部分）**。我只验证到「模板分支 + 类型 + 产物字符串」。
   要闭环需 boss 开 `http://localhost:18080` 并**强刷**（`?cb=<时间戳>`，浏览器会缓存旧 `index.html`），
   进任一**降级任务**的结果页，确认：① 顶部出现黄色横幅并给出 `N / M` 计数；② 降级闸卡片带红色
   「未经 AI 审核（降级放行）」chip 且**看不到裸 `0.85`**。
2. **`gate_reviews` 表仍空转**（设计 §10 明确不做）。闸记录仍只活在 `.engine_state/*_output.json`
   与审计 ZIP 里 —— 属「任务持久化」既有技术债，**单独立项**。
3. **strict 模式未做**（设计 §10）。降级仍放行，只做「可见 + 诚实」。
4. **未做自动换模型重试**（设计 §10）。
5. **本次未动主账号配置**，也未改代码硬校验 > LLM 判定的既定优先级。

---

## 6. 结论

档 1 + 档 2 达成设计目标 G1–G5；**G5（正常路径零回归）由宿主全量回归 + 容器 `H` 段双重证明**。
一处实缺陷（投影 500）已修并有修复前后对照；一处设计偏差（§4.2）由自测发现并已更正，
设计文档同步。

**待办**：gate ③ 独立审议（`REVIEW_gate_degradation_visibility.md`）；界面目视验收；
补审 PASS 前**不 commit**。

---

## 7. 目视验收（A3/A4 闭环）与随之发现的**两处漏改**（2026-09-19 夜增量）

用 bsk 驱动 boss 真实 Chrome（子账号 `wendy1` 已登录态），逐项目视 + `evaluate` 读页面文本判定。

### 7.1 结果页（TaskResult）—— **通过**

| 用例 | 任务 | 期望 | 实测 |
|---|---|---|---|
| 新任务（3 闸全降级） | `7f449c8c` | 横幅 3/3；各闸红标；**无裸分数** | ✅ 全「未评分」；页面全文 `评分 <数字>` = 0 |
| **混合**（部分真审，历史数据） | `793c258f` | 横幅 1/3 且点名 GateC；GateA/B 走正常分支 | ✅ 「1 / 3 道…（GateC）」；GateA/B「已由审核模型评审 · 评分 0.9 / 0.95」逐字不变 |
| **K2 回填**（历史 0.85 骗人场景） | `d483d809` | 0.85 不得以认证口径出现 | ✅ GateB/C「独立评分器 0.85（非审核结论）」；GateA「未评分」 |
| 新任务（重建后复验） | `0af765a8` | 同上 | ✅ 横幅「3 / 3 道…」；裸分数 = 0 |
| 实时闸节点（WS 路径） | `2de1aed2` 运行中 | `⚠️ 未经 AI 审核 · GateA` | ✅ 真机可见 |

### 7.2 目视发现的**两处漏改**（均已修）+ 一个范围外缺陷

1. **【已修】引擎事件实时路径漏渲染** —— 运行追踪页实时时间线出现
   `未知事件 researcher_records_synthesized`。初版修复只覆盖快照路径（`engineEventToNode`），
   实时路径（`wsEventToNode`）的 `default` 分支把它渲染成「未知事件」。
   机理与修法见 `DESIGN_*.md §12.2`。**教训：同一语义两条渲染路径，只补一条 = 没修。**
2. **【已修】闸节点快照路径漏改（更严重）** —— 修复 ① 后复验追踪页，发现降级闸仍渲染
   `✅ 审核放行 · GateA`，且紧挨着 `🔓 闸 LLM 不可用·降级放行` —— **同一页面自相矛盾**。
   根因是 `gateReviewToNode` 未走 `isDegraded`。机理与修法见 `DESIGN_*.md §12.3`。
   **复验（新 bundle `index-DdCbZoUg.js`）**：`✅ 审核放行 · Gate` = **0**（改前 3），
   `⚠️ 未经 AI 审核 · Gate` = **3**。
3. **【未修，范围外】错误态渲染成 `[object Object]`** —— 实测两处：
   ① 打开不属于当前租户的任务 → `TaskResult` 错误横幅显示 `[object Object]`；
   ② **提交任务缺必填字段（`POST /api/v1/tasks` 400）→ `TaskSubmit` 页面同样显示
   `[object Object]`，用户完全无法知道哪里填错了**（本次目视验收当场被它挡住一次）。
   store 的 `error` 存的是响应对象而非字符串。与「诚实边界」同类失真（把真实原因藏起来），
   **属既有缺陷，单独立项**，本轮未动。

### 7.3 增量的自动化验证（不烧模型配额）

新增 `scripts/verify_timeline_nodes.js`（真机证据之外的可复现证据）：用
`typescript.transpileModule` 转译 `timeline.ts`/`reviewStatus.ts` 后在 Node **真实执行**，
对两条渲染路径逐分支断言 → **PASS 18 / FAIL 0**（含：4 种引擎事件不再「未知事件」；
实时/快照两条路径的降级闸标题一致；**正常闸与既有 `agent_output_unusable` 分支逐字零变化**；
历史数据 reason 兜底；`review_status` 直采）。
> 过程自纠：harness 初版用 `===` 比较两个对象 → 恒 false，误报 4 条 FAIL（actual 与 expect
> 逐字相同）。按「探针先疑探针」原则定位为探针自身 bug，改序列化比较后全绿。

### 7.4 真机事件走向（实测，非推断）

扫全部 `tenants/*/.engine_state/*_events.jsonl`：

| 事件 | 在 WS 流（events.jsonl）中 | 结论 |
|---|---|---|
| `researcher_records_synthesized` | 6 个任务均出现 | **会**广播 → 实时路径必须处理 |
| `writer_citation_regenerated` | **0 次** | 只走快照路径 |
| `gate_llm_unavailable_degraded_advance` | **0 次** | 只走快照路径 |
| `agent_output_unusable` | 3 次 | 两条路径都会出现（原已处理） |

> 因此 `wsEventToNode` 为后两种事件加的 `case` 目前是**防御性的**（今日不触发）；
> 保留理由：`_stream_engine_events` 对事件文件是**原样转发**，事件集由引擎侧决定，
> 一旦开始广播，缺 case 就会退回「未知事件」。此为明示的设计取舍，非遗漏。

### 7.5 增量后的回归

- `node_modules/.bin/vue-tsc --noEmit` → **exit=0**（每次改动后均重跑）。
- web 镜像已单独重建（前端改动**必须单独重建 web**），新 bundle `index-DdCbZoUg.js` 已在真机验证。
- 后端本轮**零改动**，宿主回归结论沿用 §3/§4。

### 7.6 遗留

- 负向用例（**全部**闸都真审、横幅完全不渲染）未能在真机取到：子账号租户下不存在
  这样的历史任务（三道闸历来降级）。已由 7.1 混合用例 `793c258f` 间接证明
  （`llm_reviewed` 分支的 GateA/B 与改动前逐字一致）+ `verify_timeline_nodes.js` 的
  「正常闸零变化」断言直接覆盖。
- 截图存于 `.shot/`（`gates_degraded.png` / `tracking_degraded.png` / `result_degraded.png`）。
- **`[object Object]` 缺陷（7.2-③）建议尽快单独立项**：它同时打击提交表单与结果页两个入口。
