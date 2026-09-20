# VERIFICATION.md — 子账号模型 / 密钥隔离（取消共享主账号 new-api）

> 变更目标（boss 指令）：「子账号需要自己配置模型，还有自己用自己的 api key，但是你现在用的是
> 我一整套的 new-api，这个你要取消，子账号就是用户自己配置模型」
> SoT：`DESIGN_subaccount_model_isolation.md`
> 本轮为**主代理自审**（gate ②）。gate ③ 独立审议见 `REVIEW.md`（**BLOCKED**，非通过）。

## 变更范围

| 文件 | 变更 |
|---|---|
| `server/tenancy.py` | 新增子账号中性模板（`NEUTRAL_MODELS_YAML` / `NEUTRAL_MODEL_MAPPING_YAML` / `NEUTRAL_CUSTOM_PROVIDERS_YAML` + `NEUTRAL_SEED_FILES`）与 `_write_neutral_seed_files()`；`ensure_account_layout(seed_from=…)` 播种后净化 |
| `orchestrator.py` | 新增 `_has_tenant_context()`；`load_model_interfaces_and_endpoints()` 端点为空时**仅无租户上下文**才回落 `NEWAPI_*`；`NewApiLLMClient._resolve()` / `preflight()` 给可执行报错 |
| `server/admin.py` | 6 个 `public_router` 账号作用域读接口加 `Depends(_tenant_required)`（`/templates` `/agents-library` `/plugins` `/models` `/skills` `/channels`） |
| `chat_agent.py` | 新增 `ModelNotConfiguredError` + `_llm_has_endpoints()`；`step()` 入口前置拦截"未配置模型" |
| `server/api.py` | `/chat` 单独捕获 `ModelNotConfiguredError` → 400 + 字符串 detail（可执行指引） |
| `.env` | **删除 `RAT_LEGACY_GLOBAL=1`**（代码注释早已写明「Phase 2 前必须删除，留着就是假隔离」） |
| `web/src/views/ChatEntry.vue` | 默认模型不再写死 `auto-chat`；`usableModels` + `noModelConfigured` + `normalizeSelectedModel()`；未配置时告警条 + 禁用发送；`fetch` → `authFetch` |
| `web/src/views/settings/Models.vue` | 未配置模型 API 时三步引导条；`fetch` → `api.get` |
| `scripts/purge_inherited_gateway.py` | 新增：存量污染清洗（幂等，支持 `--dry-run`） |
| `scripts/verify_subaccount_isolation.py` | 新增：A 系列（进程内）+ B 系列（真 HTTP）13 项断言 |

## gate ① 编译 / 构建

- `py_compile` → `server/tenancy.py` `orchestrator.py` `server/admin.py` `server/api.py` `chat_agent.py` `scripts/purge_inherited_gateway.py` `scripts/verify_subaccount_isolation.py` 全部 rc=0 ✓
- `docker compose build api` → `naming to docker.io/library/report-agent-team-api:latest done`，exit 0 ✓
- `docker compose build web` → `vue-tsc -b && vite build` → `✓ built in 11.88s`，**0 TS error**，exit 0 ✓（两次重建均通过）

## gate ② 实测证据（全部真跑，无纸面结论）

### 2.1 存量数据清洗（幂等）

`scripts/purge_inherited_gateway.py --dry-run` 先看后改：

```
[SKIP] 9910ebbc-… (wendy) : 系统主账号，跳过（网关主人）
[DRY] af28b675-… (wendy1) config/models.yaml : 含 new-api 端点（指向主账号网关） → 换中性模板
[DRY] af28b675-… (wendy1) config/model_mapping.yaml : 指向主账号网关模型：roles.*.model=LongCat-2.0、
      gates.Gate*.model=llama-3.3-70b、chat.tool_model=LongCat-2.0 → 换中性模板
[DRY] af28b675-… (wendy1) config/custom_providers.yaml : 含主账号自定义 provider：Intern-S2-Preview-397B → 换中性模板
共扫描 5 个租户，将要修复 3 处
```

真跑后**幂等复跑 = 已修复 0 处** ✓（主账号被正确跳过）

### 2.2 隔离断言 13/13 通过

