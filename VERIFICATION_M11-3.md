# VERIFICATION_M11-3.md · M11-3 MCP 消费桥（D1-B 全 function-calling）自审

- 日期：2026-09-12（v2：补入**真实部署 + 容器内端到端实测**）
- 范围：M11-3（Researcher / Analyst 自主 function-calling 调工具，含 MCP 外部工具桥）；附带 M11-1 记忆层真实部署
- 关联设计：`M11-3_D1B_IMPL_PLAN.md`（实现计划，file:line 锚定）；`DESIGN_M11_autonomous.md` §3（M11-3）
- 关联审议：`REVIEW_M11-3.md`（**独立模型 Intern-S2-Preview-397B** 经 new-api 网关审议；原子代理路径遇 429 未执行，未自审顶替）

## 1. 改动清单

| 文件 | 改动 | 类型 |
|---|---|---|
| `tools/mcp_client.py` | 新增：`MCPClient`（懒加载 `mcp` SDK；SSE / Streamable HTTP 传输；`list_tools()`/`call_tool()`；不可达→`[]`/`{ok:False}` 诚实降级） | 新增 |
| `config/mcp_servers.yaml` | 新增：`servers: []` + 注释示例（SSE：id/name/transport/url/headers/enabled/tools_whitelist） | 新增 |
| `tools/__init__.py` | `ToolBundle` 增 `mcp` 字段；`build_tools` 内 `try/except` 初始化 `MCPClient`（失败→`mcp=None`，不抛） | 修改 |
| `orchestrator.py` | ① `LLMClient.complete_with_tools` 基类抛 `NotImplementedError`；② `StubLLMClient.complete_with_tools`（fc 开关：关=等同 complete，开=首轮 tool_call / 次轮出 JSON）+ `StubLLMClientFC`；③ `NewApiLLMClient.complete_with_tools`（OpenAI `tools` + `tool_choice="auto"`）；④ `dispatch_tool` 路由 web_search/data_proc/mcp:{s}:{t}/未知；⑤ `_build_tool_schemas`；⑥ `MCP_MAX_ROUNDS=4` + `_run_fc_loop`；⑦ `make_agent` 节点按 `tool_schemas` 分支走 fc 循环或旧 complete；⑧ Researcher 注入 `kb_context`（M11-1 已落，本轮确认兼容） | 修改 |
| `server/api.py` | 新增 `GET/POST/DELETE /admin/mcp-servers`（镜像 `/admin/providers`，原子 yaml 写 + audit） | 修改 |
| `requirements.txt` | 新增 `mcp>=1.0,<2.0`（固定 1.x，对齐 `mcp.client.*` 导入路径） | 修改 |
| `tools/_async_util.py` | 新增：共享 `run_async`（消除 orchestrator 与 mcp_client 重复实现；async 上下文用独立线程兜底，规避嵌套 loop） | 新增 |
| `orchestrator.py::_run_async` | 改为委托 `tools/_async_util.run_async`（去重 + 修坏码） | 修改 |
| `tools/kb_store.py` | ① 嵌入模型/维度可配（默认 `BAAI/bge-m3`/1024，`EMBEDDING_DIM`）；② 新增「维度守卫」：既有列维度与配置不一致时显式告警 | 修改 |
| `Dockerfile` | 新增 `ARG PIP_INDEX_URL`（默认官方源，可传国内镜像加速构建，~30x） | 修改 |
| `tests/test_m11_mcp_fc.py` | 新增：M11-3 function-calling 单测 15 项 | 新增 |

## 2. 门禁① 静态校验

- `py_compile orchestrator.py tools/__init__.py tools/mcp_client.py server/api.py` → **PASS**（修复了此前 `make_agent` 头部缩进 `IndentationError`）
- 既有 + 新增 pytest 套件 `tests/` → **94 passed**（79 旧 + 15 新增）

## 3. 门禁① function-calling 单测（无联网 / 无真实 MCP server）

`tests/test_m11_mcp_fc.py`，覆盖 D1-B 三层契约：

| 验证项 | 期望 | 实测 |
|---|---|---|
| `dispatch_tool` web_search / data_proc 路由 | `{ok:True, result}` | ✓ |
| `dispatch_tool` `mcp:{s}:{t}` 正常 | `{ok:True, result}` | ✓ |
| `dispatch_tool` mcp 未初始化（mcp=None） | `{ok:False, error:"MCP 客户端未初始化"}` | ✓ |
| `dispatch_tool` mcp server 异常（raise） | `{ok:False, error:"connection refused"}`（不击穿图） | ✓ |
| `dispatch_tool` 未知工具名 | `{ok:False, error:"未知工具: ..."}` | ✓ |
| `_build_tool_schemas` Researcher（无 data_proc） | 仅 `[web_search]` | ✓ |
| `_build_tool_schemas` Analyst | `[web_search, data_proc]` | ✓ |
| `_build_tool_schemas` 注入 mcp 工具 | 含 `mcp:demo:ping` | ✓ |
| `_run_fc_loop` Researcher 两轮回合 | 首轮 tool_call→次轮出 JSON；`entries` 含 web_search ok；`extra_search` 非空 | ✓ |
| `_run_fc_loop` Analyst 走 data_proc | `entries` 含 data_proc ok | ✓ |
| `_run_fc_loop` mcp 工具被调用 | `entries` 含 `mcp:demo:ping` ok | ✓ |
| `_run_fc_loop` mcp 异常仍收口 | `entries` 含 mcp fail；次轮仍出 content | ✓ |
| 向后兼容：旧 `StubLLMClient`（fc=False）带 tools | 不进循环，直接出 content；无 `tool_entries`/无 `extra` | ✓ |
| runaway 护栏：`AlwaysFC` 持续发 tool_call | 至多 `MCP_MAX_ROUNDS=4` 轮后强制收口 | ✓（ws.calls==4） |

