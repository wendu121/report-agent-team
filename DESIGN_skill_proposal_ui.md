# DESIGN: Skill 提案审批 UI + 采纳时裁剪精简索引版（K1）

> SoT（单一事实源）。本文件先设计后实现，实现须对齐此文，偏离须回写。
> 关联：server/admin.py 的 `/admin/skills/proposals` 已存在（list/accept/reject）；
> 本次补齐**前端审批卡** + **采纳时把 259KB 原文裁剪为精简索引版（保留可调用性）**。

## 背景 / 问题

- 研报助手 chat agent（经 `tools/chat_sandbox.py` 的 run_python/run_shell）调用
  `skill_importer.import_skill()` 把 public-apis 仓库提交了 skill 提案：
  `tenants/.../.audit/skill_proposals/apilayer-unified-suite-in-now-live.json`（payload 259KB = 整份 README）。
- 后端审批 API 已就绪（`server/admin.py:2783-2825`），但 `web/src/views/Skills.vue`
  **没有“待审批提案”卡片**（专家页 `Experts.vue:116-175` 有同款块，技能侧漏做）→ boss 在界面找不到审批入口。
- `install_spec`（`skill_importer.py:711`）当前**原样**把 259KB markdown 写入 `skills/<sid>.md`，
  注入 chat/researcher 的 system prompt 会撑爆 token 预算（`build_skill_context` 虽在注入时按 `MAX_FRAGMENT_CHARS` 截断，但那是被动截断、不保留结构）。

## 目标

1. **前端**：Skills.vue 增加“待审批提案”卡片（对齐 Experts.vue 模式），支持列出 / 通过（采纳）/ 驳回。
2. **后端**：采纳（`accept_proposal` → `install_spec`）时，把原文裁剪为**精简索引版** md：
   - 去掉营销 banner 图、注册 CTA、Postman 链接等噪音；
   - 保留**结构化 API 索引**（类别 → 代表性 URL 列表，封顶 ~40 条）；
   - 头部加**可调用说明**：告知 agent 生成研报时优先用 web_search/Tavily 检索这些公共 API 的实时数据并标注来源 URL；
   - 总字符封顶 ~5KB（确定性、幂等）。
3. **诚实边界**：提案 JSON 文件仍存完整原文（审计留痕）；仅“生效的 md”是裁剪版。
   在 md 顶部注明“本技能为精简索引版（原始来源 X KB 已裁剪），保留可调用性”。

## 文件变更清单

### 后端 `tools/skill_importer.py`
- 新增 `_condense_markdown(raw: str, spec: dict) -> str`：
  - 删 `![...](...)` 图片行、`[Sign up]...`/`postman.com`/`app.apilayer.com` 等 CTA 行；
  - 抽取含 `http` 的列表项作为“可用 API 索引”，去重、封顶 40 条；
  - 顶部拼接：`# <name>\n\n<description 首段或一句话用途>\n\n## 调用方式\n生成研报时若需下列领域数据，优先用 web_search / Tavily 检索对应公共 API 实时数据，并在研报中标注来源 URL。\n\n## 可用 API 索引\n- ...`；
  - 超限截断到 ~5KB（中文字符安全，不按字节截半句）；
  - 返回时若原文为空则返回占位说明（防御，不冒充）。
- `install_spec`（:763）改为 `_atomic_write(md_path, _condense_markdown(spec["payload"]["markdown"], spec))`。
- 不动 `save_proposal`（提案留全量原文）。

### 后端测试 `tests/test_skill_condense.py`（新增）
- 构造含 banner 图 / CTA / 50 条 API 列表的 259KB 级 mock markdown；
- `install_spec` 后断言：md 尺寸 < 8KB、不含 `raw.githubusercontent.com/.../apilayer-banner`、含“可用 API 索引”与“调用方式”、API 条数 ≤ 40；
- 断言 `build_skill_context("chat")` / `("researcher")` 能读到 `【已启用技能：` 标记（可调用性）。

### 前端 `web/src/services/skillService.ts`（新增）
- 对齐 `expertService.ts`：`ProposalItem` 类型、`listProposals()`→`GET /api/v1/admin/skills/proposals`、
  `acceptProposal(pid)`→`POST /api/v1/admin/skills/proposals/{pid}/accept`、
  `rejectProposal(pid)`→`DELETE /api/v1/admin/skills/proposals/{pid}`。
- 注意：前缀 `/api/v1` 与 Skills.vue 现有 `API` 常量一致（`${API}/admin/skills/...` 已在用）。

### 前端 `web/src/views/Skills.vue`
- `<script setup>` 引入 `skillService` 的 `listProposals/acceptProposal/rejectProposal` + `ProposalItem`；
- 新增 `proposals` / `proposalsLoading` ref 与 `loadProposals()`（onMounted 与操作后置调用）；
- `<template>` 在 StatStrip 之后、tabs 之前插入“待审批提案”`el-card`（对齐 Experts.vue:116-175）：
  列：名称/ID、级别（L1 tag）、作用 Agent（target_roles → 调研/分析/撰稿）、源 URL、操作（通过/驳回）；
  通过/驳回带 `row._accepting/_rejecting` loading，成功后 `ElMessage` + 重新 `loadProposals()` + `load()`。

## 不变更
- 后端审批路由（已实现）。
- 提案 JSON 文件内容（保留全量原文审计）。
- chat agent 的 sandbox 提交能力（不在本次范围；仅治理提案本身的展示与裁剪）。

## 验收
- `pytest tests/test_skill_condense.py` 全过；
- `vue-tsc --noEmit` 通过；
- 重建 web 镜像后，Skills 页出现“待审批提案”卡，对该提案点“通过”→ 技能库出现新条目、其 md 为精简索引版（容器内 `cat skills/<sid>.md` 验证 < 8KB 含“调用方式”）。
- 走 edict-gate 三道闸：① build/typecheck ② VERIFICATION_skill_proposal_ui.md 自审 ③ 独立子代理 REVIEW_skill_proposal_ui.md。
