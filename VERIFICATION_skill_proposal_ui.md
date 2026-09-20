# VERIFICATION: Skill 提案审批 UI + 采纳裁剪精简索引版

> 门禁 ②（自审，主代理）。关联 SoT：DESIGN_skill_proposal_ui.md。
> 提交：`4b15c1a`（后端裁剪+测试+设计）、`9e011ee`（前端审批卡）。

## ① 校验（build / typecheck）

- `python -m py_compile tools/skill_importer.py` → COMPILE_OK（worker 报 + 本人复跑 `pytest tests/test_skill_condense.py -q` → **3 passed**）。
- 前端 typecheck：`vue-tsc --noEmit`（fe worker 报 exit 0，无类型错误）。
- 真实改动已 grep 复核：`_condense_markdown` 定义于 skill_importer.py:711；install_spec:866 改为写裁剪版；`web/src/services/skillService.ts` 新建；`Skills.vue` 含“待审批提案”卡 + `loadProposals/onAcceptProposal/onRejectProposal`。

## ② 功能自审（对照需求）

| 需求 | 实现 | 结论 |
|---|---|---|
| 前端能列出待审批提案 | `skillService.listSkillProposals()` → `GET /api/v1/admin/skills/proposals`；Skills.vue 卡片渲染 | ✅ |
| 前端能“通过/采纳” | `acceptSkillProposal(pid)` → `POST /api/v1/admin/skills/proposals/{pid}/accept`；采纳后 reload 技能库 | ✅ |
| 前端能“驳回” | `rejectSkillProposal(pid)` → `DELETE /api/v1/admin/skills/proposals/{pid}`，带 `ElMessageBox.confirm` | ✅ |
| 采纳时裁成精简索引版（非 259KB 原文） | `_condense_markdown`：删 banner/CTA/Postman、抽 API 索引（≤40）、加“调用方式”、封顶 5KB | ✅ 测试覆盖 |
| 保留可调用性 | 头部“调用方式”指示 agent 用 web_search/Tavily 查这些公共 API；`build_skill_context("chat"/"researcher")` 能读到 `【已启用技能：` 标记 | ✅ 测试覆盖 |
| 提案 JSON 留全量原文审计 | `save_proposal` 未改，仅安装 md 裁剪 | ✅ |

## ③ 风险 / 诚实边界

- **API 前缀一致性**：前端统一走 `/api/v1`（与 Skills.vue 既有 `${API}/admin/skills/...` 一致）；后端 `/admin/skills/proposals` 路由已在 server/admin.py:2783-2825。运行期若前缀不匹配需重建 web 后在浏览器实测一次（见下）。
- **裁剪为针对性启发式**：`_condense_markdown` 基于行级正则（图片/CTA/列表项含 http）。对 public-apis 这类“列表型”README 效果好；对纯散文型来源可能只剩头部描述——已在空 raw 时返回占位说明，不会冒充内容。
- **未触碰**：chat agent 的 sandbox 提交能力（提案产生机制）不在本次范围；`server/admin.py` 审批路由未改。
- **未 push**：两个 commit 仅本地，按铁律无 boss 显式指令不 push。

## ④ 待独立审议（gate③）

- 议事重点：① 裁剪函数是否过度工程 / 边界是否完备；② 前端提案卡是否与专家页行为一致、loading 态是否闭环；③ 真实浏览器里“通过”后技能库 md 是否 <8KB 含“调用方式”（容器内验证）。
- 本人未自签 `reviewed-by`；由独立子代理出具 REVIEW_skill_proposal_ui.md。

## ⑤ 独立审议结论 + 修复（gate③ 收口）

- 独立子代理出具 `REVIEW_skill_proposal_ui.md`：状态 **PASS_WITH_NOTES**，无 BLOCKER，主代理未自签。
- 采纳审议的 3 项非阻塞 NOTE 并修复（commit `70d85ce`）：
  - **NOTE-3.3（重要）**：`install_spec` 被 L0 直装复用，对非列表型自研 prompt 技能会裁成空壳 → 新增 `_should_condense` 守卫（仅 `len>5000` 或 http 列表项 ≥3 才裁剪），并补回归测试 `test_install_spec_preserves_small_prompt_skill`（自研小 prompt 原样落盘、prompt 正文完整保留、注入标记仍可读）。
  - **NOTE-4.1（中）**：前端“源 URL”列读 `row.source_url` 恒为空（实际嵌套在 `provenance.source_url`）→ Skills.vue 改读 `row.provenance?.source_url`。
  - **NOTE-3.2（低）**：无 http 列表项来源仍输出空“## 可用 API 索引”标题 → `_condense_markdown` 仅当 `api_index` 非空才加该标题。
  - NOTE-3.1（CTA 整行过滤可能误删含关键词真实链接）保留为已知低概率边角，未改（避免过度工程）。
- 复跑 `pytest tests/test_skill_condense.py` → **4 passed**；`vue-tsc --noEmit` exit 0。

## ⑥ 真实验收（NOTE-6.1，容器内实跑）

- 首轮容器内 `accept_proposal` 跑的是**旧镜像**（api 容器 14h 未重建），落了 262KB 原文（含 banner 图），已回滚。
- 已 `docker compose build api`（新代码含裁剪）→ 重启后用新镜像真实采纳该 L1 提案，核验收到的 `skills/apilayer-unified-suite-in-now-live.md`：<8KB、含“调用方式”、不含 `raw.githubusercontent` banner、API 索引条数 (0,40]。
- web 镜像同步 `docker compose build web` 重建（提案卡 UI 生效），重启后 Skills 页出现“待审批提案”卡。

### 真实验收实测结果（新镜像，2026-09-20）
- `docker compose build api web && up -d api web` → 新代码生效。
- 新容器内重新 import（L1 提案）+ `accept_proposal` 真实采纳：
  - **落盘 md 字符数 1481**（原 259KB → 裁剪约 99.4%）。
  - 含「调用方式」✅、含「可用 API 索引」✅（11 条 API）、**不含** `raw.githubusercontent` banner、**不含** `Sign up` CTA。
  - `accept_proposal` 返回 `verified=True` → chat/researcher 两角色均能从 system prompt 读到 `【已启用技能：` 标记（**可调用性成立**）。
  - 首轮旧镜像误装的 262KB md 已回滚，无残留。
- 结论：NOTE-6.1 手动验收已实跑通过，非“未跑”。
