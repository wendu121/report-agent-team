# tests/test_gate_degradation_visibility.py
#
# M13-gate-degradation · 「闸降级必须对用户可见」回归用例
#
# 背景（为什么单独立一个文件）：
#   系统的审核闸在审核模型返回不可解析 JSON 时会**降级放行**（既定设计，不动），
#   但这件事实**在数据契约与 UI 上都不可见** —— 用户看到「放行 + 评分 0.85」，
#   合理推断为"AI 审核通过"。实测（子账号租户）三道闸全部降级，却仍带分数。
#
#   根因之一极其隐蔽：`orchestrator.py` 在闸降级后**还会再调一次 `call_eval()`**
#   打分；子账号里 `eval.model` 与闸模型是**同一个**，且 `call_eval` 用
#   `re.search(r"0?\.?\d+", raw)` 从自由文本抠数字、无范围校验。
#   → 一个"刚被证明出不了 JSON"的模型随口给的数，被当成质量认证。
#
#   本文件锁死三件事：
#     ① `call_eval` 对越界/畸形一律拒绝（宁缺毋滥）；
#     ② 闸记录带**结构化**依据来源 `review_status`，降级可机读、不再靠解析 reason 文本；
#     ③ 「闸降级 + 独立评分器也失败（eval_score=None）」不再击穿任务投影
#        （历史上 `GateReview.eval_score` 必填非空 → ValidationError → 任务详情 500）。
#
# 全部离线：StubLLMClient + MockProvider，不触网。
#
# 运行环境说明：本文件的「②③ 投影层」用例需要 `server.api`（依赖 asyncpg）。
# 宿主测试环境可能缺 asyncpg —— 那种情况下这些用例会 **skip**（诚实标注，不伪装通过）；
# 容器内（report-api）会真正执行。

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (  # noqa: E402
    StubLLMClient, run_report, LLMError, call_eval,
)
from tools import ToolBundle  # noqa: E402
from tools.web_search import WebSearchTool, MockProvider  # noqa: E402
from tools.data_proc import DataProcTool  # noqa: E402
from tools.doc_export import DocExportTool  # noqa: E402


# ============================================================================
# 公共脚手架
# ============================================================================


def _task():
    return {
        "topic": "AI 芯片市场研报",
        "scope": ["供给", "需求", "竞争格局"],
        "output_format_spec": "markdown",
        "constraints": ["中文输出"],
    }


def _tools():
    return ToolBundle(
        web_search=WebSearchTool(provider=MockProvider(), max_results=3),
        data_proc=DataProcTool(enabled=True),
        doc_export=DocExportTool(enabled=True, export_dir=None),
    )


class _GateAUnparseable(StubLLMClient):
    """GateA 无论重试几次都返回非 JSON（模拟 flaky 网关返回纯文本）。

    **注意**：`role == "Eval"` 走父类（返回 "0.8"）——这正是线上骗局的完整复现：
    闸降级了，但独立评分器照样给出了一个数。
    """

    def complete(self, model, system, user, **kw):
        if kw.get("role") == "GateA":
            return "经综合评估，本轮检索结果整体可用，无需返工。补充说明略。"
        return super().complete(model, system, user, **kw)


class _GatesAndEvalAllDown(StubLLMClient):
    """审核闸与独立评分器**同时**不可用（子账号真实故障的极端形态）。

    这是历史上会把任务详情页打成 500 的场景：
    闸全部降级 → 每一步 `eval_score=None` → `GateReview.eval_score: float` 校验失败。
    """

    def complete(self, model, system, user, **kw):
        role = kw.get("role")
        if role in ("GateA", "GateB", "GateC", "Eval"):
            raise LLMError("模拟：审核模型不可用（网络/模型侧故障）")
        return super().complete(model, system, user, **kw)


def _api():
    """惰性导入 server.api（依赖 asyncpg）。

    宿主缺 asyncpg 时 host 上会 skip；容器内（report-api）会真正执行。
    用 importorskip 而非 try/except 吞掉 —— skip 是**可见的**，静默例外不是。
    """
    pytest.importorskip("asyncpg", reason="宿主缺 asyncpg（server.api 依赖）；容器内执行")
    from server import api  # noqa: PLC0415

    return api


# ============================================================================
# ① call_eval 可信性守卫：越界/畸形一律拒绝（宁缺毋滥）
# ============================================================================


