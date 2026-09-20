# REVIEW_postfix_pending.md — 「结论后修复」待补审登记

> **本文件不是独立审议结论，无 `reviewed-by: independent-subagent` 标记，不得当作 gate ③ 通过凭据。**
> 它由主代理（实施者）撰写，唯一用途是：如实登记「gate ③ 结论出具之后又改了哪些代码、其中哪些
> 还没回到独立审议者处复核」，以免这笔账烂在会话里。
>
> 生效约定（沿用 2026-09-03 确立的门禁受阻硬约定）：
> **修复复核 PASS 前不 commit。** 主代理**未自签、未改写任何审议者的结论**。

## 1. 为什么存在这一轮

三次 gate ③ 独立审议均在 2026-09-19 执行完毕（见 `REVIEW.md` 顶部总览表）。在它们各自出具
结论**之后**，为响应审议者自己提出的问题，主代理又改了 4 处代码。按本项目约定，这 4 处
**不能由主代理自认修好**，必须回到独立审议者处复核。

其中 1 处已复核闭环；剩余 3 处的复核被 API 限流（429）打断：

```
429 您的使用量已超出频率限制，将在 2026-09-20 00:11:04 UTC+8 重置
  - general-purpose-29（子账号隔离审议者）→ 在其复核 HIGH-1 已完成后被打断
  - general-purpose-30（对话中断/编辑审议者）→ 复核 F4 进行中被 429 打断，附录未落盘
```

## 2. 已闭环（无需再补审）

| 项 | 文件 | 复核结论 | 证据位置 |
|---|---|---|---|
| HIGH-1 裸 `fetch` → `/templates` 401 回归 | `web/src/stores/template.ts` | **已闭环** | `REVIEW_subaccount_isolation.md` 文末「§附录 · 修复复核」 |

残余未验证项（审议者已如实标注，非缺陷）：**重建后的前端 bundle / 浏览器真机点击**。
源码未进镜像（web 为多阶段构建，源码无 bind mount），故线上 bundle 尚非此修复版本。
需由有重建权限的一方在重建后做一次浏览器确认。

## 3. 待补审（3 项）

### P-1 · `scripts/e2e_report_flow.py` 退出码（F4）

- **改了什么**：原脚本在终态非 `done`（含 `escalated` 0 字报告）时只 `print(❌)` 却仍 `exit 0`。
  现改为：`ok` → `print ✅` + `sys.exit(0)`；否则按情形打印后 **`sys.exit(1)`**，且该语句是文件
  最后一条语句。另把轮询窗口 `480s → 900s`（实测同一用例可跑到 ~486s，480s 会赶在引擎收敛前
  超时，与真失败混淆），并把「超时未收敛」与「终态但无报告」分成两种文案（用 `timed_out` 区分）。
- **为什么要审**：审议者原话——「只要该脚本被用作回归门禁，『回归全绿』就是假的，须在合入门禁前
  修复」。这是**审议者认定的必修项**，修复正确性未经验证。
- **审什么**：
  1. **静态**：非 ok 路径是否确实不存在「执行到文件末尾而不 exit」的可能；`timed_out` 的赋值
     路径是否正确（初始 `True`、命中终态置 `False`、超时保持 `True`）。
  2. **动态**：在**重建后的镜像**内跑
     `docker compose exec -T -e PYTHONPATH=/app api python -u /app/scripts/e2e_report_flow.py wendy1`
     并报告 `echo $?` 与终态 status。`done` → 期望 0；`escalated` → 期望 1。
     **若始终未遇到非 done 终态，必须明写「失败路径仅静态证明，未动态触发」**，不得为凑证据重试。
  3. 900s 窗口是否与上游超时（nginx `proxy_read_timeout`、引擎自身超时、healthcheck）错配。
- **⚠️ 环境前提（关键）**：`scripts/` **没有 bind mount**（api 的 volumes 只有
  `outputs/ .engine_state/ tenants/ config/ .audit/ agents/ gates/ templates/ .secrets/ skills/`），
  脚本是 `COPY` 进镜像的。**未重建前容器内跑的是旧脚本**（实测容器内 `grep -c "sys.exit(1)"` = 0），
  据此得出的任何 exit code 都无效。必须先 `docker compose build api && docker compose up -d api`。

### P-2 / P-3 · 两处过时 docstring

- **改了什么**：
  - `tools/__init__.py`（模块 docstring）：原文「缺失则降级 MockProvider 占位（带 source 标记，
    不冒充真实检索）」→ 改为「缺密钥的源**一律不注册**，登记到 `SearchTool.degraded`（含
    reason/fix）……仅显式配置为 mock 的源才装载，结果带 `is_mock` 标记且不进『来源』」。
  - `orchestrator.py`（`run_report` docstring）：原文「无 TAVILY_API_KEY 则按 `fallback_to_mock`
    降级并告警」→ 改为「缺密钥的 api_key 源**不注册**（登记进 `ToolBundle.degraded` 并告警），
    不会用 mock 结果顶替真检索」。
- **为什么要审**：这两处 docstring 都在描述**本里程碑已经删掉的旧行为**，属文档层面的
  「挂羊头卖狗肉」——会让下一个读者以为「占位 mock」是可接受设计。改的是注释，**无行为变更**。
- **审什么**：
  1. 对照 `tools/data_sources.py` 的 `build_search_tool()` / `load_secrets()` / `SearchTool.search_many()`
     实际行为，逐句核对新 docstring 是否**准确**（注意：`load_secrets()` 仍兼容旧 `TAVILY_API_KEY`，
     这一半是准的，不要误判为过时）。
  2. 确认改动**仅限注释**，无任何可执行语句被误改（`py_compile` 已过，但需人眼确认 diff）。
  3. 确认没有**其它**同类过时描述遗留（建议全仓扫 `fallback_to_mock` / `MockProvider` 的提及处）。

## 4. 补审通过后的提交注意事项

- 工作树混装**三个**里程碑 + 5 个文件被三处改动逐块交错
  （`orchestrator.py` 13 块 / `chat_agent.py` 14 块 / `server/api.py` 20 块 / `ChatEntry.vue` 25 块 /
  `chatService.ts`）。`REVIEW.md` 第 219 行亦载明「建议拆成两个 commit……**若 boss 同意**」。
  拆分方式需 boss 拍板，见会话内决策项。
- 仓库根有未跟踪调试残留 `.find_imgs.py`，**不应入库**。
- 无 boss 显式指令**不 push**。
