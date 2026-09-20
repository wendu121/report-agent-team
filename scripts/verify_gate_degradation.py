"""容器内验证脚本 · 闸降级可见性（M13-gate-degradation）

为什么需要它（而不是跑 pytest）：
  · `server/api.py` 依赖 asyncpg，**宿主**测试环境缺该依赖 → 相关用例只能 skip；
  · `report-api` 镜像里没有 pytest（生产镜像不该装测试框架）。
  故用本脚本在**真实容器**里直接调用被测函数，验证同一批断言。

全部离线（StubLLMClient + MockProvider），不触网、不写租户配置。
运行：docker compose exec -T api python /app/scripts/verify_gate_degradation.py
"""

import sys

sys.path.insert(0, "/app")

# 多租户契约是 fail loud：没有账号上下文就抛 TenancyError（RAT_LEGACY_GLOBAL 已明确不启用）。
# 选一个已存在的租户，让 load_* 读到真实配置。
TENANT = "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"

from server import tenancy  # noqa: E402

tenancy.set_current_account(TENANT)

ok, bad = [], []


def chk(name, cond, extra=""):
    (ok if cond else bad).append(name + (f"  :: 实测={extra}" if extra != "" else ""))


from server.api import GateReview, _coerce_routing_state  # noqa: E402
from orchestrator import StubLLMClient, run_report, LLMError, call_eval  # noqa: E402
from tools import ToolBundle  # noqa: E402
from tools.web_search import WebSearchTool, MockProvider  # noqa: E402
from tools.data_proc import DataProcTool  # noqa: E402
from tools.doc_export import DocExportTool  # noqa: E402

# ---------------------------------------------------------------------------
# A. 契约：eval_score 可空（修复前为必填非空 float）
# ---------------------------------------------------------------------------
gr = GateReview(
    decision="advance",
    reason="审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
    eval_score=None,
    review_status="degraded_unavailable",
    problem_points=["审核 LLM 不可用，已降级放行"],
    gate="GateC", round=2, timestamp="2026-09-19T00:00:00Z",
)
chk("A1 GateReview 接受 eval_score=None", gr.eval_score is None)

# ---------------------------------------------------------------------------
# B. 投影：eval_score=None 不再抛（核心实缺）
# ---------------------------------------------------------------------------
raw = {
    "round": 2, "max_rounds": 2, "status": "done", "last_gate": "GateC",
    "gate_review_history": [{
        "decision": "advance",
        "reason": "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
        "eval_score": None, "review_status": "degraded_unavailable",
        "problem_points": ["审核 LLM 不可用，已降级放行"],
        "gate": "GateC", "round": 2, "timestamp": "2026-09-19T00:00:00Z",
    }],
    "engine_events": [],
}
try:
    p = _coerce_routing_state(raw)
    chk("B1 eval_score=None 投影成功（修复前必抛 ValidationError）", True)
    chk("B2 状态保真", p.status.value == "done", p.status.value)
    chk("B3 分数保持 None（不臆造）", p.gate_review_history[0].eval_score is None)
