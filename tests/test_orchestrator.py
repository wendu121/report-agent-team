# tests/test_orchestrator.py · M3 冒烟测试（M2 铁律 7：断言 JSON 可解析 + rework 环 + 容错）
#
# 运行：pip install -r requirements.txt && pytest tests/test_orchestrator.py -q
# 仅用 StubLLMClient，无需联网 / new-api 密钥。

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (  # noqa: E402
    StubLLMClient, run_report, robust_json_load, JSONRepairError,
)
from tools.web_search import MockProvider  # noqa: E402


def _task():
    return {
        "topic": "AI 芯片市场研报",
        "scope": ["供给", "需求", "竞争格局"],
        "output_format_spec": "markdown",
        "constraints": ["中文输出"],
    }


def test_pass_reaches_done():
    """scenario=pass：全链路 advance，status=done，三道闸历史齐全。"""
    final = run_report(_task(), StubLLMClient(scenario="pass"), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "done", rs
    assert len(final["retrieval_records"]) > 0
    assert len(final["analysis_conclusions"]) > 0
    assert len(final["draft_segments"]) > 0
    assert len(rs["gate_review_history"]) == 3
    assert all(h["decision"] == "advance" for h in rs["gate_review_history"])
    # TD-009 回归加固：无 rework 时 round 应恒为 1（首轮即计为 round 1），防止 off-by-one 复发
    assert rs["round"] == 1, f"pass 场景 round 应恒为 1: {rs['round']}"
    # 引用链机器校验通过：结论引用的 source_ids 都存在于检索记录
    rec_ids = {r["id"] for r in final["retrieval_records"]}
    for c in final["analysis_conclusions"]:
        assert set(c["source_ids"]).issubset(rec_ids)


def test_rework_loop_and_convergence():
    """scenario=rework：首次 GateA rework → round 增至 2 → 二次通过 → done。"""
    final = run_report(_task(), StubLLMClient(scenario="rework"), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "done", rs
    assert rs["round"] == 2, "应发生一次 rework（round 1→2）"
    decisions = [h["decision"] for h in rs["gate_review_history"]]
    assert "rework" in decisions, "应出现至少一次 rework"
    assert decisions[-1] == "advance"


def test_max_rounds_escalate():
    """持续 rework 应被 max_rounds 闸断，转 escalate（不无限循环）。"""
    # StubLLMClient 无"持续 rework"场景，这里用猴子补丁制造 GateA 永远 rework
    class AlwaysReworkGate(StubLLMClient):
        def complete(self, model, system, user, **kw):
            if kw.get("role") == "GateA":
                return '{"decision":"rework","reason":"永远不合格","eval_score":0.1,"problem_points":["x"]}'
            return super().complete(model, system, user, **kw)

    final = run_report(_task(), AlwaysReworkGate(scenario="pass"), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "escalated", rs
    assert "超过 max_rounds" in (rs.get("escalate_reason") or ""), rs.get("escalate_reason")


# ---------------- JSON 容错（M3 引擎层责任，memory/schema.md §0-5）----------------

def test_robust_json_load_repairs():
    """修复能力：围栏 / 前后缀废话 / 裸数组。"""
    assert robust_json_load('```json\n{"a":1}\n```') == {"a": 1}
    assert robust_json_load('好的，结果如下：\n{"decision":"advance"}\n以上。')["decision"] == "advance"
    assert robust_json_load('前缀 [{"id":"rec-1"}] 后缀') == [{"id": "rec-1"}]
    try:
        robust_json_load("完全没有 JSON")
        assert False, "应抛 JSONRepairError"
    except JSONRepairError:
        pass


def test_json_recover_by_retry():
    """首次输出非法 JSON → 引擎重试一次后恢复 → 不触发 rework。"""

    class FlakyOnce(StubLLMClient):
        def __init__(self):
            super().__init__("pass")
            self._bad = True

        def complete(self, model, system, user, **kw):
            if kw.get("role") == "Researcher" and self._bad:
                self._bad = False
                return "抱歉，我无法按照要求输出，以下是分析：……（无 JSON）"
            return super().complete(model, system, user, **kw)

    final = run_report(_task(), FlakyOnce(), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "done", rs
    assert rs["round"] == 1, "重试即可恢复，不应消耗 rework 轮次"
    assert len(final["retrieval_records"]) > 0


def test_json_unrecoverable_escalates():
    """持续输出非法 JSON → 重试无效 → rework → 触顶 max_rounds → escalate（不静默崩）。"""

    class AlwaysBad(StubLLMClient):
        def complete(self, model, system, user, **kw):
            if kw.get("role") == "Researcher":
                return "我就是不想输出 JSON。"
            return super().complete(model, system, user, **kw)

    final = run_report(_task(), AlwaysBad(scenario="pass"), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "escalated", rs
    # 产出不可用属引擎层事件，留痕在 engine_events（schema §4.1），不混入 gate_review_history
    evs = rs.get("engine_events", [])
    assert len(evs) >= 2, f"两次崩坏均应留痕: {evs}"
    assert all(e["event"] == "agent_output_unusable" for e in evs)
    assert any("JSON 解析失败" in (e["reason"] or "") for e in evs), evs
    assert rs["gate_review_history"] == [], "闸门从未执行，不应有闸评审记录"


# ---------------- M4：工具真实接入后的溯源与熔断 ----------------

def _tools_bundle(provider):
    from tools import ToolBundle
    from tools.web_search import WebSearchTool
    from tools.data_proc import DataProcTool
    from tools.doc_export import DocExportTool
    return ToolBundle(
        web_search=WebSearchTool(provider=provider, max_results=3),
        data_proc=DataProcTool(enabled=True),
        doc_export=DocExportTool(enabled=True, export_dir=None),
    )


def test_fabricated_url_is_blocked():
    """Researcher 塞入不在检索结果集内的 url → 溯源硬校验拦截 → 不得进入终稿。"""

    class Fabricating(StubLLMClient):
        def complete(self, model, system, user, **kw):
            if kw.get("role") == "Researcher":
                recs = self._extract_injected_records(user)
                if recs:
                    recs[-1] = {**recs[-1], "url": "https://fabricated.example/evil"}
                return json.dumps({
                    "retrieval_records": [
                        {"id": r.get("id"), "url": r.get("url"), "title": r.get("title"),
                         "snippet": r.get("snippet"), "credibility": r.get("credibility")}
                        for r in recs
                    ],
                    "tool_status": [], "change_note": "",
                }, ensure_ascii=False)
            return super().complete(model, system, user, **kw)

    from tools.web_search import MockProvider
    final = run_report(_task(), Fabricating(), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "escalated", rs
    assert all("fabricated.example" not in (r.get("url") or "")
               for r in final["retrieval_records"]), "编造 url 不得进入 state"
    assert any("疑似编造" in (e.get("reason") or "") for e in rs.get("engine_events", []))


def test_search_failure_escalates_not_silent():
    """检索全部失败 → tool_status.ok=false → 空产出交 GateA → escalate（不静默通过）。"""
    from tools import ToolError
    from tools.web_search import WebSearchTool

    class DeadProvider:
        def search(self, query, max_results):
            raise ToolError("模拟服务不可用")

    final = run_report(_task(), StubLLMClient(), max_rounds=2, tools=_tools_bundle(DeadProvider()))
    rs = final["routing_state"]
    assert rs["status"] == "escalated", rs
    ts = final["tool_status"]
    assert ts and all(not t["ok"] for t in ts if t["tool"] == "web_search")
    assert final["retrieval_records"] == []
    # 关键：代码硬校验的 escalate 必须生效，不能被 LLM 的 advance 覆盖
    assert rs["gate_review_history"][0]["decision"] == "escalate"


def test_extract_injected_records_ignores_brackets_inside_strings():
    """回归：检索素材正文含方括号（关税明细 `一般关税[Free] + 附加关税[12.5%]`、
    文献年份 `[2025]`）时，朴素 depth 计数会在字符串内部误增减，
    把合法 JSON 数组截断 → 解析恒失败 → Researcher 误判"检索失败" → 整任务 escalate。
    解析器必须字符串感知。
    """
    recs = [
        {"id": "rec-1", "url": "https://a.com/1",
         "title": "HS 85183020 关税明细 [Free]/[12.5%]",
         "snippet": "一般关税[Free] + 附加关税[12.5%]｜[2025] 银行竞争格局",
         "credibility": "high", "source": "accio_tariff"},
        {"id": "rec-2", "url": "https://a.com/2", "title": 'say "hi" [x]',
         "snippet": "含转义引号\"与方括号[1]", "credibility": "medium"},
    ]
    payload = json.dumps(recs, ensure_ascii=False)
    got = StubLLMClient._extract_injected_records("前言 " + payload + " 后语")
    assert len(got) == 2, f"字符串内方括号不得破坏解析: {got}"
    assert got[0]["id"] == "rec-1" and got[1]["id"] == "rec-2"

    # 边界：无数组 / 空串 → 安全返回空，不抛异常
    assert StubLLMClient._extract_injected_records("没有数组") == []
    assert StubLLMClient._extract_injected_records("") == []
    # 边界：合法 JSON 数组但元素非 record（缺 url）→ 不被误认
    assert StubLLMClient._extract_injected_records('["a", "b"]') == []


if __name__ == "__main__":
    # 直接运行也能看到结果（不依赖 pytest）
    os.environ.setdefault("STUB_SCENARIO", "pass")
    for sc in ("pass", "rework"):
        print(f"\n=== scenario={sc} ===")
        f = run_report(_task(), StubLLMClient(scenario=sc), max_rounds=2,
                       tools=_tools_bundle(MockProvider()))
        print(f["routing_state"]["status"], "round=", f["routing_state"]["round"])
