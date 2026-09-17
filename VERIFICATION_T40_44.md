# VERIFICATION · T40–T44（聊天体能力补齐 + 受限沙箱）

> 门禁：py_compile → VERIFICATION 自审 → 独立子代理 REVIEW → 全量 pytest → commit（不 push）
> 关联 boss 指令「1-2-3-4全做」：② 接入 M12 skill_importer ③ 专用工具感知 + 诚实空结果 ④ 受限文件/执行沙箱

## 1. 变更清单

| 文件 | 改动 | 对应任务 |
|------|------|---------|
| `tools/data_sources.py` | Open-Meteo `_CN_CITY_ALIASES` 中文城市→英文名表；geocoding 别名回落（厦门→Xiamen）；`INTENT` 标注 weather/news/web；`SearchTool.search_many` 按查询意图路由源（仅 ChatAgent 启用） | T40 / T41 |
| `orchestrator.py` | `dispatch_tool` 新增 `install_skill` / `run_python` / `run_shell` 分支；`_build_tool_schemas` 为 ChatAgent 暴露这三个专用工具 | T42 / T43 / T44 |
| `chat_agent.py` | `CHAT_SYSTEM_PROMPT` 增天气/新闻/install_skill/沙箱工具说明 + 诚实空结果指引 | T42 / T43 |
| `tools/chat_sandbox.py` | **新增** 受限沙箱：`run_python` / `run_shell`（固定工作目录、超时、输出截断、危险命令拦截、跨平台 shell） | T44 |
| `tests/test_tools.py` | T40/T41/T44 回归 + ChatAgent 专用 schema 可见性测试（共 12 例） | 测试 |
| `tests/test_m11_mcp_fc.py` `tests/test_chat_mcp_learning.py` | `FakeWebSearch.search_many` 适配新 `route_by_intent` 形参（生产签名已加该形参） | 测试修复 |
| `tests/test_m12_review_fixes.py` | `test_open_meteo_retries_other_language_when_no_match` 改用非别名城市「伦敦」，避免别名扩展把调用数从 2 变 4 | 测试修复 |
| `.gitignore` | 忽略 `.chat_sandbox/` | T44 |

## 2. 设计要点与诚实边界

- **T40 天气 geocoding**：中文城市名 Open-Meteo geocoding 无匹配 → 回落 `_CN_CITY_ALIASES` 英文名再查（`厦门`→`Xiamen` 稳定命中）。
  - **MAJOR-4 契约保留**：geocoding 端点**网络失败（ToolError）即 `return []`**，不换语言/别名重试（同一域名同错误，重试只翻倍耗时、越 2 分钟线）。`_http_get_json_retry` 已内部退避 3 次仍失败才抛 → 直接返回空降级。
  - 仅「请求成功但无匹配」路径才试别名 + zh/en 两语言。
- **T41 意图路由**：`route_by_intent` 仅 `(role == "ChatAgent")` 启用，引擎研究检索（Researcher/Analyst）全源聚合 **零回归**。`_query_intent` 顺序 weather > news > general。
- **T42 skill_importer 接入**：`install_skill(url)` → `skill_importer.import_skill`（L0 自动装 / L1 交审批 / L2 拒绝附因）。结果结构化回注模型，由模型转述用户，**绝不静默自改 prompt/代码**。
- **T43 诚实空结果**：system prompt 明确「工具返回空/失败时点明 failed_sources 具体源」，不得用「我无法实时获取」笼统带过；`_run_fc_loop` 已把 `failed_sources` 回注模型。
- **T44 受限沙箱**：工作目录固定 `<repo>/.chat_sandbox/`（gitignore）。
  - `run_python`：**独立子进程 `-I` 隔离模式 + 受限内置命名空间**（无 `os`/`subprocess`/`shutil`/`sys`/文件 builtins，仅 stdlib import 白名单）→ 真正无法 `os.system`/读写沙箱外/逃逸；子进程 15s 超时防死循环挂住 API。
  - `run_shell`：固定 cwd + 15s 超时 + 输出截断 + **危险命令拦截 + 命令分隔符拦截**（`;`/`&&`/`||`/`|`/反引号/`$()`/换行，防 `echo hi; rm -rf /` 串联）；跨平台 shell（Linux `/bin/sh`、Windows `cmd.exe`）。
  - **诚实边界**：沙箱为「尽力收窄、限定沙箱工作目录」的边界（容器内 appuser 无 sudo，爆炸半径限于容器可写区），**非 OS 级硬边界**（无 seccomp/namespace/chroot），docstring 已据实表述，不夸大「无逃逸通道」。独立审议 HIGH 项已修复并补测。

## 3. 验证结果

- **Gate 1 · py_compile**：`orchestrator.py` `chat_agent.py` `tools/data_sources.py` `tools/chat_sandbox.py` `tools/skill_importer.py` 全部 EXIT=0。
- **Gate 4 · pytest**：`PYTHONPATH=report-agent-team pytest tests/` → **214 passed**（含本次新增 12 例 + 修复 3 例）。
  - 新增：`test_open_meteo_cn_city_geocoding_alias` / `_english_city_direct` / `_geocoding_truly_empty_returns_empty`、`test_query_intent_classification`、`test_search_many_routes_weather_only_openmeto_and_web` / `_routes_news_only_rss_and_web` / `_general_hits_all_sources` / `_no_route_calls_all`、`test_chat_sandbox_blocks_dangerous_command` / `_runs_python` / `_runs_shell`、`test_chat_agent_only_schemas_exposed_to_chatagent`。
- **回归确认**：`test_open_meteo_network_failure_does_not_retry_other_language`（MAJOR-4，==1 调用）通过；`test_open_meteo_retries_other_language_when_no_match`（==2 调用，改用伦敦）通过；`dispatch_tool` web_search 系列（M12 真实记录回归）全部通过。

## 4. 自审结论

变更对齐 boss「1-2-3-4全做」四步。天气/新闻真实可达（路由 + 别名）、skill 可真安装、沙箱受限安全、空结果诚实。引擎研究检索路径零回归（路由默认关闭）。无已知遗留 bug；`tavily` 无 key 仍 disconnected，天气/新闻意图已不调用它（路由排除非 weather/web/news 源），故不再污染。

待独立子代理 REVIEW 闭环后 commit（不 push，等 boss 显式推送指令）。