except Exception as e:  # noqa: BLE001
    chk("B1 eval_score=None 投影成功（修复前必抛 ValidationError）", False,
        f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
# C. 历史数据回填 review_status（兼容逻辑收敛在后端）
# ---------------------------------------------------------------------------
historical = {
    "round": 1, "max_rounds": 2, "status": "done",
    "gate_review_history": [
        {"decision": "advance",
         "reason": "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
         "eval_score": 0.85, "problem_points": ["x"],
         "gate": "GateC", "round": 1, "timestamp": "t"},
        {"decision": "advance", "reason": "产出满足全部校验标准。", "eval_score": 0.9,
         "problem_points": ["无"], "gate": "GateB", "round": 1, "timestamp": "t"},
    ],
    "engine_events": [],
}
p = _coerce_routing_state(historical)
chk("C1 降级记录回填 degraded_unavailable",
    p.gate_review_history[0].review_status == "degraded_unavailable",
    p.gate_review_history[0].review_status)
chk("C2 正常记录回填 llm_reviewed",
    p.gate_review_history[1].review_status == "llm_reviewed",
    p.gate_review_history[1].review_status)
chk("C3 历史数据不强行推断分数来源",
    all(g.eval_score_source is None for g in p.gate_review_history))
chk("C4 复现骗人形态：历史降级记录**仍带 0.85**，现由 review_status 识别",
    p.gate_review_history[0].eval_score == 0.85,
    p.gate_review_history[0].eval_score)

# ---------------------------------------------------------------------------
# D. 单条隔离 / E. 缺标量容错
# ---------------------------------------------------------------------------
p = _coerce_routing_state({
    "gate_review_history": [
        {"reason": "缺 decision/gate/round"},
        {"decision": "advance", "reason": "ok", "eval_score": 0.9, "problem_points": [],
         "gate": "GateA", "round": 1, "timestamp": "t"},
        "我不是 dict",
    ],
    "engine_events": [],
})
chk("D1 单条隔离：畸形条目被丢弃、合法条目保留", len(p.gate_review_history) == 1,
    len(p.gate_review_history))

p = _coerce_routing_state({"gate_review_history": [], "engine_events": []})
chk("E1 缺 round/max_rounds/status 不崩",
    p.round == 0 and p.max_rounds == 0 and p.status.value == "escalated",
    (p.round, p.max_rounds, p.status.value))

# ---------------------------------------------------------------------------
# F. 端到端：闸 + 独立评分器**同时**不可用 → 任务仍 done 且投影成功
# ---------------------------------------------------------------------------


class _AllDown(StubLLMClient):
    def complete(self, model, system, user, **kw):
        if kw.get("role") in ("GateA", "GateB", "GateC", "Eval"):
            raise LLMError("模拟：审核模型不可用（网络/模型侧故障）")
        return super().complete(model, system, user, **kw)


_tools = ToolBundle(
    web_search=WebSearchTool(provider=MockProvider(), max_results=3),
    data_proc=DataProcTool(enabled=True),
    doc_export=DocExportTool(enabled=True, export_dir=None),
)
_task = {"topic": "AI 芯片市场研报", "scope": ["供给", "需求"],
         "output_format_spec": "markdown", "constraints": ["中文输出"]}

final = run_report(_task, _AllDown("pass"), max_rounds=2, tools=_tools)
rs = final["routing_state"]
chk("F1 双挂时任务仍 done", rs["status"] == "done", rs["status"])
hist = rs["gate_review_history"]
chk("F2 全部闸标记 degraded_unavailable",
    all(g["review_status"] == "degraded_unavailable" for g in hist),
    [g.get("review_status") for g in hist])
chk("F3 双挂时分数为 None（评分器也挂了，不臆造）",
    all(g["eval_score"] is None for g in hist), [g.get("eval_score") for g in hist])
try:
    _coerce_routing_state(rs)
    chk("F4 双挂场景投影成功（修复前必崩 → 任务详情 500）", True)
except Exception as e:  # noqa: BLE001
    chk("F4 双挂场景投影成功（修复前必崩 → 任务详情 500）", False,
        f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
# G. call_eval 可信性守卫
# ---------------------------------------------------------------------------


class _RawLLM:
    def __init__(self, r):
        self._r = r

    def complete(self, *a, **k):
        return self._r


_reg = {"agents": {"Writer": {"output_key": "report_markdown"}}, "reviews": {}}
_mods = {"eval": {"model": "fake-eval"}}
for raw_, exp in [
    ("0.85", 0.85), (".5", 0.5), ("0", 0.0), ("1", 1.0),
    ("85%", None), ("评分：1. 内容完整度 0.85", None), ("2.5", None),
    ("本报告质量较好，无需打分。", None), ("", None),
]:
    got = call_eval(_RawLLM(raw_), _mods, {"report_markdown": "x"}, "GateC", _reg, role="Writer")
    chk(f"G call_eval({raw_!r}) 期望 {exp}", got == exp, got)

# ---------------------------------------------------------------------------
# H. 正常路径不回归
# ---------------------------------------------------------------------------
final = run_report(_task, StubLLMClient("pass"), max_rounds=2, tools=_tools)
rs = final["routing_state"]
chk("H1 正常路径全部 llm_reviewed",
    all(g["review_status"] == "llm_reviewed" for g in rs["gate_review_history"]),
    [g.get("review_status") for g in rs["gate_review_history"]])
chk("H2 正常路径分数来源为 gate_llm",
    all(g["eval_score_source"] == "gate_llm" for g in rs["gate_review_history"]),
    [g.get("eval_score_source") for g in rs["gate_review_history"]])
chk("H3 正常路径任务 done", rs["status"] == "done", rs["status"])

# ---------------------------------------------------------------------------
# I. 文件恢复路径：降级任务**不得被当成"不存在"**
#    （_load_task_from_file 外层是 try/except 整体吞掉 → return None，
#      修复前投影崩在这里表现为"任务 404"，比 500 更难排查）
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from server import api as _api_mod  # noqa: E402

_tmp = _Path(tempfile.mkdtemp())
_tid = "verify-degraded-0001"
(_tmp / f"{_tid}_output.json").write_text(_json.dumps({
    "user_task": {"topic": "闸降级验证", "scope": ["s"],
                  "output_format_spec": "markdown", "constraints": []},
    "routing_state": {
        "round": 2, "max_rounds": 2, "status": "done", "last_gate": "GateC",
        "gate_review_history": [{
            "decision": "advance",
            "reason": "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题",
            "eval_score": None, "problem_points": ["审核 LLM 不可用，已降级放行"],
            "gate": "GateC", "round": 2, "timestamp": "2026-09-19T00:00:00Z"}],
        "engine_events": [{
            "event": "gate_llm_unavailable_degraded_advance", "gate": "GateC",
            "reason": "审核 LLM 多次返回不可解析内容；代码硬校验已通过，降级放行",
            "round": 2, "timestamp": "2026-09-19T00:00:00Z"}],
    },
    "report_markdown": "# 闸降级验证报告",
}, ensure_ascii=False), encoding="utf-8")
(_tmp / f"{_tid}_input.json").write_text(
    _json.dumps({"account_id": TENANT}), encoding="utf-8")

_orig_sdir = _api_mod._task_state_dir
_api_mod._task_state_dir = lambda: _tmp
try:
    resp = _api_mod._load_task_from_file(_tid)
    chk("I1 降级任务的 output.json 能被恢复（修复前返回 None → 表现为任务'不存在'）",
        resp is not None)
    if resp is not None:
        _gr0 = resp.routing_state.gate_review_history[0]
        chk("I2 恢复出的闸记录带 degraded_unavailable",
            _gr0.review_status == "degraded_unavailable", _gr0.review_status)
        chk("I3 恢复出的分数为 None（不臆造）", _gr0.eval_score is None, _gr0.eval_score)
        chk("I4 报告正文保真", "闸降级验证报告" in (resp.report_markdown or ""))
        chk("I5 审计事件保真", len(resp.routing_state.engine_events) == 1,
            len(resp.routing_state.engine_events))
finally:
    _api_mod._task_state_dir = _orig_sdir

# ---------------------------------------------------------------------------
print("=" * 70)
for s in ok:
    print("  ✅", s)
for s in bad:
    print("  ❌", s)
print("=" * 70)
print(f"PASS {len(ok)} / FAIL {len(bad)}")
sys.exit(1 if bad else 0)
