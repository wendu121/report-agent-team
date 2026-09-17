# DESIGN_M11_autonomous.md · 自主学习 + 联网持久化 + MCP 接入

- 日期：2026-09-12
- 依赖：DESIGN.md v2.4（SoT）；M9-2 数据源插件架构（`build_search_tool` 多源聚合，已就绪 ✅）；M10-P4 自定义 Agent/Skill 注入（✅）；`/admin/providers` 端点范式（✅ 可镜像）
- 状态：草案，待评审议评审
- 作者：（主代理）

---

## 0. 目标与范围

让 report-agent-team 从「无状态单次任务机」升级为「**有记忆、能复盘、可扩展工具**」的自治研究系统。三块独立可交付：

| 编号 | 能力 | 对应 boss 诉求 | 解决的现状缺口 |
|---|---|---|---|
| M11-1 | 持久知识层（pgvector） | 自主学习（记忆） | 每次 `run_report()` 独立、无跨任务记忆 |
| M11-2 | 反思回写闭环 | 自主学习（改进） | 三道闸只判本报告、结论不回写、Agent 不从错误长进 |
| M11-3 | MCP 消费桥（D1-B 全 function-calling） | 接入 MCP | 引擎零 MCP 客户端，无法调外部工具 |

> 范围澄清：**联网搜索本身已具备**（`build_search_tool` + 13 零密钥真源 + Tavily，见 M9-2）。M11 不重建检索，只做「检索结果持久化 + 复盘 + 外部工具接入」。若 boss 实测觉得「搜不到」，那是部署连通性（容器内 GDELT TLS / 沙箱代理），不在本设计范围，另行排障。

---

## 1. 现状事实（实测，非纸面）

| 事实 | 证据 | 结论 |
|---|---|---|
| 无持久记忆层 | 全仓 grep `vector/embed/knowledge_base/persistent` → Python 引擎零命中（仅 node_modules/dist） | 跨任务记忆缺失，确凿 |
| 工具是结构化管道，非 function-calling | `orchestrator.py:994` `tools.web_search.search_many(queries, agent=role)`；LLM 只产 `queries` | Researcher 不能直接调任意工具 |
| 工具按 shape 分发 | `server/admin.py:773` `SHAPE_TOOL={"researcher":"web_search","analyst":"data_proc","writer":"doc_export"}` | MCP 工具需新挂载点 |
| Postgres 无向量扩展 | `docker-compose.yml:9` `image: postgres:15-alpine` | 需换 pgvector 镜像或外置向量库 |
| admin 端点可镜像 | `server/api.py:1115-1182` `/admin/providers` GET/POST/DELETE + `CustomProviderCreate` | MCP server 管理照抄 |
| 配置 SoT 已存在 | `config/plugins.yaml`（数据源）、`config/agents_library.yaml`、`config/skills.yaml` | 复盘产物可回写到这些文件（已有控制器） |

---

## 2. M11-1 持久知识层

### 2.1 存储
- **换镜像**：`docker-compose.yml` `postgres` 服务 `image` 由 `postgres:15-alpine` → **`pgvector/pgvector:pg15`**（同版本、drop-in，仅多 vector 扩展）。
- **初始化**：新增 `db/init/01_kb.sql`：`CREATE EXTENSION IF NOT EXISTS vector;` + 建 schema `kb`。
- **表 `kb.entries`**：
  ```sql
  CREATE TABLE kb.entries (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,          -- 'report_conclusion' | 'retrieval_record' | 'reflection'
    topic_hash    TEXT NOT NULL,          -- 主题归一哈希，用于去重/召回
    title         TEXT,
    content       TEXT NOT NULL,          -- 结论/素材正文
    embedding     vector(1536),           -- 见 2.3 嵌入决策
    source        TEXT,                   -- 来源（report id / plugin id / mcp server）
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now()
  );
  CREATE INDEX ON kb.entries USING hnsw (embedding vector_cosine_ops);
  ```

### 2.2 新增节点（LangGraph）
- **`KBRetrieve`（Router 之前）**：对用户任务做嵌入 → `kb.entries` 余弦 Top-K（含 `report_conclusion` + 高可信 `retrieval_record`）→ 注入 Researcher `extra_context`（复用现有 `search_results`→`extra_context` 通道，`orchestrator.py:994` 下游）。
- **`KBWrite`（GateC 之后）**：取最终报告 + `retrieval_records`，切块 → 嵌入 → upsert（按 `topic_hash` 去重，同主题更新而非堆叠）。

### 2.3 嵌入模型（决策点 D2）
- **2026-09-12 boss 确认：兼容 OpenAI 格式即可** → 即 new-api 网关 `/v1/embeddings`（OpenAI 兼容）满足；如有其他 OpenAI 格式 embedding 端点也可替换。复用 `LLMClient` 的 base_url/key 配置，零新增密钥。
- **推荐**：经 new-api `/v1/embeddings` 取 embedding。
- **降级（诚实边界）**：若网关无 embedding 模型，则 **不建向量列、改用全文 + 时间衰减排序**（仍持久化 `content`/`metadata`，只是召回退化为关键词匹配）。绝不伪造 embedding。

