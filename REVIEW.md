<!--
  【历史注记】以下描述的是 2026-09-19 19:10 配额重置**之前**的状态，当时独立审议确实一次也没跑成。
  该状态已于 2026-09-19 19:23 / 19:50 / 19:52 改变：三次独立审议全部实际执行完毕，
  结论见本文件顶部总览表与三份独立 REVIEW 文件。主代理**未自签**、未伪造任何通过结论。
  （原始状态描述保留于下，作为限流阻塞的真实记录）
  主代理**未自签**、未伪造通过结论。原因：独立 reviewer 子代理**每次**启动即被 API 限流挡下
  （429「您的使用量已超出频率限制，将在 2026-09-19 19:10:16 UTC+8 重置」）。

  尝试记录（全部失败，无一例外）：
    - 2026-09-19 16:52  "Frontend sub-account model UX"            → 429
    - 2026-09-19 16:54  "Write gateway purge script"              → 429
    - 2026-09-19 17:2x  "Backend stop/edit for chat"(chat-backend) → 429
    - 2026-09-19 17:2x  "Independent review of chat interrupt/edit" → 429
  工作树里因而**累积了两个未审议轮次**（子账号隔离 + 对话可中断/可编辑），均在配额重置后一并补审。
  配额重置后须补做独立审议，PASS 前**不得 commit**（遵循 2026-09-03 确立的门禁受阻硬约定）。
  第一轮（§1 以下）与第二轮（§2）的补审清单分别列出。
-->
# REVIEW.md — 独立审议总览（三道门禁 · 第三道）

> **本文件是 gate ③ 的索引 + 历史。三次独立审议均已实际执行**，各自结论落在独立文件中
> （每份首行含 `reviewed-by: independent-subagent` 标记、由独立子代理撰写；主代理**未自签**、
> 未改写任何审议结论）：
>
> | 里程碑 | 独立审议文件 | 结论 |
> |---|---|---|
> | 检索源诚实降级 | `REVIEW_source_honesty.md` | PASS_WITH_NOTES（无 BLOCKING） |
> | 子账号模型/密钥隔离 | `REVIEW_subaccount_isolation.md` | PASS_WITH_NOTES（无 BLOCKING）；HIGH-1 已闭环 |
> | 对话可中断/可编辑 | `REVIEW_chat_interrupt_edit.md` | PASS_WITH_NOTES（产品面 BLOCKING=0） |
>
> **⚠️ 但当前仍不可 commit，原因如实登记在这里**：上述三份结论出具**之后**，主代理又改了 4 处代码
> （1 处按审议者给的修法修一个真回归、1 处补审议者点出的测试门禁缺陷、2 处过时 docstring）。
> 这 4 处都属「gate ③ 结论之后的新改动」，按约定必须回到独立审议者处复核，**不能自认修好**：
>
> | 事后改动 | 性质 | 复核状态 |
> |---|---|---|
> | `web/src/stores/template.ts` 裸 fetch → `authFetch` | 审议者抓到的 HIGH 回归 | ✅ 已回审议者复核，HIGH-1 已闭环 |
> | `scripts/e2e_report_flow.py` 补 `sys.exit(1)` + 窗口 480→900s | 审议者点出的门禁假绿（F4） | ❌ **复核未完成**（429 打断） |
> | `tools/__init__.py` docstring 更正 | 文档层过时描述 | ❌ **复核未发起**（429 打断） |
> | `orchestrator.py` docstring 更正 | 文档层过时描述 | ❌ **复核未发起**（429 打断） |
>
> 429 重置时间：**2026-09-20 00:11:04 UTC+8**。**修复复核 PASS 前不 commit。**
> 具体待办与审什么见 `REVIEW_postfix_pending.md`。
>
> 以下 §1 / §2 保留为**历史记录**：那是配额被限流期间写下的补审清单（当时确实一次都没跑成），
> 其内容已由上述三份独立审议逐条覆盖，请以那三份文件为准。

**Verdict（历史）: `BLOCKED` —— 仅描述下文 §1/§2 所对应两轮补审在**当时**的状态。**
两轮已于 2026-09-19 19:50 / 19:52 分别由独立子代理执行完毕，结论见上方总览表。

## 为什么是 BLOCKED