```
✅ A1  子账号 config/ 不含主账号网关痕迹（12 个文件，0 命中）
✅ A1b 中性模板已实际落盘
✅ A2  子账号 endpoints=0 且 default_endpoint_id=None（容器内 NEWAPI_API_KEY 已设置=True）
✅ A2b 引擎子进程路径（RAT_ACCOUNT_ID + bind_account）同样 endpoints=0
✅ A3  未配置时抛 LLMError，文案=「…请到「设置 → 自定义 API」添加你自己的 Provider…」
✅ A4a 自加 Provider 后端点=自己的 base_url/key（probe.example.invalid / sk-probe-own-key-1234）
✅ A4b 模型解析命中子账号自己的端点（custom:probe-provider）
✅ A5  回归：主账号仍持 new-api 端点（['new-api','custom:Intern-S2-Preview-397B']）
✅ B0  探针清除后账号回到「未配置」状态（endpoints=0）
✅ B1  HTTP 子账号 /models items=1，泄漏项=[]
✅ B2  回归 HTTP 主账号 /models items=122，含 new-api
✅ B3  无 token 调 /models → 401（旧行为 200 + 泄漏）
✅ B4  HTTP 子账号 /chat → 400 + 可执行指引
结果：13/13 通过
```

### 2.3 主账号真链路回归（未被误伤）

容器内真调 `POST /api/v1/chat`（wendy token）：

```
主账号: wendy 9910ebbc-…
HTTP 200 耗时 11.2s
reply: 1+1等于2。
```

### 2.4 真实浏览器（wendy1 子账号）

| 检查 | 结果 |
|---|---|
| 登录 wendy1（净化后） | ✅ 正常，头像显示「W wendy1 子账号」 |
| 研报入口告警条 | ✅ 「尚未配置模型 API … 去配置」 |
| 发送按钮 | ✅ `disabled=true` |
| 模型下拉 | ✅ 空（不再列出主账号模型） |
| `/settings/models` 引导条 | ✅ 命中「尚未配置模型 API，下面填了也不会生效」「去配置自定义 API」「至少需要两个不同来源」 |
| `/settings/custom-providers` | ✅ 可进入，0 provider，有「＋ 新增」按钮 |

## 过程中被实测推翻的两次误判（留痕，不掩盖）

1. **A 系列全绿 ≠ 真隔离**。A 系列是进程内手动 `set_current_account` 后调加载器，只证明
   「有上下文时隔离成立」。真实浏览器里子账号的模型下拉**照样列着主账号的 new-api 网关模型**
   （`GET /api/v1/models` 返回 `auto-chat` / `LongCat-2.0` / 122 个网关模型）。
   根因 = L4：6 个接口挂在无鉴权 `public_router` + `RAT_LEGACY_GLOBAL=1` → 无上下文 → 回落全局
   （主账号 config）。**这是本轮最有价值的发现，只有真机才暴露。**
2. **B4 首跑 200 + `reply="抱歉，脑子打结了：…Connection error."`** —— 一度疑似"产品吞掉了配置缺失"。
   真因是**我自己的测试没归零**：A4 往同一账号写了探针 provider，B4 面对的是"已配置（但地址是假域名）"
   的账号。补 B0 归零断言后 400 如期返回。（同 `MEMORY.md` 坑 6 的教训：用例状态不干净，结论就不可信。）
   但仍顺带修掉了一个**真问题**：`chat_agent.step()` 的通用 `except` 会把"没配模型"吞成
   一句敷衍回复 + HTTP 200，用户永远不知道要去配 → 已改为 400 + 明确指引（详见 DESIGN §3-D8）。

## 诚实边界 / 未覆盖项

- **未做**：独立审议（gate ③）因 429 无法启动 → 见 `REVIEW.md` 顶标 `BLOCKED`。
- **未在浏览器复验主账号 UI**：主账号的 `/models`（122 items）与 `/chat`（HTTP 200 真实回复）
  已在 HTTP 层验证；UI 侧同一组件、同一 `authFetch` 路径，未再切浏览器登录态（避免污染 boss 会话）。
