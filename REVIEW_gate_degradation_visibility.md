<!-- reviewed-by: independent-subagent -->
<!--
  本文件由**独立审议子代理**在 2026-09-20 真实执行后撰写，覆盖 gate ③（第三道闸）。
  方法：读真实代码（全部 in-scope 文件）+ 跑真实命令（容器/宿主/Node/真机数据回填）
  + 主动证伪实施者自审与 DESIGN §12 / VERIFICATION §7 的每一条声明。
  主代理未参与本文件撰写，亦未自签。
-->

# REVIEW · 闸降级必须对用户可见（M13-gate-degradation）· 独立审议

> **最终结论：PASS_WITH_NOTES**
> 实施者自审：`VERIFICATION_gate_degradation_visibility.md`（不替代本文件）
> 设计 SoT：`DESIGN_gate_degradation_visibility.md`（§12 为目视验收增补）
> HEAD：`75b263a`（改动未 commit）
> 审议者：independent-subagent（2026-09-20，真实执行）

---

## 0. 一句话结论

里程碑核心目标（G1 结构化依据来源 / G2 降级显眼可见 + 任务级横幅 / G3 分数来源可区分 /
G4 `eval_score=None` 不再击穿投影 / G5 正常路径零回归）**均经独立实证成立**，
未发现任何阻断性缺陷。发现 **5 个非阻断 NOTE**（其中 2 个为契约细节不严谨 / 验证命令本身
不可用，均不影响用户可见性结论）。**可以 commit**，建议把 5 个 NOTE 作为后续小改进登记。

---

## 1. 我做了什么（方法透明度）

| 动作 | 命令 / 位置 | 结果 |
|---|---|---|
| 验证「修复前会崩」为真 | `git show HEAD:server/api.py` | `:138  eval_score: float`（必填非空）✓ 证实 |
| 容器全量断言 | `docker compose exec -T api python /app/scripts/verify_gate_degradation.py` | **PASS 31 / FAIL 0** |
| 前端双路径断言（harness 真实执行源） | `web` 下以 `WEB_ROOT`+正确 `NODE_PATH` 跑 `verify_timeline_nodes.js` | **PASS 18 / FAIL 0** |
| 宿主回归（本文件内用例） | `pytest tests/test_gate_degradation_visibility.py` | **14 passed, 5 skipped**（asyncpg 依赖，容器内执行） |
| 前端类型一致 | `vue-tsc --noEmit` | **exit=0** |
| 真机数据回填 | 对 4 个真实任务跑 `_coerce_routing_state` | 见 §3 表 |
| 全前端消费者扫描 | grep `审核放行`/`eval_score`/`gate_review_history`/`engine_events` | 仅 2 个渲染面 + 1 个存储面 |

---

## 2. 逐条裁决表（结论 / 证据 / PASS·NOTE·BLOCKING）