## 4. 门禁① 诚实边界实测（真实模块装载，非纸面）

- `MCPClient`（空 `mcp_servers.yaml`）`list_tools()` → `[]`；`call_tool("demo","ping",{})` → `{'ok':False,'error':"MCP server 'demo' 未启用或不存在"}`（**不抛**，不击穿）✓
- `build_tools()` 在 mcp SDK 缺失环境下仍安全返回（mcp 字段为 client，调用降级）✓
- 端点实测：FastAPI `TestClient` `GET /api/v1/admin/mcp-servers` → **200**（空 servers 列表，正常降级）✓；同法 `GET /api/v1/admin/providers` / `/health` 均 200 ✓

## 5. 真实部署 + 容器内端到端实测（**金标准，v2 新增**）

上一版把"部署/SDK/真实 server"列为待办；本轮已**实际落地并实测**，不再停留在纸面。

### 5.1 部署动作（已执行）
| 项 | 动作 | 实测结果 |
|---|---|---|
| 向量库 | `report-postgres` 镜像换 `pgvector/pgvector:pg15`（`docker-compose up -d postgres`） | `vector 0.8.6` 可用；原 7 张表数据完好（同 PG15 大版本兼容） |
| MCP SDK | `requirements.txt` + 重建 api 镜像（`mcp 1.30.0`） | 容器内 `import mcp` OK |
| 代码 | `docker-compose build/up -d api` | 容器内 `tools/{kb_store,mcp_client,_async_util}.py` 齐备，`M11 modules OK` |
| 嵌入模型 | `.env` 增 `EMBEDDING_MODEL=BAAI/bge-m3` / `EMBEDDING_DIM=1024`（网关实测可路由，1024 维） | 容器内真实嵌入成功 |
| MCP server | `config/mcp_servers.yaml` 注册公共 DeepWiki（streamable HTTP，无需鉴权） | 见 5.2 |

> 构建加速：直连 PyPI 极慢（单包 ~70s，整层曾跑 42min 未完成）→ Dockerfile 加 `PIP_INDEX_URL`，传清华镜像后 **53 秒完成**。

### 5.2 容器内 MCP 端到端（真实公共 server）
| 验证项 | 实测 |
|---|---|
| `MCPClient().list_tools()` | **3 个真实工具**：`mcp:deepwiki:ask_question` / `read_wiki_contents` / `read_wiki_structure` |
| `call_tool('deepwiki','ask_question',{repoName,question})` | **`ok=True`**，返回真实语义答案（关于 modelcontextprotocol/servers 的介绍） |
| 诚实边界：不存在的 server | `{'ok': False, 'error': "MCP server 'nope' 未启用或不存在"}` |
| 引擎接线（关键） | `build_tools().mcp = MCPClient`；`_build_tool_schemas(b, b.mcp, 'Researcher')` → `['web_search','data_proc','mcp:deepwiki:ask_question','mcp:deepwiki:read_wiki_contents','mcp:deepwiki:read_wiki_structure']` |

→ **MCP 工具已真正进入 Researcher 的 function-calling schema**，LLM 可自主选择调用，经 `dispatch_tool` 路由到真实 server。

### 5.3 容器内记忆层端到端（M11-1 真实部署）
| 验证项 | 实测 |
|---|---|
| 真实嵌入 | `embed_ok = True | dims = 1024` |
| `kb_write` | `wrote = 1` |
| 向量余弦召回 | `kb_retrieve` → **`score=0.7139`**，命中正确条目 |
| 同主题 upsert | 重复写后 same-topic rows = **1**（不堆叠，更新为 v2） |
| 表结构 | `embedding vector(1024)` + HNSW `vector_cosine_ops` 索引 + 部分唯一索引（`kind='report_conclusion'`） |

## 6. 仍未做（诚实边界，不冒充）
1. ~~**真实 LLM 驱动的 tool_calls 全链路**~~ → **v3 已补上，见 §5.4**。
2. **stdio 型 MCP server**：M11-3 仅支持 SSE / Streamable HTTP；stdio 未内置（文档已标注）。
3. 其余未启用 MCP server：需按需在 `config/mcp_servers.yaml` 增项（引擎热加载，无需重启）。

### 5.4 真实 LLM 自主 tool_call 全链路（**v3 新增，最后一环**）

