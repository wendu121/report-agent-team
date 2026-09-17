# tools/kb_store.py · 持久知识层（M11-1）
"""跨任务记忆：研报结论 / 检索素材 / 反思建议 落库 + 语义召回。

- 嵌入走 new-api 网关 /v1/embeddings（OpenAI 兼容，DESIGN_M11_autonomous.md 决策 D2）。
- 向量存 pgvector（postgres 镜像须为 pgvector/pgvector:pg15，见 docker-compose.yml）。
- 全部失败路径降级（诚实边界）：
  - 嵌入不可用 → 不写向量列，召回退化为全文 ILIKE；仍持久化 content/metadata。
  - DB 不可达 → 读写函数返回空/no-op，绝不抛错击穿调用方（节点层已 try/except）。

依赖：asyncpg（api 镜像已含 0.31.0）。模块级仅用标准库，导入零副作用。
"""
import os
import json
import hashlib
import logging
import urllib.request as _req

logger = logging.getLogger(__name__)

# 归一化：new-api 的 OpenAI 兼容根必须带 /v1，否则 /embeddings 会打到仪表盘 SPA 兜底。
# 兼容「配置漏写 /v1」与「已带 /v1」两种写法（与 orchestrator.NewApiLLMClient._get_client 对齐）。
def _normalize_newapi_base(raw: str) -> str:
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raw = "http://localhost:3000/v1"
    return raw if raw.endswith("/v1") else raw + "/v1"

EMBED_URL = _normalize_newapi_base(os.getenv("NEWAPI_BASE_URL")) + "/embeddings"
EMBED_KEY = os.getenv("NEWAPI_API_KEY", "sk-no-key")
# 默认对齐本环境 new-api 网关实际可用的嵌入模型（BAAI/bge-m3，1024 维）。
# 换模型务必同步改 EMBEDDING_MODEL + EMBEDDING_DIM（维度写死在表结构里，不一致会写失败降级）。
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))


