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
import re
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
# 可选：把输出维度显式透传给 provider（如智谱 embedding-3 支持 dimensions=1024，
# 从而与既有 vector(1024) 列对齐）。留空则不传，走 provider 默认维度。
EMBED_DIMENSIONS = os.getenv("EMBEDDING_DIMENSIONS", "").strip()


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
    body: dict = {"model": EMBED_MODEL, "input": texts}
    if EMBED_DIMENSIONS:
        try:
            body["dimensions"] = int(EMBED_DIMENSIONS)
        except ValueError:
            logger.warning("EMBEDDING_DIMENSIONS=%r 非整数，已忽略", EMBED_DIMENSIONS)
    payload = json.dumps(body).encode("utf-8")
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


_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[0-9a-zA-Z_]+")
# 高频虚词碎片，做最小停用（保守，只滤明显噪声，避免误伤实体词）
_STOP = {"的", "了", "吗", "呢", "啊", "是", "我", "你", "他", "她", "它",
         "在", "和", "与", "就", "都", "也", "有", "把", "被", "之",
         "什么", "怎么", "如何", "这个", "那个", "一下", "可以", "以及", "因为", "所以"}
_MAX_TOKENS = 24


def _tokenize(text: str) -> list[str]:
    """轻量分词：拉丁词（>=2 小写）+ 中文 bigram。

    仅用于「嵌入不可用」时的关键词召回——零第三方依赖、确定性、可离线。
    中文无空格，故用 bigram 近似（"跨境电商" → 跨境/境电/电商）。
    """
    text = text or ""
    toks: list[str] = []
    for w in _WORD.findall(text.lower()):
        if len(w) >= 2 and w not in _STOP:
            toks.append(w)
    for run in _CJK_RUN.findall(text):
        if run in _STOP:
            continue
        if len(run) <= 2:
            toks.append(run)
        else:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in _STOP:
                    toks.append(bg)
    seen: set = set()
    out: list[str] = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def _lexical_retrieve(conn, query: str, k: int) -> list:
    """无向量时的关键词召回：任一 token 命中即候选，按命中 token 数排序。

    替代原实现 `content ILIKE '%整句%'`——对自然语言查询几乎必然 0 命中，
    使记忆层在嵌入端点不可用时表现为「完全失忆」。
    """
    toks = _tokenize(query)[:_MAX_TOKENS]
    if not toks:
        return []
    clauses: list[str] = []
    params: list = []
    for t in toks:
        idx = len(params) + 1
        clauses.append(f"(content ILIKE ${idx} OR coalesce(title,'') ILIKE ${idx})")
        params.append(f"%{t}%")
    sql = (
        "SELECT id, kind, title, content, source, metadata, 0.0 AS score "
        "FROM kb.entries WHERE " + " OR ".join(clauses) +
        " ORDER BY created_at DESC LIMIT 200"
    )
    rows = await conn.fetch(sql, *params)
    scored = []
    for r in rows:
        hay = ((r.get("title") or "") + "\n" + (r.get("content") or "")).lower()
        hit = sum(1 for t in toks if t in hay)
        if hit:
            scored.append((hit, r))
    scored.sort(key=lambda x: -x[0])
    out = []
    for hit, r in scored[:k]:
        d = _row_to_dict(r)
        d["score"] = round(hit / max(1, len(toks)), 4)
        out.append(d)
    return out


async def kb_retrieve(query: str, k: int = 5):
    """跨会话召回：嵌入可用时走语义检索，不可用时退化为关键词（中文 bigram）召回。

    降级链（诚实边界）：vector 检索 → 关键词召回 → 空。
    关键词兜底保证「嵌入端点欠费 / 不可达」时记忆层仍可召回，而非直接失忆。
    DB 不可达返回 []。
    """
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
                if rows:
                    return [_row_to_dict(r) for r in rows]
                # 向量列存在但为空（历史写入均无向量）→ 落到关键词兜底
            return await _lexical_retrieve(conn, query, k)
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
