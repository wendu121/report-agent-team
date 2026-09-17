# tests/test_regression_edge.py · B 方案：异常回归用例集（M5 后补零覆盖高危路径）
#
# 背景：M5 实跑暴露的 5 个真 bug **没有一个被原有 25 个单测覆盖** ——
# 单测验证"代码符合我预期"，本文件验证"系统在混沌下会不会骗人/静默失败"。
# 全部基于 StubLLMClient + Mock 工具，无需真实外部 API。
#
# 覆盖顺序（boss 指定，2/3 优先）：
#   ① 闸 LLM 连续不可用 + 代码硬校验通过 → 降级放行 + 审计事件必落库
#   ② 检索全空 → GateA 强制 escalate，LLM 的 advance 不得覆盖代码判定
#   ③ max_round 耗尽 → escalate（强化断言）
#   ④ 引用章节缺 url / 伪造 url → doc_export 重建 + writer_citation_regenerated
#   ⑤ 上游 429 分区「配额耗尽型」与「瞬断型」→ 验证重试逻辑

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (  # noqa: E402
    StubLLMClient, run_report, LLMError, NewApiLLMClient, _is_non_retryable,
)
from tools import ToolBundle, ToolError  # noqa: E402
from tools.web_search import WebSearchTool, MockProvider  # noqa: E402
from tools.data_proc import DataProcTool  # noqa: E402
from tools.doc_export import DocExportTool  # noqa: E402


def _task():
    return {
        "topic": "AI 芯片市场研报",
        "scope": ["供给", "需求", "竞争格局"],
        "output_format_spec": "markdown",
        "constraints": ["中文输出"],
    }


def _tools(provider=None):
    """默认 MockProvider（不联网）；provider 可传入故障模拟器。"""
    return ToolBundle(
        web_search=WebSearchTool(provider=provider or MockProvider(), max_results=3),
        data_proc=DataProcTool(enabled=True),
        doc_export=DocExportTool(enabled=True, export_dir=None),
    )


# ============================================================================
# ① 闸 LLM 连续不可用 + 代码硬校验通过 → 降级放行 + 审计事件必落库
# ============================================================================

class _GateAUnparseable(StubLLMClient):
    """GateA 无论重试几次都返回非 JSON（模拟 flaky 网关返回截断/纯文本）。"""

    def complete(self, model, system, user, **kw):
        if kw.get("role") == "GateA":
            # 刻意不含任何 { } [ ] ，确保 robust_json_load 必然失败
            return "经综合评估，本轮检索结果整体可用，无需返工。补充说明略。"
        return super().complete(model, system, user, **kw)


def test_gate_llm_unavailable_degrades_to_advance_with_audit():
    """代码硬校验通过而闸 LLM 不可用 → 降级放行，且**必须**留下审计事件。

    这是 M5 新增逻辑，此前零覆盖。没有审计事件 = 降级放行变成不可追溯的黑箱。
    """
    final = run_report(_task(), _GateAUnparseable("pass"), max_rounds=2, tools=_tools())
    rs = final["routing_state"]

    # 1) 未被误杀：整任务仍应完成（代码硬校验已通过，不该因 LLM 抖动 escalate）
    assert rs["status"] == "done", rs

    # 2) 降级放行决策生效
    first = rs["gate_review_history"][0]
    assert first["decision"] == "advance", first
    assert "降级放行" in (first["reason"] or ""), first

    # 3) 审计事件必落库（核心断言）
    evs = rs.get("engine_events", [])
    degraded = [e for e in evs if e["event"] == "gate_llm_unavailable_degraded_advance"]
    assert len(degraded) == 1, f"应恰好留痕 1 次降级放行: {evs}"
    assert degraded[0]["gate"] == "GateA", degraded[0]
    # TD-009 回归加固：round 为 1-based，事件轮次须 >=1（不仅非 None），防止 off-by-one 复发
    assert degraded[0]["round"] >= 1, f"事件轮次须 >=1: {degraded[0]['round']}"
    assert "降级放行" in (degraded[0]["reason"] or ""), degraded[0]

    # 4) 降级不等于免检：代码硬校验本身判 rework/escalate 时，绝不能降级放行
    #    （由用例 ② 反向验证）


