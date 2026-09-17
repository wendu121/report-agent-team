# 设计文档 v2 · 账号体系与多租户资源隔离

> 提出：2026-09-16 · boss 需求：「新增登入页面，主账号关联系统，子注册账号，子不见主，主管理子」
> boss 补充裁定（第二轮）：**自己只看自己的**（含聊天记录、调用记录）；**主账号可点开查阅某个子账号**；所谓隔离是**每个账号各自一套资源**（自己的技能、自己的 token、自己的 AI 模型、自己的专家/智能体）
> 状态：**待设计评审（未写码）**

---

## 0. 与 v1 的差异（避免误读）

| 项 | v1（已废弃） | v2（本文） |
|---|---|---|
| 资源隔离 | ❌ 认定「配置无法按账号隔离」 | ✅ **推翻**：改为 `tenants/<account_id>/` 目录命名空间 |
| 「两套并存」理解 | ❌ 误读为 admin token 双轨 | ✅ 更正为**每个账号各自一套资源** |
| 任务之外的隔离目标 | 只隔离会话 | ✅ 会话 + 调用记录 + **全部可配置资源** |
| 主账号查阅子账号 | 未设计 | ✅ Phase 2 交付（只读） |
| 调用记录 | 无 | ✅ 新建 `call_records` 表，按 owner 隔离 |

---

## 1. boss 第二次裁定的四条（AskUserQuestion）

| # | 决策点 | 裁定 |
|---|---|---|
| 1 | 资源隔离落地方式 | **目录命名空间** `tenants/<account_id>/{config,skills,experts,templates,agents,gates,.secrets,.audit}` |
| 2 | 子账号初始资源 | **复制主账号资源为初始模板**，此后各自独立 |
| 3 | 「调用记录」语义 | **新建 `call_records` 表**（谁/何时/调什么模型或工具/耗时/成败/花销） |
| 4 | 交付节奏 | **分两阶段**：P1 底座与隔离；P2 主账号查阅面板 |

（第一轮裁定仍有效：轻量任务隔离、**开放注册+主审批**、引入标准库做 JWT/口令哈希、现有 admin 端点链路不动。）

---

## 2. 现状实证（全部 `file:line` 可核，来自全仓探索）

### 2.1 账号/鉴权

| 事实 | 证据 |
|---|---|
| 无任何用户表 | `server/models.py` 7 张表全无 `owner/user/tenant` 列 |
| 唯一凭证是静态 `ADMIN_TOKEN`，且**未配置即放行** | `server/admin.py:54,108-114` |
| 无 JWT / 无口令哈希 | 全仓无相关代码；`requirements.txt`（41 行）无 `passlib`/`bcrypt`/`jose`/`pyjwt` |
| 前端零凭证 | `web/src/api/client.ts:20-23` 拦截器空实现（注释即「预留 token 注入位」） |
| 无路由守卫 | `web/src/router/index.ts:62-67` 无 `beforeEach` |
| **任务不在 DB** | `server/api.py:294` 内存 `_tasks` + `.engine_state/*.json`（`models.py` 的 tasks 表是死代码） |

### 2.2 资源根路径（决定 P1 改造量）

**所有资源根都是「导入时常量」，无环境变量可覆盖**：

| 锚点 | 位置 |
|---|---|
| `BASE = Path(__file__).parent` | `orchestrator.py:62` |
| `BASE = Path(__file__).resolve().parent.parent` | `server/admin.py:46`、`tools/skills.py:23`、`tools/experts.py:33`、`tools/skill_importer.py:33`、`tools/push.py:24`、`tools/expert_migrate_v2.py:15` |
| `BASE_DIR = Path(__file__).parent.parent` | `server/api.py:16` |
| `CONFIG_DIR = BASE/"config"` | `admin.py:47`、`skills.py:24`、`experts.py:34`、`skill_importer.py:34`、`push.py:25`、`reflection.py:24` |
| **唯一现成注入缝**：`_config_dir()` / `_secrets_file()` 每次调用计算 | `tools/data_sources.py:1380-1385` |
| 已带 `path` 形参考好改造：`load_model_mapping(path)` / `load_agent_registry(path)` / `load_model_interfaces_and_endpoints(path)` | `orchestrator.py:790/792`、`1027/1035`、`903/921` |

需要改造的**具体常量清单**（P1 必须逐个替换为 per-account 解析）：

