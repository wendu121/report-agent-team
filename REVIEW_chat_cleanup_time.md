<!-- reviewed-by: independent-subagent -->
# 独立审议（gate ③）· 聊天记录/历史记录清理 + 时间节点校准

> 审议者：独立子代理（gate ③，唯一有权签署 `reviewed-by`）
> 对象：`E:\第二电脑\report-agent-team` 本轮未提交改动（DESIGN_chat_cleanup_time.md）
> 结论：**PASS_WITH_NOTES**（可进入 commit；附 2 项非阻断诚实化建议）

---

## 0. 总体核验方式与独立性声明

- **不信任主代理结论**：所有文件均亲自读取真实代码，未引用主代理自审结论。
- **复跑命令**（本人执行，非照搬）：
  - `python -m py_compile server/api.py` → `PY_COMPILE_OK`（通过）
  - `pytest tests/test_cleanup_endpoints.py -v` → **8 passed**（通过）
  - `npx vue-tsc --noEmit`（web）→ **exit 0**（通过）
- **未执行**：`git commit` / `git push`（审议者无此权限、亦不应代签）。
- 审查范围严格限定 teammate 消息枚举的 10 个文件（后端 `server/api.py` 清理区段 + 9 前端文件），工作树其余大量改动属其他并行任务，不在本档范围。

---

## 1. 最关键诚信点：存储模型偏差（DESIGN 假设 vs 实施现实）

**DESIGN 假设**：任务存 DB `tasks` 表，靠 `ondelete=CASCADE` 级联删除。
**实施者断言**：不成立——运行时任务在 `_tasks` 内存字典 + `.engine_state/{id}_*.json(l)` 文件；DB `tasks` 表是迁移期 dead schema。

**本人独立核验结论：实施者断言属实，删除实现为真删除，非假删除。**

证据链（均读真实代码）：

1. `server/api.py:375` — `_tasks: dict[str, TaskResponse] = {}` 是运行时真实存储（注释明确「内存存储（临时实现…）」）。
2. `server/api.py:718` `list_tasks()` — 历史列表**扫描 `.engine_state` 文件**：
   - `:731` 扫 `*_output.json`（已结束/升级任务）
   - `:750` 扫 `*_input.json`（运行中无 output 兜底）
   - `:768` 再合并内存 `_tasks`
   - **全程不读 DB `tasks` 表** → 印证 DB 表在运行时确实是 dead schema。
3. `_delete_task`（`server/api.py:2010`）三步真实删除：
   - `:2029` `_tasks.pop(task_id, None)` — 删内存态（list_tasks 的 :768 来源）
   - `:2031` `_remove_task_files(task_id)` — 删 `.engine_state/{id}_*.json` 与 `outputs/{id}_*` 文件（list_tasks 的 :731/:750 来源）
   - `:2034-2041` 显式 `delete(RoutingState/EngineEvent/GateReview/Task)` — 清理 DB 孤儿行
   → 删完后 `list_tasks` 既扫不到文件、也合并不到内存，**任务从真实运行时视图消失**。
4. `_clear_tasks`（`server/api.py:2047`）candidate 同时取自 `_tasks.keys()`（`:2051`）与文件 glob（`:2054-2057`），文件态任务同样纳入删除，**文件恢复态任务也能被真删**。

**结论**：删除作用于两个真实运行时存储（内存 + 文件），属真删除；DB 清理为冗余兜底（dead schema 上无副作用）。诚信点 PASS。

---

## 2. 八项必查结论（file:line 证据）

### 必查① 归属校验严格式 fail-closed（404 而非 403）
- `server/api.py:1652` `_owned_task()`：内存任务 owner 不匹配 → `:1662` 抛 404；文件恢复任务 owner 不匹配 → `:1671` 抛 404；不存在 → `:1676` 抛 404。**全程 404，无 403 分支**。
- 测试 `tests/test_cleanup_endpoints.py:162` `test_delete_task_owner_mismatch_returns_404` 断言 `status_code == 404` 且任务仍在（未被删）——**本人复跑 passed**。
- ✅ PASS

