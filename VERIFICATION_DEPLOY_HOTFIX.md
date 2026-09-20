# VERIFICATION · 部署验证 + `/models` 递归笔误热修（2026-09-18）

> boss 指示「部署起来，我要测试一下」。部署 report-agent-team（docker compose，宿主 `http://localhost:18080`）。
> 本档原只含 **1 行代码改动**（§四）+ 部署期配置/Schema 处置。**自审文档，不含 `reviewed-by`**（独立审议由 gate-③ 子代理产出）。
>
> ⚠️ **更新（2026-09-18 续）**：等待 gate-③ 配额恢复期间，按 boss「全部做完了给我」指示，把 §三 列出的 **admin Bearer 系统性缺陷** 连同其引发的 **级联登出 / 误导注册提示 / 死代码事件监听** 一并修复并端到端复测（§五）。这批修复已由 **gate-③ 独立子代理统一审议 → PASS_WITH_NOTES**（见 `REVIEW_GATE3_BATCH.md`，含 `<!-- reviewed-by: independent-subagent (gate-③) -->` 标记），并**已 commit**。`REVIEW_DEPLOY_HOTFIX.md` 的 BLOCKED 状态已解除（见其顶部「RESOLVED」）。

## 一、部署过程中发现并修复的问题

### 1. `AUTH_SECRET` 缺失（配置）→ 登录恒 500
- **现象**：注册 200，登录 500，响应体 `{"detail":"服务端未配置 AUTH_SECRET，无法签发/校验令牌；请在 .env 设置后重启容器。"}`
- **根因**：`server/auth.py:39-49 _secret()` 设计为 **fail loud**（"缺失必须 fail loud，不做随机兜底"）；而 `.env` 与 `.env.example` **均未列** `AUTH_SECRET`（模板缺口）。
- **处置**：在 `.env` 生成并写入 48 字节强随机 `AUTH_SECRET`（`.env` 已 gitignore，不入库；值不落日志）。

### 2. `chat_sessions.owner_id` 缺失（Schema 漂移）
- **现象**：带 token 请求 `/api/v1/chat/sessions` → 500，日志 `asyncpg UndefinedColumnError: column chat_sessions.owner_id does not exist`。
- **根因**：postgres 卷为旧 schema；SQLAlchemy `create_all` **只建缺失表、不改已有表列**（compose 注释亦提醒"升级场景需手动"）。
- **处置**：`ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS owner_id varchar(36);` + `CREATE INDEX IF NOT EXISTS idx_owner_created ON chat_sessions (owner_id, created_at);`
- **逐表核对**：全库仅此一处漂移 —— `accounts`/`chat_messages`/`chat_session_memories`/`call_records`/`tasks`/`routing_states`/`engine_events`/`gate_reviews` 均与 `server/models.py` 一致。

### 3. 公开端点无租户上下文 → 500（TenancyError）
- **现象**：`GET /api/v1/templates`、`/api/v1/skills`（挂在 `public_router`、**无鉴权依赖**）→ 500，日志 `TenancyError: 当前线程没有租户上下文`。
- **根因**：多租户改造后 `tenancy.tenant_path()` 需租户上下文，而 public 端点（`server/admin.py:1055 public_list_templates` 等）无法确定账号。前端确实无 token 调用它们（`web/src/views/TemplateSelect.vue`、`stores/template.ts:18`、`ChatEntry.vue:370`、`Skills.vue:251`）。
- **处置（临时，纯配置）**：启用设计内迁移阀 `RAT_LEGACY_GLOBAL=1`（`server/tenancy.py:71`）——无上下文时资源根回落全局 BASE 并打 WARN。
- ⚠️ 设计原文注明「Phase 2 前必须删除（留着就是假隔离）」→ **属临时阀，待正解**。

### 4. `GET /api/v1/models` 无限递归（代码笔误）★ 本档唯一代码改动
- **现象**：`GET /api/v1/models` **恒 500**（`Internal Server Error`），日志 `RecursionError: maximum recursion depth exceeded`（帧落 `server/admin.py:1637 _cache_aid` → `os.getenv`）。
- **根因**：`server/admin.py:1655` 写缓存时误写成 **`_models_meta()[key] = m`**（`_models_meta()` 是访问器，调用它会再次进入自身）→ 首调用对空 key 必无限递归爆栈。**正确应为 `_MODELS_META[key] = m`**（`_MODELS_META` 才是缓存 dict，见 `admin.py:1630`）。
- **修复**：`server/admin.py:1655` `_models_meta()[key] = m` → `_MODELS_META[key] = m`。

