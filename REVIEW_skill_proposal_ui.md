<!--
=====================================================================
REVIEW_skill_proposal_ui.md · 门禁③ 架构级独立代码审查
---------------------------------------------------------------------
状态：PASS_WITH_NOTES
日期：2026-09-20
独立执行：是（独立子代理出具，主代理未自签 reviewed-by）
审查对象：commit 4b15c1a（后端裁剪+测试+设计）、9e011ee（前端审批卡）
结论末行签名：<!-- reviewed-by: independent-subagent -->
=====================================================================
-->

# 独立审议报告：Skill 提案审批 UI + 采纳时裁剪精简索引版

> 本文为门禁③（独立子代理，主代理不得自签）。事实源：`DESIGN_skill_proposal_ui.md`（SoT）、
> `VERIFICATION_skill_proposal_ui.md`（主代理自审，本文在必要处反驳/补强）、以及下列真实代码。
> 本文不修改任何源码或测试，仅审查并出具结论。

## 总览结论

**PASS_WITH_NOTES**。两个 commit 的核心交付（前端审批卡 + 后端采纳裁剪）真实落地、有真实测试覆盖、
无假功能、审计留痕完整。发现 4 项 NOTE（非阻塞），其中 2 项涉及真实边界风险，建议收口前/后尽快修复。
无 BLOCKER。

---

## ① 真实删除 / 真实落地 —— PASS

**要点**：`_condense_markdown` 是否真在写 md 前被调用？测试是否对真实裁剪逻辑断言？

- `install_spec` 在落盘 md 处真实调用裁剪函数：
  `E:\第二电脑\report-agent-team\tools\skill_importer.py:866`
  `_atomic_write(md_path, _condense_markdown(spec["payload"]["markdown"], spec))` —— 非空函数、非注释、非占位。
- 采纳链路闭合：`accept_proposal`（`skill_importer.py:968-978`）→ `install_spec(spec, ...)`（:972）
  → 写裁剪版 md（:866）。`import_skill` 的 L0 直装路径也走 `install_spec`（:1027），同一条裁剪路径。
- 测试是对**真实逻辑**断言，不是只 assert 函数存在：
  - `test_skill_condense.py:137` `test_install_spec_writes_condensed_and_callable` 端到端：构造 ~259KB mock markdown
    （`_make_big_markdown` :21，断言 `len(markdown) > 250_000` :146），真实跑 `install_spec`，再断言
    `skills/<sid>.md` 存在、`len(text) < 8000`（:160）、含「可用 API 索引」「调用方式」、索引条数 (0,40]（:161-165）。
  - `test_skill_condense.py:93` `test_condense_filters_and_caps` 单测直接断言：`raw.githubusercontent.com` banner 被删、
    `Sign up/postman.com/app.apilayer.com/🎉` 被剔除、`api.apilayer.com/products/...` 产品 URL 被保留、重复条目去重、头部用 spec name。
  - `test_skill_condense.py:178-181` 断言 `build_skill_context("chat"/"researcher")` 含注入标记，证明 md 被真实拼入 system prompt。

**结论**：裁剪真实生效，测试覆盖真实行为。PASS。

---

## ② 可调用性 —— PASS（含一条正向说明 + 1 NOTE 理解边界）

**要点**：裁剪后的 md 是否真能让 chat/researcher agent 调用这些 API？头部「调用方式」是否明确？注入标记是否真能读到？

- 头部「调用方式」明确（`skill_importer.py:796-798`）：指示 agent「生成研报时若需下列领域数据，优先用
  web_search / Tavily 检索对应公共 API 的实时数据，并在研报中标注数据来源 URL」。措辞诚实——是「索引+检索指引」，
  而非冒充可执行工具。
- 注入标记真实可读：`tools/skills.py:152` `build_skill_context` 用
  `【已启用技能：{name}】\n{frag}\n【技能结束】` 包裹；`verify_installed`（:890-905）要求 `【已启用技能` 在
  各 target_role 的 context 中均出现。测试 :178-181 已验证 chat/researcher 均命中。
- **正向说明（优于旧实现）**：裁剪版 md ≤ 5000 字符，低于 `tools/skills.py:64` 的
  `MAX_FRAGMENT_CHARS = 6000`，因此**整份索引会被完整注入**，不再像旧 259KB 那样被被动截断到半句。
  本次裁剪对"列表型 README"来源实际**提升**了可调用性（agent 能看到完整 API 清单）。

**NOTE（理解边界，非缺陷）**：所谓"可调用性"指 agent 能**找到**应检索哪些公共 API 并据此用 web_search/Tavily 取数，
并非提供真实 tool 调用入口。这与 DESIGN §后端目标一致，属诚实边界，记录备查。

**结论**：PASS。

---

## ③ 过度工程 / 边界 —— PASS（3 条 NOTE，均为边界稳健性）