### 必查② 运行中禁止删（409）且前端置灰（不单靠后端拦）
- 后端：`server/api.py:2019` `if task.status in ("running","rework"): raise 409 TASK_BUSY`。
  - `TaskStatus(str, Enum)`（`server/api.py:103`）继承 `str`，故 `TaskStatus.RUNNING in ("running",...)` 为 True，**409 守卫真实生效**。
  - 测试 `test_delete_running_task_returns_409`（`:174`）与 `test_delete_rework_task_returns_409`（`:190`）本人复跑均 passed，断言 409 且任务保留。
- 前端置灰：`web/src/layouts/DefaultLayout.vue:115-126` 明确分支——`v-if="isRunning(t.status)"` 渲染 `class="history-item-del is-disabled"`（`@click.stop` 无 handler，`:119` 不绑定删除），`v-else` 才渲染可点删除（`@click.stop="delTask"`）。`isRunning` 定义于 `:355`。
- 后端 409 前端兜底：`DefaultLayout.vue:373-375` `delTask` catch 中 `e?.status === 409 → ElMessage.warning('运行中的任务不可删除')`。
- ✅ PASS（后端拦截 + 前端置灰双保险）

### 必查③ 清空会话级联（防孤儿行）
- `server/api.py:2091` `_clear_sessions()`：`:2102` 显式 `delete(ChatMessage)`、`:2103` 显式 `delete(ChatSessionMemory)`、`:2104` 显式 `delete(ChatSession)`，按 `ids`（当前账号）批量删。
- 注释 `:2100` 明确说明「ChatMessage/ChatSessionMemory 对 session 为普通 FK 列，即便 DB 不强制 ondelete 也清掉」。
- 测试 `test_clear_sessions_removes_messages_and_memory`（`:233`）本人复跑 passed：本账号 messages/memory 归零、其他账号会话不受影响。
- ✅ PASS

### 必查④ 时间校准真实有效 + 无误伤本地时间
- `web/src/utils/formatter.ts:7` 正则 `TZ_SUFFIX_RE = /(?:Z$|[+-]\d{2}:?\d{2}$)/`；`:9-12` `parseDbTime`：无尾缀 → 补 `Z` 再解析；有 `Z`/`±HH:MM` → 原样。
- 心算样例：
  - `2026-09-20T04:00:00` → 无尾缀 → `new Date("...T04:00:00Z")` = 04:00 UTC = **北京时间 12:00** ✅（修复前会被当本地时间显示 04:00，慢 8h）
  - `2026-09-20T04:00:00+08:00` → 命中 `[+-]\d{2}:?\d{2}$` → 原样 = **北京时间 04:00** ✅（不重复补 Z，无误伤）
- 三处设置页替换：
  - `Accounts.vue:175` import `formatTime`，`:209` `fmt(v)` 用 `formatTime(v)` 格式化 DB 时间。
  - `Agents.vue:126` import `formatTime`，DB 时间 `:294` `return formatTime(iso)`；本地 `lastSaved` `:261` `new Date().toLocaleString('zh-CN')` **保留未改**（符合 DESIGN §3：「本地生成时间无 UTC bug 不动」）。
  - `Gates.vue:139` import `formatTime`，DB 时间 `:301` `formatTime(iso)`；`lastSaved` `:268` 本地 `new Date()` 保留未改。
- `DefaultLayout.vue` 原本地 `formatTime` 副本已移除（grep 无 `function formatTime`），统一引共享 util（`:239` import，`:133` 用 `formatTime(t.updated_at)`）。
- 误伤核查：本档范围内 `Agents.vue:261`/`Gates.vue:268` 的 `lastSaved` 与 DESIGN 标注的 `Models.vue:557`（本次未改）均为本地 `new Date()`，**均未被误改为 UTC 解析**，显示逻辑正确。
- ✅ PASS