- `server/admin.py`：`MAPPING_PATH:48`、`AUDIT_DIR:49`、`MODELS_PATH:1435`、`AGENTS_DIR:379`、`GATES_DIR:592`、`TEMPLATES_DIR:754`、`TEMPLATE_AUDIT_DIR:757`、`LIBRARY_PATH:767`、`PLUGINS_PATH:1360`、`SECRETS_PATH:1362`、`audit_dir():186`、`:2160 plugins 第二寻址`、`skills 的 from-import 绑定:1846-1853`、`lessons:2248`
- `server/api.py`：`CUSTOM_PROVIDERS_CFG:991`、`CUSTOM_PROVIDERS_SEC:992`、`MCP_SERVERS_CFG:1163`、`audit_dir:1372`
- `tools/`：`skills.py:25/26`、`experts.py:35-38,381`、`skill_importer.py:36-40`、`push.py:26/27`、`reflection.py:25`、`mcp_client.py:23 DEFAULT_CFG`、`tools/__init__.py:88 MCPClient(config_path=...)`
- `orchestrator.py`：`_load_md:686/687`、`per_gate:716`、`_load_custom_providers:829/830`

### 2.3 ⚠️ 进程级缓存：不改会「跨账号串号」（P1 必杀项）

| 缓存 | 位置 | 风险 |
|---|---|---|
| `_CHAT_TOOL_MODEL`（**一次性记忆，永不失效**） | `chat_agent.py:160,166` | 首个账号的值污染全部账号 |
| `_chat_llm_client` / `_chat_agent` **全局单例** | `api.py:940,945,975` | 子账号拿到主账号的模型和 key |
| `_CUSTOM_MODELS_CACHE`（key 无账号维度，TTL 120s） | `orchestrator.py:863,864` | 跨账号串模型/串 key |
| `_GATEWAY_CACHE` / `_MODELS_META` | `admin.py:1438,1441` | 网关元数据串号 |
| `self._tools_cache`（TTL 300s） | `mcp_client.py:55,74-75` | MCP 工具列表串号 |
| `_bundle_mtime`（按 4 文件 mtime 失效） | `chat_agent.py:247-248,271-286` | 需改为 per-account mtime |

### 2.4 ⚠️ 引擎子进程边界

`engine_runner.py:74` 在**独立进程**里调 `load_model_interfaces_and_endpoints()`，只经 `input.json` 收参（`engine_client.py:50-51`）。→ per-account 根**必须显式序列化进 `input.json`**，否则子进程永远读全局 `BASE`。

### 2.5 顺带发现的既有缺陷（诚实记账）

- **`experts/` 没有挂载**：`docker-compose.yml:81-99` 挂了 config/templates/agents/gates/skills/.secrets，但**没挂 `experts/`** → UI 写的专家包容器重建即丢。P1 顺手补。
- `.secrets/custom_providers.json`：业务 key 混在多租户里，Namespacing 后按账号分。

---

## 3. 总体架构

```
/app
├── tenants/
│   ├── <main_id>/          # 主账号（引导产线 = 现有全局配置的迁移副本）
│   │   ├── config/{model_mapping,models,agents_library,plugins,custom_providers,mcp_servers,chat_playbook,experts,skills,reflections.proposed}.yaml
│   │   ├── agents/  gates/  templates/  skills/  experts/
│   │   ├── .secrets/{plugins.env,custom_providers.json}
│   │   └── .audit/…
│   └── <sub_id>/           # 子账号（建号时从主账号模板复制）
└── shared/…               # 只读内置默认（新账号无模板时的兜底）
```

**核心模块 `server/tenancy.py`**（新建）：

```python
_current_account_id: ContextVar[str | None] = ContextVar("account_id", default=None)

def set_current_account(aid: str) -> None: ...        # 由鉴权依赖注入
def current_account_id() -> str: ...                   # 缺省 → 抛 TenancyError（fail loud）
def account_root(aid: str | None = None) -> Path: ...  # BASE/"tenants"/aid
def tenant_path(*parts) -> Path: ...                   # 在当前账号根下拼路径
def ensure_account_layout(aid: str, seed_from: str | None) -> None: ...  # 建号/复制模板
```

**所有 2.2 清单里的常量改为调用 `tenant_path(...)`**；三人必须一次改干净，漏一处即为越权读。

### 3.1 路径解析降级策略（诚实边界）