- **未改**：`异基座`硬约束（`admin.py:_validate_mapping`）刻意不放松 → 子账号需自备 **≥2 个不同来源**
  的 Provider 才能保存映射。这是治理规则，已在 UI 明说，未偷偷放宽。
- **已知遗留**（不在本轮范围）：~~子账号自配 provider 若 base_url 写错，报错仍是
  `Connection error`（被包成「脑子打结了」），可读性待改进~~ → **已在第二轮修掉**
  （见下文 §R2 变更 5：上游 `LLMError` 不再伪装成内部故障）；`server/websocket.py` 的任务流
  仍用全局状态目录（多租户文件级隔离缺口，非本轮引入）。

---
---

# 第二轮 · 对话可中断（停止生成）与可修正（编辑重发）

> SoT：`DESIGN_chat_interrupt_edit.md`（含 §6「验证阶段挖出的真根因」）
> 本轮为**主代理自审**（gate ②）。gate ③ 独立审议**本轮再次被 429 挡下** → `REVIEW.md` 顶标仍为 `BLOCKED`。

## R2-1 变更范围

| 文件 | 变更 |
|---|---|
| `server/main.py` | `_RequestContextMiddleware` 由 `BaseHTTPMiddleware` 改为**纯 ASGI 中间件**（本次最关键的一行修复；原因见下） |
| `server/api.py` | `/chat` 新增断连探测 watcher + `cancel` 透传 + **中止即整轮零落库**；新增 `POST /chat/sessions/{id}/truncate`；响应新增 `cancelled` / `user_message_id` / `assistant_message_id` |
| `orchestrator.py` | 新增 `LoopCancelled`；`_run_fc_loop(..., cancel=)` 在**每轮调 LLM 前**与**每个工具执行前**检查取消 |
| `chat_agent.py` | `step(..., cancel=)`；`LoopCancelled` 单独截住（不落通用 except）；新增 `LLMError` 分支 → **上游故障如实上报**，不再回「抱歉，脑子打结了」+ HTTP 200 |
| `web/src/services/chatService.ts` | `/chat` 传 `AbortSignal`、超时、新增响应字段类型 |
| `web/src/services/chatSessionService.ts` | 新增 `truncateFrom()` |
| `web/src/views/ChatEntry.vue` | 停止按钮；编辑气泡 + 编辑横幅（明示「将删除该条及其后 N 条并重新生成」）；abort 分支；竞态提示 |
| `scripts/verify_chat_interrupt_edit.py` | 新增：7 项断言（A1/A2 直连、B1 经反代、A3 正常轮、A4 截断+幂等、A5 越权） |
| `scripts/probe_disconnect_middleware.py` | 新增：中间件吞断连的**对照实验**（根因证据） |

## R2-2 gate ① 编译 / 构建

- `py_compile` → `server/main.py` `server/api.py` `chat_agent.py` `orchestrator.py` `scripts/*.py` 全部 rc=0 ✓
- `docker compose build api` exit 0；`up -d api` → `Up (healthy)` ✓
- 镜像内**导入期断言**：`APP_ROUTES 11`、`MW_is_base? False`、`MW_call_is_asyncgen? True` ✓
  （即已确认不再继承 `BaseHTTPMiddleware`，且 `__call__` 是纯 ASGI 协程）
- `docker compose build web` → `vue-tsc -b && vite build` → `✓ built`，**0 TS error**，exit 0 ✓
- 线上产物核对：`grep -rl '这次没能及时停下' /usr/share/nginx/html/assets/` → 命中 → 部署的确是**新**前端 ✓

## R2-3 根因：为什么第一版「停止」仍是假按钮（真机证伪）

首轮按设计实现并部署后，A1/A2 **FAIL**：RST 断开 3s，服务端日志**没有**任何断连记录，
工具循环跑到第 2、3 轮，最终整轮照常落库（与修复前基线一致）。

对照实验 `scripts/probe_disconnect_middleware.py`（同进程两个 app，**只差一个中间件**，同一 RST 手法）：

| 配置 | 服务端感知断开 | handler 被取消 |
|---|---|---|
| 无中间件 | ✅ `DISCONNECT_DETECTED` | ❌ |
| 套 `BaseHTTPMiddleware` | ❌ **永远感知不到** | ❌ |

