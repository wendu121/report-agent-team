<!-- reviewed-by: intern-s2-preview-397b-via-newapi (独立模型审议，非主代理自签) -->
<!-- 审议机制说明：原计划用独立子代理做 gate③，但子代理启动遇 API 429（主会话限流）。
     按 boss 指示改用 Intern-S2-Preview-397B（经 new-api 网关 http://localhost:3000/v1 直调）
     完成独立审议，避开主会话 429。审议者与被审代码作者非同源。 -->

# REVIEW_M11-3.md · M11-3 MCP 消费桥（D1-B 全 function-calling）独立审议

- 日期：2026-09-12
- 范围：M11-3（`tools/mcp_client.py` / `orchestrator.py` function-calling 改造 / `/admin/mcp-servers`）
- 审议者：**Intern-S2-Preview-397B**（独立模型，经 new-api 网关调用，model id `intern-s2-preview-397b`）
- 关联自审：`VERIFICATION_M11-3.md`
- 关联计划：`M11-3_D1B_IMPL_PLAN.md`

## 0. 审议轮次与最终结论

| 轮次 | 审议者 | 结果 | 说明 |
|---|---|---|---|
| — | 子代理（未执行） | BLOCKED | 子代理启动遇 API 429 限流，无审议产出；未自审顶替 |
| 第 1 轮 | Intern-S2-Preview-397B | **BLOCKED** | 报 2 项 MAJOR（`_run_async` 嵌套 loop、`tool_entries` 未初始化） |
| 第 2 轮 | Intern-S2-Preview-397B | **PASS-WITH-NOTES** | 2 项 MAJOR 已复核解决；新增 1 MINOR + 1 NOTE，均已处理 |

**最终：gate③ 通过（PASS-WITH-NOTES）。** 无未决 MAJOR。

## 1. 第 1 轮 findings 与主代理复核（**不盲从**）

| 等级 | 位置 | 第 1 轮结论 | 主代理复核 | 证据 |
|---|---|---|---|---|
| MAJOR | `tools/mcp_client.py::_run_async` | 嵌套事件循环崩溃风险 | **机制属实，生产不可达；但"兜底分支"确为坏码 → 已修** | 复现 `RuntimeError: Cannot run the event loop while another loop is running`；但引擎跑在**独立子进程**（`server/engine_client.py`→`subprocess`→`engine_runner.py`→`run_report`），进程内无运行 loop，主路径走 `asyncio.run`。已改为"独立线程 `asyncio.run`"兜底 |
| MAJOR | `orchestrator.py::make_agent` | `tool_entries` 未初始化 → NameError | **证伪（误报）** | `orchestrator.py:1210` 明确有 `tool_entries: list[dict] = []`（在 fc 分支 1299 之前）。第 1 轮 bundle 只给了 diff，省略了该行；审议者自认"看不到完整函数" |
| MINOR | `tools/mcp_client.py::_list_one/_call_one` | `_connect` 前抛错 → `finally` 中 `_safe_close(session, ctx)` 触发 UnboundLocalError | 成立（被外层 try 兜住，不击穿图） | **已修**：`ctx = session = None` 前置初始化 + `_safe_close` 判空 |
| MINOR | `orchestrator.py::_run_fc_loop` | 达 `MCP_MAX_ROUNDS` 且末轮 content 空 → 触发无谓 rework | 成立（属降级，非崩溃） | 记为已知限制（诚实边界），不伪造 content |
| NOTE | `config/mcp_servers.yaml` 未跟踪 | 配置文件未入库，新克隆缺失 | 成立 | **已纳入提交**（本文件与 M11-3 同批 commit） |
| NOTE | `StubLLMClient.complete_with_tools` | 次轮判断依赖 `【工具执行结果】` 字符串匹配 | 成立（测试稳定性小债） | 记为技术债，不影响生产 |

## 2. 第 2 轮复审结论（Intern-S2-Preview-397B 原文要点）

**verdict: PASS-WITH-NOTES**

### 上一轮 MAJOR 复核（审议者判定）
| 原 MAJOR | 判定 | 依据 |
|---|---|---|
| `_run_async` 嵌套 loop 崩溃 | **已修复** | 架构事实确认（引擎在独立子进程，主路径 `asyncio.run`）；新增 `threading.Thread` 兜底；实测旧实现抛 `RuntimeError`、新实现正常；pytest 94 passed |
| `make_agent` 中 `tool_entries` 未初始化 | **已修复** | 完整源码显示 `tool_entries: list[dict] = []` 在 `node` 起始处初始化，`extend(fc_entries)` 不再触发 NameError |

### 第 2 轮新提项（均已在主代理侧处理）
| 等级 | 位置 | 问题 | 处置 |
|---|---|---|---|
| MINOR | `orchestrator.py::_run_async` 与 `tools/mcp_client.py::_run_async` | 两处重复实现，未来修复易不同步 | **已消除重复**：抽出 `tools/_async_util.run_async`，两处统一引用 |
| NOTE | `tools/kb_store.py:22-23` | 默认嵌入维度 1536→1024，但 `CREATE TABLE IF NOT EXISTS` 不改既有列；旧 1536 维库插入 1024 向量会报错 | **已加"维度守卫"**：建表后读 `format_type()`，维度与 `EMBEDDING_DIM` 不一致时 `logger.warning` 显式告警（不静默失能） |

## 3. 独立审议结论

gate③ **通过（PASS-WITH-NOTES）**。无未决 MAJOR；第 1 轮误报已用 `orchestrator.py:1210` 证据证伪；两项真实缺陷（`_safe_close` 判空、`_run_async` 重复/兜底坏码）与两项 NOTE 均已修复或显式记录。审议者独立确认：新增功能经单测覆盖 + MCP 端到端实测通过。

> 诚实边界：审议者明确其无法运行真实 LLM 的 tool_calls 路径（本轮以 `StubLLMClientFC` 覆盖代码路径）；真实 LLM 工具选择未实跑，属部署/运行验证范畴，已在 `VERIFICATION_M11-3.md` §5 如实标注，未冒充已验证。