- 进程内：**ContextVar**（由 FastAPI 依赖 `CurrentAccount` 在请求入口设置）。
- 子进程：写入 `input.json` 的 `tenant_root` 字段，子进程 `set_current_account` 后复原。
- CLI / 后台任务：无 ContextVar 时**显式传 `aid`**，不允许静默回落全局（`shared/`），避免「假隔离」。

---

## 4. 数据模型变更

### 4.1 新表 `accounts`（`server/models.py`，随 `create_all` 自动建）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | uuid4，同时是 `tenants/<id>` 目录名 |
| `username` | String(64) unique | |
| `password_hash` | Text | bcrypt(rounds=12) |
| `role` | String(16) | `main` \| `sub` |
| `parent_id` | String(36) nullable FK | 子账号指向主账号 |
| `status` | String(16) | `pending` \| `active` \| `disabled` \| `rejected` |
| `is_system_main` | Boolean | 引导主账号唯一标记 |
| `created_at` / `updated_at` / `approved_at` / `approved_by` | DateTime | |

### 4.2 新表 `call_records`（boss 裁定 3，P1 建表开始写，P2 面板消费）

`id, account_id(索引), kind(model\|tool\|mcp\|plugin), target, ok, latency_ms, cost_hint, created_at, meta_json`
用途：① 子只见自己（`account_id == me.id`）② **主账号点开某子账号时的数据源**（P2）。

### 4.3 既有表补列

- `chat_sessions` 加 `owner_id` + 索引。
- `chat_messages` / `chat_session_memories` **不加列**（经 `session_id` 继承归属，避免冗余不一致）。
- `engine_events` / `gate_reviews` / `tasks` 系列：**不动**（tasks 表当前是死代码，真数据在内存 + `.engine_state` 文件中）。

---

## 5. 隔离规则（逐条可验收）

| 资源 | 子账号 | 主账号 |
|---|---|---|
| 聊天会话 / 消息 / 记忆 | 只见 `owner_id == me.id` | 只见自己；**P2 可点开某个子账号只读查阅** |
| 调用记录 `call_records` | 只见自己 | 自己 + P2 查阅指定子账号 |
| 模型映射 / 模型目录 / 密钥 / MCP / 自定义 Provider | **只读自己的 `tenants/<id>/config/*` + `.secrets/*`** | 同（各自一套） |
| 技能 / 专家包 / Agent-Gate 提示词 / 任务模板 | 自己的目录 | 自己的目录 |
| 越权访问他人资源 | **一律 404**（不返 403，防枚举探测） | — |
| 删除子账号 | — | 目录保留归档（`tenants/<id>` 重命名为 `<id>.deleted`），**不级联删** |

### 5.2 任务隔离（闭合 R1 · boss 第三轮指令）

现状：任务**不在 DB**——`server/api.py:294` 内存字典 `_tasks` + `.engine_state/<task_id>_*.json`；`TaskResponse`（`api.py:195-217`）与 `CreateTaskRequest`（`:229-242`）全无归属字段。

实现口径（**最小且真隔离**）：

| 环节 | 处理 |
|---|---|
| 建任务 | **服务端注入** `owner_id = 当前账号`（客户端传一律忽略），写入 `_tasks[id].owner_id` 与 `TaskResponse.owner_id` |
| 列表 `GET /tasks`（`api.py:622`）与调试列表（`:891`） | 按 `owner_id == me.id` 过滤 |
| 详情 `GET /tasks/{id}`（`:721`）、复核（`:751`）、审计（`:829`） | **越权一律 404**（先取再判归属，不返 403） |
| `.engine_state` 文件 | 文件名仍是 task_id（不含账号信息 → 不泄露）；归属判定以内存条目为准。**孤儿文件**（历史上遗留、内存已不在）视为无主 → 对所有人不可见 |
| 迁移 | **无需**：内存数据重启即失，天然无历史包袱；孤儿文件留待人工清理（记录在 `scripts/` 输出里） |
| 主账号 | 只看自己的任务；**P2** 查阅指定子账号时一并列其任务（与 §5.1 同一抽屉） |

### 5.3 资源端「登录即租户」（闭合 R7 · boss 第三轮指令）

见 §6.3。

### 5.1 主账号「查了什么」的查阅面板（**Phase 2**）

- 入口：设置 → 账号管理 → 点某个子账号 → 「查阅」抽屉：分页展示其 `call_records` + 最近会话列表（**只读，不可代发消息**）。
- 审计：主账号每次查阅写 `admin.log`（记 who/when/whose），防止滥用不可追溯。

---

## 6. 认证与令牌

