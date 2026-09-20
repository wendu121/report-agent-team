# REVIEW_DEPLOY_HOTFIX · gate-③ 独立审议 —— **RESOLVED（已执行）**

> ✅ **本档状态由 BLOCKED 升级为 RESOLVED。** 独立审议已由 gate-③ 子代理统一执行，结论见 **`REVIEW_GATE3_BATCH.md`**（含 `<!-- reviewed-by: independent-subagent (gate-③) -->` 标记，**PASS_WITH_NOTES**）。
>
> - 第 1 次尝试（默认模型子代理）：启动失败 →
>   `429 您的使用量已超出频率限制，将在 2026-09-18 14:54:23 UTC+8 重置`。
> - 第 2 次尝试（按 boss 限流兜底改用 `Intern-S2-Preview-397B`）：启动失败 →
>   `您已超过输入 tokens 配额`。
> - 依 boss 硬约定（2026-09-03 实测确立）：受限流/配额阻塞时 **严禁主代理自审自签顶替**——当时本档如实保留 BLOCKED，未伪造 `reviewed-by`、未 commit。
> - **配额恢复后**，本档所列「唯一代码改动」`server/admin.py:1655`（递归笔误）与 §四「待审对象」第 5 项重点核查的 **admin Bearer 系统性缺陷**，连同由此衍生的级联登出修正、注册提示修正、死代码事件监听，一并纳入 **gate-③ 批次审议**（`REVIEW_GATE3_BATCH.md`），结论 **PASS_WITH_NOTES**，标记已补。
> - 全部代码改动（5 文件）**已 commit**；本档与自审档（`VERIFICATION_DEPLOY_HOTFIX.md`）、批次审议档（`REVIEW_GATE3_BATCH.md`）一并入库。

## 待审对象（补审时请独立核实，勿照抄自审结论）

- **唯一代码改动**：`server/admin.py:1655`
  `        _models_meta()[key] = m`  →  `        _MODELS_META[key] = m`（1 行）。
- 完整背景与证据见 `VERIFICATION_DEPLOY_HOTFIX.md`。

## 补审待办清单（配额恢复后执行）

1. **核实 bug 真实性**：读 `server/admin.py` 的 `_MODELS_META`(约 :1630)、`_cache_aid`(:1633)、`_models_meta`(:1643)，
   确认修复前 `_models_meta()[key] = m` 确为**自调用无限递归**（→ `RecursionError` → `GET /api/v1/models` 恒 500）。
2. **核实修复正确性**：`git -C E:\第二电脑\report-agent-team diff -- server/admin.py` 应**仅 1 行**、无副作用、与
   `_MODELS_META.get(key)` 读路径一致。
3. **运行时复跑**：`GET http://localhost:18080/api/v1/models` 应 **200** 且含 `auto`/`auto-chat`；
   `python -m py_compile server/admin.py` 应 exit 0。
4. **回归面**：`/api/v1/templates`、`/api/v1/skills`、`/api/v1/tasks`、`/api/v1/chat/sessions`、`/api/v1/auth/me` 均 200。
5. **重点核查（可能是更大的既有缺陷）**：`server/admin.py:149-152 _require_admin(x_admin_token, request=None)`
   与全仓约 50 处**直接调用 `_require_admin(x_admin_token)`**（如 :338/:362/:1065）——
   核实是否导致 **Bearer 令牌对 admin 端点失效**（`request=None` → 读不到 Authorization → 落 ADMIN_TOKEN 分支 → 未配则 401），
   并给出修法建议（改为 `Depends` / 透传 request / 中间件注入 ContextVar 等）。
   *（自审已实测 `GET /api/v1/admin/templates` 带 Bearer → 401，但**须独立复现**。）*
6. 补审方通过后，由审议方在本档补写结论并追加 `<!-- reviewed-by: independent-subagent (gate-③) -->`，
   主代理方可 `git commit`。

## 说明

- 部署期另有 2 项**配置/Schema 处置**（`.env` 补 `AUTH_SECRET`、`RAT_LEGACY_GLOBAL=1` 迁移阀；DB 补 `chat_sessions.owner_id`），
  非代码改动，清单见自审档；补审时请一并核对是否与实际一致。