### 2.4 改动文件
| 文件 | 改动 |
|---|---|
| `docker-compose.yml` | postgres 镜像 → pgvector/pgvector:pg15 |
| `db/init/01_kb.sql` | 新增（建 extension + 表 + 索引） |
| `tools/kb_store.py` | 新增：嵌入封装 + `kb_retrieve(query,k)` / `kb_write(entries)`（asyncpg，连 `ASYNC_DATABASE_URL`） |
| `orchestrator.py` | `run_report` 增加 `KBRetrieve` / `KBWrite` 节点；`KBRetrieve` 输出注入 Researcher context |
| `server/api.py` | 新增 `GET /api/v1/kb?q=&k=` 供前端/调试查看知识库 |

---

## 3. M11-2 反思回写闭环

### 3.1 捕获
任务结束时（GateC pass 或 escalate）收集：各 Gate 的 review 结论、escalate_reason、`rework` 轮数、`retrieval_records` 的 source/credibility 分布、空产出源。

### 3.2 生成建议（不直接改系统）
由 Analyst 或独立 `Reflector` 节点产出 **`Reflection` 条目**（落 `kb.entries` kind=`reflection`，同时写 `config/reflections.proposed.yaml`）：
- 例：「主题 X 反复需源 Y → 建议启用插件 Y」；「Agent Z prompt 触发 rework → 建议技能微调」。
- 每条带 `status: proposed` + `target`（plugins.yaml / skills.yaml / agents_library.yaml 的具体键）。

### 3.3 人工确认入库（守诚实边界，绝不静默自改 prompt）
- 新增 `GET /admin/reflections`（列出 proposed）、`POST /admin/reflections/{id}/accept`（校验 target 合法后写回对应 yaml，复用现有原子写）。
- 前端「反思建议」面板：列表 + 接受/驳回。接受后才真正改 `skills.yaml` 等（已有热加载，下个任务生效）。
- **铁律**：任何 `proposed` 永不自动 apply；引擎不因 reflection 改变行为，除非 boss/管理员显式 accept。

### 3.4 改动文件
| 文件 | 改动 |
|---|---|
| `orchestrator.py` | 任务结束汇总 gates/records → `tools/reflection.py::generate_reflection` |
| `tools/reflection.py` | 新增：汇总 + 生成 Reflection + 落库 |
| `config/reflections.proposed.yaml` | 新增（git-ignored，运行时生成） |
| `server/admin.py` | 新增 `/admin/reflections` GET、`/admin/reflections/{id}/accept` POST |
| `web/src/views/` + `reflections` 面板 | 新增（可选，M11-2b） |

---

## 4. M11-3 MCP 消费桥

### 4.1 决策 D1 — MCP 接入方式（boss 拍板：直接上 D1-B）

> **2026-09-12 boss 决策：MCP 直接上 D1-B（全 function-calling），不做 D1-A 过渡。**

当前 Researcher 是「LLM 产 queries → 编排层调工具」，**非 function-calling**。D1-B 把它升级为真 function-calling：

- **Researcher / Analyst 改为 function-calling 节点**：LLM 输出 `tool_calls:[{name, args}]`，编排层 `dispatch_tool(name, args)` 分发到 `web_search`（`SearchTool.search_many` 兼容包装）/ `data_proc` / `mcp:{server}:{tool}`，支持**多轮 tool_call**（LLM 看工具结果再决定下一步），直到输出终态内容。
- **工具 schema 注入**：`make_agent` 接收 `tools_schema`（OpenAI 格式 `{"type":"function","function":{"name","description","parameters"}}`）；Researcher 拿 `web_search` + 所有启用 MCP 工具，Analyst 拿 `data_proc` + MCP。
- **MCP 工具执行器**：`mcp_bridge.call_tool(server, tool, args)` → `session.call_tool(...)` → 规整结果（带 `source=mcp:{server}:{tool}` + `credibility`）；失败按现有 `except Exception` 兜底，绝不冒充可用。
- **能力最全**：只读/查询、多步写、有状态工具全覆盖；无需 D1-A 过渡。代价是 Researcher 节点改造较大（见 4.3）。

### 4.2 配置 SoT
`config/mcp_servers.yaml`：
```yaml
servers:
  - id: my-browser
    name: 浏览器自动化
    transport: sse            # stdio | sse | http
    url: http://host.docker.internal:3001/sse
    headers: { Authorization: "Bearer ${MCP_MY_BROWSER_TOKEN}" }
    enabled: true
  # stdio 型：command/args + env，须打进镜像
```

### 4.3 桥接实现
- `tools/mcp_bridge.py`（新增）：
  - `load_mcp_servers()` 读 yaml（与 `load_data_sources` 对称）；
  - 对每个 enabled server：连 client（`mcp` SDK：`StdioServerParameters` / `SSEClientTransport` / `StreamableHTTPClientTransport`），`list_tools()` → 包成 `MCPToolSearch` provider，注册进 `build_search_tool` 聚合（与现有 keyless provider 同构，`source=mcp:{id}:{tool}`）；
  - 执行：`MCPToolSearch.search_many(queries)` 把 query 派发给匹配 tool（按 tool 描述 + query 语义，简单关键词路由即可），结果带 `source` 与 `credibility`。