class _RawLLM:
    """只回固定字符串的假 LLM，用于直测 call_eval 的解析守卫。"""

    def __init__(self, raw: str):
        self._raw = raw
        self.seen_roles = []

    def complete(self, model, system, user, **kw):
        self.seen_roles.append(kw.get("role"))
        return self._raw


_FAKE_REG = {"agents": {"Writer": {"output_key": "report_markdown"}}, "reviews": {}}
_FAKE_MODELS = {"eval": {"model": "fake-eval-model"}}


def _eval_with(raw: str):
    return call_eval(
        _RawLLM(raw), _FAKE_MODELS, {"report_markdown": "# 研报\n正文"},
        "GateC", _FAKE_REG, role="Writer",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.85", 0.85),           # 正常：唯一合法形态
        (".5", 0.5),              # 合法：无前导零
        ("0", 0.0),               # 合法：边界下界
        ("1", 1.0),               # 合法：边界上界
        ("85%", None),            # 越界：百分号形态会被抠成 85.0 —— 必须拒绝
        ("评分：1. 内容完整度 0.85", None),  # 误抠：会取到 first-match "1." → 1.0
        ("2.5", None),            # 越界：> 1
        ("本报告质量较好，无需打分。", None),  # 无数字
        ("", None),               # 空串
    ],
)
def test_call_eval_rejects_untrustworthy_scores(raw, expected):
    """`call_eval` 只接受 [0,1] 区间的数字；其余一律 None。

    原实现 `float(re.search(r'0?\\.?\\d+', raw).group(0))` 无任何校验，
    "85%" 会产出 85.0 并一路流到前端展示成"评分"。
    """
    assert _eval_with(raw) == expected


def test_call_eval_uses_eval_model_and_eval_role():
    """守卫不得改变既有调用姿势（仍用 models['eval']['model'] + role='Eval'）。"""
    llm = _RawLLM("0.5")
    call_eval(llm, _FAKE_MODELS, {"report_markdown": "x"}, "GateC", _FAKE_REG, role="Writer")
    assert llm.seen_roles == ["Eval"]


# ============================================================================
# ② 闸记录带结构化 review_status：降级可机读，不再靠解析 reason 文本
# ============================================================================


def test_degraded_gate_is_marked_structurally():
    """闸降级放行 → review_status == 'degraded_unavailable'。

    同时锁死那个"骗人"的组合：**降级了却仍有分数**。
    分数来自独立评分器而非审核闸 —— 由 eval_score_source 区分。
    """
    final = run_report(_task(), _GateAUnparseable("pass"), max_rounds=2, tools=_tools())
    rs = final["routing_state"]
    assert rs["status"] == "done", rs

    first = rs["gate_review_history"][0]
    assert first["decision"] == "advance", first
    assert first["review_status"] == "degraded_unavailable", first
    # 核心断言：分数存在 ≠ 闸审过。来源必须标成独立评分器。
    assert first["eval_score"] is not None, f"stub 的 Eval 会返回 0.8: {first}"
    assert first["eval_score_source"] == "independent_scorer", first
    # 审计事件仍须留痕（既有契约不回归）
    evs = [e for e in rs.get("engine_events", [])
           if e["event"] == "gate_llm_unavailable_degraded_advance"]
    assert len(evs) == 1, f"应恰好留痕 1 次降级放行: {evs}"


def test_normal_gate_is_marked_llm_reviewed():
    """正常路径：闸 LLM 出了可解析结论 → review_status == 'llm_reviewed'，分数来源为闸 LLM。"""
    final = run_report(_task(), StubLLMClient("pass"), max_rounds=2, tools=_tools())
    rs = final["routing_state"]
    assert rs["status"] == "done", rs

    hist = rs["gate_review_history"]
    assert hist, "正常路径应留下闸记录"
    for gr in hist:
        assert gr["review_status"] == "llm_reviewed", gr
        assert gr["eval_score_source"] == "gate_llm", gr


def test_review_status_always_present_on_every_branch():
    """回归保护：review_status 必须在**所有**分支被赋值（否则 UnboundLocalError）。"""
    for llm in (StubLLMClient("pass"), _GateAUnparseable("pass")):
        rs = run_report(_task(), llm, max_rounds=2, tools=_tools())["routing_state"]
        for gr in rs["gate_review_history"]:
            assert gr.get("review_status") in (
                "llm_reviewed", "code_verified", "degraded_unavailable"
            ), gr