根因：`server/main.py` 的 `_RequestContextMiddleware` 继承 `BaseHTTPMiddleware`，
它在 app 外再包一层 receive/send（内存流），`http.disconnect` 被它自己消费 →
端点里 `await request.is_disconnected()` **恒为 False**。它不报错、不告警，只让你的「停止」变成假的。

修复后同一条链路（真机日志，时间戳为证）：

```
09:17:17,748 INFO [server.api] 检测到客户端断开 → 置取消信号: session=57d0cdd9…
09:17:20,039 INFO [chat_agent] ChatAgent 轮次被用户中止（本轮不落库）
09:17:20,040 INFO [server.api] 对话轮次被用户中止 → 整轮不落库: session=57d0cdd9…
```

**断开 → 丢弃 = 2.3 秒**；该窗口内**零工具调用**（无 `search_many` 记录）→ 证明取消发生在
「下一次 LLM 调用之前 / 下一次工具执行之前」，即**真的省下了后续轮次与工具开销**，不只是"没落库"。

## R2-4 gate ② 实测证据

`docker compose exec -T -e PYTHONPATH=/app api python -u /app/scripts/verify_chat_interrupt_edit.py` → **9/9 PASS**（exit 0）：

```
[A0]    中间件体检：已注册 2 个中间件，BaseHTTPMiddleware 派生类 = 0 个        PASS
[A1/A2] 直连 api:8000，RST 断开 → 等满 100s，chat_messages = 0 条            PASS
[B1]    经 nginx（web:80，浏览器真实链路），同上 → 等满 100s = 0 条            PASS
[A3]    正常一轮（不中断）HTTP 200 intent=chat reply='1+1等于2。' 0 → 2 条    PASS
[A3]    响应回传 user_message_id / assistant_message_id（编辑重发要用）        PASS
[A4]    截断：2 条 → HTTP 200 {deleted:2} → 剩余 0，deleted 与实际减少量一致   PASS
[A4]    幂等复跑 → HTTP 404「消息不存在或不属于该会话」（fail loud，不误删）    PASS
[A5]    越权：wendy1 截断 wendy **仍在**的消息 → 404 且 2 条一条没少            PASS
[A6]    无归属会话（owner_id IS NULL）继续对话 → 404（fail closed）           PASS
```

**为什么 A0 是一条"长期护栏"而不只是本次断言**：本次根因（`BaseHTTPMiddleware` 吞 `http.disconnect`）
**不报错、不告警**，只是让「停止」静默变成假按钮。将来任何人为了「加个 header」顺手把
`BaseHTTPMiddleware` 加回去，功能就会再坏一次而没人发现。故把它钉成测试：
`app.user_middleware` 中出现任何 `BaseHTTPMiddleware` 派生类即 FAIL。

**为什么 A1/A2 的「100 秒 0 条」是有效证据**：修复前基线（`probe_abort_persistence.py`）是
「2s 断开 → 服务端照跑到 35s → **把 user+assistant 都写进库**」，且首轮验证里写入发生在 **78s**。
所以必须等满 100s（超过最晚写入点）仍为 0，才能排除「只是还没写完」。100s > 78s，判据成立。

**为什么必须补 B1**：浏览器不走 `api:8000`，走 `web:80 → nginx → api:8000`。只测直连会漏掉
「反代是否吞掉断连」这一层。B1 用同一 RST 手法打 nginx，结果同样 0 条 → 真实链路成立。

### R2-4b 验证过程中另查出并修掉的跨租户泄漏（**非本轮引入**）

查「越权」时顺手核了归属校验的全仓一致性，发现两处**写法不一致**：

| 位置 | 写法 | `owner_id IS NULL` 时 |
|---|---|---|
| `_owned_session()`（`GET /chat/sessions/{id}` 等） | `if aid and s.owner_id != aid` | **拒绝**（严格） |
| `/chat` 内联校验（`api.py`） | `if aid and s.owner_id and s.owner_id != aid` | **放行**（宽松，短路） |