## 二、验证证据（实测）

| 项 | 结果 |
|---|---|
| 门禁① `py_compile server/admin.py`（托管 py 3.13.12） | **exit 0** |
| `docker compose build/up -d api` 重建 | 成功；容器 `healthy` |
| 修复前 `GET /api/v1/models` | **500** `Internal Server Error` |
| 修复后 `GET /api/v1/models` | **200**，`{"items":[{"id":"auto",...},{"id":"auto-chat",...}]}` |
| `/api/v1/templates`（无 token） | **200**（`标准研报` 等真实数据） |
| `/api/v1/skills`（无 token） | **200**（`财务分析` 等真实数据） |
| 注册 `POST /auth/register` | **200**（首个账号 → `role=main,status=active`） |
| 登录 `POST /auth/login` | **200**（签发 JWT，tokLen=268） |
| `/api/v1/tasks`（带 token） | **200** `[]` |
| `/api/v1/chat/sessions`（带 token） | **200** `{"items":[]}` |
| `/api/v1/auth/me`（带 token） | **200** |

## 三、诚实边界（未修 / 待决策）

- **admin 端点 Bearer 鉴权失效（系统性，约 50 处调用点）——✅ 已修（见 §五）**：
  `server/admin.py:149-152 _require_admin(x_admin_token, request=None)` 依赖 FastAPI 注入 `request`，但全仓调用处均为**直接调用** `_require_admin(x_admin_token)`（如 `admin.py:338/362/1065...`），`request` 落 `None` → `auth_header = None` → 跳过 Bearer 分支 → 落入 `X-Admin-Token` 后门分支（未配 `ADMIN_TOKEN` 则 **401**）。
  前端固定用 `Authorization: Bearer`（`web/src/api/client.ts:39`）→ **设置/控制台相关页面（模型映射、模板编辑、Agent/Gate 等）实际不可用**。**现已通过 `contextvars.ContextVar` + Starlette `BaseHTTPMiddleware` 注入每请求 `Request` 修复**（§五），Bearer 令牌可正常读取；此缺陷是后续「级联登出」的根因，一并消除。
- `RAT_LEGACY_GLOBAL=1` 为迁移期阀，**非最终态**；正解应是让 public 端点获得确定语境（或补鉴权依赖 + 前端携带 token）。
- `.env.example` 未文档化 `AUTH_SECRET`（模板缺口）——**未改**（避免未经闸的仓库改动），建议纳入下次小改。
- 部署用测试账号已清理：`accounts=0`、孤儿 tenant 目录已删（`tenants/` 仅剩既有的 `baseline-probe`），留干净白纸给 boss 注册首个主账号。

## 四、结论

- **初版代码改动 1 行**：`server/admin.py:1655`（递归笔误）。
- 门禁① py_compile 通过；部署后端到端复测 `/models` 由 500 → **200**，公开三端点 + 鉴权端点全 200，无新增回归。
- 其余为**部署期配置/Schema 处置**（`.env` 两项、DB 一列一索引），非代码。
- **续批（§五）代码改动 4 文件**：admin Bearer 缺陷 + 级联登出 + 误导注册提示 + 死代码事件监听，已由 gate-③ 独立审议 PASS_WITH_NOTES 并 commit。

## 五、补充批次修复（admin Bearer / 级联登出 / 注册提示 / 死代码）——已 commit

> 触发：boss「@skill:browser-skill 你自己试一下，页面对不对」+「不要一步一步的问，你就全部做完了给我」。
> 真实浏览器（bsk 驱动 Chromium）复测发现：登录态下进入「设置 → 模型映射 / Agent 管理」等 admin 页 → 后端 admin 端点因 Bearer 读不到返回 **401** → 前端 401 拦截器清 token 并跳 /login → **级联登出**，控制台页面实际不可用；且注册页提示「等待主账号审批」与首个注册即主账号的事实不符。

### 5.1 根因与修复