def _db_dsn() -> str:
    """取异步 DSN，并把 SQLAlchemy 前缀 postgresql+asyncpg:// 规整为 asyncpg 接受的 postgresql://。"""
    url = os.getenv("ASYNC_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if not url:
        return ""
    return url.replace("postgresql+asyncpg://", "postgresql://")


def topic_hash(text: str) -> str:
    """主题归一哈希，用于同主题去重/召回。"""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def embed(texts):
    """批量嵌入（OpenAI 兼容）。失败返回 None，由调用方降级。"""
    if not texts:
        return []
    payload = json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8")
    req = _req.Request(
        EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {EMBED_KEY}"},
    )
    try:
        # 禁用沙箱/系统代理，避免内部地址被 proxy 拦（与 _discover_custom_models 同策略）
        opener = _req.build_opener(_req.ProxyHandler({}))
        with opener.open(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [d["embedding"] for d in data.get("data", [])]
    except Exception as e:  # noqa: BLE001
        logger.warning("知识层嵌入失败，降级无向量召回: %s", e)
        return None


def _row_to_dict(r) -> dict:
    meta = r.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}
    return {
        "id": r.get("id"),
        "kind": r.get("kind"),
        "title": r.get("title"),
        "content": r.get("content"),
        "source": r.get("source"),
        "metadata": meta or {},
        "score": round(float(r.get("score") or 0.0), 4),
    }


async def kb_retrieve(query: str, k: int = 5):
    """语义召回；嵌入不可用时退化为全文 ILIKE。DB 不可达返回 []。"""
    dsn = _db_dsn()
    if not dsn or not query.strip():
        return []
    try:
        import asyncpg

        await _ensure_schema(dsn)
        conn = await asyncpg.connect(dsn)
        try:
            vecs = embed([query])
            if vecs:
                rows = await conn.fetch(
                    """SELECT id, kind, title, content, source, metadata,
                              (1 - (embedding <=> $1::vector)) AS score
                       FROM kb.entries
                       WHERE embedding IS NOT NULL
                       ORDER BY embedding <=> $1::vector
                       LIMIT $2""",
                    str(vecs[0]), k,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, kind, title, content, source, metadata, 0.0 AS score
                       FROM kb.entries
                       WHERE content ILIKE $1
                       ORDER BY created_at DESC LIMIT $2""",
                    f"%{query}%", k,
                )
            return [_row_to_dict(r) for r in rows]
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("知识层召回失败（降级跳过）: %s", e)
        return []


async def _ensure_schema(dsn: str) -> None:
    """幂等建库（vector 扩展 + kb schema + 表 + 索引）。失败仅告警，不阻断任务。"""
    try:
        import asyncpg

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS kb;")
            # 维度由 EMBEDDING_DIM 决定（非 f-string，避免与 JSONB 默认值 '{}' 的花括号冲突）
            _ddl = """
                CREATE TABLE IF NOT EXISTS kb.entries (
                       id          BIGSERIAL PRIMARY KEY,
                       kind        TEXT NOT NULL,
                       topic_hash  TEXT NOT NULL,
                       title       TEXT,
                       content     TEXT NOT NULL,
                       embedding   vector(__DIM__),
                       source      TEXT,
                       metadata    JSONB DEFAULT '{}'::jsonb,
                       created_at  TIMESTAMPTZ DEFAULT now()
                   );""".replace("__DIM__", str(EMBED_DIM))
            await conn.execute(_ddl)
            # 维度守卫（第 2 轮独立审议 NOTE）：CREATE TABLE IF NOT EXISTS 不会改既有列，
            # 若旧库 embedding 列维度 != EMBED_DIM，写入会静默失败降级 → 显式告警。
            try:
                row = await conn.fetchrow(
                    "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                    "WHERE attrelid = 'kb.entries'::regclass AND attname = 'embedding'")
                existing = (row or {}).get("t")
                if existing and existing != f"vector({EMBED_DIM})":
                    logger.warning(
                        "知识库 embedding 列为 %s，与 EMBEDDING_DIM=%s 不一致 → "
                        "向量写入将失败降级。请对齐 EMBEDDING_DIM 或重建该列/Schema。",
                        existing, EMBED_DIM)
            except Exception as e:  # noqa: BLE001
                logger.debug("维度守卫检查跳过: %s", e)
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS kb_entries_report_topic_idx "
                "ON kb.entries (topic_hash) WHERE kind = 'report_conclusion';"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS kb_entries_created_idx "
                "ON kb.entries (created_at DESC);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS kb_entries_embedding_idx "
                "ON kb.entries USING hnsw (embedding vector_cosine_ops);"
            )
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("知识层 schema 初始化失败（降级跳过）: %s", e)


async def kb_write(entries: list):
    """批量写入；按 topic_hash 去重 upsert。嵌入失败则 embedding=NULL（仍可全文召回）。"""
    if not entries:
        return 0
    dsn = _db_dsn()
    if not dsn:
        return 0
    try:
        import asyncpg

        await _ensure_schema(dsn)
        conn = await asyncpg.connect(dsn)
        try:
            texts = [e.get("content", "") or "" for e in entries]
            vecs = embed(texts)
            n = 0
            for i, e in enumerate(entries):
                vec = vecs[i] if vecs else None
                th = topic_hash(e.get("topic") or (e.get("content", "")[:200]))
                await conn.execute(
                    """INSERT INTO kb.entries (kind, topic_hash, title, content, embedding, source, metadata)
                       VALUES ($1,$2,$3,$4,$5::vector,$6,$7)
                       ON CONFLICT (topic_hash) WHERE kind='report_conclusion' DO UPDATE
                         SET content=EXCLUDED.content, embedding=EXCLUDED.embedding,
                             title=EXCLUDED.title, source=EXCLUDED.source,
                             metadata=EXCLUDED.metadata, created_at=now()""",
                    e.get("kind"), th, e.get("title"), e.get("content"),
                    str(vec) if vec else None, e.get("source"),
                    json.dumps(e.get("metadata") or {}, ensure_ascii=False),
                )
                n += 1
            return n
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("知识层写入失败（降级跳过）: %s", e)
        return 0
