# VERIFICATION_chat_first.md — 对话优先入口 · 阶段 1

> 关联设计：`DESIGN_chat_first.md`
> 时间：2026-09-11 17:49 +08
> 验证人：主代理自审（独立审议见 `REVIEW_chat_first.md`）
> 模式：仅本地验证；未推送。

---

## ✅ 一图看懂交付了什么

```
用户消息 ──► ChatAgent.step() ──► {intent: chat|generate_report, reply, report?}
                                       │                        │
                                       │                        └─► /api/v1/tasks (复用 create_task)
                                       └─► 直接返回 reply         └─► task_id 回前端，跳 /tasks/{id}
```

- **后端新增**：`chat_agent.py` + `server/api.py` 末尾 `/chat` 路由。
- **前端改造**：`web/src/views/ChatEntry.vue` 重写为「消息气泡 + 底部输入框」聊天界面，`web/src/services/chatService.ts` 提供 SDK。
- **不重写引擎**：现有 `run_report` / `create_task` 100% 复用。

---

## 二、闸① 编译 / 类型检查（必须全绿）

| 项 | 命令 | 结果 |
|---|---|---|
| Python 语法 | `python -m py_compile server/api.py chat_agent.py` | ✅ exit 0 |
| 前端类型 | `node vue-tsc -b` | ✅ exit 0 |
| 前端构建 | `node vite build` | ✅ exit 0，1.6 MB JS（element-plus 占大头） |

---

## 三、闸② 端到端行为验证（实跑，非纸面）

### T1 · 闲聊不触发任务（最重要！这是 Boss 的痛点）

```bash
curl -X POST http://localhost:18080/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"你是谁","history":[],"model":"auto-chat"}'
```

**实测响应**：
```json
{
  "reply": "你好！我是「研报助手」，可以帮你生成结构化的研报、分析报告。需要的话可以直接告诉我想了解的主题，我会为你写一份完整的报告。",
  "intent": "chat",
  "task_id": null,
  "task_status": null
}
```
- ✅ intent = chat
- ✅ task_id = null（**没有启动任务**）
- ✅ reply 是真人话，不是「正在生成报告…」

### T2 · 明确研报请求触发任务

```bash
curl -X POST http://localhost:18080/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"帮我生成一份关于跨境电商物流现状的报告","history":[],"model":"auto-chat"}'
```

**实测响应**：
```json
{
  "reply": "好的，我将为您生成一份关于跨境电商物流现状的报告。",
  "intent": "generate_report",
  "task_id": "ed04f1d3-3aa2-4f1d-877a-f39117cfd459",
  "task_status": "running"
}
```
- ✅ intent = generate_report
- ✅ task_id 是真实 UUID（通过复用 create_task 进入现有 LangGraph 管道）
- ✅ task_status = running

### T3 · 任务真实在跑（不被假启动糊弄）

```bash
curl http://localhost:18080/api/v1/tasks/{id}
```
返回 `"status": "running"`，且 8 秒后状态仍为 running（说明后台引擎进程真的跑起来了）。

---

## 四、契约 / 边界检查

### 4.1 JSON 解析健壮性

`chat_agent._safe_parse()` 防御：
- ✅ LLM 在 ```json``` 包裹 → 自动剥
- ✅ 截断的 JSON → 降级 `intent=chat, reply=raw_text`
- ✅ 非法 JSON → 同上 + logger.warning
- ✅ `topic < 4 字` → 强制降级 chat 并要求澄清
- ✅ 缺 reply → 兜底"好的。"

### 4.2 LLM 失败不挂服务

```bash
curl -X POST .../chat -d '{"message":"你是谁"}'  # 缺 model 字段
# → 200 OK + {intent:chat, reply:"抱歉，脑子打结了：LLM 调用失败..."}
```

- ✅ 不抛 500，回复友好提示
- ✅ 用户能继续对话
- ✅ 不偷跑任务

### 4.3 任务创建失败不挡回复

`_get_chat_agent()` 抛异常 → 端点返 500（致命错误必须暴露）；
**但** ChatAgent 内部决定 generate_report 而 create_task 失败时 → 降级为「回复里附提示」，不让任务创建失败掩盖对话。

---

## 五、前端 UI 改造点（摘要）

| 文件 | 改动 |
|---|---|
| `web/src/services/chatService.ts` | 新增 chat() + TS 类型 |
| `web/src/views/ChatEntry.vue` | 完全重写：消息气泡 + 报告卡片 + 场景 chips + 切换智能体 |
| `web/src/utils/chatEntry.ts` | 复用现成 KNOWN_AGENTS / CHAT_PRESETS（未改） |
| `web/src/utils/emoji.ts` | 复用现成 agentGlyph / pluginGlyph（未改） |

UI 关键约束（来自 Boss 要求）：
- ✅ 顶部：Agent 头像/名 + 「切换智能体」+「插件/模型」按钮
- ✅ 中部：消息流（user 蓝绿渐变气泡；assistant 浅灰气泡 + 报告卡片）
- ✅ 底部：输入框 + 「场景建议」chips（点击填入输入框，非自动启动）

---

## 六、与设计文档的一致性

| 设计条款 | 落地位置 | 状态 |
|---|---|---|
| ChatAgent 不重写引擎 | `api.py` 仅复用 `create_task` | ✅ |
| 阶段 1 同步、无流式 | `agent.step()` 同步调用 `llm.complete()` | ✅ |
| 阶段 1 不持久化 | 全在请求体内，无 DB 写入 | ✅ |
| 工具契约 JSON 严格 | `_safe_parse()` 强制只吃 JSON 字段 | ✅ |
| 三阶段渐进 | 本次只交阶段 1；阶段 2（流式）/ 3（持久化）未动 | ✅ |

---

## 七、已知留待阶段 2/3

- **流式**：当前 /chat 是非流式；LLM 思考时前端只显示「思考中…」点阵。阶段 2 上 SSE。
- **web_search 工具**：阶段 1 ChatAgent 没有 web_search 工具（工具调用是阶段 2 起步）。
- **持久化**：历史消息目前只存在前端 ref + 请求透传，不存库。阶段 3 才上 Postgres。
- **「快速通道」**：旧「新任务」表单页（`/new-task`）未下架，作为 high-power 用户直跳；阶段 3 决策保留或去掉。

---

## 八、收口判断

**VERIFICATION = PASS**：行为契约、编译、UI 关键路径全部实测通过，未发现阻断性问题。
本文件为自审（主代理），**independent-subagent 审议见 REVIEW_chat_first.md**，未得 reviewer PASS 不得 commit。

—— 2026-09-11 17:49 +08 · 主代理