**要点**：`_condense_markdown` 是否过度复杂？空 raw、超大行、无 http 列表项来源等边界是否稳妥？是否会误删真实 API 条目？

- 复杂度评估：函数约 100 行（`skill_importer.py:711-811`），聚焦"删图/删 CTA/抽索引/封顶/头部"，为针对性启发式，
  **不过度工程**。PASS。

**NOTE-3.1（误删风险·低）：CTA 为整行过滤，可能误删含关键词的真实 API 列表项**
- `skill_importer.py:747` `cta_re = re.compile(r"Sign up|postman\.com|app\.apilayer\.com|🎉|🥳", re.I)`，
  且 `:764` 对**整行**匹配即 `continue` 删除。若某真实 API 条目写作
  `- [Sign up to get key](https://real-api.com/v1/x)`，因整行含 "Sign up" 会被整条丢弃。
- 当前对 `api.apilayer.com`（注意是 `api.` 非 `app.`）已正确保留（测试 :118 断言），风险仅在
  "含 Sign up / postman.com 字样的真实链接"边角。**建议**：改为仅当整行是纯 CTA（如同时含注册链接且无 API 索引结构）才删，
  或只对"裸 CTA 链接"做子串删除而非整行过滤。低概率、低影响，非阻塞。

**NOTE-3.2（空章节·低）：无 http 列表项来源时仍输出空「## 可用 API 索引」标题**
- `skill_importer.py:799,810`：当 `api_index` 为空（散文型 / 纯代码型来源），md 仍含
  `## 可用 API 索引` 标题但无条目，末尾仅附裁剪注。**建议**：`api_index` 为空时省略该标题段，避免空章节。

**NOTE-3.3（潜在回归·中·重要）：`install_spec` 被 L0 直装复用，对非列表型 md 会损坏内容**
- `import_skill` 的 L0 分支（无外链/无代码/非网页）直接 `install_spec(spec)`（`skill_importer.py:1027`）。
  一旦 `install_spec` 一律走 `_condense_markdown`，一个**自研的小体积 prompt 型 L0 技能**（正文是步骤/结构指令、几乎无 http 列表项）
  会被裁成"头部 + 空索引"，**实际 prompt 内容全部丢失**，技能落盘后近乎失效。
- 本次交付的显式验收对象（apilayer L1 提案→accept→裁剪）不受影响，且测试仅覆盖 L1 列表型来源（未覆盖 L0 路径）。
- DESIGN_skill_proposal_ui.md §后端明确写"`install_spec` 改为写裁剪版"，实现与 SoT 对齐；但 SoT 本身未区分 L0/L1，
  该副作用属设计盲区。
- **建议（强烈，非阻塞但建议收口前处理）**：① 仅在来源"列表型/大体积"时裁剪——例如
  `len(raw) > 5000 或 http 列表项数 ≥ N` 时走 `_condense_markdown`，否则原样写；或 ② 在 `accept_proposal` 内裁剪、
  `install_spec` 保留原样写（需相应调整 L0 路径）；并补一条 L0 prompt 型 md 不被损坏的测试。
  在修复前，使用 `import_skill` 经 URL 直装 L0 自研技能的 boss 可能得到空壳技能。

**结论**：函数本身不过度工程、边界基本稳妥；3 条 NOTE 建议修复，无 BLOCKER。

---

## ④ 前端一致性 —— PASS（1 条 NOTE）

**要点**：Skills.vue 提案卡是否与 Experts.vue 行为一致？loading/错误/刷新是否闭环？`skillService` 的 `/api/v1` 前缀是否与后端路由匹配？

- **路由前缀匹配（已实锤，非假功能）**：
  - 前端 `skillService.ts:23,34,45` 走 `/api/v1/admin/skills/proposals`(GET)、
    `/api/v1/admin/skills/proposals/{pid}/accept`(POST)、`/api/v1/admin/skills/proposals/{pid}`(DELETE)。
  - 后端 `server/admin.py:2783/2796/2812` 注册 `@router.get/post/delete("/admin/skills/proposals...")`，
    且 `server/main.py:110` `app.include_router(admin_router, prefix="/api/v1")` —— 前缀吻合，**通过/驳回确有后端支撑**，无假功能。
  - 后端 handler 真实调用 `skill_importer.accept_proposal/reject_proposal`（admin.py:2806,2822），链路闭合。
- **loading/错误/刷新闭环（PASS）**：
  - `onAcceptProposal`（Skills.vue:345-357）：`_accepting` 置 true → 调accept → `ElMessage.success` → `loadProposals()` + `load()` → finally 复位。
  - `onRejectProposal`（:359-379）：`ElMessageBox.confirm` 二次确认（取消则 return）→ `_rejecting` → reject → 刷新。
  - 错误均经 `ElMessage.error`。与 Experts.vue `onAccept/onReject`（Experts.vue:300-333）**完全对齐**：两者均为"通过无确认、驳回有确认"，行为一致。