### 必查⑤ 过度工程检查
- 新增 5 个 helper（`_audit_task_change`/`_remove_task_files`/`_delete_task`/`_clear_tasks`/`_clear_sessions`）均为单职责、被端点直接复用，无冗余抽象。
- 无新增依赖、无新增配置文件、无新引入的中间层。
- DB 关联表显式清理虽对 dead schema 为冗余，但属诚实兜底（防历史遗留孤儿行），非过度工程。
- ✅ PASS

### 必查⑥ 确认弹窗（防误触，含数量/不可恢复）
四处置均有 `ElMessageBox.confirm` 且文案含「不可恢复」+ 数量：
- 聊天单删 `DefaultLayout.vue:330-331`：「删除后该对话全部消息不可恢复」；取消 `:335` `return`（不动）。
- 聊天清空 `:387-390`：`将删除全部 ${chatSessions.length} 个会话，且不可恢复`；空列表 `:385` 前置 `return` 不弹。
- 历史单删 `:362-363`：「删除后该任务及其关联记录不可恢复」。
- 历史清空 `:410-413`：「将清空全部已结束的历史任务（运行中的任务会保留），且不可恢复」。
- ✅ PASS

---

## 3. 复跑验证结果（本人执行）

| 项 | 命令 | 结果 |
|---|------|------|
| 后端语法 | `python -m py_compile server/api.py` | PY_COMPILE_OK |
| 清理端点测试 | `pytest tests/test_cleanup_endpoints.py -v` | **8 passed** |
| 前端类型 | `npx vue-tsc --noEmit`（web） | **exit 0** |

---

## 4. PASS_WITH_NOTES：非阻断诚实化建议（不阻塞 commit）

**NOTE-1（文档诚信，非阻断）**：端点 docstring 与运行时存储模型不一致。
`server/api.py:2113-2116` 的 `delete_task` 文档仍写「删除 Task 行（RoutingState/EngineEvent/GateReview 靠 DB ondelete=CASCADE 级联）」——这是 DESIGN 的旧假设，与真实实现（内存+文件删除 + 显式清孤儿行）不符。代码本身正确（`_delete_task` 与 `_remove_task_files` 的 docstring `:1990-1991` 已如实描述），仅此端点 docstring 残留旧说法。**建议**：将该 docstring 第三点改为「删除内存态 + 状态文件 + 显式清 DB 关联孤儿行」，以免后续维护者被误导。测试文件 `tests/test_cleanup_endpoints.py:10-11` 的描述已正确，可作参照。

**NOTE-2（死分支，非阻断）**：`rework` 状态在运行时不可达。
后端 `TaskStatus` 枚举（`server/api.py:103-108`）仅含 running/done/escalated/aborted，**无 rework**；`list_tasks` 也只会产出 running/done/escalated/aborted。因此 `api.py:2019` / `:2074` 的 `("running","rework")` 中之 `rework` 分支及前端 `isRunning`（`:356`）、`statusLabel`（`:349`/`DefaultLayout:350`）中的 rework 均为**防御性死代码**。无害，但属对不存在状态的过度预留；若确认产品永不出现 rework，可精简。不阻断。

---

## 5. 结论

- **关键诚信点（真删除 vs 假删除）**：PASS——删除作用于真实运行时存储（内存 `_tasks` + `.engine_state` 文件），非对 dead schema 做样子。
- **必查 6 项**：全部 PASS（含 404 fail-closed、409 双保险、会话级联、时区补 Z 无误伤、无过度工程、四处确认弹窗）。
- **复跑**：py_compile / 8 测试 / vue-tsc 三项全绿。
- **结论**：**PASS_WITH_NOTES**，可进入 commit；NOTE-1/NOTE-2 为后续诚实化优化，不阻断。

<!-- reviewed-by: independent-subagent -->
