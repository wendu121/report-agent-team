# REVIEW_chat_first.md — 对话优先入口 · 阶段 1

> ⚠️ **本里程碑第 N 轮 BLOCKED（独立审议未执行，原因：子代理 Agent 调用连续 2 次超时，未产出 review 文档）**
> 保留历史真实状态：本文件不构成"已审通过"的结论。
> 主代理**未自签** `<!-- reviewed-by: independent-subagent -->`，**禁止**在未获独立子代理 PASS 前 commit。

---

> reviewed-by: independent-subagent  ← (待补，未签)
> reviewer-model: (待定，建议 Intern-S2-Preview-397B)
> date: (待补)
> verdict: **BLOCKED** ← 强制置位，禁止改写

## 结论（一句话）

**本里程碑当前不能 commit**——独立审议子代理连续 2 次启动后"对话超时，请重试"，主代理按 edict-gate 硬约定**严禁自审自签顶替**。需重试独立审议或等配额/调度恢复后补审。

## 严重程度

- CRITICAL: —
- MAJOR: —
- MINOR: —
- NIT: —

（**全部待补**，因独立审议未执行）

## 详细发现（按文件）

（**待补**，独立审议未执行 → 无独立证据）

### chat_agent.py
- (待补)

### server/api.py (/chat section)
- (待补)

### web/src/services/chatService.ts
- (待补)

### web/src/views/ChatEntry.vue
- (待补)

## 与 VERIFICATION_chat_first.md 的一致性

(待补——独立审议未执行，无法独立校验)

## 关键独立证据

(待补——独立审议未执行，无独立 grep / 复现输出)

---

## 补审待办（必须按顺序完成才能 commit）

补审启动后，独立子代理须独立验证以下要点（**不要信任 VERIFICATION_chat_first.md 的结论，要重跑**）：

### 必查项（覆盖主代理自审盲点）

1. **`create_task` 字段映射** —— `chat_agent` 决策 `generate_report` 时构造的 `CreateTaskRequest`，各字段（`user_task.topic/scope/output_format_spec/constraints`、`plugins`、`model`）是否与既有契约完全一致？跨文件比对 `server/api.py` 顶部 CreateTaskRequest 定义。
2. **`_safe_parse` 真实 LLM 行为** —— 给 LLM 喂：
   - (a) 严格合法 JSON
   - (b) ```json ``` 包裹
   - (c) topic < 4 字
   - (d) 截断 JSON
   - (e) 编码异常字符
   看 `_safe_parse` 在每种情况下是否按预期回落 `intent=chat` + 友好 reply，不抛 500。
3. **`_get_chat_agent` 线程安全** —— 锁？懒加载并发触发时会否重复 NewApiLLMClient？是否有死锁？
4. **前端 `ChatEntry.vue` 真实交互** —— 进入页面后：
   - 消息占位 → LLM 思考中 → 真实回复，过程中是否流畅？
   - 报告卡片出现 → 点击"查看进度 →"是否正确跳 `/tasks/{id}`？
   - 切换智能体 / 选数据源 / 选模型 三处 popover 是否都正常开关？
5. **CORS / 跨域** —— `/chat` 是否被 CORS 放行？OPTIONS 预检能否通过？
6. **副作用隔离** —— 调用 `/chat` 触发任务后，引擎子进程能否正常起停？Runaway 时能否回收？
7. **诚实审计** —— ChatEntry.vue 是否还有"前端假配置"（UI 可改、引擎忽略）的痕迹？ChatAgent step 是否在 LLM 失败时如实报错？
8. **安全** —— LLM 解析出的 `report.topic` 直接喂给 `create_task.user_task.topic`，下游 LangGraph 会用作检索关键词。如果有人注入 `<script>` 或异常字符，是否会破坏流水线？是否需要清洗/长度限制？

### 建议补查项

- 阶段 2 流式（current: 阶段 1 是同步）——当前端"思考中"点阵时，LLM 真的在算吗？有没有死锁？
- ChatAgent 与 engine_process_manager 是否共享 LLM client 实例？还是各自独立？
- 引用既有 `web/src/api/client.ts` 的 axios 实例，CORS token 头是否需要为 `/chat` 加？

---

## 触发记录（供回溯）

| 时间 | 事件 |
|---|---|
| 2026-09-11 17:42 | 主代理实现 `chat_agent.py` |
| 2026-09-11 17:44 | 主代理追加 `server/api.py` `/chat` 端点 |
| 2026-09-11 17:46 | 主代理重写 `ChatEntry.vue` |
| 2026-09-11 17:47 | 重建 `api` + `web` 容器，端到端 curl 实测 PASS |
| 2026-09-11 17:50 | 主代理写 `VERIFICATION_chat_first.md`（自审 PASS） |
| 2026-09-11 17:5x | 第一次启动独立审议子代理 → `对话超时，请重试` |
| 2026-09-11 17:5x | 第二次启动独立审议子代理 → `对话超时，请重试` |
| 2026-09-11 17:5x | **BLOCKED**，本文件强制置位 |

---

—— 主代理（2026-09-11）：按 edict-gate 铁律，**未经独立子代理 PASS 不得 commit**。
—— 补审 PASS 后才允许改本文件 verdict 至 `PASS` 并补全上述章节。