| # | 检查项 | 结论 | 证据（file:line 或命令输出） | 判定 |
|---|---|---|---|---|
| 1 | `eval_score=None` 容错真修好；「修复前会崩」为真 | 修复前 `eval_score: float` 必填；现 `Optional[float]=None`，容器 B1/B3/I1 与宿主用例全绿 | `git show HEAD:server/api.py:138`；`server/api.py:142`；容器 `B1 ✅/B3 ✅/I1 ✅`；宿主 `test_gate_review_model_accepts_null_eval_score` PASS | **PASS** |
| 2 | 两条渲染路径一致且无第三处渲染点 | 全前端仅 `TaskResult.vue` 卡片 + `timeline.ts`（`wsEventToNode` 实时 / `gateReviewToNode`+`engineEventToNode` 快照）消费；`stores/task.ts` 仅存储。降级闸两路径均产 `title="⚠️ 未经 AI 审核 · GateX"`、`status=warning`；真机回填 `793c258f→1/3`、`7f449c8c/0af765a8/d483d809→3/3` 与 VERIFICATION §7.1 逐字一致 | grep 命中清单；`timeline.ts:50,194`；`harness ②/③`；§3 回填表 | **PASS**（附 NOTE-A/E） |
| 3 | `call_eval` 整条匹配无未声明回归；主账号路径不受影响 | 主路径 `eval_score_source="gate_llm"`（闸 LLM 自带分时不走 `call_eval`）；容器 `H1/H2/H3` 全 `llm_reviewed`/`gate_llm`；容器 `G` 段 9 例含 `评分：1. 内容完整度 0.85→None`、`85%→None`、`2.5→None`；整条匹配收紧已在 DESIGN §4.2 声明 | `orchestrator.py:2353,2359`；容器 `H*✅/G*✅` | **PASS**（附 NOTE-D：行为变更仅作用于兜底路径，已声明） |
| 4 | `TaskResult.vue` 降级不显示裸分；`degradedCount===0` 横幅完全不渲染 | 横幅 `v-if="degradedCount > 0"`；降级分支只显示「未评分」或「独立评分器 X（非审核结论）」，裸 `0.85` 不可能出现 | `TaskResult.vue:10`；`:86-89` | **PASS** |
| 5 | `normalizeReviewStatus` reason 兜底可靠 | 前端 marks `['降级放行','审核 LLM 不可用']` 与后端 `_coerce_gate_reviews` 完全一致；新数据恒带 `review_status`，兜底仅作用历史 | `reviewStatus.ts:14`；`server/api.py:179` | **PASS**（附 NOTE-C：文档化风险） |
| 6 | `verify_timeline_nodes.js` 真执行源；别名替换脆弱性 | 脚本 `fs.readFileSync` 直接读 `web/src/utils/timeline.ts`+`reviewStatus.ts`，`transpileModule` 后真实执行，18/0 通过 | 脚本 `:16-29`；本次运行 PASS 18/0 | **PASS**（附 NOTE-B：规定命令本身跑不起来） |
| 7 | `researcher_records_synthesized`/`writer_citation_regenerated` 改 info/warning 合理且文档化 | 信息性/补救性事件着 info/warning，不再误标红色错误；DESIGN §12.4 已记录 | `timeline.ts:222-230`；DESIGN §12.4 | **PASS** |
| 8 | 范围外项未混入 | `[object Object]` 渲染（VERIFICATION §7.2-③）已单列另立；`gate_reviews` 表空转（设计 §10 不做）；`TaskReview.vue` 确认无闸/分渲染 | 按 brief 范围核实 | **PASS** |

---

## 3. 真机数据回填（独立复现 VERIFICATION §7.1）

在容器内用真实投影代码 `_coerce_routing_state` 跑 4 个真实任务：

| 任务 | 类型 | degradedCount（投影后） | 与 VERIFICATION §7.1 是否一致 |
|---|---|---|---|
| `7f449c8c` | 3 闸全降级（新数据，带 `review_status`） | **3** (GateA/B/C) | ✅ 横幅 3/3 |
| `793c258f` | 混合（历史，无 `review_status`） | **1** (GateC) | ✅ 横幅 1/3 点名 GateC；GateA/B=`llm_reviewed` |
| `d483d809` | 历史 0.85 骗人场景 | **3** (GateA/B/C) | ✅ GateB/C 显示「独立评分器 0.85（非审核结论）」 |
| `0af765a8` | 三引擎事件齐备（新数据） | **3** | ✅ 横幅 3/3 |

> 关键证伪点：`793c258f` 原始 `gate_review_history` 全部 `review_status=None`、三闸都有分数
> （0.9/0.95/0.9）；投影后**仅 GateC** 被回填为 `degraded_unavailable`，GateA/B 为 `llm_reviewed`。
> 证明后端 reason 文本回填逻辑精确区分，且与目视结论自洽——不是「一律降级」的草率处理。

---

## 4. 非阻断 NOTE（不影响放行，建议登记后续改进）

- **NOTE-A（低危·契约不严谨）**：WS 侧 `GateCompleteEvent.eval_score: float`
  （`server/websocket.py:116`）与 `GateCompleteData.eval_score: number`（`web/src/types/engine.ts:73`）
  **非空**，与「`eval_score` 可空」契约矛盾。实现上 `orchestrator.py:2396` 把降级闸的
  `eval_score=None` 在 WS 线缆上 coerce 成 `0.0`。`emit_gate_complete` 是**死代码**
  （引擎流走 `_stream_engine_events` 直发 JSONL，不经此模型），故无运行时崩溃；当前 UI
  实时 `gate_complete` 节点 `description` 取 `reason`（降级闸 reason 必有值），故**用户看不到该 0.0**。
  但线缆数据是伪造的 0.0，属契约不诚实。建议把这两处 `eval_score` 改可选 / 允许 null 以与契约一致。
  （不影响本里程碑「降级可见」结论。）