def test_gate_llm_unavailable_does_not_degrade_when_code_says_rework():
    """反向边界：代码硬校验判 rework 时，即便闸 LLM 不可用也**不得**降级放行。"""
    from orchestrator import machine_check

    # 构造一个代码必判 rework 的 state（引用了不存在的 rec id）
    state = {
        "retrieval_records": [{"id": "rec-1", "url": "https://a/1", "title": "A",
                               "snippet": "s", "credibility": "medium"}],
        "analysis_conclusions": [{"id": "con-1", "source_ids": ["rec-404"]}],
    }
    dec, reason = machine_check("GateB", state)
    assert dec == "rework", "代码硬校验应判 rework"
    assert "rec-404" in reason and "rec-1" in reason, \
        "rework 原因须给出可用 id 集合，否则模型下一轮仍会瞎编"


# ============================================================================
# ② 检索全空 → GateA 强制 escalate，禁止 LLM 的 advance 覆盖代码判定
# ============================================================================

class _DeadProvider:
    def search(self, query, max_results):
        raise ToolError("模拟检索服务不可用")


class _GateAAlwaysAdvance(StubLLMClient):
    """GateA 主观认为"没问题"，试图放行一份空检索结果。"""

    def complete(self, model, system, user, **kw):
        if kw.get("role") == "GateA":
            return json.dumps({
                "decision": "advance", "reason": "我觉得检索结果已经足够了",
                "eval_score": 0.99, "problem_points": ["无"],
            }, ensure_ascii=False)
        return super().complete(model, system, user, **kw)


def test_empty_retrieval_escalates_despite_llm_advance():
    """代码 escalate > LLM advance：空检索是业务致命项，LLM 说放行也不算数。"""
    final = run_report(_task(), _GateAAlwaysAdvance("pass"), max_rounds=2,
                       tools=_tools(_DeadProvider()))
    rs = final["routing_state"]

    assert rs["status"] == "escalated", rs
    assert final["retrieval_records"] == [], "空检索不得产出占位记录"

    gate = rs["gate_review_history"][0]
    # 核心断言：LLM 的 advance 被代码判定覆盖
    assert gate["decision"] == "escalate", f"LLM advance 覆盖了代码判定: {gate}"
    assert "检索完全无有效素材" in gate["reason"], gate
    assert gate["reason"] != "我觉得检索结果已经足够了", "不得沿用 LLM 的主观理由"

    # 工具失败须如实留痕，不静默
    ts = final["tool_status"]
    assert ts and all(not t["ok"] for t in ts if t["tool"] == "web_search")

    # 不得继续推进到 Analyst（escalate 后应终止，而非带着空素材往下写）
    assert final["analysis_conclusions"] == [], "空检索不得继续产出分析结论"


# ============================================================================
# ③ max_round 耗尽 → escalate（强化断言）
# ============================================================================

class _GateAAlwaysRework(StubLLMClient):
    def complete(self, model, system, user, **kw):
        if kw.get("role") == "GateA":
            return json.dumps({
                "decision": "rework", "reason": "永远不合格",
                "eval_score": 0.1, "problem_points": ["x"],
            }, ensure_ascii=False)
        return super().complete(model, system, user, **kw)


def test_max_rounds_exhausted_escalates_with_bounds():
    """持续 rework → 触顶 max_rounds → escalate；且轮次/历史条目数严格有界。"""
    final = run_report(_task(), _GateAAlwaysRework("pass"), max_rounds=2, tools=_tools())
    rs = final["routing_state"]

    assert rs["status"] == "escalated", rs
    assert "超过 max_rounds" in (rs.get("escalate_reason") or ""), rs.get("escalate_reason")

    # 强化断言：轮次严格封顶，不无限循环
    assert rs["round"] == rs["max_rounds"], f"轮次应封顶在 max_rounds: {rs}"
    assert len(rs["gate_review_history"]) == 2, \
        f"GateA 应恰好被评审 2 次（每轮 1 次）: {rs['gate_review_history']}"
    assert all(h["decision"] == "rework" for h in rs["gate_review_history"])

    # 卡在 GateA，下游从未执行
    assert rs["last_gate"] == "GateA", rs
    assert final["analysis_conclusions"] == [], "GateA 未放行，Analyst 不应产出"
    assert final["draft_segments"] == [], "GateA 未放行，Writer 不应产出"

    # 每轮 rework 都要带上定向原因，否则模型下一轮无从改起
    assert rs["rework_target_agent"] in (None, "Researcher"), rs
    assert rs["rework_reason"], "rework 必须携带原因"


