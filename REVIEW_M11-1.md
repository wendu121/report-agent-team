<!-- reviewed-by: independent-subagent -->

# REVIEW · M11-1 持久知识层

**Verdict: PASS-WITH-NOTES**

Implementation matches the design contract in spirit and the safety/degradation boundary is honestly honored. Three items need verification/cleanup before this can be called fully done; none are hard blockers.

## Findings

| Sev | Location | Description | Recommendation |
|---|---|---|---|
| MAJOR | `tools/kb_store.py:97,176` | Semantic path relies on `$1::vector` (retrieve) and `$5::vector` (write) with a Python `str` bound param (`str(vecs[0])`). asyncpg has no native `vector` type; whether the cast applies depends on pgvector's `text→vector` assignment cast being honored by asyncpg's param encoding. If it errors, the `except` silently returns `[]` / no-op — the headline semantic feature could be dead without any signal. | Verify in 闸② e2e (two same-topic runs → second recalls first via cosine, not ILIKE). If it fails, register a pgvector codec or cast through an explicit `text` literal. |
| MINOR | `orchestrator.py:1558-1560` | `retrieval_record` metadata stores only `{"url": ...}`; the source `credibility` is dropped on write. On recall, `item.get("credibility")` is always `None` (`orchestrator.py:1026`), so stored credibility is never shown. | Persist `credibility` into metadata and surface it. |
| MINOR | `orchestrator.py:1547` | Design §2.2 said KBWrite "切块 → 嵌入". Code writes `report_md[:8000]` as ONE entry — no chunking. Long reports silently lose their tail and get a single coarse embedding. | Chunk the report (e.g. by section/overlap) and write multiple `report_conclusion` rows per run, or accept the truncation as documented. |
| NOTE | `tools/kb_store.py:20-22` | Design §2.3 said "复用 LLMClient 的 base_url/key 配置". Code uses separate `NEWAPI_BASE_URL`/`NEWAPI_API_KEY`/`EMBEDDING_MODEL` envs. Zero-additional-key holds only if ops sets these == LLMClient config. | Confirm with boss; document the new env vars in deploy config. |
| NOTE | `db/init/01_kb.sql`, `docker-compose.yml` | Design §2.1 implied the init script builds the table; in practice `_ensure_schema()` self-applies at runtime and `01_kb.sql` is reference-only (not mounted). Functionally fine, wording drift. | Note in deploy docs that runtime self-creates schema. |
| NOTE | `orchestrator.py:1024-1025` | kb injection reads `item.get("topic")` / `item.get("summary")`, but `_row_to_dict` (`kb_store.py:59`) emits only `content`/`title`/`metadata`. These keys are dead; it falls back correctly to `title`/`content`. Works, but misleading. | Use existing keys directly; remove dead lookups. |
| NOTE | `orchestrator.py:1500-1510` | `_run_async`: new event-loop branch inside an already-running loop (ainvoke path) is safe for a self-contained coroutine but fragile. API endpoint correctly `await`s `kb_retrieve` directly (no `_run_async`). | Acceptable; keep as-is. |

## What I VERIFIED by reading the code (not the summary)

- **Correctness ✅**: `node_kb_write` reads `report_markdown` (`orchestrator.py:83,1541`) and filters `retrieval_records` by `cred is not None and cred >= 0.5` (`:1552-1553`). Graph wiring is sound: `START→kb_retrieve→router`; router and every terminal gate map `END→kb_write`, then `add_edge("kb_write", END)` (`:1600-1615`) — no orphaned/unreachable nodes. kb injection is researcher-only and appends to `extra_context` (`:1019-1031`), no malformation.
- **SQL/injection ✅**: ILIKE value `f"%{query}%"` is passed as bound param `$1` (`kb_store.py:105`) — safe. All writes use `$1..$7` binds (`:174-184`). No interpolated SQL.
- **Partial unique index ✅**: `ON kb.entries (topic_hash) WHERE kind='report_conclusion'` (`:138-139`) matches `ON CONFLICT (topic_hash) WHERE kind='report_conclusion'` (`:177`) exactly. `retrieval_record` has no constraint → free append, no collision.
- **Honest boundary ✅**: `_db_dsn()` returns `""` when `ASYNC_DATABASE_URL`/`DATABASE_URL` missing → `kb_retrieve` returns `[]` (`:80-81`), `kb_write` returns `0` (`:159-161`). `embed()` returns `None` on failure (`:56`) → vector NULL, ILIKE fallback, never fake embeddings. All call sites wrapped in try/except → no exception breaches a `run_report` task.
- **async ✅**: `query_knowledge_base` is `async def` and `await`s `kb_retrieve` directly (`api.py:1136`) — correct. Nodes use `_run_async` for sync context.
- **Schema/compose ✅**: `postgres:15-alpine → pgvector/pgvector:pg15` (`docker-compose.yml:12`); table/idx DDL matches `01_kb.sql`.

## Open questions for boss

1. Is the `::vector` cast actually exercised in your environment? (see MAJOR) — without a passing cosine e2e we cannot claim semantic memory works, only that it degrades safely.
2. Confirm `NEWAPI_*` embedding env vars are provisioned (and equal to the LLM gateway config), else the layer runs in permanent ILIKE-only degradation.
3. Accept single-entry `report_md[:8000]` truncation, or want chunking added?

## 补审说明（主代理补充事实证据，非重签结论）

- **MAJOR 已实证消解**：VERIFICATION_M11-1.md §4 记录了**真实 pgvector 容器（pgvector/pgvector:pg15）集成测试**——`kb_write` 成功写入 `vector(1536)` 列（列表经 `str(vec)::vector` 强转），`kb_retrieve` 余弦召回返回 `score=1.0`，证明 `$N::vector` 强转在 asyncpg 下被 pgvector 的 `text→vector` 赋值转换正确执行，**语义路径真实可用，非静默降级**。该 MAJOR 仅属「纸面不可证、须真实集成验证」类，已闭环。
- **MINOR-1（credibility 落库）已修复**：`node_kb_write` 的 `retrieval_record` metadata 现含 `credibility`（orchestrator.py:1560），召回注入时从 `item.metadata.credibility` 取回显示。
- **NOTE（dead keys topic/summary）已清理**：注入段改为直接读 `title`/`content`，删除失效的 `topic`/`summary` 回退（orchestrator.py:1022-1030）。
- **MINOR-2（切块）按 M11-1 v1 文档化接受**：单条 `report_md[:8000]` 落库，长报告截断；切块为后续增强，不在 M11-1 范围（门禁②实测通过）。
- 三道门禁结论：① `py_compile` + 79 pytest 通过 + 真实 pgvector 集成通过；② VERIFICATION_M11-1.md 自审通过；③ 本独立审议 PASS-WITH-NOTES（MAJOR 已补证消解）。可提交（本地，不 push）。

