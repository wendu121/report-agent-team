-- db/init/01_kb.sql · M11-1 持久知识层 schema（参考/手动用）
-- 生产实际由引擎 tools/kb_store.py::_ensure_schema() 在首次读写时幂等自建，
-- 无需挂载本目录；本文件仅作手动预建 / 审计对照之用。

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS kb;

CREATE TABLE IF NOT EXISTS kb.entries (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,                 -- 'report_conclusion' | 'retrieval_record' | 'reflection'
    topic_hash  TEXT NOT NULL,
    title       TEXT,
    content     TEXT NOT NULL,
    embedding   vector(1536),                  -- 嵌入缺失时为 NULL（降级全文召回）
    source      TEXT,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 仅 report_conclusion 按主题去重（每主题保留最新一份）；retrieval_record / reflection 自由追加不冲突。
CREATE UNIQUE INDEX IF NOT EXISTS kb_entries_report_topic_idx
    ON kb.entries (topic_hash) WHERE kind = 'report_conclusion';
CREATE INDEX IF NOT EXISTS kb_entries_created_idx ON kb.entries (created_at DESC);
-- HNSW 索引仅在已有向量数据后创建收益最大；空表建也无害
CREATE INDEX IF NOT EXISTS kb_entries_embedding_idx
    ON kb.entries USING hnsw (embedding vector_cosine_ops);