子账号模型/密钥隔离这一轮（详见 `DESIGN_subaccount_model_isolation.md`、
`VERIFICATION.md`）在实施中途尝试启动独立审议子代理，两个子代理均**立即**失败：

```
Error: Failed to execute task "Frontend sub-account model UX" after subagent was created:
429 您的使用量已超出频率限制，将在 2026-09-19 19:10:16 UTC+8 重置
Error: Failed to execute task "Write gateway purge script" after subagent was created:
429 您的使用量已超出频率限制，将在 2026-09-19 19:10:16 UTC+8 重置
```

按既定硬约定：**主代理不得自审自签顶替**。故本文件只声明阻塞事实，不产出审议结论。
实施与验证因此由主代理独立完成（含"实施者自测"性质的 13 项断言），
**其独立性不足，不能替代 gate ③**。

## 本轮变更摘要（供补审者定位）

改动 10 个文件 + 2 个新脚本 + 1 份设计文档，核心是「子账号不再继承主账号 new-api 网关」：

1. `server/tenancy.py` — 子账号播种中性模板（models.yaml 清空端点、model_mapping 留空骨架、custom_providers 清空）
2. `orchestrator.py` — `_has_tenant_context()`；端点为空时**仅无租户上下文**才回落 `NEWAPI_*`
3. `server/admin.py` — 6 个 `public_router` 账号作用域读接口加鉴权依赖
4. `chat_agent.py` + `server/api.py` — "未配置模型"从被吞成敷衍回复改为 400 + 可执行指引
5. `.env` — 删除 `RAT_LEGACY_GLOBAL=1`
6. 前端 `ChatEntry.vue` / `Models.vue` — 引导 UI + `authFetch`
7. `scripts/purge_inherited_gateway.py` / `scripts/verify_subaccount_isolation.py`

## 补审待办（配额恢复后按此清单审）

**A. 隔离是否真的无可绕过（最高优先）**

- [ ] `_has_tenant_context()` 的异常分支：tenancy 模块导入失败返回 `False`（=允许 env 回落），
      在引擎子进程 / 单测 / 脚本三种真实入口下会不会被触发？触发即重新打开泄漏，需逐入口断言。
      重点：`server/engine_runner.py` 在 `input_data` **没有** `account_id` 时（历史任务记录）
      会不会走到"无上下文"分支 → 若会，老任务是否可能用主账号网关？
- [ ] 除 `/templates` `/agents-library` `/plugins` `/models` `/skills` `/channels` 外，
      是否还有其它账号作用域读接口挂在无鉴权路由上？建议全仓扫 `APIRouter(` 与
      `tenancy.config_path(` 的调用点做交叉比对（`server/websocket.py` 已知用全局状态目录，待判风险）。
- [ ] 删除 `RAT_LEGACY_GLOBAL=1` 是否打断了某条真实链路？需覆盖：登录/注册、研报任务全流程
      （任务创建 → 引擎子进程 → WebSocket 流 → 结果页）、能力市场四个页、推送渠道。
- [ ] `_write_neutral_seed_files()` 的"覆盖判定"（内容逐字节等于主账号原样副本才覆盖）：
      若主账号后来改了自己的 models.yaml，子账号那份原样副本就不再"逐字节相同" → 会被误判为
      "用户改过"而**不被净化**。这是否是真实可达的漏洞？建议构造该时序复现。
- [ ] `purge_inherited_gateway.py` 的污染特征是否完备：只按 `id == "new-api"` /
      模型名白名单 / `base` 白名单判定。若主账号网关端点被改名（如 `id: my-gateway`），
      存量子账号会漏洗 —— 是否需要改成"端点 api_key 展开后非空"这类更本质的判据？

**B. 诚实性与错误路径**

- [ ] `400 + 字符串 detail` 是否是全局最优：`ApiError` 的 `String(body.detail)` 会把对象变
      `"[object Object]"`（本轮踩到并绕开）。这是**共用缺陷**，其它返回对象 detail 的端点
      （如 `CHAT_AGENT_FAILED` 的 500）在 UI 上是否都显示成 `[object Object]`？建议一并修。
- [ ] `ModelNotConfiguredError` 的前置闸放在 `step()` 入口，是否漏掉其它进入 LLM 的路径
      （如专家偏好模型、研报意图分支、`propose_lesson`）？