- **口令**：`bcrypt` 直调（`hashpw`/`checkpw`，rounds=12）。**不用 passlib**——passlib 1.7.4（2020 停更）与 bcrypt≥4.0.1 已知不兼容（`AttributeError: module 'bcrypt' has no attribute '__about__'`），少一层抽象少一个故障点。
- **JWT**：`python-jose[cryptography]` HS256，payload `{sub, username, role, parent_id, iat, exp}`，TTL 12h（`AUTH_TOKEN_TTL_HOURS` 覆写）。
- **密钥**：`.env` 新增 `AUTH_SECRET`；**缺失 fail loud**（`/auth/*` 返 500 + 明确文案 + ERROR 日志），不随机兜底（否则重启即全登出且难排查）。
- 依赖变动：`requirements.txt` 增 `python-jose[cryptography]`、`bcrypt`（+ compose web/api 重建，按最快网络情况耗时较长）。

### 6.1 端点（`server/auth.py` → `/api/v1/auth`）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/auth/register` | 无 | 建 `pending` 子账号；系统尚无账号时引导为主账号并 `active` |
| POST | `/auth/login` | 无 | `pending`→403「等待主账号审批」；`disabled/rejected`→403 |
| GET | `/auth/me` | Bearer | 自身信息 |
| POST | `/auth/change-password` | Bearer | |
| GET | `/auth/accounts` | Bearer(main) | 子账号列表（**不含主账号**） |
| POST | `/auth/accounts/{id}/approve` \| `reject` \| `disable` \| `enable` \| `reset-password` | Bearer(main) | 管理动作 |
| DELETE | `/auth/accounts/{id}` | Bearer(main) | 归档不删数据 |
| GET | `/auth/accounts/{id}/calls` \| `/sessions` | Bearer(main) | **P2**：查阅指定子账号（只读 + 写审计） |

鉴权依赖：`get_current_account`（同时 `set_current_account` 到 ContextVar）；`require_main` 包一层。
**挂载**：`server/main.py:70-75` 加入 `auth_router`。

### 6.2 建号时的资源初始化（boss 裁定 2）

### 6.3 资源端鉴权：登录即租户（闭合 R7）

**旧模型（有缺陷）**：`server/admin.py:54,108-114` 用单一共享静态口令 `X-Admin-Token`，且**未配置时 `return "dev_no_token"` 直接放行**（当前実装就是这样）。它既没有用户概念，也无法承载「每个账号只改自己的资源」。

**新模型**：

| 端点类别 | 鉴权 |
|---|---|
| `/admin/*`（models/agents/gates/templates/plugins/skills/experts/channels/…） | **JWT 必填**：任何 `active` 账号凭自己的 token 操作**自己的** `tenants/<id>/…`；路径隔离由 `server/tenancy.py` 兜底 |
| `/auth/accounts/*` 等账号管理端点 | 额外要求 `role == main`（`require_main`） |
| `X-Admin-Token` | **保留但不再退化**：仅在 `.env` 显式配置 `ADMIN_TOKEN` 时生效，作为应急后门；**未配置 = 不存在该通道**（堵死裸奔） |
| WebSocket | 本轮不鉴权（R8 已知缺口，保持诚实标注） |

统一口径：所有资源读写先从句柄取当前账号 → `tenant_path()` 拼路径。**没有 JWT 就没有 ContextVar → `TenancyError` fail loud**（不许静默回落全局，防「假隔离」）。

`approve` 通过时（不是注册时）→ `ensure_account_layout(aid, seed_from=主账号 id)`：把主账号整个 `tenants/<main_id>/` **深拷贝**为初始模板；此后子账号完全独立。**拷贝时须排除运行时目录**（`outputs/`、`.engine_state/`、`.audit/`），否则会连带拷走别人的任务与日志（见 §10 R5）。

---

## 7. 前端改动

| 文件 | 改动 |
|---|---|
| `web/src/views/Login.vue` | **新增**：登录/注册（含「待主账号审批」提示），令牌体系样式 |
| `web/src/stores/authStore.ts` | **新增**：token（`pinia-plugin-persistedstate` 已在 `main.ts:3,14`）、me、login/logout |
| `web/src/router/index.ts` | 加 `/login`、`/settings/accounts`；`beforeEach` 守卫（白名单 `/login`） |
| `web/src/api/client.ts:21-23` | 填充既有空拦截器：注入 Bearer；401 → 清 token 跳登录 |
| `web/src/views/settings/Accounts.vue` | **新增**（仅主可见）：审批/停用/启用/重置密码/删除；`PageHead` + `StatStrip`；**P2** 加「查阅」抽屉 |
| `web/src/layouts/DefaultLayout.vue` | 头部当前账号 + 退出；仅主账号显示「账号管理」 |

