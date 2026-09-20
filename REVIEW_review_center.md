# REVIEW · 统一审核中心（Review Center）

> 独立审议（gate③）：对「统一审核中心」一批改动做独立代码审查
> 日期：2026-09-20
> 审查对象：DESIGN_review_center.md + VERIFICATION_review_center.md + 11 个落地文件
> 审查准则：boss 质量铁律（禁假配置 / 审核权在老板手上 / 真实验证非纸面）

---

## ① 结论：**PASS_WITH_NOTES**

代码实现与设计逐条对齐，治理铁律（决策权在老板、容器内不 push、租户隔离、决策不可被覆盖）均被真实落实，单测独立实跑 6 项全绿。唯一缺口是**前端类型检查未能在本沙箱独立实跑**（环境缺 `web/node_modules`），已由人工类型核验覆盖，列作 NOTE，不构成 BLOCKER。

---

## ② 必查项逐条结论

### 1. 治理模型：决策 vs 执行分离 ✅ PASS
- `server/admin.py:3050` `admin_review_approve` 仅调用 `review_queue.approve(item_id, operator)`，返回清单条目。
- `tools/review_queue.py:98` `approve()` 只写 `decision=approved` + `approved_by` + `approved_at`，**无任何 `subprocess` / `git` / 系统调用**。
- 全 4 个新端点（admin.py:3008 / 3050 / 3064 / 3080）通读，**零 git push 调用**。真正 push 由宿主 AI 代理消费 approved 项后执行（design §2 / §9）。
- **判定**：未偷偷 push，不触发 BLOCKED。

### 2. 租户隔离 ✅ PASS
- `tools/review_queue.py:23` `_queue_path()` 走 `from tools.skill_importer import _tenant_root`。
- `tools/skill_importer.py:36` `_tenant_root()` 严格优先 `server.tenancy.account_root()`，**仅在 `ImportError`（server 包不可导入）时回落 `BASE`，`TenancyError` 会直接抛出而非静默回落全局** = 无假隔离。
- 清单文件落在 `<tenant_root>/.audit/review_queue.json`，天然 per 租户。
- 单测 `test_tenant_isolation`（test_review_queue.py:62）真覆盖：ta 写入后切 tb，`list_items()` 返回 `[]`、`get_item("cs-x")` 返回 `None`，切回 tb 只剩自己的条目。**真覆盖，非纸面**。

### 3. 幂等 / 决策不被覆盖 ✅ PASS
- `tools/review_queue.py:64` `_DECISION_FIELDS = (decision, approved_by, approved_at, pushed_at, pushed_ref, reject_reason)`。
- `upsert_pending_push`（:67）逻辑：已存在同 id 时，`for k,v in item.items(): if k in _DECISION_FIELDS: continue; existing[k]=v` —— **非决策字段覆盖，决策字段原样保留**；新插入还主动 `pop` 掉误带决策字段并置 `pending`。
- 单测 `test_upsert_new_and_preserves_decision_on_rerun`（:20）**显式断言**：老板 `approve` 后 gate 重跑 upsert，结果 `decision=='approved'`、`approved_by=='boss'` 保留，同时 `title`/`commits` 被新值覆盖。回归被钉死。

### 4. 前端动作路由 ✅ PASS
- 技能提案 → `acceptSkillProposal` / `rejectSkillProposal`（skillService.ts:33/44，均核实存在）。
- 专家提案 → `acceptExpertProposal` / `rejectExpertProposal`（expertService.ts:106/110，均核实存在）。
- 待推送 → `approvePendingPush` / `rejectPendingPush`（reviewService.ts:48/59，对应 `POST .../approve` / `/reject`）。
- 决策状态标签四态 `pending/approved/rejected/pushered` 与 schema 字段一致（`ReviewCenter.vue` `decisionTagType`/`decisionLabel`）。
- **防重复提交**：`pending_push` 行 `decision` 为 `approved`/`rejected`/`pushered` 时（ReviewCenter.vue:104-117）只显示状态文本，**不渲染通过/打回按钮**；`pushered` 显示 `已推送 <ref>`，`approved` 显示「已批准·待代理推送」。`_busy` 加载态在提交期间禁用按钮。无决策后未禁用导致重复提交的问题。

### 5. 鉴权 ✅ PASS
- 4 个新端点均首行 `_require_admin(x_admin_token)`：
  - `admin_review_queue` :3011
  - `admin_review_approve` :3053
  - `admin_review_reject` :3069
  - `admin_review_mark_pushed` :3085