- [ ] 前端"未配置"判定（`!!endpoint_id && kind !== 'auto'`）与后端语义是否严格一致？
      会不会出现"前端认为已配置、后端认为没有"的错位（例如 provider enabled=false 时）？

**C. 回归与副作用**

- [ ] 主账号全流程端到端（本轮只验了 `/models` HTTP + 一次 `/chat`，未跑研报任务、未跑能力市场）。
- [ ] `异基座`硬约束对子账号的适用性：单 provider 的子账号**永远无法保存映射**。
      确认这是有意的治理约束（本轮判断为"有意，且在 UI 明说"），而非把子账号锁死到不可用。
- [ ] 前端 `usableModels` 归零后 `selectedModel` 落空 → 请求不传 model → 后端用
      `chat.tool_model`。子账号该字段为空时 `_resolve_chat_model` 返回 `"auto-chat"`，
      会不会被发到用户自己的 provider 上导致 400（而非被 `ModelNotConfiguredError` 拦住）？
      —— 注意：本轮 B4 走的是"完全无端点"分支，**这条"有端点但 tool_model 空"的路径未被覆盖**。

**D. 独立复现要求**

- [ ] 不要采信 `VERIFICATION.md` 的结论，请**独立重跑**：
      `docker compose exec -T -e PYTHONPATH=/app api python /app/scripts/verify_subaccount_isolation.py`
      并自行核对 B1–B4 是否为真 HTTP 断言而非进程内自证。
- [ ] 建议换基座模型审议（本项目实测：Intern-S2-Preview-397B 可信；`auto-fast` /
      `auto-reasoning` 曾出现无证据 PASS 与编造行号，勿用）。

## 未决风险（主代理自陈，供补审者重点打）

1. **测试状态污染**：本轮两次误判都源于"用例没归零"（B0 已补归零断言，但同类风险仍在
   ——B4 只覆盖"零端点"，未覆盖"有端点但映射为空"）。
2. **`purge` 脚本只在 wendy1 上验过**：它识别到 3 处污染并改写正确，但没有第二类污染的样本
   来验证特征完备性。
3. **`.env` 删开关是本轮最"重"的动作**：它改变了"无上下文"的全局语义，影响面超出子账号模型
   这一件事，回归面（尤其研报任务全链路）本轮**未完整覆盖**。

---
---

# §2 第二轮 · 对话可中断（停止生成）与可修正（编辑重发）· BLOCKED

> SoT：`DESIGN_chat_interrupt_edit.md` · 自审：`VERIFICATION.md` §R2
> **独立审议同样未执行**（429，第 4 次尝试失败，证据见文件顶部注释）→ 自审的 7/7 PASS
> **独立性不足，不能替代 gate ③**。

## 本轮变更摘要（供补审者定位）

改动 10 个文件 + 3 个新脚本：

1. **`server/main.py`** — `_RequestContextMiddleware`：`BaseHTTPMiddleware` → **纯 ASGI 中间件**
2. `server/api.py` — `/chat` 断连探测 + 取消透传 + **中止即整轮零落库**；新增 `POST /chat/sessions/{id}/truncate`；响应加 `cancelled` / `user_message_id` / `assistant_message_id`；**`/chat` 归属校验改严格式**（`owner_id IS NULL` 由放行改为 fail closed）
3. `orchestrator.py` — `LoopCancelled`；`_run_fc_loop(cancel=)` 在「每轮 LLM 调用前」「每个工具执行前」两处检查
4. `chat_agent.py` — `step(cancel=)`；`LoopCancelled` 单独截住；**新增 `LLMError` 分支：上游故障如实上报**
5. 前端 `chatService.ts` / `chatSessionService.ts` / `ChatEntry.vue` — 停止按钮、编辑横幅、abort 分支、竞态提示
6. `scripts/verify_chat_interrupt_edit.py`（9 断言，含 A0 中间件护栏 / A6 无归属会话）、
   `scripts/probe_disconnect_middleware.py`（根因对照实验）、
   `scripts/backfill_null_session_owner.py`（存量无归属会话归位，幂等 + dry-run）

## 已由主代理真跑的证据（补审者**不要采信**，请自行复现）