- `build_tools`（`tools/__init__.py`）扩展：在聚合时并入 MCP providers（受 `plugin_filter` / enabled 驱动，真·控制器）。

### 4.4 管理端点（镜像 `/admin/providers`）
- `server/api.py`：`GET/POST/DELETE /admin/mcp-servers` + `McpServerCreate`（校验 transport/url/command 合法，密钥走 `.secrets/mcp.env` 的 `MCP_<ID>_TOKEN`，**不落 yaml**）。
- 热加载：每次任务 `build_tools` 重读（同现有数据源），增删 MCP server 下个任务即生效，无需重启。

### 4.5 容器坑（必读）
- **stdio 型 MCP**：server 命令必须打进 `api` 镜像（在 `server/Dockerfile` 装依赖/二进制），否则容器内找不到。
- **SSE/HTTP 型**：只需 URL，最省事，**优先推荐**。注意 `host.docker.internal` 解析（已在 compose 网络内配过）。
- **诚实边界**：MCP 工具结果同样经过 GateA 可信度校验，不得因其「外部」身份绕过溯源/escalate 逻辑；coming_soon/失败 tool 与现有数据源同对待（绝不冒充可用）。

### 4.6 改动文件
| 文件 | 改动 |
|---|---|
| `config/mcp_servers.yaml` | 新增（SoT） |
| `tools/mcp_bridge.py` | 新增：加载/连接/list_tools/包装为 provider |
| `tools/data_sources.py` | `build_search_tool` 接受 mcp providers 并入聚合（加 `source` 分支） |
| `tools/__init__.py` | `build_tools` 调 `load_mcp_servers` 并入 |
| `server/api.py` | `/admin/mcp-servers` CRUD + 密钥落 `.secrets/mcp.env` |
| `web/src/views/settings/` | MCP server 管理 UI（仿 CustomProviders.vue） |

---

## 5. 关键架构决策汇总

| ID | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | MCP 接入方式 | **D1-B：Researcher/Analyst 升 function-calling（boss 拍板直接上）** | D1-A：MCP 工具当检索源（已弃用，不做过渡） |
| D2 | 嵌入模型 | new-api `/v1/embeddings` | 降级：无 embedding，全文+时间召回 |
| D3 | 向量存储 | pgvector（换镜像 drop-in） | 外置向量库（过度工程，不取） |
| D4 | 复盘应用 | 人工 accept 才写回 yaml | 自动 apply（**拒绝**：违诚实边界） |

---

## 6. 诚实边界 / 风险

1. **D1-B 改造面大**：Researcher/Analyst 改为 function-calling 循环，需回归现有单测（stub 编排、tool_status 断言）。`web_search.search_many` 契约（`tool="web_search"`）保持不变，仅新增 function-calling 入口。
2. **嵌入降级**：无 embedding 模型时知识层退化为全文检索，仍持久化、仍有价值，但语义召回弱。
3. **pgvector 镜像切换**：`postgres_data` 卷复用安全（extension 建在已有 DB 上，不迁数据）；但若 boss 已手动装过扩展需先确认无冲突。
4. **MCP stdio 必须在镜像内**：漏打包 = 容器内连不上，已在前端 UI/文档明示。
5. **反思不静默自改**：任何 `proposed` 必须人工 accept，引擎不因 reflection 改变行为。

---

## 7. 验证计划（三道闸）

- **闸① 构建/编译**：`py_compile` 全绿；`pytest tests/ -q`（M11 新增单测：kb_store 读写、mcp_bridge list_tools 桩、reflection 生成）；`vue-tsc --noEmit` + `vite build`（仅 M11-2b/3b 动前端时）。
- **闸② VERIFICATION_M11.md**：自审 PASS，含 e2e：跑两遍同主题报告 → 第二次 `KBRetrieve` 召回第一次结论；加 MCP server（桩 SSE）→ `build_search_tool` 含 `source=mcp:*`；生成 reflection → accept 后 `skills.yaml` 变更下一任务生效。
- **闸③ 独立子代理 REVIEW_M11.md**：独立审议 PASS（主代理不自签），重点查 D1-A 边界诚实性、pgvector 迁移安全、反思人工门禁。

---

## 8. 实施顺序建议

1. **M11-1**（知识层）：换镜像 + 建表 + `kb_store` + 两节点 → 先有记忆。
2. **M11-3**（MCP D1-A）：`mcp_bridge` + 配置 + 端点 → 先有扩展能力（改动独立、可测）。
3. **M11-2**（反思）：捕获 + 生成 + 人工 accept → 最后做改进闭环（依赖前两者产出物）。

每步独立 commit（父仓 `git commit --only report-agent-team`），**不 push**（铁律，等 boss 显式指令）。
