"""M10-P4 回归：API 传输层不得用闭集硬编码 Agent / Gate 名。

背景（实测）：M10-P4 交付自定义 Agent（MarketScout / EcomAnalyst / SourcingAdvisor）。
server/api.py 的投影模型原先把 agent/gate 字段写成 Literal 闭集
（Literal["Researcher","Analyst","Writer"] / Literal["GateA","GateB","GateC"]），
导致自定义 Agent/闸的值触发 ValidationError：

    ToolStatus(agent="MarketScout")       -> FAIL
    GateReview(gate="GateD")              -> FAIL
    RoutingState(rework_target_agent="EcomAnalyst") -> FAIL
    RoutingState(last_gate="GateD")       -> FAIL

且 `task.routing_state = RoutingState(**routing)` 一旦抛错，整个任务投影崩溃
（任务卡在 running、前端拿不到结果）。合法集合应由引擎层 registry 保证，
传输层只做「收到什么就传什么」。此文件把该契约钉死，防止闭集回潮。
"""

import pytest

from server.api import (
    AnalysisConclusion,
    DraftSegment,
    EngineEvent,
    GateReview,
    RetrievalRecord,
    ReviewRequest,
    RoutingState,
    TaskResponse,
    ToolStatus,
)


def test_tool_status_accepts_custom_agent():
    """自定义 Agent 名（如 MarketScout）必须能通过传输层校验。"""
    ts = ToolStatus(agent="MarketScout", tool="web_search", ok=True, error=None)
    assert ts.agent == "MarketScout"


def test_gate_review_accepts_custom_gate():
    """自定义闸名（如 GateD）必须能通过传输层校验。"""
    gr = GateReview(
        decision="advance", reason="ok", eval_score=0.9, problem_points=["无"],
        gate="GateD", round=1, timestamp="2026-01-01T00:00:00",
    )
    assert gr.gate == "GateD"


def test_routing_state_accepts_custom_agent_and_gate():
    """路由状态携带自定义角色/闸时不得抛 ValidationError（M9-1 一等公民承诺）。"""
    rs = RoutingState(
        round=1, max_rounds=2, last_gate="GateD", status="running",
        rework_target_agent="SourcingAdvisor", rework_reason="返工",
    )
    assert rs.rework_target_agent == "SourcingAdvisor"
    assert rs.last_gate == "GateD"


def test_engine_event_accepts_custom_agent_and_gate():
    ev = EngineEvent(
        event="agent_output_unusable", agent="EcomAnalyst", gate="GateD",
        reason="产出不可用", round=1, timestamp="2026-01-01T00:00:00",
    )
    assert ev.agent == "EcomAnalyst"


def test_review_request_accepts_custom_rework_target():
    """人工复核重试目标可为自定义 Agent，不得被闭集拦死。"""
    rr = ReviewRequest(action="retry", reviewer_comment="c", rework_target_agent="EcomAnalyst")
    assert rr.rework_target_agent == "EcomAnalyst"


@pytest.mark.parametrize("agent", ["Researcher", "Analyst", "Writer", "MarketScout",
                                   "EcomAnalyst", "SourcingAdvisor"])
def test_builtin_and_custom_agents_alike(agent):
    """内置与自定义 Agent 一视同仁（回归：不得只因名字不在白名单就拒绝）。"""
    assert ToolStatus(agent=agent, tool="t", ok=True, error=None).agent == agent


# ---------------------------------------------------------------------------
# M10-P4：产出记录模型须与引擎真实产出 shape 对齐。
# 背景（实测告警）：引擎产出经 API 序列化时报 PydanticSerializationUnexpectedValue，
# 根因是模型声明与实现漂移：
#   - RetrievalRecord 缺 source（M9-2 多源聚合新增的字段）
#   - ToolStatus 缺 source，且 error 在成功时缺失（引擎不写该键）→ Required 告警
#   - AnalysisConclusion.confidence 声明 float，但 prompt 契约产出的是 "high|medium|low"
# 这些不阻塞响应（dict 仍透传），但污染日志且前端 TS 类型拿不到字段。此处钉死对齐。
# ---------------------------------------------------------------------------

def test_retrieval_record_declares_source():
    """多源聚合的 source 字段必须在模型上声明，否则序列化告警且前端拿不到来源。"""
    r = RetrievalRecord(id="rec-1", url="u", title="t", snippet="s",
                        credibility="medium", source="taobao_suggest")
    assert r.source == "taobao_suggest"
    dumped = r.model_dump()
    assert "source" in dumped and dumped["source"] == "taobao_suggest"


def test_tool_status_source_and_optional_error():
    """ToolStatus 须容忍 source 存在 + error 缺失（成功时引擎不写 error 键）。"""
    ok_item = ToolStatus(agent="MarketScout", tool="web_search",
                         source="taobao_suggest", ok=True)
    assert ok_item.source == "taobao_suggest" and ok_item.error is None
    fail_item = ToolStatus(agent="MarketScout", tool="web_search", ok=False, error="检索返回空结果")
    assert fail_item.error == "检索返回空结果"