- 根因对照实验：无中间件 → 感知断开；`BaseHTTPMiddleware` → 永远感知不到
- 真机日志：`检测到客户端断开 09:17:17,748` → `整轮不落库 09:17:20,040`（**2.3s**），该窗口零工具调用
- `verify_chat_interrupt_edit.py` → **9/9 PASS**（A0 中间件 / A1·A2 直连 / B1 经 nginx / A3 正常轮 / A4 截断+幂等 / A5 越权 / A6 无归属）
- 回归：`verify_subaccount_isolation.py` → **13/13 PASS**；`e2e_report_flow.py wendy` → 研报引擎全链路
- 存量数据：`chat_sessions` 中 `owner_id IS NULL` 由 1 个 → 0 个（回填后复核）

## 补审待办（配额恢复后按此清单审 — 重点关注「是不是还是假按钮」）

**A. 取消链路是否真的贯穿（最高优先）**

- [ ] **`BaseHTTPMiddleware` 只剩这一个实例吗？** 全仓扫 `BaseHTTPMiddleware` 与
      `@app.middleware("http")`（后者内部也是它）。任何一处都会让该路由的 `is_disconnected()`
      恒为 False。**建议补一条自动化断言**：启动后断言 `app.user_middleware` 里不含
      `BaseHTTPMiddleware` 派生类 —— 否则将来有人为了「加个 header」顺手加回去，停止按钮又静默变假。
- [ ] `cancel` 是否覆盖**所有**能烧 token / 能写库的路径：把 `cancel` 从 `api.chat` 到
      `chat_agent.step` 到 `_run_fc_loop` 逐行追踪；特别看 `step()` 在 `_run_fc_loop` **之后**
      的沉淀/学习分支（`propose_lesson`、写会话记忆）在取消时是否也被跳过。
- [ ] `asyncio.wait_for(..., timeout=300)` 超时路径与 `cancel` 路径会不会互相掩盖？
      超时时 `cancel_evt` 未置位，`result` 未赋值 → 会不会走到未定义变量 / 仍然落库？
- [ ] `to_thread` 里的同步线程在请求被取消后**不会被杀**，只是循环边界退出。确认
      「取消后线程仍会跑完当前那一轮」这一诚实边界在 UI / 文档里没有被吹掉。

**B. `truncate` 的安全性与语义**

- [ ] 越权断言是否**真的**证明是「挡了越权」而非「消息不存在」的巧合 404？
      （本轮已改为用仍存在的消息测，请复核 A5 的构造）
- [x] `owner_id` 为 NULL 的历史会话会不会被任意账号访问？ → **会的，已修**：
      `/chat` 用宽松式（NULL 即放行），库中确有 1 个这样 8 条消息的会话。
      已补 `scripts/backfill_null_session_owner.py` 归位 + `/chat` 改严格式 + A6 断言。
      **请复核**：① 回填是否真的把该会话交回主账号（不要只看我的复核输出）；
      ② 严格化后主账号还能正常打开那条会话；③ **全仓还有 3 处同类宽松写法**
      （`api.py` 的 task / rest 归属校验，本轮未动）—— 请判定它们各自是否也有真实可达的泄漏。
- [ ] 删除「该条及其之后」的排序依据 `created_at` 若有并列（同毫秒插入 user+assistant 很常见），
      顺序是否稳定？会不会删多/删少？
- [ ] 截断与「先删后发」之间不是原子的：删成功但新消息发送失败 → 用户丢了消息却没重新生成。
      是否需要补偿提示（当前文案是否已说清）？

**B+. A0 护栏本身**

- [ ] `verify_chat_interrupt_edit.py` 的 A0 只检查 `app.user_middleware`（应用级）。
      挂在 `APIRouter(dependencies=...)` 或 `app.add_middleware` 之外的 `ServerErrorMiddleware`
      / `ExceptionMiddleware` 是否也在扫描范围内？该断言是否可能被绕过（例如中间件在
      router 级注册、或 `dispatch` 里改用 `request.receive` 的封装）。

**C. 纯 ASGI 中间件替换的回归面**

- [ ] `server/admin.py` 约 50 个端点靠 ContextVar 读 `Authorization`。请**独立**用真 HTTP
      覆盖（不要只看 `verify_subaccount_isolation.py` 的 13 项），尤其：
      `/api/v1/admin/*` 写操作（PUT/DELETE）、WebSocket 路径（`scope["type"] == "websocket"` 走的是
      `if scope["type"] != "http"` 直通分支，确认它不会漏注入而影响 ws 内的鉴权）。
