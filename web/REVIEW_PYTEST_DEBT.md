<!-- reviewed-by: independent-subagent -->

# REVIEW — pytest 预存债还清（TD-010：测试离线化 + 多端点 mock 接缝修复）

角色：独立代码审议子代理（门禁③）
日期：2026-09-10
工作目录：E:\第二电脑\report-agent-team
Python：C:/Users/sfkj/.workbuddy/binaries/python/versions/3.13.12/python.exe

---

## Verdict

**PASS_WITH_NOTES**

生产代码零改动；两条根因均通过纯测试修复闭合；全量 41 passed，无真实网络调用；注入接缝成立。

---

## Summary

P1 交付的 8 failed pytest 预存债，主代理诊断出双根因（A：P1 keyless 真源让裸调 run_report 的测试变真联网；B：多端点重构后 mock 注入到废弃属性），并给出纯测试修复方案。独立审议确认根因机理成立、修复正确、验证结果可信。

---

## MAJOR（阻塞项）

无。

- 生产代码（orchestrator.py / tools/ / server/）在 git diff 中零改动。仅 `tests/test_orchestrator.py` 与 `tests/test_regression_edge.py` 有变更（16 insert, 7 delete）。
- 无残留真实网络调用：全量 4.01s 完成（修复前 660s），若仍有真实 Tavily/arXiv 等调用不可能此量级。
- mock 注入接缝成立：`c._clients["t"] = fake` 与 `complete()` 的 `_get_client` 路径对齐。

---

## MINOR（外观）

- `orchestrator.py:37` 的 `datetime.utcnow()` DeprecationWarning 在 41 个测试中触发 40 次。主代理已在 VERIFICATION_PYTEST_DEBT.md §4 标注为低优先级外观债。不阻塞。
- `test_orchestrator.py` 顶部 `from tools.web_search import MockProvider` import 之后，文件内 `test_fabricated_url_is_blocked`（165 行）又重复 import 了一次 `MockProvider`。属冗余 import，不阻塞。

---

## Gaps

无。所有必读文件均可读，所有必跑命令均可执行。

---

## 实读确认（真实 file:line）

### 1. NewApiLLMClient 多端点缓存（orchestrator.py 243-336）

- `__init__`（243-256）：`self._clients: Dict[str, Any] = {}`（254 行），按 endpoint_id 缓存 OpenAI 客户端。
- `_resolve`（258-277）：`_default_endpoint_id` 缺省返回 `"t"`（测试传入的端点 id）。
- `_get_client`（279-293）：若 `endpoint_id not in self._clients` 才构造真 `OpenAI`；否则返回缓存。`complete()`（308-336）在 311 行调用 `self._get_client(endpoint_id)`。
- 结论：只要 `c._clients["t"]` 被 fake 覆盖，`complete("m", ...)` → `_resolve("m")` → `("t", "m", ...)` → `_get_client("t")` 命中缓存 fake，**不再创建真 OpenAI 客户端**。键 `"t"` 与 `_mk_gateway_client` 构造的 `endpoints[0]["id"]="t"`（test_regression_edge.py:287）完全一致。

### 2. _mk_gateway_client 注入接缝（test_regression_edge.py 284-297）

- 284-292 行构造 `NewApiLLMClient(endpoints=[{"id": "t", ...}], default_endpoint_id="t")`。
- 296 行：`c._clients["t"] = fake`（原为 `c._client = fake`）。
- 注释（294-295 行）明确说明多端点架构下须注入端点缓存槽。旧属性 `_client` 已废弃。
- 结论：接缝正确，7 个重试逻辑测试（300 行起）不再被真客户端覆盖 fake。

### 3. test_orchestrator.py 全部 run_report 调用点（grep 实证）

共 8 处调用（含 `__main__` 块），**全部带 `tools=` 参数**：

- 30 行：`tools=_tools_bundle(MockProvider())`
- 49 行：`tools=_tools_bundle(MockProvider())`
- 68 行：`tools=_tools_bundle(MockProvider())`
- 103 行：`tools=_tools_bundle(MockProvider())`
- 120 行：`tools=_tools_bundle(MockProvider())`
- 166 行：`tools=_tools_bundle(MockProvider())`
- 184 行：`tools=_tools_bundle(DeadProvider())`（已有）
- 199 行（__main__）：`tools=_tools_bundle(MockProvider())`

