# tests/test_m11_reflection.py · M11-2 反思回写闭环（stub，零网络）
import importlib
import sys
from pathlib import Path

import pytest

# 保证项目根在 sys.path（server 目录懒加载 tools）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.reflection as reflection  # noqa: E402


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    """把反思落库路径重定向到临时目录，避免污染真实 config/。"""
    monkeypatch.setattr(reflection, "REFLECTIONS_CFG", tmp_path / "reflections.proposed.yaml")
    return tmp_path


def _state(escalate=None, recs=None, rework=None, topic="AI 芯片"):
    rs = {}
    if escalate is not None:
        rs["escalate_reason"] = escalate
    if rework is not None:
        rs["rework"] = rework
    return {
        "user_task": {"topic": topic},
        "routing_state": rs,
        "retrieval_records": recs or [],
        "tool_status": [],
    }


# --- 生成器：确定性、可验证、零幻觉 ---

def test_escalate_review_emitted():
    out = reflection.generate_reflection(_state(escalate="闸判定业务严重问题"))
    assert len(out) == 1
    e = out[0]
    assert e["kind"] == "escalate_review"
    assert e["status"] == "proposed"
    assert "escalate_reason" in e["evidence"][0]
    assert e["proposed_change"]["op"] == "none"


def test_clean_state_no_false_suggestion():
    # 3 条高可信素材、未 escalate、无 rework → 应产出 0 条（不造无据建议）
    recs = [
        {"source": "wikipedia", "credibility": 0.9},
        {"source": "arxiv", "credibility": 0.8},
        {"source": "tavily", "credibility": 0.95},
    ]
    assert reflection.generate_reflection(_state(recs=recs)) == []


def test_plugin_hint_on_low_credibility_source():
    recs = [
        {"source": "wikipedia", "credibility": 0.1},
        {"source": "wikipedia", "credibility": 0.2},
        {"source": "arxiv", "credibility": 0.9},
    ]
    out = reflection.generate_reflection(_state(recs=recs))
    hints = [e for e in out if e["kind"] == "plugin_hint"]
    assert len(hints) == 1
    assert hints[0]["target"]["key"] == "sources.wikipedia.enabled"
    assert hints[0]["proposed_change"]["op"] == "none"


def test_low_yield_hint():
    out = reflection.generate_reflection(_state(recs=[{"source": "arxiv", "credibility": 0.9}]))
    assert any(e["kind"] == "low_yield" for e in out)


def test_rework_hotspot():
    out = reflection.generate_reflection(_state(rework=2, recs=[{"source": "a", "credibility": 0.9}] * 3))
    assert any(e["kind"] == "rework_hotspot" for e in out)


# --- 落库：append / load / set status 往返 ---

def test_append_load_set_roundtrip(tmp_cfg):
    entries = reflection.generate_reflection(_state(escalate="x"))
    n = reflection.append_reflections(entries)
    assert n == 1
    loaded = reflection.load_reflections()
    assert len(loaded) == 1
    rid = loaded[0]["id"]
    updated = reflection.set_reflection_status(rid, "accepted")
    assert updated is not None and updated["status"] == "accepted"
    assert reflection.load_reflections(status="accepted")[0]["id"] == rid
    assert reflection.load_reflections(status="proposed") == []


def test_append_idempotent(tmp_cfg):
    entries = reflection.generate_reflection(_state(escalate="x"))
    assert reflection.append_reflections(entries) == 1
    assert reflection.append_reflections(entries) == 0  # 同 id 跳过


def test_append_multiple_status_filter(tmp_cfg):
    # 真实用法：每任务 generate 一次 + append 一次（node_reflect 即此路径）
    e1 = reflection.generate_reflection(_state(escalate="a"))
    reflection.append_reflections(e1)
    e2 = reflection.generate_reflection(_state(recs=[{"source": "w", "credibility": 0.1},
                                                     {"source": "w", "credibility": 0.2},
                                                     {"source": "x", "credibility": 0.9}]))
    reflection.append_reflections(e2)
    assert len(reflection.load_reflections()) == 2


# --- node_reflect 集成（降级 no-op，不抛错）---

def test_node_reflect_writes_file(tmp_cfg):
    from orchestrator import node_reflect

    st = _state(escalate="终态 escalate")
    result = node_reflect(st)
    assert result == {}  # 不改 state
    assert reflection.load_reflections()  # 文件已写入


def test_node_reflect_degrade_on_bad_state(tmp_cfg):
    from orchestrator import node_reflect

    # 传入不可预期结构也不得抛错（降级 no-op）
    assert node_reflect({"routing_state": {"escalate_reason": "boom"}}) == {}
