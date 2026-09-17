# DESIGN · 对话优先 ChatAgent（取代一次性表单）

> 状态：草案 v0.1（待 boss 审阅）
> 范围：把现有「/新任务」页面从「填主题→选插件→生成」表单，升级为「正常 AI 对话 + 按需调用研报技能」的 ChatAgent 范式（对齐 Accio Work）

---

## 一、目标 & 现状

**现状问题**
- `/新任务` 页本质是「表单先」：填主题、选插件、点「生成研报」直接进 LangGraph 任务流水线。
- 用户打「你是谁」「能干嘛」这类闲聊，被当成研报主题跑任务。
- 与 Accio Work（聊天优先的 Agent）体验差距大。

**目标**
- 入口页面 = 真·聊天：用户跟「研报助手」自然对话。
- 研报生成 = ChatAgent 可调用的一个技能（tool/skill），不是默认主流程。
- 闲聊、技能调用、报告生成都在同一个对话流里。

---

## 二、架构总览

```
用户 ──→ 聊天 UI（消息气泡 + 输入框）
            │
            │ POST /api/v1/chat {session_id, message}
            ▼
        ChatAgent ──→ LLM（chat 模型）
            │
            ├── tool: web_search（数据源检索）
            ├── tool: fetch_url（取指定 URL 摘要，可选）
            └── tool: generate_report ← 触发现有 LangGraph 引擎
                    │
                    ▼
              run_report(user_task, ...) → task_id → 轮询/WS → 最终 markdown
```

**关键点**
- ChatAgent 是新模块，跟现有 `orchestrator.py` 的多智能体是**两个独立系统**：
  - ChatAgent：对话 + 工具调度，单 LLM，工具可以是数据源检索、报告生成。
  - 现有 LangGraph 引擎：报告生成技能的实现，**降级为 ChatAgent 的一个工具**。
- 两个引擎共用同一份 LLM 客户端、`config/models.yaml`、`config/plugins.yaml`，但 prompt/role 不同。

---

## 三、模块设计

### 3.1 后端：`chat_agent.py`（新）

```python
# 伪代码
class ChatAgent:
    def __init__(self, llm: LLMClient, tool_registry: ToolRegistry): ...

    async def step(self, session: ChatSession, user_msg: str) -> AsyncIterator[ChatEvent]:
        # 1. 把 user_msg append 到 session.history
        # 2. 调 LLM，prompt = 系统提示 + 历史 + 工具描述
        # 3. 流式 yield 文本片段 / tool_call / tool_result 事件
        # 4. 若 LLM 返回 tool_call → 执行工具 → 把工具结果追加到 history → 再调 LLM
        # 5. 直到 LLM 返回 final 文本（或达到 tool 深度上限）
```

**系统提示词骨架**（待润色）
> 你是「研报助手」，一个能聊天、能调工具的 AI 助手。
> - 用户问你是谁 / 你能干嘛 / 闲聊 → 自然回答
> - 用户想研究某行业/公司/技术 → 先用 web_search 检索，再决定是否生成完整研报
> - 用户明确要求"生成报告/出一份研报/详细分析" → 调用 generate_report 工具，传入主题与必要参数
> - 不要主动生成长篇报告，除非用户明确要

**事件流（WS 推送）**
- `chat_token` — 流式文本片段
- `chat_tool_call` — ChatAgent 决定调哪个工具
- `chat_tool_result` — 工具返回（成功/失败）
- `chat_done` — 一轮对话结束

### 3.2 工具契约

```python
# tools/chat_tools.py
class WebSearchTool:
    name = "web_search"
    description = "按关键词检索数据源，返回摘要列表（来源 title/url/snippet/source/source_id）"
    args_schema = {"query": str, "limit": int = 5, "plugins": list[str] = None}

class GenerateReportTool:
    name = "generate_report"
    description = "调用多智能体系统生成结构化研报，阻塞到返回 markdown 全文（数分钟）"
    args_schema = {
        "topic": str,
        "scope": list[str] = [],          # 行业概况/竞争格局/关键风险/...
        "constraints": list[str] = [],
        "output_format_spec": str = "",
        "plugins": list[str] = None,      # 数据源白名单；None = 用模型默认
        "agents": list[str] = None,
        "model": str = None,
        "gate_model": str = None,
    }
    async def run(self, args) -> str:
        # 1. POST /tasks → 拿 task_id
        # 2. 同步等任务到 done/escalated（最长 30 分钟）
        # 3. 返回 {"task_id": ..., "status": ..., "report_markdown": ..., "escalate_reason": ...}
```