- `_require_admin`（admin.py:159）已堵「无凭据裸奔」旧分支（无 `ADMIN_TOKEN` 时旧实现返回 `dev_no_token` 放行，现已删除 → 一律 401）。`mark-pushed` 虽由宿主代理调用，仍走 admin 鉴权，符合 design §6。

### 6. 类型 / 编译 ✅ PASS（后端实跑；前端 NOTE）
- **后端单测独立实跑**（本沙箱）：`python -m pytest tests/test_review_queue.py -q` → **6 passed in 1.30s**。
- `py_compile` 层面：`tools/review_queue.py`、`server/admin.py` 新端点、`scripts/seed_review_queue.py` 在主代理 `py_compile` 全过（其 VERIFICATION 记录）；本沙箱单测已间接证明 `tools/review_queue` 可正常 import 与流转。
- **前端 `vue-tsc --noEmit` 未能在本沙箱实跑**：本环境 `web/node_modules` 缺失（非代码问题）。已做人工类型核验：
  - `reviewService.ts` 导入 `authFetch`（client.ts:39 确认导出）、`ReviewQueueItem`/`ReviewQueueResult` 接口自洽，`encodeURIComponent` 包装 id。
  - `ReviewCenter.vue` 导入的 4 个既有服务函数名与真实导出完全一致；`ReviewItemKind`/`ReviewDecision` 联合类型与后端聚合返回 shape 匹配（skill/expert 提案 `decision=None`、pending_push 带 `commits/gate/decision`）。
  - 未发现类型不一致。但**此结论未由编译器证实**，详见 NOTE #1。

### 7. 诚实边界（seed 退化）✅ 可接受
- `scripts/seed_review_queue.py:38` `git_unpushed()` 退化链：`origin/master..HEAD` → `origin..HEAD` → `--oneline -8`（最近 8 条）。
- 该脚本定位清晰：宿主侧演示/日常灌数工具，**非生产门禁**。退化到最近 8 条仅影响「无有效 remote ref 时灌哪批 commit」，不污染清单语义、不覆盖已做决策（重新 seed 仍保留 `_DECISION_FIELDS`）。**判定：可接受，不记为缺陷**。

### 8. 禁硬编码 ✅ PASS
- 前端 `ReviewCenter.vue` `<style scoped>` 全部使用令牌：`var(--surface)`、`var(--border)`、`var(--brand)`、`var(--ink-*)`、`var(--el-color-danger)`、`var(--bg-soft)`、`var(--r-*)` 等，均有 hex 兜底（优雅降级），**无脱离令牌的硬编码色值**。
- 后端不回显任何密钥/凭证；`approved_by` 仅存老板 JWT `sub` 身份，无密钥泄漏。

---

## ③ 非阻断 NOTE

1. **前端编译未在本沙箱独立实跑**（环境缺 `web/node_modules`，`vue-tsc` 缺失）。人工类型核验通过，主代理 VERIFICATION 记录 `vue-tsc --noEmit` exit 0。建议主代理在 `build api` / 提交前于其环境复跑一次 `cd web && ./node_modules/.bin/vue-tsc --noEmit` 坐实（与端点级容器冒烟一并做，见主代理 VERIFICATION §4）。
2. **聚合端点不做 gate 状态读时过滤**：`GET /admin/review/queue` 对 `pending_push` 全量返回，不校验 `gate.status`。当前安全靠「写入端」——edict-gate 仅在 `PASS`/`PASS_WITH_NOTES` 时写 `pending_push` 条目，BLOCKED 不会进清单。若未来有人手工 `upsert` 一条 `gate.status=BLOCKED` 的条目，它会出现在清单。属设计内写入门禁，非阻断；可后续在 `upsert_pending_push` 加一道 `gate.status in (PASS, PASS_WITH_NOTES)` 断言作为纵深防御。
3. **`counts` 双来源**：后端返回 `counts`，但前端 `ReviewCenter.vue` 用本地 `computed` 重算（基于 `items`）。两者一致、无害；保留后端 `counts` 目前前端未直接使用。可后续统一为单来源（非阻断）。
4. **`mark-pushed` 与 `approve` 共用 admin 令牌**：design §6 提及「可独立令牌或本地守护」。当前实现由宿主代理持 admin 令牌调用，符合「代理执行」语义，可接受；若日后需区分「老板 UI」与「代理守护」权限，可引入独立守护令牌。

---

## ④ 补审待办
- 无 BLOCKER，无补审待办。
- 唯一建议补验项：NOTE #1 的 `vue-tsc` 实跑 + 运行容器端点级 `curl` 冒烟（主代理 VERIFICATION §4 已列为剩余验收，非代码缺陷）。

---

<!-- reviewed-by: independent-subagent -->