# ============================================================================
# ④ 引用章节缺 url / 伪造 url → doc_export 重建 + writer_citation_regenerated
# ============================================================================

class _WriterBadCitation(StubLLMClient):
    """Writer 产出不合规的引用章节：伪造 url / 裸 id / 章节缺失。"""

    def __init__(self, mode: str):
        super().__init__("pass")
        self.mode = mode

    def complete(self, model, system, user, **kw):
        if kw.get("role") == "Writer":
            data = json.loads(super().complete(model, system, user, **kw))
            head = "# AI 芯片市场研报\n\n## 市场概况\nAI 芯片市场供需两旺。\n"
            if self.mode == "fabricated":
                # 形式齐全但 url 全是编造的（M4 曾真实发生的 bug）
                data["report_markdown"] = (
                    head + "\n## 引用 / 来源\n"
                    "- [rec-1](https://evil.example/a)\n- [rec-2](https://evil.example/b)\n"
                )
            elif self.mode == "bare_id":
                # 只有裸 id，无 url（M5 首次实跑就是这个形态）
                data["report_markdown"] = head + "\n## 引用 / 来源\n- rec-1\n- rec-2\n"
            elif self.mode == "missing":
                data["report_markdown"] = head  # 完全没有引用章节
            return json.dumps(data, ensure_ascii=False)
        return super().complete(model, system, user, **kw)


@pytest.mark.parametrize("mode", ["fabricated", "bare_id", "missing"])
def test_bad_citation_section_is_rebuilt_and_audited(mode):
    """三种不合规引用章节 → doc_export 确定性重建 + 审计事件 + 终稿可回溯。"""
    final = run_report(_task(), _WriterBadCitation(mode), max_rounds=2, tools=_tools())
    rs = final["routing_state"]
    md = final["report_markdown"]
    recs = final["retrieval_records"]

    assert rs["status"] == "done", f"{mode}: {rs}"
    assert recs, "前置条件：应有检索记录"

    # 1) 审计事件必落库
    evs = rs.get("engine_events", [])
    regen = [e for e in evs if e["event"] == "writer_citation_regenerated"]
    assert len(regen) == 1, f"{mode}: 应恰好重建 1 次: {evs}"
    assert regen[0]["agent"] == "Writer", regen[0]

    # 2) 终稿引用章节必须覆盖**全部**检索记录的 id 与 url（可点击回溯）
    assert "## 引用 / 来源" in md, f"{mode}: 终稿缺引用章节"
    for r in recs:
        assert r["id"] in md, f"{mode}: 终稿缺记录 {r['id']}"
        assert r["url"] in md, f"{mode}: 终稿缺记录 {r['id']} 的 url，无法回溯"

    # 3) 编造的 url 绝不得残留在终稿
    if mode == "fabricated":
        assert "evil.example" not in md, "编造 url 未被清除"


def test_valid_citation_section_is_not_regenerated():
    """反向边界：引用章节已合规时**不应**重建（避免无谓改动与假审计事件）。"""
    from tools.doc_export import DocExportTool

    recs = [{"id": "rec-1", "url": "https://a/1", "title": "A", "credibility": "medium"},
            {"id": "rec-2", "url": "https://a/2", "title": "B", "credibility": "medium"}]
    md = ("# R\n\n## 正文\n内容\n\n## 引用 / 来源\n"
          "- [rec-1](https://a/1) — A（可信度: medium）\n"
          "- [rec-2](https://a/2) — B（可信度: medium）\n")
    _, regenerated, _ = DocExportTool(enabled=True).ensure_citation_section(md, recs)
    assert regenerated is False, "合规章节被误判为需重建"


