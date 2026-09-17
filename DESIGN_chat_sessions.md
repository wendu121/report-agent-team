# DESIGN_chat_sessions.md — 聊天会话 + 跨会话记忆

> 版本：v1.0 | 状态：实现中 | 对齐 M10 chat_memory

## 1. 需求

Boss 要求：
1. **聊天记录**：每次聊天是一个独立会话，可在左侧栏浏览、点进去恢复
2. **记忆**：跨会话记住上下文（之前聊过的主题/结论，新会话可延续）
3. 插件归插件、模型归模型、自定义 API 归设置（已在上批完成）

## 2. 后端设计

### 2.1 数据库模型（server/models.py 新增）

**`ChatSession`** — 会话主表

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| topic | String(200) | 会话主题（首条用户消息自动生成） |
| model | String(100) | 当时选中模型 |
| agents | String(500) | 逗号分隔选中 agents |
| plugins | String(500) | 逗号分隔选中 plugins |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

**`ChatMessage`** — 消息表

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| session_id | String(36) FK | 关联 session |
| role | String(20) | user/assistant |
| content | Text | 消息内容 |
| task_id | String(36) | 关联研报任务（可空） |
| task_status | String(50) | 任务状态 |
| created_at | DateTime | 创建时间 |

**`ChatSessionMemory`** — 跨会话记忆

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| session_id | String(36) FK | 源 session |
| key | String(100) | 记忆键（如 topic/conclusion/entities） |
| value | Text | 记忆值 |
| created_at | DateTime | 记忆创建时间 |

### 2.2 新 API 端点（server/api.py）

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| GET | `/api/v1/chat/sessions` | — | 列表（按 updated_at 降序，limit=50） |
| POST | `/api/v1/chat/sessions` | {topic?, model, agents?, plugins?} | 新建 session，返回 id |
| GET | `/api/v1/chat/sessions/{id}` | — | 详情 + 消息列表 |
| DELETE | `/api/v1/chat/sessions/{id}` | — | 删除 session + 消息 |
| PUT | `/api/v1/chat/sessions/{id}` | {topic} | 更新主题 |
| GET | `/api/v1/chat/sessions/{id}/memory` | — | 获取记忆键值对 |
| POST | `/api/v1/chat/sessions/{id}/memory` | {key, value} | 写入记忆 |

### 2.3 /chat 端点变更

- 请求新增 `session_id` 字段（可选）
- 无 session_id → 自动新建 session，返回 `session_id`
- 有 session_id → 从 DB 读历史消息拼成 history，追加新消息
- 响应新增 `session_id` 字段
- 消息结束后异步提取记忆（基于 assistant 最后一条回复的关键词/实体）

## 3. 前端设计

### 3.1 左侧栏（DefaultLayout.vue）

在「历史记录」区下方新增「聊天记录」折叠区：
- 列表项：主题 + 时间 + 模型标签
- 点击 → 路由到 `/chat/{sessionId}`
- 空状态提示

### 3.2 路由

新增 `/chat/:sessionId` 路由，ChatEntry 组件复用

### 3.3 Store（web/src/stores/chatSession.ts）

- `currentSessionId` / `sessions` / `messages`
- `loadSessions()`, `loadSession(id)`, `createSession()`, `deleteSession(id)`
- 记忆：`getMemory(sessionId)`, `setMemory(sessionId, key, value)`

### 3.4 ChatEntry.vue 改造

- 从路由 params 读 session_id → 加载历史消息
- onSend 携带 session_id
- 无 session 时自动创建
- 记忆面板（可选）：展示当前 session 的记忆标签

## 4. 跨会话记忆策略

**轻量方案**（先做）：
- 每次 session 结束后，从 assistant 最后一条回复中提取 3-5 个关键词/实体
- 存入 `ChatSessionMemory`，key=`entities`，value=JSON 数组
- 新 session 创建时，检索最近 N 个 session 的 memory，拼入 system prompt

**后续可做**：RAG 向量检索 + 摘要索引，本次不做。

## 5. 里程碑与闸

| 阶段 | 内容 | 闸 |
|---|---|---|
| A | 数据库模型 + CRUD API | py_compile + curl |
| B | /chat 集成 session | e2e curl |
| C | 前端左侧栏 + 路由 + store | vue-tsc + vite build |
| D | 跨会话记忆提取 + 注入 | e2e curl + UI |
| E | 独立审议 | REVIEW 三道闸 |