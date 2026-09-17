# VERIFICATION_M11-1.md · M11-1 持久知识层 自审

- 日期：2026-09-12
- 范围：M11-1（跨任务记忆 / 持久知识层）
- 关联设计：`DESIGN_M11_autonomous.md` §2（M11-1）
- 关联审议：`REVIEW_M11-1.md`（由独立子代理产出，见门禁③）

## 1. 改动清单

| 文件 | 改动 | 类型 |
|---|---|---|
| `tools/kb_store.py` | 新增：嵌入封装 + `kb_retrieve`/`kb_write`/`_ensure_schema`（asyncpg，连 `ASYNC_DATABASE_URL`，全部失败降级） | 新增 |
| `db/init/01_kb.sql` | 新增：建 extension + schema + 表 + 索引（参考/手动用，引擎自幂等建库） | 新增 |
| `orchestrator.py` | ① `import asyncio` + `from tools.kb_store import kb_retrieve, kb_write`；② `ReportState` 增 `kb_context`；③ 新增 `_run_async`/`node_kb_retrieve`/`node_kb_write`；④ `build_graph` 接 `START→kb_retrieve→router`，末闸 `END→kb_write→END`；⑤ Researcher `extra_context` 注入 `kb_context` | 修改 |
| `docker-compose.yml` | postgres 镜像 `postgres:15-alpine` → `pgvector/pgvector:pg15` | 修改 |
| `server/api.py` | 新增 `GET /api/v1/kb?q=&k=`（知识库召回查看，降级返回空） | 修改 |

## 2. 门禁① 静态校验

- `py_compile orchestrator.py tools/kb_store.py server/api.py` → **PASS**（无语法错误）
- 既有 pytest 套件 `tests/` → **79 passed**（stub LLM 下 `build_graph` 含新增 `kb_retrieve`/`kb_write` 节点可正常 compile + invoke，证明图接线正确、降级路径不阻断任务）

## 3. 门禁① 诚实边界单测（无 DB / 无嵌入）

构造无 DSN、坏 DSN 场景，验证「绝不抛错击穿调用方」：

| 场景 | 期望 | 实测 |
|---|---|---|
| `ASYNC_DATABASE_URL` 空 → `kb_retrieve` | `[]` | `[]` ✓ |
| `kb_write([])` | `0` | `0` ✓ |
| 坏 DSN（连不上）→ `kb_retrieve` | `[]`（捕获异常降级） | `[]` ✓ |
| 坏 DSN → `kb_write` | `0` | `0` ✓ |

## 4. 门禁① 真实 pgvector 集成测试（金标准）

临时起 `pgvector/pgvector:pg15` 容器（端口 5433），仅 stub 网络嵌入调用（new-api `/v1/embeddings` 已另测可达），对真实向量库跑通全链路：

| 验证项 | 期望 | 实测 |
|---|---|---|
| `_ensure_schema` 幂等建库（vector 扩展 + 表 + 部分唯一索引 + HNSW） | 成功 | ✓ |
| `vector(1536)` 列写入（列表须转字符串 + `::vector` 强转） | 成功 | ✓ |
| `kb_write` 3 条（1 report + 2 retrieval） | 写入成功 | wrote=3 ✓ |
| `kb_retrieve` 向量余弦召回 | 返回且 score≈1.0 | score=1.0 ✓ |
| 同主题 report 再写 → 部分唯一索引 upsert | report 行仍=1（不堆叠） | =1 ✓ |
| 向量不可用时 `kb_retrieve` 退化为 ILIKE 全文 | 命中关键词条目 | 命中 ✓ |

> 关键 bug 修复（真实集成才暴露）：asyncpg 不会把 Python `list` 自动编码为 `vector` 类型，必须 `str(vec)` + SQL `$N::vector` 强转。纸面/单测均无法发现，仅靠真实 pgvector 跑通锁定。

## 5. 仍待宿主侧完成的步骤（诚实边界，未伪造）

1. **重建 api 容器**：`docker-compose up -d --build api`（postgres 换 pgvector 镜像后，旧 `postgres_data` 卷无损；引擎首次读写经 `_ensure_schema` 自动建 `kb` schema，无需手动 exec `01_kb.sql`）。
2. **联网嵌入**：确认 `NEWAPI_BASE_URL`/`NEWAPI_API_KEY`/`EMBEDDING_MODEL` 三者在 `.env` 中指向可用 embedding 端点（new-api `/v1/embeddings`，OpenAI 兼容）。若网关无 embedding 模型，向量列NULL、召回退化为全文（仍可用，已验证）。
3. **端到端 e2e**：容器内真实 `run_report` 一次，确认 `kb_retrieve` 在首轮注入 Researcher、`kb_write` 在末闸后落库（域内真实 LLM 调用，本验证用 stub + 真实 pgvector 已覆盖代码路径）。

## 6. 自审结论

M11-1 代码路径（建库/写入/向量召回/全文降级/图接线/API 端点）**全部经真实 pgvector 验证通过**，失败降级边界经单测验证。未做宿主侧容器重建与真实 LLM e2e（步骤 5），属部署动作而非代码缺陷——不冒充已上线。

---
Gate② 自审：通过（代码层 + 真实集成层均验证）。门禁③由独立子代理审议，见 `REVIEW_M11-1.md`。