# ============================================================================
# ⑤ 429 区分「配额耗尽型」与「瞬断型」→ 验证重试逻辑
# ============================================================================

def _fake_openai(script):
    """伪造 OpenAI client：按脚本依次返回内容或抛异常，并记录调用次数。"""
    holder = {"calls": 0, "script": list(script)}

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kw):
            holder["calls"] += 1
            item = holder["script"].pop(0)
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=item))])
    return _Client(), holder


def _mk_gateway_client(script, max_retries=2):
    # 旧签名 (base_url, api_key) → 新签名（endpoints + default_endpoint_id）
    c = NewApiLLMClient(
        endpoints=[{"id": "t", "name": "t", "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "sk-test"}],
        interfaces=[],
        default_endpoint_id="t",
        max_retries=max_retries,
    )
    fake, holder = _fake_openai(script)
    # 多端点架构（多端点 commit 后）：complete() 走 _clients[endpoint_id] 缓存，
    # 须把 fake 注入端点 't' 的缓存槽；旧单客户端属性 _client 已废弃。
    c._clients["t"] = fake
    return c, holder


def test_transient_429_is_retried(monkeypatch):
    """瞬断型 429（Rate limit，稍后即恢复）→ 应重试并最终成功。"""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    c, holder = _mk_gateway_client(
        [RuntimeError("Error code: 429 - rate limit reached for requests"), "ok"])
    assert c.complete("m", "s", "u") == "ok"
    assert holder["calls"] == 2, "瞬断错误应重试一次后成功"


def test_quota_exhausted_429_is_not_retried(monkeypatch):
    """配额耗尽型 429 → **立即放弃**，不得重试（重试无用且烧本就见底的额度）。"""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    c, holder = _mk_gateway_client(
        [RuntimeError("Error code: 429 - Quota exceeded for aiplatform.googleapis.com, limit: 20")],
        max_retries=3)
    with pytest.raises(LLMError) as ei:
        c.complete("m", "s", "u")
    assert holder["calls"] == 1, f"配额耗尽不应重试，实际调用 {holder['calls']} 次"
    assert "不可重试" in str(ei.value), ei.value


@pytest.mark.parametrize("msg", [
    "Error code: 401 - invalid token",
    "Error code: 404 - model_not_found: no such model",
    "no available channel for model",
    "您的额度已用尽",
])
def test_non_retryable_errors_abort_immediately(monkeypatch, msg):
    """鉴权/模型不存在/无可用渠道/额度 → 一律不可重试。"""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    c, holder = _mk_gateway_client([RuntimeError(msg)], max_retries=3)
    with pytest.raises(LLMError):
        c.complete("m", "s", "u")
    assert holder["calls"] == 1, f"{msg!r} 不应重试"


def test_transient_error_exhausts_retries_then_raises(monkeypatch):
    """持续性瞬断故障 → 用尽重试次数后才失败（且错误信息标明重试次数）。"""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    c, holder = _mk_gateway_client(
        [RuntimeError("connection reset") for _ in range(5)], max_retries=2)
    with pytest.raises(LLMError) as ei:
        c.complete("m", "s", "u")
    assert holder["calls"] == 3, f"max_retries=2 应共尝试 3 次，实际 {holder['calls']}"
    assert "已重试 2 次" in str(ei.value), ei.value


def test_is_non_retryable_helper():
    """判定函数本身：瞬断型放行重试，配额/鉴权/模型类拦截。"""
    assert not _is_non_retryable(RuntimeError("429 rate limit reached"))
    assert not _is_non_retryable(RuntimeError("connection reset by peer"))
    assert not _is_non_retryable(RuntimeError("APITimeoutError: Request timed out"))
    assert _is_non_retryable(RuntimeError("Quota exceeded for gemini, limit: 20"))
    assert _is_non_retryable(RuntimeError("invalid token"))
    assert _is_non_retryable(RuntimeError("model_not_found"))
