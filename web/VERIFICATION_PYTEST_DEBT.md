# VERIFICATION — pytest 预存债还清（TD-010：测试离线化 + 多端点 mock 接缝修复）

日期：2026-09-10  ·  主代理自审（门禁②）

## 一、债的真相（诚实纠错）

P1 交付时报告「11 failed 为预存债（9-10 多端点 commit 疑引入），与 P1 零 import 依赖已证」——**该判断部分错误**。实测复盘（流式 debug 脚本逐超步观察）：

| 根因 | 归属 | 影响面 |
|---|---|---|
| **A. P1 keyless 真源让离线测试变真联网**：keyless provider 接入后 `build_search_tool` 的 `has_real=True` → `using_mock_search=False`，test_orchestrator.py 裸调 `run_report`（未传 tools）时 build_tools() 拉起真实 Tavily/arXiv/CrossRef/Wikipedia/SemanticScholar | **P1 引入（我的债）** | 1 个失败（test_max_rounds_escalate）+ 套件 11 分钟（真实网络重试/SSL 超时） |
| **B. 多端点重构断了 mock 注入接缝**：`NewApiLLMClient` 从单客户端 `self._client` 改为 per-endpoint 缓存 `self._clients[endpoint_id]`，测试 helper 仍注入已废弃的 `c._client` → fake 被真客户端覆盖 → 打到 127.0.0.1:9 真实连接 | 多端点 commit（367c62d）引入 | 7 个失败（全部重试/不可重试逻辑测试） |

**根因 A 的失败机理**（debug 实证）：真实检索 34 条 search_results 注入 prompt 后，stub 的 `_extract_injected_records` 括号配对提取被真实内容中的 `[`/`]`（markdown 链接等）干扰 → 返回 [] → retrieval_records=0 → GateA machine_check 判 escalate（620 行）→ 走 node_escalate 缺省 reason，绕过 router 的 max_rounds reason 分支 → 断言「超过 max_rounds」失败。用离线 MockProvider 复跑同场景，reason 正确 = 引擎逻辑无 bug，纯测试环境不纯。

## 二、修复（仅改测试，生产代码零改动）

1. `tests/test_regression_edge.py` `_mk_gateway_client`（~294）：`c._client = fake` → `c._clients["t"] = fake`（新接缝），附注释说明。
2. `tests/test_orchestrator.py`：6 处裸调 `run_report`（29/47/65/103/120/199 行，含 `__main__` 块）统一补 `tools=_tools_bundle(MockProvider())`；顶部补 `MockProvider` import。复用文件内已有的 `_tools_bundle` helper（与 test_regression_edge 的 `_tools()` 同构）——该文件此前的约定就是「M4 后所有 run_report 测试须显式传离线 tools」，6 处漏网是 P1 之前就存在的债，P1 只是把它引爆。

## 三、验证证据

- 修复前失败子集复跑：8 failed（含 80s+ 网络重试等待）
- 修复后失败子集：**8 passed, 0.80s**
- 修复后全量：**41 passed, 0 failed, 2.79s**（修复前 660.90s，提速 236 倍）
- py_compile：本次仅改 2 个测试文件，pytest 全绿即语法+行为双重验证
- 副作用检查：生产代码 diff = 0；两测试文件 diff 仅注入接缝 + tools 参数 + import；`test_empty_retrieval_escalates_despite_llm_advance`（故意空检索场景）等既有离线测试全部保持通过 = 未破坏故障模拟路径

## 四、遗留

- `datetime.utcnow()` DeprecationWarning（orchestrator.py:37）40 条——外观债，低优先级，可后续统一换 `datetime.now(UTC)`。
- 教训固化：**测试清单里出现网络超时/429/SSL 错误 = 测试不纯**，必须先查 tools 注入而不是怀疑引擎逻辑；「与 X 零 import 依赖」不能证明「运行时零依赖」——build_tools 的依赖是运行时注入的。