- [ ] contextvars 在 `await` 边界的传播与 reset：并发两个请求时会不会串号（A 请求读到 B 的 token）？
      建议并发压测 20 个不同账号请求交叉断言。

**D. `LLMError` 诚实分支**

- [ ] 新分支返回 **HTTP 200 + ⚠️ 文案**。这与 `ModelNotConfiguredError` 的 400 不一致 ——
      「上游 503」到底该 200 还是 5xx？前端 `ChatEntry.vue` 会不会把它渲染成正常回答？
      需确认 UI 上有明显区分（不要又是一个「看着正常、其实失败」）。
- [ ] `LLMError` 的 `str(e)` 直接进 UI，是否可能泄漏内部端点 / key 片段？

**E. 独立复现要求**

- [ ] 不要采信本节结论，请自行重跑：
      `docker compose exec -T -e PYTHONPATH=/app api python -u /app/scripts/probe_disconnect_middleware.py`
      以及 `.../verify_chat_interrupt_edit.py`，并**亲眼确认** A1/A2 的 100s 窗口与 B1 经的是 nginx。
- [ ] 本轮**未做**真实浏览器点击验证（A6/A7 未执行）。建议补审时用浏览器实测：
      wendy 发长问题 → 点「停止」→ loading 结束 + 文本回输入框 + DB 无新增；
      编辑一条历史消息 → 发送 → 旧消息及其后消失。
- [ ] 建议换基座模型审议（本项目实测：`Intern-S2-Preview-397B` 可信；`auto-fast` /
      `auto-reasoning` 曾出现无证据 PASS 与编造行号，勿用）。

**F. 引擎质量闸的波动（本轮跑回归时顺带发现，非本轮引入）**

- [ ] 同代码、同 prompt 跑 `e2e_report_flow.py wendy` 两次：一次 `escalated`（GateA 连续两轮
      rework → 触顶 `max_rounds=2`）产出 **0 字报告**，一次 `done`（round=1）产出 **5998 字**。
      历史上 4 次 run 的 GateA 判决都不同（见 `VERIFICATION.md` §R2-5）。请独立复跑数次，
      统计 escalate 发生率。
- [ ] 需要 boss 决策（**不要自行放宽**）：`max_rounds=2` 是否偏低？GateA 对
      「可信度均为 medium 且未说明」判 rework 是否过严？
- [ ] 更该修的是 **escalate 后的兜底交付**：现在直接 0 字，用户等 160s 什么也拿不到。
      是否应落一份「附质量警告的草稿」？请评估。
- [ ] `scripts/e2e_report_flow.py` **escalate 时仍 exit 0** → 该脚本作为回归门禁会漏报失败，
      必须修（终态非 done 就应非 0 退出），否则将来「回归全绿」是假的。

## 未决风险（主代理自陈，供补审者重点打）

1. **「停止」仍有一处不彻底**：在飞的那次 LLM 调用无法掐断（已如实写进设计与 UI，但仍是用户体验缺口）。
2. **前端零浏览器验证**：本轮前端只有 `vue-tsc` 通过 + 产物含新字符串两条证据，
   「按钮点得动、编辑横幅真的出现、竞态提示真的显示」**没有人点过**。
3. **中间件替换是本轮最"重"的动作**：它位于**所有**请求的必经路径上，风险面远超「停止/编辑」这一个功能。
   已跑两套回归（13/13 + 研报全链路），但 WebSocket 与并发串号**未覆盖**。
4. **两个轮次混在一个工作树里**，补审跨度大、易漏；若 boss 同意，**建议拆成两个 commit 分别补审**。


---

<!-- 合并自远端 origin/master（75b263a）：以下为同名文档的远端版本，整段保留以免信息丢失 -->

<!-- reviewed-by: independent-subagent -->
# Independent Code Review — LLM 输入预算闸 (NewApiLLMClient)

## Verdict
PASS_WITH_NOTES

