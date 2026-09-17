# tests/test_m11_mcp_fc.py · M11-3 D1-B function-calling 消费桥单测
#
# 仅用 StubLLMClient / StubLLMClientFC，无需联网 / new-api 密钥 / 真实 MCP server。
# 覆盖：
#   - dispatch_tool 路由（web_search / data_proc / mcp:{s}:{t} / 未知 / mcp 未初始化）
#   - _build_tool_schemas（web_search 恒有；data_proc 仅 Analyst；mcp 工具注入）
#   - _run_fc_loop（researcher 多轮：首轮 tool_call → 次轮出 JSON）
#   - 向后兼容：旧 StubLLMClient（fc=False）带 tools 也不进循环，直接出 content
#   - 诚实边界：MCP server 异常 / 不可达 → {ok:False} 不击穿；循环仍正常收口
#   - runaway 护栏：LLM 持续发 tool_call → 至多 MCP_MAX_ROUNDS 轮

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (  # noqa: E402
    StubLLMClient, StubLLMClientFC,
    dispatch_tool, _build_tool_schemas, _run_fc_loop, MCP_MAX_ROUNDS,
)
from tools import ToolBundle  # noqa: E402


# ---------------- fakes ----------------

class FakeWebSearch:
    def __init__(self):
        self.calls = 0

    def search_many(self, queries, agent=None, route_by_intent=False):
        self.calls += 1
        entries = [{
            "id": "r1", "url": "https://real.example/a", "title": "A",
            "snippet": "s", "credibility": "high", "source": "tavily",
        }]
        # M12：status 是与检索记录完全不同的形态（{agent,tool,source,ok,error}）。
        # 此前写成 `return entries, entries`，因无人检查 status 而侥幸通过；
        # dispatch_tool 修复后 status 会参与诚实降级，必须归位为真实形态。
        status = [{"agent": agent, "tool": "web_search",
                   "source": "tavily", "ok": True}]
        return entries, status


class FakeDataProc:
    def run_requests(self, requests, agent=None):
        entries = [{"agent": agent, "tool": "data_proc", "ok": True}]
        return [{"expr": r.get("expr"), "value": 42} for r in requests], entries


class FakeMCP:
    def __init__(self, fail=False):
        self.fail = fail

    def list_tools(self):
        return [{
            "server": "demo", "name": "ping",
            "description": "ping demo", "input_schema": {"type": "object", "properties": {}},
        }]

    def call_tool(self, server, tool, args):
        if self.fail:
            raise RuntimeError("connection refused")
        return {"ok": True, "result": "pong", "source": f"mcp:{server}:{tool}"}


def _bundle(web=None, dp=None, mcp=None):
    return ToolBundle(
        web_search=web or FakeWebSearch(),
        data_proc=dp,  # None 表示未装配
        doc_export=None,
        mcp=mcp,
    )


# ---------------- dispatch_tool ----------------

def test_dispatch_web_search():
    b = _bundle()
    r = dispatch_tool("web_search", {"queries": ["x"]}, b, None, "Researcher")
    assert r["ok"] is True and r["source"] == "web_search"
    assert r["result"]


def test_dispatch_data_proc():
    b = _bundle(dp=FakeDataProc())
    r = dispatch_tool("data_proc", {"requests": [{"expr": "1+1"}]}, b, None, "Analyst")
    assert r["ok"] is True and r["source"] == "data_proc"
    assert r["result"]["calc"]


def test_dispatch_mcp_ok():
    b = _bundle(mcp=FakeMCP())
    r = dispatch_tool("mcp:demo:ping", {"arg1": "demo"}, b, b.mcp, "Researcher")
    assert r["ok"] is True and r["result"] == "pong"


def test_dispatch_mcp_uninitialized():
    b = _bundle()  # mcp=None
    r = dispatch_tool("mcp:demo:ping", {}, b, None, "Researcher")
    assert r["ok"] is False and "未初始化" in r["error"]


def test_dispatch_mcp_failure_degrades():
    b = _bundle(mcp=FakeMCP(fail=True))
    r = dispatch_tool("mcp:demo:ping", {}, b, b.mcp, "Researcher")
    assert r["ok"] is False and "connection refused" in r["error"]


def test_dispatch_unknown_tool():
    b = _bundle()
    r = dispatch_tool("mystery", {}, b, None, "Researcher")
    assert r["ok"] is False and "未知工具" in r["error"]


# ---------------- _build_tool_schemas ----------------

def test_schemas_researcher_minimal():
    schemas = _build_tool_schemas(_bundle(), None, "Researcher")
    names = [s["function"]["name"] for s in schemas]
    assert names == ["web_search"]


def test_schemas_analyst_has_dataproc():
    schemas = _build_tool_schemas(_bundle(dp=FakeDataProc()), None, "Analyst")
    names = [s["function"]["name"] for s in schemas]
    assert names == ["web_search", "data_proc"]


