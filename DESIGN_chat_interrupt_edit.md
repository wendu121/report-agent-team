# DESIGN · 对话可中断与可修正（停止生成 / 编辑重发）

> 状态：**v1.0 已定稿**（2026-09-19）
> 触发：boss 实测「我发送以后我发现我打错了字，但是我暂停不了，更改不了」
> 关联：`DESIGN_subaccount_model_isolation.md`（同一入口的上一轮）、`API_SPEC.md`（/chat 契约）

---

## 1. 问题（boss 的原始诉求）

截图实证：boss 在子账号 `wendy1` 的对话页发出
「你帮我**谢**一份关于2026年的外卖行业的报告」（`谢` 是 `写` 的错字），
页面停在 **「思考中…」**，他既**停不下来**，也**改不了**那个错字。

两个诉求：
- **P1 能停**：正在生成时能中止。
- **P2 能改**：发错了能改内容重发。

---

## 2. 实测约束（先证伪，再设计）

设计前用探针 `scripts/probe_abort_persistence.py` 在真机（容器内）测了三件事。
**结论直接推翻了「前端点个取消就行」的直觉**：

| 探针问题 | 实测结果 | 推论 |
|---|---|---|
| 客户端硬断开（RST）后，服务端 agent 还跑吗？ | **照跑完**（2s 断开 → 35s 仍在跑并结束） | 前端 abort **不影响服务端**；纯前端「停止」= **假按钮**（模型照样烧 token） |
| 跑完的消息会落库吗？ | **会**（user 35字 + assistant 2898字 都写进 `chat_messages`） | 停止后 UI 与 DB **必然不一致**；重载会话会「复活」用户以为已取消的那轮 |
| 只靠前端编辑历史消息可行吗？ | 不可行 | 打错的那条**已经在库里**，只改 UI 等于**历史里永远留着错字**，下轮对话模型仍会看到它 → 假修正 |

**结论：P1 与 P2 都必须服务端协作才不是假的。**

另有一条实测（同批）：`/chat` 的 `agent.step` 跑在 `asyncio.to_thread` 里，
进程内可访问到请求对象 → 可以用 Starlette `request.is_disconnected()` **真实探测**
浏览器断开（AbortController / 关标签页 / 断网都能覆盖），**不需要**新造一个
`/cancel` 端点，也不需要跨进程的取消信号（部署是单 worker，见 Dockerfile CMD）。

---

## 3. 方案

### 3.1 P1 停止生成 —— 断连驱动的协作式取消

```
浏览器 AbortController.abort()
        │
        ├─► 前端：立即结束 loading、把输入内容放回输入框
        │
        └─► 服务端：/chat 的 watcher 协程轮询 request.is_disconnected()
                     → set(cancel_event)
                     → agent.step 在工具循环的**每轮边界**检查该事件
                     → 抛 LoopCancelled → 停止后续 LLM 调用与工具执行
                     → **不落库、不写会话记忆**
```

- **取消语义 = 整轮零落盘**。用户消息也不存：否则会留下一条孤立的用户消息，
  重载会话时「幽灵复现」，且错字永久污染后续 history。
- **能停到哪一步（诚实边界）**：已经在飞的**那一次** LLM HTTP 调用无法掐断
  （OpenAI SDK 同步调用不可中断），会在循环边界处被丢弃。
  收益是**不再继续**后续轮次与工具调用（原循环上限 `MCP_MAX_ROUNDS = 4`）。
  这一点必须在 UI 文案上说清，不能吹成「毫秒级中断」。
- **可观测**：取消时记 `logger.info`（含 task/session/已完成轮数），
  便于事后判断「是真取消还是超时」。

### 3.2 P2 编辑重发 —— 服务端截断 + 前端回填

- 前端：user 气泡 hover → **编辑**；点击进入「编辑态」（横幅提示 + 取消按钮），
  文本回填输入框。
- 发送时：**先删后发**。调用截断端点删除该条**及其之后**的所有消息，
  成功后才发新请求 —— 保证「DB 里的历史」与「页面上的历史」始终一致。
- **不许静默降级**：截断失败（404/网络）→ **不发新消息**，明确报错。
  否则就是「看着改了，其实没改」，属于假修正。