The change is well-structured and achieves its stated goal: it turns `NewApiLLMClient`
into a generic OpenAI-compatible client with an input-token-budget gate, and the
self-heal retry is correctly ordered **before** the `_UNSUPPORTED_PARAM_RE` / `_is_non_retryable`
paths in **both** `complete()` and `complete_with_tools()`. Security is clean (no hardcoded
secrets), and `tools`/`tool_choice` are provably never dropped during self-heal. It was also
proven by a real e2e run. However, there is a genuine latent correctness bug in
`fit_input_budget` (orchestrator.py:141) that defeats the budget gate in the
empty-`system` path and that the self-heal cannot recover from. It is latent only because
every current call site passes a non-empty `system`; it should be fixed before merge.

## Findings

### F1 — `fit_input_budget` user-truncation guard is wrong for empty/`sys_t==0` system (orchestrator.py:140-142)
`usr_budget = max(0, max_input_tokens - sys_t)` then the truncation is gated by
`if usr_budget < max_input_tokens:` (line 141). Since `usr_budget == max_input_tokens - sys_t`
whenever `sys_t <= max_input_tokens`, this condition reduces to `sys_t > 0`.
- When `system` is empty (`estimate_tokens("") == 0`, see orchestrator.py:109-110), `usr_budget == max_input_tokens`, so `usr_budget < max_input_tokens` is **False** and the oversized `user` is **never truncated**, even though `sys_t + usr_t > max_input_tokens` (early-return at line 138 was skipped).
- Worse, the self-heal cannot recover: halving `factor` (lines 580-581 / 642-643) changes `cur_max` but never changes `sys_t == 0`, so `usr_budget` and the guard stay identical on every retry. The 400 self-heal loops until `factor` floors at `0.125` and then exhausts retries → permanent `LLMError` instead of a truncated success.
- The intended comment ("system 未占满，裁 user") is also misleading: the real condition should be "trim user when the user itself exceeds its share", i.e. `if estimate_tokens(user) > usr_budget:`. As written, the branch is effectively dead for `sys_t == 0`.
- Impact today: all real call sites (orchestrator.py:1272, 1643, 1825, 2011, etc.) and the e2e script (verify_llm_openai_e2e.py:71,86) pass a non-empty `system`, so the bug is **latent**, not exercised by the verified path. But it silently breaks the gate for any empty-system caller and for `complete_with_tools` if ever called with an empty system.
- Suggested fix: replace the guard at line 141 with `if estimate_tokens(user) > usr_budget:` (and keep `usr_budget = max(0, max_input_tokens - sys_t)`).

### F2 — Negative budget when `max_input_tokens - reserved_output_tokens <= 0` (orchestrator.py:559 / 623, 140)
`fit_input_budget` is called with `cur_max - self._reserved_output_tokens` (default reserved = 4096).
If `LLM_MAX_INPUT_TOKENS` or the constructor `max_input_tokens` is set below `reserved_output_tokens`
(e.g. a tiny-context model, or `max_input_tokens < 4096`), the budget argument becomes `<= 0`.
Then `usr_budget = max(0, <=0 - sys_t) = 0` and the line-141 guard `0 < (<=0)` is **False**, so user is
never truncated; only `system` is clamped to 1 token (line 144-146). The oversized `user` is sent verbatim → 400 not prevented. This is the same root cause as F1 (the guard fails for non-positive budgets). Default config (128000) is safe; flag for small-model configurations.

### F3 — Documented factor floor `0.125` (1/8) is never actually reached as a request (orchestrator.py:556-585 / 620-647)
`for attempt in range(self._max_retries + 1)` with the default `max_retries=2` yields **3** attempts.
Each 400 self-heal does `factor *= 0.5; continue`, producing request factors `1.0 → 0.5 → 0.25`.
The final `factor = 0.125` is set on the last iteration's `continue` but the loop then ends, so a request
at `1/8` never executes. The comments at lines 578-579 / 642-643 promise "最低 0.125 / 1/8", which is not
honored. Not blocking (the primary gate is `fit_input_budget` truncation, and halving twice to 1/4 is already
extreme), but the stated guarantee is false. Either raise `max_retries` for the self-heal or decouple
halving iterations from the retry count.

## Notes (non-blocking)

