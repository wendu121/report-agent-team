# DESIGN · 聊天记录/历史记录清理 + 时间节点校准

> 日期：2026-09-20 ｜ 状态：待实施
> 起因（boss 原话）：「聊天记录和历史记录要新增清除记录、单点删除的功能，要不然看起来很乱」「整个项目的时间节点，应该跟随网络或者地理位置」。

---

## 0. 现状实测（设计依据）

| # | 现状 | 证据 |
|---|------|------|
| 1 | 聊天记录**已有**单条删除：hover 才显出垃圾桶图标（`DefaultLayout.vue:71-73` → `delChat` → `DELETE /chat/sessions/{id}`），**无二次确认**，且 discoverable 差 | `web/src/layouts/DefaultLayout.vue:71` |
| 2 | 聊天记录**无「清空全部」** | `sect-tools` 只有搜索 + 新建（:47-59） |
| 3 | 历史记录（研报任务）**完全没有删除**——前端无按钮，后端无 `DELETE /tasks/*` 端点 | `server/api.py` 全部 tasks 端点 = GET/POST/review/audit-export/report |
| 4 | 时间显示差 8 小时：DB 存 **naive UTC**（`models.py:30-31` `datetime.utcnow`），API 返回的 isoformat **无 `Z` 后缀**；前端 `new Date(无Z串)` 按浏览器**本地时区**解析 → 把 UTC 钟面时间当北京时间显示 | `web/src/utils/formatter.ts:5`、`DefaultLayout.vue:318-323`、`Accounts.vue:208`、`Agents.vue:293`、`Gates.vue:300` |
| 5 | 删任务关联表安全：`RoutingState`/`EngineEvent`/`GateReview` 均 `ForeignKey("tasks.task_id", ondelete="CASCADE")` | `server/models.py:67,93,121` |
| 6 | `ChatMessage.task_id` 是**无 FK 的普通列**（可空）——删任务后聊天消息里的「查看报告」链接会 404 | `server/models.py:257` |

---

## 1. K1 聊天记录：单删确认 + 一键清空

- **单条删除**：保留现有 hover 图标，但加 `ElMessageBox.confirm`（「删除后该对话全部消息不可恢复」），取消不动。删除当前打开的会话 → 跳 `/`（现有逻辑保留）。
- **一键清空**：`sect-tools` 新增清空图标按钮（hover 提示「清空全部聊天记录」）→ confirm 明确显示**将删除 N 个会话** → 调新端点 `DELETE /chat/sessions`（见 K3-B）→ 刷新 store；当前会话被清 → 跳 `/`。

## 2. K2 历史记录：单删 + 一键清空（后端新增 3 个端点）

- **`DELETE /tasks/{task_id}`**（新）：
  - 归属校验**严格式 fail-closed**（`owner_id != 当前账号 → 404`，绝不用宽松式——历史教训 §记忆 2.5）。
  - **`status ∈ {running, rework} → 409`**（运行中禁止删，防孤儿引擎状态）；其余状态可删。
  - 删除 Task 行即可——三张关联表靠 DB `ondelete=CASCADE` 级联（实测 #5）。
  - 写审计（audit），返回 `{ok, task_id}`。
  - **诚实边界**：`ChatMessage.task_id` 无 FK 不级联（#6）——聊天里旧报告链接点击后 404 由任务详情页既有错误态承接，不做回填清理（克制，不过度工程）。
- **`DELETE /tasks`**（新，一键清空）：删当前账号**全部非运行中**任务，返回 `{ok, deleted, skipped_running}`；有 running 被跳过时前端提示「N 个运行中任务已保留」。
- **`DELETE /chat/sessions`**（新，一键清空）：删当前账号全部会话（`ChatMessage`/`ChatSessionMemory` 显式随删——它们对 session 无 DB 级联，须确认模型关系后逐表删或建级联），返回 `{ok, deleted}`。
- **前端**：`history-item` 加 hover 删除图标（与聊天记录同款交互）+ confirm；`sect-head` 右侧/工具区加「清空全部」。运行中任务的删除按钮置灰 + tooltip「运行中不可删除」。