真机核查数据：`SELECT count(*) FROM chat_sessions WHERE owner_id IS NULL` → **1 个会话、8 条消息**。
即：**任何已登录账号都能继续这个会话**，其历史会被读出来喂给模型 = 跨租户内容泄漏。
（内容是一段早期的「hello / 你现在都会做什么」测试对话，敏感度低，但机制是真漏。）

两半都修了（缺一不可）：
1. **数据半**：新增 `scripts/backfill_null_session_owner.py`（幂等 + `--dry-run`）→ 先 dry-run 看，
   再执行：`发现 1 个无归属会话 → 归位到 wendy`，复核 `仍有 0 个`。
   把它归位到**系统主账号**（多租户改造前唯一的用户），这样严格化之后原主人**仍能访问**，
   而不是被锁在门外。
2. **代码半**：`/chat` 改为与 `_owned_session` 一致的严格式 `if aid and s.owner_id != aid`
   （fail closed）。并由新增的 **A6** 断言钉死：人为把 `owner_id` 置空后继续对话必须 404。

**如实说明**：这是**存量数据 + 既有宽松判断**的组合问题，不是本轮改动引入的；
本轮只是「因为做越权测试才撞见」。全仓另有 3 处同类宽松写法（`api.py` 的 task / rest 归属校验），
**本轮未动**（改动任务可见性语义的风险与收益不匹配），已列入 `REVIEW.md` §2 补审待办。

## R2-5 回归（改了 `server/main.py`，必须验）

`server/admin.py` 有约 50 个端点签名不含 `Request`，依赖该中间件注入的 ContextVar 读
`Authorization`（`admin.py:177-178`）。中间件换成纯 ASGI 后重跑上一轮的套件：

**① `scripts/verify_subaccount_isolation.py` → 13/13 PASS**（exit 0）
关键几项：`B1 子账号 /models 不含主账号网关模型`（items=1，泄漏项=[]）、
`B2 主账号 /models 仍含 new-api`（items=122）、`B3 无 token → 401`、
`B4 未配置模型 → 400 + 可执行指引`。
→ 说明**中间件替换没有打断鉴权 / 租户上下文注入**这条链路。

**② `scripts/e2e_report_flow.py wendy`（研报引擎全链路）→ 同代码同 prompt 跑两次，结果不同**

| 次 | 终态 | 轮次 | 报告长度 | 耗时 |
|---|---|---|---|---|
| 第 1 次 | `escalated` | 2（GateA rework → rework 触顶） | **0 字** | 160.8s |
| 第 2 次 | `done` | 1（GateA 直接 advance） | **5998 字** | 213.7s |

> ⚠️ 注意：该脚本 **escalated 时 exit code 仍是 0**（它只报告现象，不把终态当失败）。
> 所以「E2E_EXIT=0」**不等于**「回归通过」—— 必须看终态。这本身是测试脚本的一个缺陷（已记录待改）。

**第一次为何 escalate（读 engine events 得到，非猜测）**：

```
round=1  GateA → rework  reason=检索结果全部可信度为 low，且未给出说明
round=2  GateA → rework  reason=…medium，且未给出说明；存在…low 的情况
→ rework 将使 round=3 超过 max_rounds=2 → escalate
```

**是否为本次改动引入？证据指向「否」**：同一条 prompt 的 4 次 run 里 GateA 判决本就波动：

| task | round 1 | round 2 | 终态 |
|---|---|---|---|
| `4aa456ef`（09:04） | **advance**（来源可信度均 medium） | — | done，4976 字 |
| `317799d1`（09:06） | **rework**（标题与 snippet 不完全匹配） | **advance** | done |
| `4651fcec`（本轮 1 次） | **rework**（全部 low） | **rework** | **escalated，0 字** |
| 本轮 2 次 | **advance** | — | **done，5998 字** |

本轮 diff **未触碰** GateA、`max_rounds`、检索源或可信度判定；波动来自
「LLM 质量判决 + 当时的检索源可得性」。故判定为**既有波动**而非本次回归。

**但这是一个真实的产品体验缺口，必须报给 boss 决策**（不由我单方面放宽质量闸）：

- `max_rounds=2` 意味着**只容得下一次 rework**；第二轮 GateA 仍不满意就直接 escalate，
  用户等了 160s 拿到的是**空报告**（概率不低：4 次里 1 次）。
