# 独立代码评审 · 主账号查阅子账号「操作记录」（CallRecord）

门禁：③ 独立评审（不修改任何代码，仅审阅）
评审对象（当前磁盘内容）：`tools/call_log.py`、`orchestrator.py` (`_run_fc_loop`)、
`server/auth.py`、`web/src/views/settings/Accounts.vue`、`scripts/verify_call_records.py`、
`VERIFICATION_call_records.md`，并交叉核对 `server/tenancy.py`、`server/database.py`、
`server/engine_runner.py`、`server/engine_client.py`、`server/models.py`、`server/main.py`、`requirements.txt`。

## Verdict: PASS_WITH_NOTES

实现整体正确、可落地，且诚实披露了已知限制。未发现阻塞级（MAJOR）缺陷。要点：

- **写入端绝不阻断主链路**：`record_call` 自身 `try/except` 吞掉一切异常；调用点的 `_log` 又包了一层 `try/except`；`_aid` 解析也包了。任何 DB/序列化错误都不会逃逸进 `_run_fc_loop`。
- **账号归属正确**：子进程路径 `RAT_ACCOUNT_ID` 由 `engine_client.py:98`（注入子进程环境）+ `engine_runner.py:40,43`（`os.environ` + `bind_account`）在解析任何资源前设好；进程内 API 路径由 `get_current_account` 写入 ContextVar。`_run_fc_loop` 内的 `tenancy.current_account_id(allow_none=True)` 两者都能解析到**发起账号**本人 id，子账号调用记在子账号名下。
- **读取端作用域正确**：`require_main` 挡非主账号（403）；`parent_id == main.id` 校验（404 统一处理，防枚举）；仅回查 `CallRecord.account_id == child_id`，ORM 参数绑定，无注入面；`limit` 钳制 1~200、`offset>=0`。
- **敏感性与截断**：`detail` 截断至 500 字符（`_DETAIL_LIMIT`），且仅在失败时写入 `r.get("error")` / `"LLM 调用异常"`，**未**把工具 `result` 正文带入。
- **表会自动创建**：`CallRecord` 已在 `models.py` 定义，`init_database()` 在 `main.py` lifespan 经 `Base.metadata.create_all(checkfirst=True)` 建表；`psycopg2-binary` 已在 `requirements.txt`，与 VERIFICATION 的 `SYNC_COUNT=1` 一致。
- **诚实度**：`VERIFICATION_call_records.md` 明确承认真实 JWT HTTP 链路未端到端跑（哨兵 `account_id` 自写自读），且如实列出 MCP/插件合并、耗时仅覆盖模型等限制。

需补两处说明性备注（非缺陷），见下。

## 逐文件发现表

| 文件 | 行 | 严重度 | 说明 |
|---|---|---|---|
| `tools/call_log.py` | 28-58 | INFO | `record_call` 全程 `try/except` 吞异常，无账号上下文 no-op，最佳努力写入。正确，不阻断主链路。 |
| `tools/call_log.py` | 41 | INFO | `detail` 截断 `_DETAIL_LIMIT=500`；`detail` 为 `None` 或空串时落 `NULL`。正确。 |
| `tools/call_log.py` | 42-55 | MINOR | 每次调用 `get_sync_session()` 新建一条**同步** DB 连接。热循环每 model/tool 各一次，高并发下可能触及默认连接池（5+10）上限；超限时该次写入静默 no-op（设计上可接受降级，但建议确认容器 `DATABASE_URL` 池上限或改用连接复用/批量）。非阻塞。 |
| `orchestrator.py` | 1663-1667 | INFO | `_aid` 解析包 `try/except`，失败置 `None` → 后续 no-op。正确。 |
| `orchestrator.py` | 1669-1676 | INFO | `_log` 内部再包 `try/except`，双重保险。正确。 |
| `orchestrator.py` | 1681-1688 | INFO | `llm.complete_with_tools` 成功记 `kind="model"`；失败记 `ok=False` 后 `raise`，错误语义与改造前一致。正确。 |
| `orchestrator.py` | 1701-1703 | INFO | `dispatch_tool` 后记 `kind="tool"`，`ok=r.get("ok")`，`detail=r.get("error")`（仅错误摘要，非结果正文）。正确。 |
| `orchestrator.py` | 1649-1732 | INFO | `_aid` 在函数入口解析一次；`_run_fc_loop` 每 agent 复调，但同一进程内环境/ContextVar 恒定 → 同一报告内所有 agent 归属一致。账号归属正确。 |
| `server/auth.py` | 29 | INFO | 新增 `from server.models import Account, CallRecord`。正确。 |
| `server/auth.py` | 181-191 | INFO | `CallRecordOut` 不含敏感正文，字段与表一致。正确。 |
| `server/auth.py` | 389-433 | INFO | `require_main` 依赖 → 非主 403；`parent_id == main.id` 校验 → 非本主子账号一律 404；仅回 `CallRecord.account_id == child_id`；ORM 参数绑定无注入；`limit` 钳 1~200（401）、`offset>=0`（402）。作用域与既有 `delete_account` 同源同构。正确。 |
| `server/auth.py` | 389-433 | NOTE | 模型注释列了用途 ①「自己只看自己的」与 ②「主账号查子账号」。本端点只实现 ②；**未提供账号自读自己记录的端点**。若 ① 是预期范围，属范围裁剪，应在文档点明（非本功能缺陷，但建议披露避免误解）。 |
| `web/Accounts.vue` | 57-61 | INFO | 「查阅操作记录」按钮 `v-if="row.status !== 'pending'"`，与 spec 一致。正确。 |
| `web/Accounts.vue` | 70-104 | INFO | 对话框 `v-model="recordsVisible"`，表格绑定 `records`，列映射 `kind/target/ok/latency_ms/created_at/detail`，与 `CallRecordOut` 一致。`destroy-on-close` 干净。正确。 |
| `web/Accounts.vue` | 121-124 | INFO | `records/recordsLoading/recordsVisible/recordsUser` 引用齐备。正确。 |
| `web/Accounts.vue` | 151-153 | INFO | `kindLabel` 映射 `model/tool/mcp/plugin`；当前仅产出 model/tool，mcp/plugin 分支为死标签但无害。 |
| `web/Accounts.vue` | 206-219 | INFO | `viewRecords` 拉取 `/auth/accounts/${row.id}/call-records?limit=50`，`row.id` 即子账号 id，与端点形参对应。错误走 `ElMessage.error`。正确。 |
| `web/Accounts.vue` | 76, 100-103 | MINOR | 内层记录表无 `#empty` 模板，空数据时显示 el-table 默认空文案（可接受）；footer「最近 {{records.length}} 条」在 loading 期间会先显示 0 条（仅外观）。非缺陷。 |
| `scripts/verify_call_records.py` | 28-65 | INFO | 用哨兵 `account_id`（长度≤36）自写自读、读后清理；验证「写→读→倒序→端点可导入→表存在」。作为单元级探针有效，且**未**冒充真实 JWT HTTP 链路（与 VERIFICATION 披露一致）。正确。 |
| `VERIFICATION_call_records.md` | 1-67 | MINOR | 诚实披露了：哨兵非真 JWT（57-60）、MCP/插件合并（54-56）、耗时仅模型（61-62）、失败模型仍上抛（63）。**缺一项部署说明**：已在运行、本次表变更前启动的旧容器，需重启才会触发 `create_all` 建 `call_records` 表（新容器启动即自动建）。建议补一句，以免运维误以为「热更新即生效」。 |