def test_schemas_injects_mcp():
    schemas = _build_tool_schemas(_bundle(mcp=FakeMCP()), FakeMCP(), "Researcher")
    names = [s["function"]["name"] for s in schemas]
    assert "mcp:demo:ping" in names


# ---------------- _run_fc_loop ----------------

def test_fc_loop_researcher_two_rounds():
    """StubLLMClientFC：首轮发 web_search tool_call，次轮（已回注结果）出 JSON。"""
    ws = FakeWebSearch()
    b = _bundle(web=ws)
    llm = StubLLMClientFC(scenario="pass")
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, None, "Researcher"),
        b, None, "Researcher", "researcher")
    assert isinstance(raw, str) and raw
    assert len(entries) == 1 and entries[0]["tool"] == "web_search" and entries[0]["ok"]
    assert extra  # web_search 结果被并入 extra_search


def test_fc_loop_analyst_dataproc():
    ws = FakeWebSearch()
    b = _bundle(web=ws, dp=FakeDataProc())
    llm = StubLLMClientFC(scenario="pass")
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, None, "Analyst"),
        b, None, "Analyst", "analyst")
    assert any(e["tool"] == "data_proc" and e["ok"] for e in entries)


def test_fc_loop_mcp_tool_called():
    b = _bundle(mcp=FakeMCP())
    llm = StubLLMClientFC(scenario="pass")
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, b.mcp, "Researcher"),
        b, b.mcp, "Researcher", "researcher")
    assert any(e["tool"] == "mcp:demo:ping" and e["ok"] for e in entries)


def test_fc_loop_mcp_failure_still_closes():
    """MCP 异常 → {ok:False} 记录 → 循环第二轮收口（诚实边界，不击穿）。"""
    b = _bundle(mcp=FakeMCP(fail=True))
    llm = StubLLMClientFC(scenario="pass")
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, b.mcp, "Researcher"),
        b, b.mcp, "Researcher", "researcher")
    assert any(e["tool"] == "mcp:demo:ping" and not e["ok"] for e in entries)
    assert isinstance(raw, str) and raw  # 第二轮仍产出最终 content


def test_backward_compat_old_stub_no_loop():
    """旧 StubLLMClient（fc=False）即便带 tools 也直接出 content，不进工具循环。"""
    ws = FakeWebSearch()
    b = _bundle(web=ws)
    llm = StubLLMClient(scenario="pass")  # fc 默认 False
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, None, "Researcher"),
        b, None, "Researcher", "researcher")
    assert not entries  # 无工具循环，无 tool_entries
    assert not extra
    assert isinstance(raw, str) and raw


def test_fc_loop_runaway_cap():
    """LLM 持续发 tool_call 不收口 → 至多 MCP_MAX_ROUNDS 轮（防 runaway）。"""

    class AlwaysFC(StubLLMClient):
        def __init__(self):
            super().__init__("pass")
            self.fc = True

        def complete_with_tools(self, model, system, user, tools, **kw):
            # 无视【工具执行结果】，永远发 tool_call
            return {
                "content": "",
                "tool_calls": [{
                    "id": "c", "type": "function",
                    "function": {"name": "web_search",
                                 "arguments": json.dumps({"queries": ["x"]})},
                }],
            }

    ws = FakeWebSearch()
    b = _bundle(web=ws)
    llm = AlwaysFC()
    raw, entries, extra = _run_fc_loop(
        llm, "auto", "sys", "user", _build_tool_schemas(b, None, "Researcher"),
        b, None, "Researcher", "researcher")
    assert ws.calls == MCP_MAX_ROUNDS
    assert len(entries) == MCP_MAX_ROUNDS


# ---------------- M12 回归 ----------------

def test_dispatch_web_search_returns_records_not_status():
    """M12 回归：dispatch_tool 必须把「检索记录」而非 status 作为 result 返回。

    原实现写成 `_, entries = search_many(...)`，真实记录被丢弃，模型在
    function-calling 路径（ChatAgent / Researcher / Analyst）里只能看到
    {agent,tool,source,ok,error} 空壳，从而误答「检索返回空结果」。
    """
    b = _bundle()
    r = dispatch_tool("web_search", {"queries": ["x"]}, b, None, "Researcher")
    assert r["ok"] is True
    assert r["result"][0]["url"] == "https://real.example/a"
    assert r["result"][0]["title"] == "A"
    # status 另存，且形态为 ok/error 记录而非检索记录
    assert r["status"] and r["status"][0]["ok"] is True
    assert "url" not in r["status"][0]


def test_dispatch_web_search_accepts_bare_string_queries():
    """模型常把 queries 数组写成裸字符串；按字符迭代会产生一堆单字 query。"""
    ws = FakeWebSearch()
    b = ToolBundle(web_search=ws, data_proc=None, doc_export=None, mcp=None)
    r = dispatch_tool("web_search", {"queries": "厦门天气"}, b, None, "ChatAgent")
    assert r["ok"] is True