- **NOTE-B（低危·验证命令不可用）**：brief 给定前端命令
  `cd web && NODE_PATH=../node_modules node ../scripts/verify_timeline_nodes.js`
  **实际跑不起来**——① `process.env.WEB_ROOT` 未设置 → `path.join(undefined,'src')` 报错；
  ② `NODE_PATH=../node_modules` 指向项目根 `node_modules`，但 `typescript` 实际在
  `web/node_modules`。我以 `WEB_ROOT="…/web" NODE_PATH=node_modules` 补 env 后才跑通 18/0。
  harness 本身有效、确实执行真实源；但**规定命令需修正**，否则他人复现会误判为失败。

- **NOTE-C（低危·文档化风险）**：`normalizeReviewStatus` 仅按 `reason` 文本兜底
  （`review_status` 缺失时）。若后端未来改降级 reason 文案却未同步 marks，历史数据会静默误判为
  `llm_reviewed`。当前新数据恒带 `review_status`，兜底仅作用历史，风险可控；DESIGN/代码注释已点名。

- **NOTE-D（低危·已声明的预期行为变更）**：`call_eval` 改为 `re.fullmatch` 后，闸 LLM 未给分、
  评分器返回带散文的分数（如「质量评分：0.9」）会从「能抠出 0.9」变为 `None`（显示「未评分」）。
  这是 DESIGN §4.2 明确写入的**有意收紧**（宁缺毋滥），非未声明回归；主账号常见路径
  （闸 LLM 在 JSON 内自带 `eval_score`，走 `gate_llm` 源，不触发 `call_eval`）不受影响，已由容器 `H*` 证明。

- **NOTE-E（低危·时间线表面未区分 code_verified）**：`code_verified` 闸在两渲染路径的
  时间线条目标题均为「✅ 审核放行 · GateX」（status success），未体现「⚙️ 代码硬校验裁决」；
  而 `TaskResult.vue` 卡片 chip 通过 `REVIEW_STATUS_LABEL` 正确显示「代码硬校验裁决」。
  两路径**彼此一致**（无矛盾、无「假可见性」），仅时间线表面未呈现该 nuances；非本里程碑核心
  （核心是降级可见）。如需闭环可后续让时间线也读 `REVIEW_STATUS_TIMELINE` 区分 code_verified。

---

## 5. 范围纪律确认

本审议**仅**针对以下 11 个文件：`orchestrator.py`、`server/api.py`、`server/audit_export.py`、
`server/websocket.py`、`web/src/types/api.ts`、`web/src/types/engine.ts`、
`web/src/utils/timeline.ts`、`web/src/utils/reviewStatus.ts`、`web/src/views/TaskResult.vue`、
`tests/test_gate_degradation_visibility.py`、`scripts/verify_gate_degradation.py`（及
`scripts/verify_gate_real.py` 判据切换、`scripts/verify_timeline_nodes.js`）。
工作树中其余未提交改动（`chat_agent.py`、`server/admin.py`、`tools/*`、`ChatEntry.vue` 等）
**不在本轮范围**，未纳入审议。

---

## 6. 最终裁决

**PASS_WITH_NOTES。**

- 全部 8 项必查点 **PASS**；核心实缺（D1 投影 500）经 git 历史 + 容器 + 宿主三重证实已修复且「修复前确实会崩」。
- 双渲染路径一致性、降级不显示裸分、正常路径零回归、审计导出含新字段、前端类型一致——
  均经独立命令实证，未被实施者自审误导。
- 5 个 NOTE 均为低危、非阻断，且多数已文档化；其中 NOTE-B（规定验证命令需补 env）仅影响他人复现便利性，不影响交付正确性。

**可以 commit。** 建议把 NOTE-A（WS 契约非空）、NOTE-B（命令 env）、NOTE-E（时间线 code_verified）登记为后续小改进。
**范围外**的 `[object Object]` 渲染缺陷（VERIFICATION §7.2-③）与 `gate_reviews` 表空转（设计 §10）维持「单独立项」，本里程碑不予处理。