- **Security — clean.** No hardcoded API keys/secrets in the diff or in `scripts/verify_llm_openai_e2e.py`.
  `_max_input_tokens = int(os.getenv("LLM_MAX_INPUT_TOKENS", max_input_tokens))` (orchestrator.py:457) reads
  from env; `api_key` is injected via `cfg["api_key"]` from `.env` (gitignored). The verify script reads
  `NEWAPI_API_KEY` from env or the gitignored `.env` (verify_llm_openai_e2e.py:28-39). Good.
- **Ordering — correct.** In `complete()` the input-too-long self-heal (lines 580-585) precedes
  `_UNSUPPORTED_PARAM_RE` (588-597) and `_is_non_retryable` (599-600). In `complete_with_tools()` the same
  order holds (642-647 before 650-658 and 659-660). No dead-code risk. Good.
- **tools/tool_choice preserved — correct.** `complete_with_tools()` keeps `tools`/`tool_choice` in `body`
  (lines 615-616) and `params` is rebuilt from `body` each iteration (line 624); the self-heal only mutates
  `factor` and `continue`s. No code path drops tools. The existing test
  `tests/test_llm_drop_params.py:161-163` confirms tools survive all retries. Good.
- **Return values used — correct.** `sys_t, usr_t = fit_input_budget(...)` (lines 559 / 623) and the
  truncated values are sent in the messages (lines 565-566 / 629-630); the originals `system`/`user` are
  never sent. No ignored return values. Good.
- **Truncation direction — correct.** `_trunc_keep_tail` returns `text[-keep:]` (tail kept, head dropped,
  orchestrator.py:127) — matches the requirement. `system` truncation keeps the head (line 145). Correct.
- **`_trunc_keep_tail` is length-proportional, not token-proportional** (orchestrator.py:125-127). For text
  whose tail is denser in tokens than its head (e.g. CJK-heavy tail), the kept tail may still exceed
  `max_tokens`. This is acceptable because `complete()`'s input-too-long self-heal is the backstop — but note
  the backstop only works when F1 is fixed for the empty-system case.
- **`estimate_tokens` `+1` bias** (orchestrator.py:115) slightly over-estimates; harmless for budgeting
  (conservative) but can over-trim by ~1 token at exact boundaries. Minor.
- **`_INPUT_TOO_LONG_RE` over-broadness** (orchestrator.py:151-155): patterns like `token.*exceed` /
  `exceed.*context` could match non-input errors (e.g. `"token quota exceeded"` rate-limit 429), causing a
  spurious factor-halving retry. Low impact (worst case wastes one retry before falling through to
  `_is_non_retryable`). Consider tightening the patterns to input-context-specific phrases.
- **`system` overflow discards user** (orchestrator.py:143-146): when `system` alone exceeds the whole budget,
  both branches fire and `user` is truncated to the empty marker while `system` is also cut. Ideally only
  `system` would be trimmed in that case, but it still fits the budget and avoids 400 — acceptable.

## Post-review fixes applied (by main agent, after verdict)
Verdict `PASS_WITH_NOTES` stands; the three findings were addressed and re-verified (not re-signed here).

- **F1 fixed**: `fit_input_budget` user-truncation guard changed from `if usr_budget < max_input_tokens:` to
  `if usr_t > usr_budget:` (orchestrator.py:141). Now truncates user whenever the user itself exceeds its
  share, regardless of whether `system` is empty. Re-verified by new e2e case **[3b]**: empty `system` +
  110001-token oversized `user` → truncated to ~3908 tokens, returns 188 chars, **zero 400** (previously this
  path would 400-loop until retries exhausted).
- **F2 fixed**: budget passed to `fit_input_budget` is now clamped — `budget = max(1, cur_max - self._reserved_output_tokens)`
  in both `complete()` (line ~559) and `complete_with_tools()` (line ~623). Non-positive budget can no longer
  slip through the guard.
- **F3 fixed**: loop bound changed to `for attempt in range(max(self._max_retries + 1, 4))` in both methods, so
  the documented factor floor `0.125` (1/8) is actually issued (≥4 attempts). Comments updated to match.

Re-verification: `py_compile orchestrator.py` rc=0; `scripts/verify_llm_openai_e2e.py` all four checks green
(incl. new [3b]); concrete model `LongCat-2.0` returns real 66-char answer confirming live token output.
No further blocking issues. `<!-- reviewed-by: independent-subagent -->` unchanged.