## 3. K3 时间节点校准（跟随网络/地理位置）

- **根因判定**：不是"没跟网络时间"——数据源是 UTC（本就是网络标准时间），**是前端解析把 UTC 钟面当成北京时间**，所以永远慢 8 小时（早上 9 点的任务显示成凌晨 1 点）。
- **修法（零迁移、零后端改动）**：`web/src/utils/formatter.ts` 加 `parseDbTime(iso: string): Date`——正则判定无时区尾缀（无 `Z`/`+hh:mm`）→ 追加 `Z` 再 `new Date()`；`formatTime` 内部改用它。显示仍走浏览器 `toLocaleString`/getHours = **浏览器本地时区**（地理位置）+ 系统时间（NTP 网络时间）。这正是 boss 要的「跟随网络或地理位置」。
- **替换面**（全项目 grep 过）：
  - `utils/formatter.ts::formatTime`（TaskResult / TimelineNode 共用，改一处两处生效）；
  - `DefaultLayout.vue:318` 本地复制版 formatTime → 改引共享 util（顺带消灭重复实现）；
  - `Accounts.vue:208` / `Agents.vue:293` / `Gates.vue:300` 三处 `new Date(iso).toLocaleString` → 换 `formatTime` 或 `parseDbTime`；
  - `Agents.vue:260` / `Gates.vue:267` / `Models.vue:557` 的 `lastSaved = new Date()`（**本地生成时间，无 UTC bug**，不动）。
- **明确不做**（克制）：① compose 不加 `TZ`——容器时区改动会让 `.audit` 文件名与历史日志时间轴突变，混入本档验收反而说不清，单独立项；② 引擎 prompt 不新增「当前日期」注入——现网 prompt 无此需求实证，避免范围蔓延；③ DB 不迁移（继续存 naive UTC，这是正确实践）。

---

## 4. 改动清单（文件级）

**后端（1 文件）**
- `server/api.py`：+3 端点（`DELETE /tasks/{id}`、`DELETE /tasks`、`DELETE /chat/sessions`），归属/状态校验 + 审计。
- `tests/test_cleanup_endpoints.py`（新）：单删归属 404 / running 409 / 清空计数 / 会话清空连带消息与记忆，一次性租户隔离，不触网。

**前端（7 文件）**
- `utils/formatter.ts`：+`parseDbTime`，`formatTime` 改用之。
- `layouts/DefaultLayout.vue`：聊天清空按钮、历史单删/清空、confirm 三处、本地 formatTime 换共享版。
- `services/chatSessionService.ts`：+`clearSessions()`。
- `services/historyService.ts`（或 store 所在）：+`deleteTask()` / `clearTasks()`。
- `stores/chatSessionStore.ts` / `stores/history.ts`：+`clearAll()` / `remove()` action。
- `views/settings/Accounts.vue`、`Agents.vue`、`Gates.vue`：时间显示换 `parseDbTime`。

## 5. 验收标准（全部真机/真跑）

1. `pytest tests/test_cleanup_endpoints.py` 宿主全绿；全量扣除已知失败项零回归。
2. `vue-tsc --noEmit` exit 0。
3. bsk 真机：单删聊天（含确认弹窗、取消不动）；清空聊天后计数归零、当前会话跳 `/`；删 running 任务按钮置灰；删 done 任务列表即时消失；清空历史提示 skipped 数。
4. 时间：取一条 `updated_at` 裸值手工 +8h 核对前端显示一致；`new Date(parseDbTime(x))` 与 UTC ISO 显式串等价。
5. gate ③ 独立审议 PASS 后方可 commit（分批：后端一笔、前端一笔，`git commit --only`）。

## 6. 风险与边界

- 清空操作**不可恢复**——全部走二次确认，confirm 文案写明数量。
- bulk 删除与单 worker 部署无并发竞态（单 uvicorn worker）。
- 409 语义：运行中/返工中任务保留，前端明示，不做「强制删除」。
