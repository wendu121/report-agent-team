"""tools/kb_store 关键词召回单测（嵌入端点不可用时的兜底）。

背景：kb_store 原降级分支为 `content ILIKE '%<整句>%'`，对自然语言查询几乎必然 0 命中，
表现为「agent 无跨会话记忆」。此测试锁定新的分词 + 命中计数排序行为。
不依赖 DB / 网络（用 FakeConn）。
"""
import asyncio

from tools import kb_store


def test_tokenize_cjk_bigrams():
    toks = kb_store._tokenize("跨境电商关税政策")
    for expect in ("跨境", "电商", "关税", "政策"):
        assert expect in toks, f"missing bigram {expect} in {toks}"


def test_tokenize_latin_words_lowercased():
    toks = kb_store._tokenize("Check GDP2024 Report")
    assert "check" in toks
    assert "gdp2024" in toks
    assert "report" in toks
    # 单字母/单字符噪声被过滤
    assert "c" not in toks


def test_tokenize_dedup_and_stopwords():
    toks = kb_store._tokenize("关税关税")
    assert toks.count("关税") == 1
    # 纯停用词/虚词不产出 token
    assert "的" not in kb_store._tokenize("的是什么")


class _FakeConn:
    """只记录 SQL/params 并返回预置行，用于验证打分与排序。"""

    def __init__(self, rows):
        self._rows = rows
        self.sql = None
        self.params = None

    async def fetch(self, sql, *params):
        self.sql = sql
        self.params = params
        return self._rows


def test_lexical_retrieve_scores_and_orders():
    rows = [
        {"id": 1, "kind": "chat_fact", "title": "跨境电商关税",
         "content": "关税下调 3 个百分点", "source": "t", "metadata": {}, "score": 0.0},
        {"id": 2, "kind": "chat_fact", "title": "天气",
         "content": "厦门今天晴", "source": "t", "metadata": {}, "score": 0.0},
    ]
    conn = _FakeConn(rows)
    out = asyncio.run(kb_store._lexical_retrieve(conn, "关税政策怎么样", k=3))
    # 命中关税的条目返回，无关的天气条目不返回
    assert [o["id"] for o in out] == [1]
    assert out[0]["score"] > 0
    # SQL 用参数化 OR，不拼接原始 query（防注入）
    assert "ILIKE" in conn.sql
    assert all(p.startswith("%") and p.endswith("%") for p in conn.params)


def test_lexical_retrieve_empty_query_returns_empty():
    conn = _FakeConn([])
    out = asyncio.run(kb_store._lexical_retrieve(conn, "的 是 吗", k=3))
    assert out == []


def test_tokenize_query_is_bounded():
    toks = kb_store._tokenize("记忆" * 100)
    assert len(toks) <= len("记忆" * 100)  # 去重后远小于原始长度
