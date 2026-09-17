# tests/test_converge_check.py · M13-Converge 漂移检测专项测试

"""
运行：pytest tests/test_converge_check.py -q

验证 scripts/converge_check.py 的三类 converge 检查：
  - GateA coverage（scope 子问题 vs retrieval_records）
  - GateB broken_reference（source_ids 与 retrieval_records 的关联）
  - GateC completeness（scope 覆盖 + 引用章节完整性）
  - 幂等去重、merge_into_state、gaps_to_rework_reason
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.converge_check import (  # noqa: E402
    converge_gateA,
    converge_gateB,
    converge_gateC,
    check,
    merge_into_state,
    gaps_to_rework_reason,
)


# ----------------------------------------------------------------------------
# 构造辅助
# ----------------------------------------------------------------------------
def _task(scope=None):
    # 注意：不能用 `scope or default`，因为 `[]` 是 falsy 会错误地 fallback。
    # 显式传入 None 时才使用默认值，空列表 = 明确不覆盖 scope。
    return {
        "topic": "AI 芯片市场研报",
        "scope": list(scope) if scope is not None else ["供给", "需求", "竞争格局"],
        "output_format_spec": "markdown",
        "constraints": ["中文输出"],
    }


def _recs(ids=("rec-1", "rec-2"), url_prefix="https://example.com", snippets=None):
    """构造检索记录，可选 custom snippets（None=使用默认含"供给端"的 snippet）。"""
    default_snippets = {
        "rec-1": "讨论了供给端的核心内容，供给产能吃紧。",
        "rec-2": "分析了需求格局，需求旺盛增长。",
    }
    return [
        {
            "id": iid,
            "url": f"{url_prefix}/{iid}",
            "title": f"关于 {iid} 的研究",
            "snippet": (snippets or default_snippets).get(iid, "核心内容分析"),
            "credibility": "high",
        }
        for iid in ids
    ]


# ----------------------------------------------------------------------------
# GateA：coverage 检查
# ----------------------------------------------------------------------------
def test_gateA_full_coverage_no_gaps():
    """所有 scope 子问题都被检索记录覆盖 → 无 gap。"""
    task = _task(["供给", "需求"])
    recs = [
        {"id": "rec-1", "url": "https://a.com/1", "title": "供给端分析", "snippet": "供给产能吃紧", "credibility": "high"},
        {"id": "rec-2", "url": "https://a.com/2", "title": "需求端格局", "snippet": "需求旺盛", "credibility": "high"},
    ]
    gaps = converge_gateA(task, recs, round_num=1)
    assert gaps == [], f"全覆盖不应有 gap，实际: {gaps}"


def test_gateA_missing_coverage():
    """缺少 '竞争格局' 覆盖 → 产生 1 条 gap。"""
    task = _task(["供给", "需求", "竞争格局"])
    recs = _recs(["rec-1", "rec-2"])
    gaps = converge_gateA(task, recs, round_num=1)
    assert len(gaps) == 1, f"应只有 1 条 gap，实际: {len(gaps)}"
    g = gaps[0]
    assert g["type"] == "coverage"
    assert g["gate"] == "GateA"
    assert "竞争格局" in g["description"]
    assert g["detected_at"] == 1


def test_gateA_empty_records_all_missing():
    """检索为空 → 所有 scope 子问题均缺口。"""
    task = _task(["供给", "需求"])
    gaps = converge_gateA(task, [], round_num=2)
    assert len(gaps) == 2
    assert all(g["type"] == "coverage" for g in gaps)


def test_gateA_empty_scope_no_gaps():
    """scope 为空 → 无 gap（不报假阳性）。"""
    task = _task([])  # 显式空列表
    gaps = converge_gateA(task, _recs(), round_num=1)
    assert gaps == [], f"空 scope 不应有 gap，实际: {gaps}"


# ----------------------------------------------------------------------------
# GateB：broken_reference 检查
# ----------------------------------------------------------------------------
def test_gateB_valid_references_no_gaps():
    """每条结论都有有效 source_ids → 无 gap。"""
    recs = _recs(["rec-1", "rec-2"])
    cons = [
        {"id": "con-1", "claim": "供给紧张", "source_ids": ["rec-1"], "confidence": "high"},
        {"id": "con-2", "claim": "需求旺盛", "source_ids": ["rec-2"], "confidence": "high"},
    ]
    gaps = converge_gateB(recs, cons, round_num=1)
    assert gaps == []


def test_gateB_empty_source_ids():
    """结论 source_ids 为空 → 产生 gap。"""
    recs = _recs(["rec-1"])
    cons = [{"id": "con-1", "claim": "供给紧张", "source_ids": [], "confidence": "high"}]
    gaps = converge_gateB(recs, cons, round_num=1)
    assert len(gaps) == 1
    assert gaps[0]["type"] == "broken_reference"
    assert "无 source_ids" in gaps[0]["description"]


def test_gateB_missing_reference_id():
    """结论引用了不存在的 id → 产生 gap。"""
    recs = _recs(["rec-1"])
    cons = [{"id": "con-1", "claim": "供给紧张", "source_ids": ["rec-99"], "confidence": "high"}]
    gaps = converge_gateB(recs, cons, round_num=1)
    assert len(gaps) == 1
    assert "rec-99" in gaps[0]["description"]


# ----------------------------------------------------------------------------
# GateC：completeness 检查
# ----------------------------------------------------------------------------
def test_gateC_full_completeness_no_gaps():
    """终稿覆盖所有 scope + 引用章节完整 → 无 gap。"""
    task = _task(["供给", "需求"])
    segs = [
        {"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]},
        {"id": "seg-2", "section": "需求", "content": "需求旺盛。", "conclusion_ids": ["con-2"]},
    ]
    md = (
        "# AI 芯片市场研报\n\n"
        "## 供给\n供给端产能吃紧。\n\n"
        "## 需求\n需求旺盛。\n\n"
        "##  引用 / 来源\n- [rec-1](https://a.com/rec-1)\n- [rec-2](https://a.com/rec-2)\n"
    )
    recs = _recs(
        ["rec-1", "rec-2"],
        url_prefix="https://a.com",
        snippets={
            "rec-1": "供给端产能吃紧，对应供给章节。",
            "rec-2": "需求旺盛增长，对应需求章节。",
        },
    )
    gaps = converge_gateC(task, segs, md, recs, round_num=1)
    assert gaps == [], f"全完整不应有 gap，实际: {gaps}"


def test_gateC_missing_scope_item():
    """终稿缺 '竞争格局' 章节 → 产生 coverage gap。"""
    task = _task(["供给", "竞争格局"])
    segs = [{"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]}]
    md = "## 供给\n供给端产能吃紧。\n\n## 引用/来源\n- [rec-1](https://a.com/1)\n"
    recs = _recs(["rec-1"])
    gaps = converge_gateC(task, segs, md, recs, round_num=1)
    assert any(g["type"] == "coverage" and "竞争格局" in g["description"] for g in gaps)


def test_gateC_missing_citation_section():
    """缺引用章节 → 产生 missing_section gap。"""
    task = _task(["供给"])
    segs = [{"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]}]
    md = "## 供给\n供给端产能吃紧。\n"  # 无引用章节
    recs = _recs(["rec-1"])
    gaps = converge_gateC(task, segs, md, recs, round_num=1)
    assert any(g["type"] == "missing_section" for g in gaps)


def test_gateC_incomplete_citation():
    """引用章节缺 url → 产生 incomplete_citation gap。"""
    task = _task(["供给"])
    segs = [{"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]}]
    md = "## 引用/来源\n- rec-1\n"  # 有 id 但缺 url
    recs = [{"id": "rec-1", "url": "https://a.com/1", "title": "T", "snippet": "S", "credibility": "high"}]
    gaps = converge_gateC(task, segs, md, recs, round_num=1)
    assert any(g["type"] == "incomplete_citation" for g in gaps)


# ----------------------------------------------------------------------------
# check() 统一入口
# ----------------------------------------------------------------------------
def test_check_all_three_gates():
    """全量 check()：构造有缺口 state → 应同时返回 GateA + GateB + GateC 的 gap。"""
    task = _task(["供给", "需求", "竞争格局"])
    recs = _recs(["rec-1"])  # 只有一条
    cons = [{"id": "con-1", "claim": "测试", "source_ids": ["rec-99"], "confidence": "high"}]
    segs = [{"id": "seg-1", "section": "供给", "content": "供给端吃紧。", "conclusion_ids": ["con-1"]}]
    md = "## 引用/来源\n- [rec-1](https://a.com/1)\n"
    state = {
        "user_task": task,
        "retrieval_records": recs,
        "analysis_conclusions": cons,
        "draft_segments": segs,
        "report_markdown": md,
        "routing_state": {"round": 1},
    }
    gaps = check(state, round_num=1)
    # 至少应有 GateA（竞争格局缺失）、GateB（rec-99 不存在）
    types = {g["type"] for g in gaps}
    assert "coverage" in types, f"应有 coverage gap，实际 types={types}"
    assert "broken_reference" in types, f"应有 broken_reference gap，实际 types={types}"


def test_check_powerful_no_gaps():
    """全完整 state → 无 gap（幂等性）。"""
    task = _task(["供给"])
    # URL 必须和 md 里的完全一致
    recs = [{"id": "rec-1", "url": "https://a.com/1", "title": "T1",
             "snippet": "供给端产能吃紧。", "credibility": "high"}]
    cons = [{"id": "con-1", "claim": "供给紧张", "source_ids": ["rec-1"], "confidence": "high"}]
    segs = [{"id": "seg-1", "section": "供给", "content": "供给端产能吃紧。", "conclusion_ids": ["con-1"]}]
    md = "## 引用/来源\n- [rec-1](https://a.com/1)\n"
    state = {
        "user_task": task,
        "retrieval_records": recs,
        "analysis_conclusions": cons,
        "draft_segments": segs,
        "report_markdown": md,
        "routing_state": {"round": 1},
    }
    gaps = check(state, round_num=1)
    assert gaps == [], f"全完整应无 gap，实际: {gaps}"


# ----------------------------------------------------------------------------
# merge_into_state
# ----------------------------------------------------------------------------
def test_merge_into_state_appends():
    """merge_into_state：新增 gap 应被追加，已有 id 不重复。"""
    state = {
        "gap_tasks": [
            {"id": "existing-1", "type": "coverage", "description": "旧缺口"},
        ],
    }
    new_gaps = [
        {"id": "new-1", "type": "broken_reference", "description": "新缺口"},
        {"id": "existing-1", "type": "coverage", "description": "重复"},
    ]
    merged = merge_into_state(state, new_gaps)
    ids = [g["id"] for g in merged["gap_tasks"]]
    assert ids.count("existing-1") == 1, "已有 id 不应重复"
    assert "new-1" in ids, "新 gap 应被追加"
    assert len(merged["gap_tasks"]) == 2


def test_merge_into_state_empty_existing():
    """state 无 gap_tasks → merge 后应有新 gaps。"""
    state = {}
    new_gaps = [{"id": "g1", "type": "coverage", "description": "x"}]
    merged = merge_into_state(state, new_gaps)
    assert merged["gap_tasks"] == new_gaps


# ----------------------------------------------------------------------------
# gaps_to_rework_reason
# ----------------------------------------------------------------------------
def test_gaps_to_rework_reason_empty():
    """空 gaps → 返回当前原因或空串。"""
    assert gaps_to_rework_reason([], "已有原因") == "已有原因"
    assert gaps_to_rework_reason([]) == ""


def test_gaps_to_rework_reason_with_gaps():
    """有 gaps → 生成 human-readable 文本。"""
    gaps = [
        {"type": "coverage", "description": "缺口1"},
        {"type": "broken_reference", "description": "缺口2"},
    ]
    result = gaps_to_rework_reason(gaps, "原有原因")
    assert "Converge 补充缺口" in result
    assert "缺口1" in result
    assert "缺口2" in result


def test_gaps_to_rework_reason_no_existing():
    """无当前原因 → 以 [Converge 漂移检测] 开头。"""
    gaps = [{"type": "coverage", "description": "某缺口"}]
    result = gaps_to_rework_reason(gaps)
    assert result.startswith("[Converge 漂移检测]")
    assert "某缺口" in result


# ----------------------------------------------------------------------------
# 边缘情况
# ----------------------------------------------------------------------------
def test_round_num_propagation():
    """round_num 应传播到每条 gap_task 的 detected_at。"""
    task = _task(["供给"])
    gaps = converge_gateA(task, [], round_num=3)
    assert all(g["detected_at"] == 3 for g in gaps)


def test_gateA_unicode_coverage():
    """中文 scope 关键词应能命中中文检索记录。"""
    task = _task(["华为芯片"])
    recs = [
        {"id": "rec-1", "url": "https://a.com/1", "title": "华为芯片分析", "snippet": "华为芯片产能扩张", "credibility": "high"},
    ]
    gaps = converge_gateA(task, recs, round_num=1)
    assert gaps == [], f"中文关键词应命中，实际 gaps: {gaps}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