- 放宽 GateA 或调大 `max_rounds` 都是**降低质量闸**的动作，属于产品口径决策，不是修 bug；
  我**不动**（避免「靠削弱检查让测试变绿」这种最坏做法）。
- 更该修的是**「escalate 后的兜底交付」**：至少把已完成的 Researcher/Analyst 中间产物
  落成一份「附质量警告的草稿」，而不是 0 字。已列入 `REVIEW.md` §2 补审待办与后续待办。

## R2-6 诚实边界 / 未覆盖项（不吹）

1. **在飞的那一次 LLM 调用掐不断**。取消只在「下一次 LLM 调用前 / 下一次工具前」生效。
   实测这次是 2.3s，但那是因为该轮模型调用恰好很快返回；若模型正在生成一个 60s 的长回复，
   用户点停止后**仍要等这次调用返回**才会停。UI 文案与设计 §3.1 均按此写，未吹成「毫秒级中断」。
2. **未做真实浏览器点击验证**（A6/A7）。本轮完成的是服务端 + 反代 + HTTP 契约层的真跑；
   前端逻辑（按钮态、编辑横幅、abort 分支、竞态提示）只有 `vue-tsc` 通过 + 产物含新字符串两条证据，
   **没有**在浏览器里手点「停止」和「编辑重发」。→ 已列为补审/后续必做项。
3. **单 worker 假设**：取消信号用进程内 `threading.Event`，依赖 `Dockerfile` CMD 未加 `--workers`。
   将来多 worker 必须重做（设计 §5 已划界）。
4. **会话记忆在取消时也不写**：这是有意（否则用户以为取消掉的内容会以「记忆」形式复活）。
5. **`truncate` 用 404 而非 403**：防枚举的有意选择（与现有 `GET /chat/sessions/{id}` 一致）。

## R2-7 本次验证过程中被推翻的自身错误（留痕）

1. **测试自身的级联假失败 ×2**：
   - A4 首版把「截断后剩 0 条」写死 → 在 A1/A2 泄漏 2 条时误报 FAIL，掩盖「截断其实是对的」。
     已改为断言 `剩余 == 截断前 - deleted`。
   - A3 首版复用了 A1/A2 已被 `cleanup` 掉的会话 → HTTP 404「会话不存在」，
     并把 A4/A5 一起带成 FAIL。已改为各用例独立建会话。
   **教训与 MEMORY 坑 6 同类：用例状态不干净 / 断言写死，结论就不可信；测试自身的 bug 也是 bug。**
2. **A5 首版是「假 PASS 风险」**：原写法用一条**已被删掉**的消息去试越权，404 可能只是
   「消息不存在」而非「挡了越权」。已改为用**仍在库里**的消息试，并同时断言「一条都没被删」。

修复后 `py_compile` rc=0；e2e 四项全绿（含新增 [3b]）。`REVIEW.md` 顶部 `reviewed-by: independent-subagent` 与 verdict 维持，本文为落实记录，非主代理自签结论。


---

<!-- 合并自远端 origin/master（75b263a）：以下为同名文档的远端版本，整段保留以免信息丢失 -->

# VERIFICATION.md — LLM 接口 OpenAI 兼容 + 输入 token 预算闸

> 变更目标（用户指令）：把 LLM 接口改成通用 OpenAI 兼容模式 + 配 new-api 跑通真实端到端。
> 实查：仓库已有 `NewApiLLMClient`（本就 OpenAI 兼容），真问题是**缺输入 token 预算**导致 new-api 超窗 400 `input length too long`。本变更补预算闸 + 自愈，并实测跑通。

## 变更范围
- `orchestrator.py`（未提交，工作树改动）：
  - 新增模块级函数：`estimate_tokens` / `_trunc_keep_tail` / `fit_input_budget` / `_INPUT_TOO_LONG_RE` / `_is_input_too_long`
  - `NewApiLLMClient.__init__` 新增 `max_input_tokens: int = 128000`、`reserved_output_tokens: int = 4096`（可被 `LLM_MAX_INPUT_TOKENS` 覆盖）
  - `complete()` 与 `complete_with_tools()` 增加 `factor` 输入预算 + input-too-long 自愈（factor 最低 0.125，逐级减半）