- **NOTE-4.1（源 URL 列空白·中）：** `Skills.vue:48` 读 `row.source_url`，但提案 spec 的 `source_url`
  嵌套在 `provenance.source_url`（`build_spec` 见 `skill_importer.py:594`；顶层无 `source_url`）。
  后端 `list_proposals` 返回完整 spec（admin.py:2792-2793），故 `row.source_url` 恒为 `undefined`，
  "源 URL"列将永远显示「—」。DESIGN §前端列定义要求展示源 URL，此处与设计不符。
  **建议**：模板改为 `row.provenance?.source_url`，或在后端 `admin_list_skill_proposals` 返回时扁平化出 `source_url`。非阻塞（通过/驳回功能不受影响）。

**结论**：前端与后端真实连通、与专家页一致；NOTE-4.1 建议修。PASS。

---

## ⑤ 审计留痕 —— PASS

**要点**：`save_proposal` 是否仍存全量原文（不裁剪提案 JSON）？

- `save_proposal`（`skill_importer.py:939-946`）`json.dumps(spec, ensure_ascii=False, indent=2)` 写出**完整 spec**，
  其中 `payload.markdown` 是未裁剪的原文（259KB 级）。自 `4b15c1a` 起该函数**未被改动**（grep 确认本次 diff 仅动 `install_spec`/`_condense_markdown`）。
- 裁剪只发生在 `install_spec` 写 `skills/<sid>.md`（:866），与提案 JSON 互不干扰。DESIGN §目标3「提案 JSON 仍存完整原文」成立。

**结论**：PASS。审计留痕完整。

---

## ⑥ 诚实边界 —— PASS（1 条 NOTE：自审的手动验收项未在本轮实跑）

**要点**：有无"假功能/假配置"？UI 显示但通过/驳回实际无后端支撑？裁剪声称精简但实际仍写全量？

- **无假功能**：通过/驳回（④已证）真实命中后端 `accept_proposal/reject_proposal`，继而真实落盘/删提案。
- **裁剪真实**：259KB → < 8KB（测试 :160 实证），非"声称精简仍写全量"。
- **无假配置**：`skillService.ts` 接口与 `server/admin.py` 路由、前缀完全对应；无悬空常量。
- **NOTE-6.1（自审诚实性）**：`VERIFICATION_skill_proposal_ui.md:25,32` 将"容器内 `cat skills/<sid>.md` 验证 <8KB 含调用方式"
  及"真实浏览器里点通过"列为**待实跑**手动验收项；本轮独立审查未执行容器/浏览器实测（无运行环境），仅以 pytest 自动化 + 静态代码核对佐证。
  **建议**：主代理在收口说明中显式标注该手动验收"待 boss 实跑"，或补一条端到端冒烟记录，避免把"未跑"误记为"已通过"。

**结论**：无假功能/假配置/假裁剪；NOTE-6.1 为自审措辞诚实性提醒。PASS。

---

## 逐项结论汇总

| # | 审查要点 | 结论 |
|---|---------|------|
| 1 | 真实删除/真实落地（裁剪被调用、测试断言真实逻辑） | PASS |
| 2 | 可调用性（调用方式明确、注入标记可读） | PASS |
| 3 | 过度工程/边界（含 L0 复用误伤、CTA 误删、空章节） | PASS（NOTE-3.1 / 3.2 / 3.3） |
| 4 | 前端一致性（路由匹配、loading/刷新闭环、与专家页对齐） | PASS（NOTE-4.1） |
| 5 | 审计留痕（提案存全量原文） | PASS |
| 6 | 诚实边界（无假功能/假配置/假裁剪） | PASS（NOTE-6.1） |

**非阻塞改进项（按优先级）**：
1. NOTE-3.3（重要）：`install_spec` 被 L0 直装复用，对非列表型 md 会损坏内容 → 加裁剪守卫 + 补 L0 测试。
2. NOTE-4.1（中）：源 URL 列因字段嵌套取不到 → 改读 `provenance.source_url` 或后端扁平化。
3. NOTE-3.1（低）：CTA 整行过滤可能误删含关键词的真实 API 条目 → 改为更窄的匹配。
4. NOTE-3.2（低）：无 http 列表项时省略空「可用 API 索引」标题。
5. NOTE-6.1（自审诚实性）：显式标注容器/浏览器手动验收为"待 boss 实跑"。

**收口建议**：当前交付满足显式验收（apilayer L1 提案→accept→<8KB 索引版、前端审批卡连通真实后端、审计留痕）。
NOTE-3.3 虽标非阻塞，但属真实能力回归，建议主代理在收口前加固裁剪守卫；其余 NOTE 可随后续迭代修复。

<!-- reviewed-by: independent-subagent -->