---

## 8. 迁移方案（无 Alembic）

1. `scripts/bootstrap_tenants.py`：把现有**全局** `config/`、`templates/`、`agents/`、`gates/`、`skills/`、`.secrets/` 迁移为 `tenants/<main_id>/…`（先 `--dry-run` 打印计划，再执行；幂等）。
2. `scripts/migrate_add_owner_id.py`：`ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS owner_id` + 回填存量会话归主账号；幂等、支持 `--dry-run`。
3. `docker-compose.yml`：挂载改为 `./tenants:/app/tenants`、**补 `./experts:/app/experts`**（修既有缺陷），保留其余挂载给 `shared/` 兜底。
4. `call_records` 新表由 `create_all` 自动建。

---

## 9. 验证计划（三道闸 · 真实跑）

| 闸 | 内容 |
|---|---|
| ① 校验 | `py_compile` 全量 + `npm run build` EXIT=0 |
| ② 自审 | `VERIFICATION.md` 新节（含 §10 风险如实抄录） |
| ③ 独立审议 | 独立子代理，禁主代理自签 `reviewed-by` |

**实证用例**：

- E1 引导：首注册 → `active` + `role=main` + `tenants/<id>` 已由全局配置迁移生成
- E2 后续注册 → `pending`，login → 403 明确文案；approve 后**资源目录已复制自模板**
- E3 **资源隔离**：子账号 A 改自己的模型/密钥/技能后，B 读取仍是自己那份（读文件实证，不看 UI）
- E4 **缓存串号专项**：两个账号在同进程连续聊天/取模型，验证各自拿到自己的 base_url 与 key（针对 §2.3 六个缓存）
- E5 **子进程**：engine_runner 实跑一次任务，确认子进程加载的是**发起账号**的 model_mapping
- E6 **自己只看自己**：子 A 的 `/chat/sessions` 不含 B 的行；越权访问他人 session → **404**
- E7 `call_records` 隔离写入 + 子只见自己
- E8 P2：主账号查阅某子账号可见其 calls/sessions，且查阅行为被写入审计
- E9 `AUTH_SECRET` 缺失 → 500 fail loud，不得静默放行
- E10 迁移脚本 `--dry-run` 与实跑幂等；`experts/` 挂载后写包不丢

---

## 10. 诚实边界 / 风险（必须摆出来）

- ~~**R1 任务未隔离**~~ → **已闭合**（boss 第三轮指令）：任务补 `owner_id`，列表/详情/复核按调用者过滤，越权 404。细则见 §5.2。
- **R2 改造面广、易漏**：§2.2 清单近 30 处路径常量，**漏一处就是越权读**；必须靠 E3/E4 双用例 + 独立审议交叉验证，不能只靠肉眼看 diff。
- **R3 subprocess 风险高**：`engine_runner` 子进程是独立进程。若 `tenant_root` 没写进 `input.json`，会静默读全局 → 结果「看起来正常但串号」。E5 是专门针对它的防线。
- **R4 单进程 ContextVar**：任何脱离请求上下文的后台线程/定时任务都要显式传 `aid`，否则 fail loud；这是有意的强约束（防静默回落全局造成假隔离）。
- **R5 拷贝模板的体量**：子账号初始资源可能较大（技能/专家包），需明确排除 `outputs/`、`.engine_state/`、`.audit/` 等运行时目录，只拷「配置类」内容。
- **R6 安全不完备**：无验证码/登录限流/失败锁定；公网暴露前必须补。
- ~~**R7 `ADMIN_TOKEN` 旧链路裸奔**~~ → **已闭合**（boss 第三轮指令）：资源端改为「登录即租户」（§6.3），不再依赖共享静态口令；`X-Admin-Token` 退化为空的可选应急后门，不再存在「未配置即放行」。
- **R8 WS 未带凭证**（`wsService.ts:20-22`），本轮不动，需后续 `?token=` 方案。

---

## 11. 未做的事

- ❌ 未写一行实现代码（截至本文撰写时；老板已下达开码指令，实现另行提交）
- ❌ 未改 `requirements.txt` / `docker-compose.yml`（待落地时一次性改）
- ❌ Phase 2 查阅面板的实现细节在此不再展开