## MAJOR / MINOR 问题与修复建议

无 MAJOR（阻塞）问题。

MINOR（建议，非阻塞）：

1. **部署说明缺口**（`VERIFICATION_call_records.md` / 部署文档）
   `call_records` 表由 `main.py` lifespan 的 `init_database()` 在**容器启动时** `create_all(checkfirst=True)` 创建。`psycopg2-binary` 已在 `requirements.txt`，新容器无虞；但若生产已有一个本功能上线前就跑着的 `api` 容器，必须**重启容器**才会建表，否则读取端点会报 relation 不存在（被 `require_main` 之后的查询抛 500，而不会静默）。
   建议：在 VERIFICATION 的「诚实边界」或部署变更单中补一行：「`call_records` 表随容器启动自动创建；已有运行中的旧容器请重启 `api` 服务一次以建表。」

2. **热循环每调用一次新建同步连接**（`tools/call_log.py:42`）
   每次 `record_call` 走 `get_sync_session()` = 新 `SyncSessionLocal()`，同步引擎默认池 5+10。报告高峰期（多并发任务 × 每轮 model+多 tool）可能瞬时打满池，超限时该次审计写入静默丢弃（不报错、不阻断，符合设计）。
   建议（可选优化，非阻塞）：增大同步引擎 `pool_size`/`max_overflow`，或把审计写入改为进程内缓冲、周期性批量提交；至少确认 `DATABASE_URL` 池上限能覆盖并发峰值。

3. **自读端点缺失**（`server/auth.py` + `CallRecord` 模型注释）
   模型注释写明用途 ①「自己只看自己的调用记录」，但本次只落地 ②「主账号查子账号」。当前没有账号读取**自己**记录的 HTTP 端点。若 ① 属预期，建议补一个 `GET /auth/me/call-records` 或在文档明确声明本变更仅做 ②，避免后续误解为漏做。

4. **`detail` 敏感性边界**（`orchestrator.py:1703`）
   工具失败写入 `r.get("error")`（错误摘要，非 `result` 正文），方向正确、不泄敏。但个别工具的错误串理论上可能回显部分入参；在当前实现下属工具侧行为。建议长期在 `dispatch_tool` 层对 `error` 也做截断/脱敏，本评审不视为缺陷。

## Known limitations（诚实披露，非缺陷）

- **MCP / 插件类调用合并为 `kind="tool"`**：`dispatch_tool` 不区分来源，MCP 工具与插件调用都落 `kind="tool"`（target 为工具名，UI 可见具体名）。细分 mcp/plugin 需改 `dispatch_tool` 内部识别来源，属后续增强。VERIFICATION 已如实披露。
- **`latency_ms` 仅覆盖模型调用**：`dispatch_tool` 不返回耗时，`kind="tool"` 记录 `latency_ms` 为 `NULL`，UI 显示「—」；模型调用已打点。VERIFICATION 已如实披露。
- **真实 JWT HTTP 链路未端到端实测**：核验用哨兵 `account_id` 自写自读，未走「主账号持 JWT → GET 子账号 call-records」的带鉴权往返。读取端逻辑与 `delete_account` 等 `require_main` 作用域端点同源同构，`parent_id == main.id` 已逐一对照，可信度高但不等于实测了带鉴权 HTTP。建议首次联调由主账号在 UI 点一次「查阅操作记录」做实落。VERIFICATION 已如实披露。
- **失败模型调用仍 `ok=False` 后上抛**：错误语义与改造前完全一致，引擎仍走 `_agent_fail`，审计写入不掩盖失败。VERIFICATION 已如实披露。
- **`CallRecord` 无 TTL / 清理策略**：流水只增不清，长期增长依赖运维或后续归档；本变更未引入清理任务（可接受，建议后续补 retention）。
- **自读（用途①）未暴露**：见 MINOR-3，仅实现主账号查子账号（用途②）。

<!-- reviewed-by: independent-subagent (gate-③) -->
