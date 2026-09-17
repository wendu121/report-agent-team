# tests/test_m11_3_synthesis.py · Researcher 降级兜底单测（真实 e2e 回归）
#
# 背景：M11-3 首次真实 LLM e2e 中，LongCat-2.0 在 fc-loop 拿到真实检索结果后，
# 最终 JSON 偶发缺 retrieval_records 键 / 产出空数组 → agent_output_unusable → escalate。
# 修复：引擎侧用真实检索结果确定性合成（2.7 节），本测试锁定该行为。
#
# 仅用 Stub，无需联网。

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (  # noqa: E402
    StubLLMClient, make_agent, load_agent_registry,
)
from tools import ToolBundle  # noqa: E402


class FakeWebSearch:
    def search_many(self, queries, agent=None):
        entries = [{
            "id": "r1", "url": "https://real.example/a", "title": "A",
            "snippet": "s", "credibility": "high", "source": "tavily",
        }]
        return entries, [{"agent": agent, "tool": "web_search", "ok": True}]


def _bundle():
    return ToolBundle(web_search=FakeWebSearch(), data_proc=None, doc_export=None, mcp=None)


class _BadFinalFC(StubLLMClient):
    """fc 客户端：final content 缺 retrieval_records 键（线上实测故障形态 1）。"""
    fc = True

    def complete_with_tools(self, model, system, user, tools=None,
                            role=None, shape=None, kind=None):
        return {"content": json.dumps({"foo": "bar"}), "tool_calls": []}


class _EmptyRecordsFC(StubLLMClient):
    """fc 客户端：final content retrieval_records=[]（线上实测故障形态 2）。"""
    fc = True

    def complete_with_tools(self, model, system, user, tools=None,
                            role=None, shape=None, kind=None):
        return {"content": json.dumps({"retrieval_records": [], "tool_status": []}),
                "tool_calls": []}


def _state():
    return {
        "user_task": {"topic": "t", "scope": ["s1"], "output_format_spec": "markdown",
                      "constraints": []},
        "routing_state": {"round": 1, "rework_reason": None},
        "search_results": [],
    }


def _models():
    return {"roles": {"Researcher": {"model": "stub"}}}


def _run(llm):
    reg = load_agent_registry()
    node = make_agent("Researcher", llm, _models(), tools=_bundle(), reg=reg)
    return node(_state())


def test_synthesize_when_key_missing():
    """模型最终 JSON 缺 retrieval_records → 引擎从真实检索结果合成，不 rework。"""
    patch = _run(_BadFinalFC())
    recs = patch.get("retrieval_records")
    assert isinstance(recs, list) and len(recs) == 1
    assert recs[0]["url"] == "https://real.example/a"
    assert recs[0]["source"] == "tavily"
    evs = patch["routing_state"]["engine_events"]
    assert any(e.get("event") == "researcher_records_synthesized" for e in evs)


def test_synthesize_when_empty_records():
    """模型最终 JSON retrieval_records=[] 但引擎有真实结果 → 合成（空数组+有数据=模型错误）。"""
    patch = _run(_EmptyRecordsFC())
    recs = patch.get("retrieval_records")
    assert isinstance(recs, list) and len(recs) == 1
    evs = patch["routing_state"]["engine_events"]
    assert any(e.get("event") == "researcher_records_synthesized" for e in evs)


def test_no_synthesis_without_search_results():
    """检索完全失败（search_results 空）→ 不合成；模型缺键则按契约 _agent_fail（rework 信号）。"""
    bundle = ToolBundle(web_search=FakeWebSearch(), data_proc=None, doc_export=None, mcp=None)
    # 让检索返回空：用返回空结果的 fake
    class EmptySearch:
        def search_many(self, queries, agent=None):
            return [], [{"agent": agent, "tool": "web_search", "ok": False, "error": "down"}]

    bundle.web_search = EmptySearch()
    reg = load_agent_registry()
    node = make_agent("Researcher", _BadFinalFC(), _models(), tools=bundle, reg=reg)
    res = node(_state())
    # 模型既无真实数据又不按契约输出空数组 → rework（不会凭空合成编造）
    assert res.get("agent_result") == "parse_fail"
    assert res["routing_state"].get("rework_target_agent") == "Researcher"
    evs = res["routing_state"]["engine_events"]
    assert not any(e.get("event") == "researcher_records_synthesized" for e in evs)