- `scripts/verify_llm_openai_e2e.py`（新增）：真实 e2e 验证脚本

## gate ① 编译校验
- `py_compile orchestrator.py` → rc=0 ✓

## gate ② 真实 e2e 实证（搭配 new-api，脚本实跑输出）
- **[1] preflight**：`ok: True`，模型数 **120** → 证明 new-api 标准 OpenAI 兼容 + 网关连通正常
- **[2] 真实 complete**：首跑 `auto-chat` 短提问返回 47 字中文真回答（"LangGraph 是一种基于图结构的框架…"）。复跑时 `auto-chat` 通用别名背后的免费通道（aihubmix）触达「未充值账号仅 10 次」免费额度上限，网关返回 200 + 额度提示文案（非 400/非崩溃，代码优雅透传）。改用具体模型 `LongCat-2.0` 复测返回 66 字真回答（"LangGraph 是一个基于 LangChain 构建的…"）→ 证明 OpenAI 兼容路径真实出 token（额度问题属上游环境变量，非代码缺陷）。
- **[3] 预算压测**：超大 user 估算 **110001** token → `fit_input_budget` 截断到 **3908**（≤3900）；`complete()` 用 `max_input_tokens=8000` 跑通返回 103 字 → 截断 + input-too-long 自愈生效，**零 400 崩溃**
- **[4] estimate_tokens sanity**：中文 48 字→77（≈48×1.6）、英文 20 词→12（≈拉丁词+字符）→ 中英文计数合理

## 自审要点（主代理自查，非独立审议）
- **安全**：无硬编码密钥。`KEY` 经 env `NEWAPI_API_KEY` 或仓库 `.env` 读取；`.env` 已被 `.gitignore`，不会入库。
- **通用性**：`fit_input_budget` 与 input-too-long 自愈不依赖具体模型窗口大小 → 适配 new-api 背后 26+ 通道窗口各异。
- **不裁 tools**：`complete_with_tools` 自愈仅裁 system/user 文本，**绝不**自动剔除 `tools`/`tool_choice`（否则把"有工具依据的调用"偷偷降成凭空作答 = 制造幻觉）。
- **保序**：input-too-long 自愈先于 `_is_non_retryable` 黑名单判定（注释已说明，避免成为死代码）。
- **日志可见性**：已修 `root logger` 无 handler 问题（前序坑 16），自愈 `logger.warning` 会真实落盘。

## 已知限制
- `estimate_tokens` 为启发式近似（沙箱无 tiktoken），极端混合文本可能低估；自愈重试下限 `factor=0.125`（1/8），仍超则如实抛错。
- 仅验证 new-api 网关路径；离线 stub 路径未改动、未回归。

## 结论
gate ① ✓、gate ② ✓（真实 e2e 实证）。提交独立子代理审议（gate ③）。

## 复审后修复（gate ③ 笔记落实，reviewer=agent-768f2a15，verdict=PASS_WITH_NOTES）
独立审议提出 3 条笔记，均已落实并复验：

1. **`fit_input_budget` 漏裁门（#1，真 bug）**：原 user 截断门 `if usr_budget < max_input_tokens` 代数等价于 `sys_t > 0`，system 为空时超大 user 永不截断 → 400 无法自愈。改为 `if usr_t > usr_budget`（以 user 自身是否超预算为准）。**复验 [3b]：空 system + 110001 token 超大 user → 截断返回 188 字，零 400。已修。**
2. **负预算（#2，同根）**：`cur_max - reserved_output_tokens` 可能 ≤0 致负预算漏裁。改为 `budget = max(1, cur_max - reserved_output_tokens)` 夹紧下限。已修。
3. **factor 1/8 不可达（#3，文档/行为不符）**：默认 `max_retries=2` 仅 3 次尝试，factor 降到 0.25 即耗尽，0.125 被设但永不发出。改为 `for attempt in range(max(self._max_retries + 1, 4))` 保证 ≥4 次，1/8 可达。已修（注释同步）。