无任何裸调 run_report（未传 tools）。6 处 P1 漏网点已补全。

### 4. _tools_bundle 构造参数（test_orchestrator.py 132-143 行 vs tools/__init__.py 22-28 行）

- `ToolBundle` 字段：`web_search`（25 行）、`data_proc`（26 行）、`doc_export`（27 行）、`using_mock_search`（28 行，默认 False）。
- `_tools_bundle`（134-143 行）构造：`web_search=WebSearchTool(provider=provider, max_results=3)`、`data_proc=DataProcTool(enabled=True)`、`doc_export=DocExportTool(enabled=True, export_dir=None)`。三字段名与 `ToolBundle` 完全一致。
- `MockProvider` 来自 `tools.web_search`（test_orchestrator.py 16 行、test_regression_edge.py 27 行），与 `WebSearchTool(provider=...)` 签名兼容。

### 5. 空检索场景仍在通过

- `test_empty_retrieval_escalates_despite_llm_advance` 位于 test_regression_edge.py:130 行。
- 实测 pytest 全量列表中该测试通过（41 passed 包含）。
- 故障模拟路径未被破坏：空检索 → GateA escalate 的硬校验逻辑仍生效。

### 6. 生产代码零改动（git diff 实证）

- `git diff -- orchestrator.py tools/ server/` 输出空。
- `git diff --stat -- tests/ web/` 仅显示 2 个测试文件改动，无 VERIFICATION_PYTEST_DEBT.md 改动（该文件为预读文档，非本次提交产物）。
- 主代理自述「生产代码零改动」与实测一致。

---

## 实跑命令与退出码

### 命令 1：全量 pytest

```
cd E:\第二电脑\report-agent-team
C:/Users/sfkj/.workbuddy/binaries/python/versions/3.13.12/python.exe -m pytest tests/ -q --tb=short
```

**退出码：0**

```
.........................................                                [100%]
============================== warnings summary ===============================
tests/test_orchestrator.py: 20 warnings
tests/test_regression_edge.py: 20 warnings
  E:\第二电脑\report-agent-team\orchestrator.py:37: DeprecationWarning: datetime.datetime.utcnow() is deprecated ...
-- Docs: https://docs.pytest.org/en/howto/capture-warnings.html
41 passed, 40 warnings in 4.01s
```

- **通过数：41 passed，0 failed**
- **时长：4.01s**（秒级，非 11 分钟级 → 无残留真实网络调用）
- 40 条 warning 均为 `datetime.utcnow()` DeprecationWarning（外观债，见 MINOR）。

### 命令 2：git diff 范围核对

```
git -C "E:\第二电脑\report-agent-team" diff --stat -- tests/ web/
```

**退出码：0**

```
report-agent-team/tests/test_orchestrator.py    | 19 +++++++++++++------
report-agent-team/tests/test_regression_edge.py |  4 +++-
2 files changed, 16 insertions(+), 7 deletions(-)
```

仅 2 个测试文件改动，生产代码零改动。

### 命令 3：空检索场景确认

grep `test_empty_retrieval_escalates` → test_regression_edge.py:130 行定义，pytest 全量通过列表中包含（41 passed 覆盖）。

---

## 根因机理复核（与主代理自审一致）

- **根因 A**：P1 keyless 让 `build_search_tool` 的 `has_real=True`，裸调 `run_report` 时 `build_tools()` 拉起真实网络源。`test_max_rounds_escalate` 的真实检索内容干扰 `_extract_injected_records` 括号配对 → 返回 [] → GateA 误判 escalate → 绕过 max_rounds reason 分支。修复：6 处裸调补 `tools=_tools_bundle(MockProvider())`。已验证。
- **根因 B**：多端点重构后 `complete()` 走 `self._clients[endpoint_id]` 缓存，`_mk_gateway_client` 仍注入 `c._client`（已废弃）→ fake 被真客户端覆盖 → 7 个重试测试打 127.0.0.1:9 真实连接。修复：改注 `c._clients["t"] = fake`。已验证。

---

## 结论

8 failed 预存债已纯测试修复闭合，生产代码零改动，全量 41 passed / 4.01s，无真实网络调用，mock 注入接缝成立。裁决 **PASS_WITH_NOTES**。仅留 `datetime.utcnow()` DeprecationWarning 外观债（MINOR），低优先级，后续统一修复即可。