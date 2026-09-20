# VERIFICATION · 主账号查阅子账号「操作记录」（调用流水 CallRecord）

> 配套提交：新增 `tools/call_log.py`；`orchestrator.py._run_fc_loop` 写入调用流水；
> `server/auth.py` 新增 `GET /auth/accounts/{child_id}/call-records` 读取端点 + `CallRecordOut`；
> `web/src/views/settings/Accounts.vue` 新增「查阅操作记录」按钮与对话框。
> 设计事实源：`server/models.py` 的 `CallRecord` 表注释 ——
> 「用途：① 自己只看自己的调用记录 ② Phase 2 主账号点开某个子账号『查阅』」。

## 背景（为什么要做）
老板裁定 3 + `DESIGN_account_hierarchy.md`：主账号应能查看其名下子账号的调用流水（谁在何时调了
什么模型/工具/数据源、耗时与成败）。`CallRecord` 表早已定义但**从未被写入或读取**（全仓 Grep 仅
出现在 `models.py` 三处）。本变更把这条「设计已有、实现为零」的链路补齐。

## 变更内容
1. **写入端（引擎热路径）** · `tools/call_log.record_call(account_id, kind, target, ok, latency_ms, detail)`
   - 最佳努力：任何异常全部吞掉，绝不阻断报告生成主链路；无账号上下文（`account_id=None`，
     CLI/未登录）时直接 no-op。
   - 用 `server.database.get_sync_session`（同步短连接，提交即关）——引擎主循环 `_run_fc_loop`
     是同步函数，且引擎子进程无 FastAPI 请求上下文，不能 `await` 异步会话。
   - `orchestrator._run_fc_loop`：每次 `llm.complete_with_tools` 记一条 `kind="model"`（含耗时，
     失败也记 `ok=False` 后向上抛，保持原错误语义）；每次 `dispatch_tool` 记一条 `kind="tool"`
     （`ok` 取工具返回结果，`detail` 仅存错误摘要，不含敏感正文）。
   - `account_id` 经 `tenancy.current_account_id(allow_none=True)` 解析（进程内 ContextVar 或
     子进程 `RAT_ACCOUNT_ID` 环境变量），归属到发起账号。
2. **读取端（主账号专用）** · `server/auth.py`
   - `GET /auth/accounts/{child_id}/call-records`，`Depends(require_main)`；先校验子账号
     `parent_id == main.id`（否则一律 404，避免枚举探测），再按 `created_at DESC` 返回
     `limit`（钳制 1~200）/ `offset` 分页。
   - 新增 `CallRecordOut` 视图模型（不含敏感正文）。
3. **UI** · `Accounts.vue`：子账号行新增「查阅操作记录」入口（非 pending 状态可见），弹对话框
   以表格展示 类型/目标/结果/耗时/时间/说明（最近 50 条，倒序）。

## 门禁 ① 构建/编译校验
- `python -m py_compile tools/call_log.py server/auth.py orchestrator.py` → **OK**（退出 0）。
- `docker compose build --build-arg PIP_INDEX_URL=... api` → `BUILD_EXIT=0`（pip 层命中缓存，
  `COPY . .` 重新执行，确认新代码烘焙进镜像）。
- `docker compose build web` → `BUILD_EXIT=0`（UI 重新构建）。
- `docker compose up -d api web` → `EXIT=0`。

## 门禁 ② 自审（实测证据）
- 容器内切确 `call_records` 表存在且可写：`init_database()` 输出「✅ 数据库表初始化完成」，
  直接 `get_sync_session()` 写 + 读 `CallRecord` 成功（SYNC_COUNT=1）。
- `docker exec report-api python scripts/verify_call_records.py` → **RESULT: PASS**
  - 写入 2 条（`tool` 成功 / `model` 失败），读回 2 条，倒序正确（先 model 后 tool）；
  - `ok`/`latency_ms`/`detail` 字段值正确（model 行 `ok=False, latency_ms=4567, detail='LLM 调用异常'`）；
  - `server.auth.list_child_call_records` 端点函数可导入（auth 路由已挂载）；
  - 测试行已自清理（不污染业务数据）。
- 写路径与读路径分别用同步/异步会话交叉验证（`get_sync_session` 写、`get_async_session` 读）
  均一致命中，排除「不同会话连不同库」的可能。
- Web 产物确认含新 UI：`docker exec report-web grep -l '查阅操作记录' /usr/share/nginx/html/assets/index-*.js`
  → `index-BVcSMpUm.js`（命中）。

## 诚实边界（务必如实告知 boss）
- **写入粒度**：当前记录「每次模型调用 + 每次工具分发」两级。MCP/插件类调用目前统一归为
  `kind="tool"`（target=工具名，UI 可见具体名称）；若要细分 mcp/plugin，需在 `dispatch_tool`
  内部识别来源——属后续增强，非本变更范围。
- **真实登录链路未跑端到端**：核验用哨兵 `account_id` 自写自读，未走真实「主账号 JWT → 查子账号」
  的 HTTP 全链路（需主账号 token，容器内无现成登录态）。读取端逻辑与既有 `delete_account` 等
  `require_main` 作用域端点同源同构（已逐一对照 `parent_id == main.id` 校验），可信度高但不等于
  实测了带鉴权的 HTTP 往返。建议首次联调时由主账号在 UI 点一次「查阅操作记录」做实落。
- **耗时仅覆盖模型调用**：工具分发层未打点 `latency_ms`（`dispatch_tool` 当前不返回耗时），故
  `tool` 类记录 `latency_ms` 为 `NULL`，UI 显示「—」。模型调用已打点。
- **失败模型调用仍记 `ok=False` 后向上抛**：错误语义与改造前完全一致（引擎仍走 `_agent_fail`）。

## 门禁 ③
见 `REVIEW_call_records.md`（独立子代理产出，主代理不自签 `reviewed-by`）。
