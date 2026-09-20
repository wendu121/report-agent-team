# VERIFICATION · 统一审核中心（Review Center）

> 关联设计：`DESIGN_review_center.md`（v0.1 草稿）
> 日期：2026-09-20
> 自审人：主代理（AI lead）｜独立审议：见 `REVIEW_review_center.md`

---

## 1. 本次落地内容（文件清单）

**后端**
- `tools/review_queue.py`（新建）：待推送变更集清单存储（per 租户 `.audit/review_queue.json`），原子写 + 租户隔离，函数 `list_items / get_item / upsert_pending_push / approve / reject / mark_pushed`。
- `server/admin.py`（末尾追加 4 端点）：`GET /admin/review/queue`（聚合三类）、`POST /admin/review/queue/{id}/approve`、`POST /admin/review/queue/{id}/reject`、`POST /admin/review/queue/{id}/mark-pushed`。鉴权沿用 `_require_admin(x_admin_token)`。
- `tests/test_review_queue.py`（新建）：6 条单测。
- `scripts/seed_review_queue.py`（新建）：宿主侧把未推送 commit 写入清单（演示/日常灌数据用）。

**前端**
- `web/src/services/reviewService.ts`（新建）：`listReviewQueue / approvePendingPush / rejectPendingPush`。
- `web/src/views/ReviewCenter.vue`（新建）：审核中心单页（三类区块 + 通过/打回 + 详情抽屉 + 决策状态标签）。
- `web/src/router/index.ts`：新增 `/review` 路由。
- `web/src/layouts/DefaultLayout.vue`：侧边栏新增「审核中心」菜单项 + `activeMenu` 映射 `/review`。

---

## 2. 校验结果（实测）

| 项 | 命令 | 结果 |
|----|------|------|
| 后端语法 | `py_compile server/admin.py tools/review_queue.py scripts/seed_review_queue.py` | ✅ OK |
| 后端单测 | `pytest tests/test_review_queue.py -q`（宿主系统 python，隔离不触 asyncpg） | ✅ **6 passed** |
| 前端类型 | `vue-tsc --noEmit`（web/） | ✅ exit 0，0 错误 |
| seed 实跑 | `python scripts/seed_review_queue.py` | ✅ 写入 `tenants/9910ebbc…/.audit/review_queue.json`，1 条目（id=local-unpushed，8 commits，gate 文档已索引） |
| 单测覆盖点 | —— | upsert 新增强 + 重跑保留决策字段（幂等）✅；approve→reject→mark_pushed 全流程 ✅；缺失项抛 `ReviewQueueError`(404) ✅；list/get ✅；租户隔离 ✅；落盘可重载 ✅ |

**单测关键结论（设计核心诉求）**：`upsert_pending_push` 在老板已 `approve` 后再次被 gate 重跑触发 upsert 时，**决策字段（decision/approved_*/pushed_*/reject_reason）被保留**，非决策字段被新值覆盖——证明「覆盖老板已做决策」的回归被钉死。

---

## 3. 设计对齐核对

- ✅ §2 治理模型：决策权在老板 UI（通过/打回按钮）；`approve` 仅写决策，**不**在容器内 push（容器内无宿主 .git/凭证，符合 §9 约束）。真正 push 由宿主 AI 代理消费 approved 项执行。
- ✅ §3 复用：skill/expert 提案直接复用现有 `GET/POST /admin/{skills,experts}/proposals` 端点（前端 `acceptSkillProposal`/`acceptExpertProposal` 等），本功能只新增聚合 + 待推送三类端点。
- ✅ §5 schema：`review_queue.json` 字段与 `DESIGN_review_center.md` §5 一致；seed 脚本写入同 schema。
- ✅ §6 端点：4 个新端点路径/方法/鉴权与文档一致。
- ✅ §7 前端：三类区块 + 详情抽屉 + 决策状态标签（pending/approved/rejected/pushered）；侧边栏入口 + 路由 + activeMenu 高亮。
- ✅ §11 待拍板按建议落地：① 粒度=change-set（seed 用单 id `local-unpushed` 聚合当批）；② 专家提案纳入（聚合 + 前端区块）；③ Skills 现有卡保留、本中心为统一入口；④ 打回支持原因（`ElMessageBox.prompt` + `reject_reason` 回写）。

---

## 4. 诚实边界（未实测项）

1. **端点级容器冒烟未跑**：`GET/POST /admin/review/queue/*` 尚未在运行中的 api 容器内用 JWT 实测（需 `docker compose build api` 重建镜像 + 签 admin JWT 调 curl）。代码严格复用已验证的 `_require_admin` + `skill_importer.list_proposals` / `experts.list_expert_proposals` 模式，`py_compile` 通过，但**容器内端到端联调是剩余验收项**，建议下次 `build api` 后跑一条 `curl` 确认聚合返回 shape。
2. **浏览器真机点击未做**：老板此前要求 Skills 卡浏览器实测（已做）。本中心页面的真机点击（点「批准推送」→ 清单 decision 变 approved）建议与端点冒烟一并做；当前以 `vue-tsc` + 单测 + 代码审查覆盖。
3. **approve→push 闭环**：`mark-pushed` 由宿主 AI 代理在 push 后回写，属设计内的「代理执行」环节，本次未触发（无待 push 的真实 commit 被批准）。逻辑单测已覆盖 `mark_pushed` 状态流转。

---

## 5. 自审结论

代码层面：编译/类型/单测全绿，设计逐条对齐，seed 可真实灌数据。剩余为**运行时容器联调 + 浏览器真机**（边界 §4），非代码缺陷。

⚠️ 按治理铁律：**主代理不自签 `reviewed-by`**。gate③ 独立审议见 `REVIEW_review_center.md`；未过审前不 `git commit` 本批文件。