# ============================================================================
# ③ 投影层容错：eval_score=None 不得击穿任务投影（实缺修复）
# ============================================================================


def test_double_failure_keeps_task_alive_and_projectable():
    """闸 + 独立评分器同时不可用 → 任务仍 done，且投影不再崩。

    这是本次修的**实测复现缺陷**：eval_score=None 曾令 GateReview 校验失败，
    实时路径 500、文件恢复路径把任务当成"不存在"。
    """
    final = run_report(_task(), _GatesAndEvalAllDown("pass"), max_rounds=2, tools=_tools())
    rs = final["routing_state"]
    assert rs["status"] == "done", rs

    hist = rs["gate_review_history"]
    assert hist
    for gr in hist:
        assert gr["review_status"] == "degraded_unavailable", gr
        assert gr["eval_score"] is None, f"评分器也挂了，不应有分数: {gr}"
        assert gr["eval_score_source"] is None, gr

    api = _api()
    projected = api._coerce_routing_state(rs)     # 修复前此处必抛 ValidationError
    assert projected.status.value == "done"
    assert all(g.eval_score is None for g in projected.gate_review_history)


def test_gate_review_model_accepts_null_eval_score():
    """契约：eval_score 可空（修复前是必填非空 float）。"""
    api = _api()
    gr = api.GateReview(
        decision="advance",
        reason="审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
        eval_score=None,
        review_status="degraded_unavailable",
        problem_points=["审核 LLM 不可用，已降级放行"],
        gate="GateC",
        round=2,
        timestamp="2026-09-19T00:00:00Z",
    )
    assert gr.eval_score is None


def test_coerce_backfills_review_status_for_historical_data():
    """历史任务无 review_status（本设计新增）→ 按 reason 文本回填，使旧数据同样可见。"""
    api = _api()
    raw = {
        "round": 2, "max_rounds": 2, "status": "done", "last_gate": "GateC",
        "gate_review_history": [
            {"decision": "advance",
             "reason": "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
             "eval_score": 0.85, "problem_points": ["审核 LLM 不可用，已降级放行"],
             "gate": "GateC", "round": 2, "timestamp": "2026-09-19T00:00:00Z"},
            {"decision": "advance", "reason": "产出满足全部校验标准。",
             "eval_score": 0.9, "problem_points": ["无"],
             "gate": "GateB", "round": 1, "timestamp": "2026-09-19T00:00:01Z"},
        ],
        "engine_events": [],
    }
    projected = api._coerce_routing_state(raw)
    assert projected.gate_review_history[0].review_status == "degraded_unavailable"
    assert projected.gate_review_history[1].review_status == "llm_reviewed"
    # 历史数据无从判断分数来源 → 不强行推断，保持 None
    assert all(g.eval_score_source is None for g in projected.gate_review_history)


def test_coerce_isolates_malformed_entries():
    """单条隔离：一条畸形不得连坐整批（策略与 _validate_records 一致）。"""
    api = _api()
    raw = {
        "round": 1, "max_rounds": 2, "status": "done",
        "gate_review_history": [
            {"reason": "缺 decision/gate/round，必然无法投影"},
            {"decision": "advance", "reason": "正常一条", "eval_score": 0.9,
             "problem_points": ["无"], "gate": "GateA", "round": 1,
             "timestamp": "2026-09-19T00:00:00Z"},
            "我不是 dict",
        ],
        "engine_events": [],
    }
    projected = api._coerce_routing_state(raw)
    assert len(projected.gate_review_history) == 1
    assert projected.gate_review_history[0].gate == "GateA"


def test_coerce_survives_missing_required_scalars():
    """routing 缺 round/max_rounds/status（截断写入）也不得整体失败。"""
    api = _api()
    projected = api._coerce_routing_state({"gate_review_history": [], "engine_events": []})
    assert projected.round == 0
    assert projected.max_rounds == 0
    assert projected.status.value == "escalated"


# ============================================================================
# ④ 审计导出必须带上新字段（否则审计包看不到"这道闸到底审没审"）
# ============================================================================


def test_audit_fields_include_review_status():
    from server import audit_export  # noqa: PLC0415

    assert "review_status" in audit_export._GATE_FIELDS
    assert "eval_score_source" in audit_export._GATE_FIELDS