| # | 缺陷 | 根因 | 修复 | 文件 |
|---|---|---|---|---|
| A | admin 端点 Bearer 失效（级联登出根因） | `_require_admin(x_admin_token, request=None)` 中 `request` 在 ~50 处手动调用点恒为 `None` → 读不到 `Authorization` → 落 `X-Admin-Token` 分支（未配则 401） | 引入 `contextvars.ContextVar[_current_request]` + Starlette `BaseHTTPMiddleware` 每请求注入 `Request`；`_require_admin` 回退读 ContextVar | `server/admin.py`、`server/main.py` |
| B | 401 级联登出 | 前端 401 拦截器无条件 `setToken('')` + 跳登录，连「正常但需权限的 admin 请求偶发 401」也把用户踢出 | 区分 401（清 token+登出）与 403（仅告警不踢出）；新增 `FORBIDDEN_EVENT` | `web/src/api/client.ts`、`web/src/main.ts` |
| C | 误导注册提示 | `Login.vue` 无论首注册与否都提示「等待主账号审批」 | 按 `register()` 返回的 `role/status` 区分：首注册→「您是主账号，已自动激活，可直接登录」；其余→「等待主账号审批」 | `web/src/views/Login.vue` |
| D | 死代码事件监听 | `UNAUTHORIZED_EVENT` 在 `main.ts` 仅定义未 `addEventListener`，401 处理实际不生效 | 在 `main.ts` 挂载 `UNAUTHORIZED_EVENT` / `FORBIDDEN_EVENT` 监听（登出+跳登录 / 告警） | `web/src/main.ts` |

### 5.2 验证证据（实测，两层）

**后端 API（urllib 脚本，重建后复跑）**

| 项 | 结果 |
|---|---|
| 全新注册首个账号 | `role=main, status=active`（此前因残留 probe 账号误判为 `sub/pending`，已 `DELETE FROM accounts WHERE username LIKE 'probe_verify_%'` 清理） |
| `GET /api/v1/admin/models`（带 Bearer） | **200**（修复前 401） |
| `GET /api/v1/admin/templates`（带 Bearer） | **200**（修复前 401） |
| `GET /api/v1/admin/agents`（带 Bearer） | **200**（修复前 401） |
| `GET /api/v1/auth/me`（带 Bearer） | **200** |
| `py_compile server/admin.py server/main.py` | exit 0 |
| `web` 镜像 `npm run build`（含 `client.ts` 类型修正） | 成功，bundle `index-DxZ2upZy.js` |

**真实浏览器（bsk 驱动 Chromium，宿主 `http://localhost:18080`）**

| 项 | 结果 |
|---|---|
| 注册 `boss_test_main_01` 后页面提示 | `✓ 注册成功！您是本系统的主账号，已自动激活，请用相同账号密码登录。`（修复前误为「等待主账号审批」） |
| 登录后直访 `/settings/models` | **渲染完整真实配置**（6 角色 / 3 审核闸、「校验通过·Gate 与所审 Agent 均异基座」、可编辑字段、保存按钮），**未跳 /login**（级联登出已消除） |
| 登录后直访 `/settings/agents` | **渲染完整真实配置**（6 可编辑角色、`agents/analyst.md` prompt 正文、预览渲染、保存/撤销按钮），未跳 /login |
| 测试账号清理 | `boss_test_main_01` 已从 DB 删除 + 孤儿 tenant 目录已删；`tenants/` 仅剩 `baseline-probe`；`C:\Users\sfkj` 下临时观测/验证文件已清理 |

### 5.3 诚实边界（残余，非阻塞）

- `contextvars` 为 task 作用域 + `finally` 复位，无跨请求泄漏风险；Starlette `BaseHTTPMiddleware` 要求 `starlette>=0.27`（环境满足）。
- WebSocket 路径不受中间件影响（admin 端点均为 REST）。
- `RAT_LEGACY_GLOBAL=1` 迁移阀仍属临时态（§一.3），待 Phase 2 正解替代。
- `.env.example` 仍未文档化 `AUTH_SECRET`（模板缺口），留待下次小改。
- bsk CLI 已于本期从 0.2.1 自动升级至 0.3.0；screenshot 在 0.2.1 无 `--path` 标志，故改用 `observe` 语义快照取证（证据见上文各 `obs_*.txt` 已清理）。