- 消息 id 来源：
  - 历史加载：`GET /chat/sessions/{id}` 已返回 `messages[].id`
  - 本次会话新发的：`/chat` 响应新增 `user_message_id` / `assistant_message_id`
  - 两者都没有（= 该条从未落库，例如持久化降级或已取消的一轮）→ 本地删除即正确，
    无需调服务端。

### 3.3 契约增量（向后兼容，只增字段/端点）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 响应新增 `cancelled: bool`；新增 `user_message_id` / `assistant_message_id`（可空） |
| POST | `/chat/sessions/{session_id}/truncate` | body `{from_message_id}`；删除该消息及其之后的所有消息；返回 `{ok, deleted}`；越权/不存在 → 404 |

---

## 4. 验收（真跑，不接受纸面）

| # | 断言 | 手段 |
|---|---|---|
| A1 | 中途断开后，服务端在**数秒内**停止（明显早于自然完成的 30s+） | 探针 + 容器日志时间戳 |
| A2 | 断开的那一轮 `chat_messages` **零新增** | 直查 DB |
| A3 | 正常（不中断）一轮仍照常落库 —— **不能为了能停把正常路径弄坏** | 真 HTTP |
| A4 | `truncate` 删掉指定条及其之后，返回 deleted 数；幂等复跑不误删 | 真 HTTP + DB 计数 |
| A5 | 越权：用 A 账号 truncate B 账号的会话 → 404 | 真 HTTP |
| A6 | 浏览器实测：`wendy1` 发长问题 → 点「停止」→ loading 结束、文本回输入框、DB 无新增 | 真实浏览器 |
| A7 | 浏览器实测：编辑一条历史消息 → 发送 → 旧消息及其后消失、新消息按新内容生成 | 真实浏览器 |

---

## 5. 不做（明确划界，避免过度工程）

- 不做 token 级流式输出（流式是另一个议题，与「能不能停」正交）。
- 不做「部分回答保留」（非流式下没有可保留的中间态）。
- 不做服务端取消队列/Redis 广播（单 worker 部署，`is_disconnected` 已够；
  将来真上多 worker 必须重做，届时以此文为准据）。

---

## 6. v1.1 修正：验证阶段挖出的真根因（BaseHTTPMiddleware 吞断连）

> 时间：2026-09-19 · 触发：A1/A2 首轮真机验证 **FAIL**（DB 在 78s 出现了 2 条）

**现象**：按 §3.1 实现完并部署后，RST 断开 3 秒，服务端日志**没有**「检测到客户端断开」，
工具循环继续跑到第二、三轮，最终整轮照常落库 —— 与修复前基线完全一致。

**排查**（`scripts/probe_disconnect_middleware.py`，同进程内起两个**除中间件外完全相同**的
app，用同一种裸 socket RST 断开，做对照）：

| 配置 | 服务端感知断开 | handler 被取消 |
|---|---|---|
| 无中间件 | ✅ `DISCONNECT_DETECTED` | ❌ |
| 套 `BaseHTTPMiddleware` | ❌ **永远感知不到** | ❌ |

**根因**：`server/main.py` 的 `_RequestContextMiddleware` 继承 `BaseHTTPMiddleware`。
该类会在 app 外层再包一层 receive/send（内存流），`http.disconnect` 被它自己消费，
于是端点里 `await request.is_disconnected()` **永远返回 False**。
`BaseHTTPMiddleware` 是「请求上下文 / 加 header」这类中间件最顺手的写法，
也是**静默废掉断连探测的经典陷阱** —— 它不会报错、不会告警，只会让你的「停止」变成假按钮。

**修正**：改成**纯 ASGI 中间件**（实现 `__call__(scope, receive, send)`，把 `receive` 原样透传），
功能完全等价（只为读 `Authorization` 头注入 ContextVar，不消费 body），
断连探测随即恢复。已在 import 期断言 `BaseHTTPMiddleware not in MRO`。

**教训**：① 「中间件不报错」≠「中间件无害」，能用纯 ASGI 就别用 `BaseHTTPMiddleware`；
② 验收必须走**浏览器真实链路**（`web:80` → nginx → api），只测 `api:8000` 直连会漏掉反代这一层
（故补 B1）；③ 测试自身也会错 —— A4 首版把「截断后剩 0 条」写死，在 A1/A2 泄漏时会**级联假失败**，
掩盖「截断其实是对的」，已改为断言 `剩余 == 截断前 - deleted`。