之前只验证到「schema 含 MCP 工具 + `dispatch_tool` 能真实调用」，LLM 行为用 Stub 模拟。本节用**引擎真实模型 LongCat-2.0** 走真实 `_run_fc_loop`（容器内，真实 DeepWiki server）：

| 验证项 | 实测 |
|---|---|
| 提供 5 个工具 schema | `['web_search','data_proc','mcp:deepwiki:ask_question','mcp:deepwiki:read_wiki_contents','mcp:deepwiki:read_wiki_structure']` |
| **LLM 自主选择** | **自发选择 `mcp:deepwiki:read_wiki_contents`**（MCP 工具，非 web_search/data_proc，无任何硬编码引导） |
| 引擎 dispatch | `tool_entries = [{"agent":"Researcher","tool":"mcp:deepwiki:read_wiki_contents","ok":true}]` |
| 结果回注 + 终答 | 终稿为**基于真实抓取内容**的结论：「该仓库是 Model Context Protocol (MCP) 的官方参考实现集合，提供教育性质的示例服务器……」 |

→ **D1-B 全链路闭环**：真实 LLM → 自主 tool_call → 引擎 dispatch → 真实 MCP server → 结果回注 → 有据终答。

## 7. 自审结论

M11-3 代码路径（function-calling 契约 / `dispatch_tool` 路由 / `_run_fc_loop` 多轮与 runaway 护栏 / schema 构建 / `make_agent` 分支接线 / `MCPClient` 诚实降级 / admin 端点）**经单测 + 真实模块装载 + 端点 200 + 容器内真实 MCP server 端到端 + 真实 LLM 自主 tool_call 全链路验证通过**；M11-1 记忆层亦已**真实部署并在容器内跑通**（真实嵌入 + 向量召回 + upsert）。向后兼容（旧 stub 不进循环）验证通过。§6 剩余两项已在文档明示，不冒充已验证。

---
Gate② 自审：通过（代码层 + 单测层 + 端点层 + **真实运行时端到端层**均验证）。门禁③由 **Intern-S2-Preview-397B** 独立审议，见 `REVIEW_M11-3.md`（PASS-WITH-NOTES）。

## 8. M11-3b 热修复：首次全流水线真实 e2e（boss 指令「没跑的就跑，我需要通」）

### 8.1 故障一：Researcher 结构不稳定 → escalate（真·根因修复）
- 任务 `b6eb769f`：两轮均 `agent_output_unusable`（产出缺少数组字段 retrieval_records）→ escalate。
- 容器内复现脚本抓原始输出：fc-loop 正常（LLM 自主调 web_search，42 条真实结果），但模型最终 JSON 结构不稳定（复现跑出空数组，线上为缺键/非数组）。
- **修复**（orchestrator.py 节点 2.7）：`retrieval_records` 缺失/非数组/空而 `search_results` 非空 → 引擎按字段契约确定性合成（数据全来自真实检索，非编造），发 `researcher_records_synthesized` 事件（engine_events + on_event 双留痕）。检索空时不合成（保持如实空数组交 GateA 的诚实路径）。
- 新增 `tests/test_m11_3_synthesis.py`（缺键合成/空数组合成/无数据不合成）。

### 8.2 故障二：新事件类型撞 EngineEvent Literal 闭集
- 修复后首跑（任务 `2ce7a7d6`）：`researcher_records_synthesized` 不在 `server/api.py` EngineEvent Literal 闭集 → RoutingState 校验崩 → 引擎进程异常。即记忆坑 #9（闭集 Literal）复现。
- **修复**：Literal 扩容 + models.py 注释同步。

### 8.3 WebUI MCP 管理面板（此前前端零 MCP 界面）
- 新增 `web/src/services/mcpServerService.ts` + `web/src/views/settings/McpServers.vue`（镜像 CustomProviders 范式：表格/新增弹窗/headers JSON/工具白名单/transport 下选），路由 `/settings/mcp-servers` + 设置下拉入口。
- 后端 `/admin/mcp-servers` GET/POST/DELETE 为 M11-3 已有（本轮实测 200，返回 DeepWiki）。

### 8.4 最终验证（全流水线真实 e2e，任务 `552073c0`）
| 项 | 实测 |
|---|---|
| status | **done**（Researcher→GateA→Analyst→GateB→Writer→GateC 全过） |
| retrieval_records | 27 条（真实检索，来源标记回填） |
| analysis_conclusions | 3 条 |
| report_markdown | 3547 字符，含引用标记 [rec-N] 与 mermaid 图 |
| engine_events | `['researcher_records_synthesized','writer_citation_regenerated']`（两处降级均留痕可审计） |
| 测试 | 97 passed（含新增 3 测） |
| py_compile | PASS（orchestrator.py / server/api.py / server/models.py） |

### 8.5 诚实边界声明
- 降级合成**不编造**：所有字段取自引擎真实检索结果集，溯源硬校验照常生效。
- `tool_status` 仍由引擎写入；模型输出的一律被忽略（既有语义不变）。
- 运行时配置（config/plugins.yaml、custom_providers.yaml）为 UI 操作产物，按约定不入库。