def test_analysis_conclusion_confidence_is_str():
    """confidence 是字符串枚举（对齐 agents/analyst.md 输出契约），不是 float。"""
    c = AnalysisConclusion(id="con-1", claim="x", source_ids=["rec-1"], confidence="low")
    assert c.confidence == "low"


def test_projection_models_emit_no_serializer_warning():
    """回归：用引擎真实 shape 构造 + 序列化，不得触发 Pydantic 序列化告警。"""
    import warnings

    payload = {
        "id": "con-1", "claim": "续航是首要需求", "source_ids": ["rec-1", "rec-3"],
        "confidence": "high",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AnalysisConclusion(**payload).model_dump()
        RetrievalRecord(id="rec-1", url="u", title="t", snippet="s",
                        credibility="medium", source="taobao_suggest").model_dump()
        ToolStatus(agent="MarketScout", tool="web_search", source="taobao_suggest", ok=True).model_dump()
    pyd = [w for w in caught if "Pydantic" in str(w.category) or "Pydantic" in str(w.message)]
    assert not pyd, f"不应有 Pydantic 序列化告警: {[str(w.message)[:120] for w in pyd]}"


def test_validate_records_returns_model_instances():
    """_validate_records 必须返回模型实例（而非原 dict），否则序列化阶段会告警。"""
    from server.api import _validate_records

    raw = [{"id": "rec-1", "url": "u", "title": "t", "snippet": "s",
            "credibility": "medium", "source": "taobao_suggest"}]
    out = _validate_records(RetrievalRecord, raw)
    assert len(out) == 1 and isinstance(out[0], RetrievalRecord)
    assert out[0].source == "taobao_suggest"
    # 空/None → []（保持原默认语义）
    assert _validate_records(RetrievalRecord, None) == []
    assert _validate_records(RetrievalRecord, []) == []


def test_validate_records_quarantines_bad_records(capsys):
    """shape 漂移的记录须**单条隔离**（丢弃并告警），而非整批抛错/整任务失败。

    独立审议 M3 指出：初版 fail-loud 抛 ValidationError，会被上层宽泛 except 捕获
    并把**整份报告**降级为 ESCALATED——一条脏记录拖垮整个任务，比修复前更差。
    故改为单条 quarantine：坏记录丢弃 + 显式打印，好记录照常返回。
    """
    from server.api import _validate_records

    raw = [
        {"id": "rec-1", "url": "u", "title": "t", "snippet": "s",
         "credibility": "medium", "source": "taobao_suggest"},
        {"id": "rec-bad"},  # 缺必填字段 → 应被隔离
        {"id": "rec-2", "url": "u2", "title": "t2", "snippet": "s2",
         "credibility": "high", "source": "amazon_suggest"},
    ]
    out = _validate_records(RetrievalRecord, raw, "retrieval_records")
    # 好记录保留（2 条），坏记录被隔离（不抛错）
    assert len(out) == 2
    assert [r.id for r in out] == ["rec-1", "rec-2"]
    # fail loud：隔离动作必须可见（打印含字段名与被隔离原因）
    printed = capsys.readouterr().out
    assert "shape 漂移" in printed and "retrieval_records" in printed


def test_validate_records_skips_non_dict():
    """非 dict 元素（引擎异常产出）同样隔离而非抛错。"""
    from server.api import _validate_records

    out = _validate_records(RetrievalRecord, ["not-a-dict", None])
    assert out == []


def test_task_response_serializes_engine_output_without_warning():
    """端到端回归：引擎真实产出 shape 经 TaskResponse 序列化须零 Pydantic 告警。"""
    import warnings

    from pydantic import TypeAdapter
    from server.api import _validate_records

    engine_out = {
        "retrieval_records": [{"id": "rec-1", "url": "u", "title": "t", "snippet": "s",
                               "credibility": "medium", "source": "taobao_suggest"}],
        "analysis_conclusions": [{"id": "con-1", "claim": "c", "source_ids": ["rec-1"],
                                  "confidence": "low"}],
        "draft_segments": [{"id": "seg-1", "section": "s", "content": "c",
                            "conclusion_ids": ["con-1"]}],
        "tool_status": [{"agent": "MarketScout", "tool": "web_search",
                         "source": "taobao_suggest", "ok": True}],
    }
    task = TaskResponse(
        task_id="t", status="done", created_at="c", updated_at="u",
        user_task={"topic": "x", "scope": [], "output_format_spec": "m", "constraints": []},
        routing_state={"round": 1, "max_rounds": 3, "last_gate": "GateC",
                       "status": "done", "rework_target_agent": None, "rework_reason": None},
    )
    task.retrieval_records = _validate_records(RetrievalRecord, engine_out["retrieval_records"])
    task.analysis_conclusions = _validate_records(AnalysisConclusion, engine_out["analysis_conclusions"])
    task.draft_segments = _validate_records(DraftSegment, engine_out["draft_segments"])
    task.tool_status = _validate_records(ToolStatus, engine_out["tool_status"])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dumped = TypeAdapter(TaskResponse).dump_python(task, mode="json")
    pyd = [w for w in caught if "Pydantic" in str(w.message)]
    assert not pyd, f"不应有序列化告警: {[str(w.message)[:150] for w in pyd]}"
    assert dumped["retrieval_records"][0]["source"] == "taobao_suggest"