### 3.3 前端：`ChatEntry.vue` 改造

**改造后结构**

```vue
<div class="chat-shell">
  <!-- 顶部精简头：agent 头像 + 切换智能体 -->
  <div class="chat-head">...</div>

  <!-- 消息流 -->
  <div class="chat-stream" ref="stream">
    <Bubble v-for="msg in messages" :msg="msg" />
  </div>

  <!-- 输入框（对话工具栏） -->
  <div class="chat-composer">
    <el-input placeholder="跟研报助手聊聊…" />
    <button class="send-btn">⏎</button>
  </div>
</div>
```

**消息类型**
- `user` — 用户气泡（右对齐）
- `assistant` — AI 文本流（左对齐）
- `tool_call` — 「🔍 正在检索… / 📄 正在生成报告…」中间卡
- `tool_result` — 报告生成完成后展示卡片：标题 + 状态 + 「查看详情」按钮 → 跳 `/tasks/{id}`

### 3.4 持久化

**新表 `chat_sessions`（Postgres）**
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | 主键 |
| user_id | str | 暂时本地部署用 `local` |
| title | str | 自动从首条用户消息提取 |
| created_at | timestamp | |
| updated_at | timestamp | |

**新表 `chat_messages`**
| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | |
| session_id | uuid FK | |
| role | str | `user` / `assistant` / `tool_call` / `tool_result` |
| content | text | |
| tool_name | str | 工具名（仅 tool 类型有） |
| tool_args | jsonb | 工具参数 |
| tool_result | jsonb | 工具结果 |
| created_at | timestamp | |

**API**
- `POST /api/v1/chat/sessions` — 新建会话
- `GET /api/v1/chat/sessions` — 列表
- `GET /api/v1/chat/sessions/{id}/messages` — 历史消息
- `WS /api/v1/chat/sessions/{id}/stream` — 流式对话（双向）

---

## 四、与现有系统的对接

| 现状 | 改动后 |
|---|---|
| `/`（ChatEntry）= 表单，直接进 LangGraph | `/` = 聊天入口，ChatAgent 调度 |
| `/tasks/{id}`（TaskTracking）= 唯一任务视图 | **保留**：作为 `generate_report` 工具完成后的「查看详情」目标 |
| `/templates`、`/agents`、`/market` | **保留**：能力市场入口，ChatAgent 也可读这些配置 |

`generate_report` 工具复用 `server/api.py` 的 `POST /api/v1/tasks` 入口（不重写 run_report），保证多智能体引擎逻辑零改动。

---

## 五、分阶段交付

### 阶段 1（最小可跑版本）— **先出，不重写**
1. `chat_agent.py`：单轮 ChatAgent，调 LLM，工具仅 `generate_report`（无 web_search）。
2. `POST /api/v1/chat` 同步端点（不流式，先跑通）。
3. `ChatEntry.vue` 改成消息流 + 输入框；点发送调 `/chat` 拿回复。
4. ChatAgent 收到用户「生成关于 X 的报告」→ 调 generate_report → 阻塞拿 markdown → 把 markdown 当 assistant 消息返回。
5. 闲聊识别（关键词 + 简单 prompt）→ 不调工具，直接回答。

### 阶段 2（流式 + web_search 工具）
1. ChatAgent 流式输出（`chat_token` 事件）。
2. 增加 `web_search` 工具。
3. WS 双向协议。

### 阶段 3（持久化 + 历史会话）
1. Postgres 表 + 列表 API。
3. UI 加左侧聊天会话列表（类似 ChatGPT）。

---

## 六、不在本次范围

- 多模态（图片/PDF 输入）
- 语音输入
- 多用户/权限
- ChatAgent 自定义 prompt 模板化（先用硬编码）
- 取消在跑的 LangGraph 任务（沿用现有 `/tasks/{id}/review` 流程）

---

## 七、待 boss 决策

1. **阶段 1 是不是先做？**（确认后我写 `VERIFICATION_chat_first.md` + `REVIEW_chat_first.md`，走三道闸）
2. **数据源接入 ChatAgent 的 web_search 工具**：直接复用现有 `tools.data_sources.build_search_tool`？还是新写一份给 chat 用？
3. **会话持久化**：阶段 1 先内存掉电不存，阶段 3 再上 Postgres？还是有别的优先级？
4. **左导航的「新任务」入口**：阶段 1 改成默认进入聊天？还是保留入口到原表单？