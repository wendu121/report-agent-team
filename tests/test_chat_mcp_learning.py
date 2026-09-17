# tests/test_chat_mcp_learning.py · ChatAgent 阶段 2（自主调工具 + 学习 + 经验）单测
#
# 用 FakeLLM + FakeBundle 验证：
# 1) ChatAgent 能走 _run_fc_loop 自主调 web_search（tool_calls 含 web_search）
# 2) 普通回答不被误判为 generate_report（_classify 不丢答案）
# 3) 「记住：…」存为经验草稿（propose_lesson 被调用）
# 4) generate_report JSON 协议仍生效
import json

import pytest

from chat_agent import ChatAgent


class FakeWebSearch:
    def search_many(self, queries, agent=None, route_by_intent=False):
        # 真实签名：search_many -> (results, status)，results 才是检索记录。
        # 【M12 修正】此 fake 原写成 ([], [记录])——把记录放在 status 位，是照着
        # orchestrator.dispatch_tool 丢弃真实结果的 bug（写死 `_, entries =`）写出来的。
        # bug 已修（dispatch 返回 results 作 result、status 另存），fake 同步归位，
        # 否则这条用例实际在反向锁定 bug。
        return [{
            "id": "r1", "url": "http://example.com", "title": "测试来源",
            "snippet": "摘要", "source": "tavily",
        }], [{"agent": agent, "tool": "web_search", "source": "tavily", "ok": True}]


class FakeBundle:
    def __init__(self):
        self.web_search = FakeWebSearch()
        self.data_proc = None  # 跳过 data_proc 工具
        self.mcp = None


class FakeLLM:
    """第 1 次返回 tool_call，第 2 次返回终态文本。"""

    def __init__(self, final_text, first_tool="web_search"):
        self._n = 0
        self.final_text = final_text
        self.first_tool = first_tool

    def complete_with_tools(self, model, system, user, tools=None, role=None, shape=None, kind=None):
        self._n += 1
        if self._n == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "function": {"name": self.first_tool,
                                 "arguments": json.dumps({"queries": ["测试问题"]})},
                }],
            }
        return {"content": self.final_text, "tool_calls": []}


def test_chat_agent_calls_web_search():
    llm = FakeLLM(final_text="根据检索结果，X 的情况是这样的。")
    agent = ChatAgent(llm=llm, bundle=FakeBundle())
    out = agent.step(history=[], user_msg="X 的情况是什么？", model="auto-chat")
    assert out["intent"] == "chat"
    assert out["reply"] == "根据检索结果，X 的情况是这样的。"
    assert "web_search" in out["tool_calls"]
    assert len(out["sources"]) == 1  # 检索记录被带回


def test_chat_agent_no_false_generate_report():
    # 答案本身含大括号也不应被误判为研报
    llm = FakeLLM(final_text="结论：{key: value} 表示状态正常。")
    agent = ChatAgent(llm=llm, bundle=FakeBundle())
    out = agent.step(history=[], user_msg="状态代表什么？", model="auto-chat")
    assert out["intent"] == "chat"
    assert "状态正常" in out["reply"]


def test_chat_remember_trigger_proposes_lesson(monkeypatch):
    captured = {}

    def fake_propose(text):
        captured["text"] = text
        return True

    monkeypatch.setattr("tools.chat_learning.propose_lesson", fake_propose)
    agent = ChatAgent(llm=FakeLLM(final_text="x"), bundle=FakeBundle())
    out = agent.step(history=[], user_msg="记住：回答技术架构问题优先用 deepwiki", model="auto-chat")
    assert out["intent"] == "chat"
    assert "经验草稿" in out["reply"]
    assert captured["text"] == "回答技术架构问题优先用 deepwiki"


def test_chat_generate_report_intent():
    llm = FakeLLM(final_text=json.dumps({
        "intent": "generate_report",
        "report": {"topic": "锂电池行业研究报告", "scope": ["行业概况"]},
    }))
    agent = ChatAgent(llm=llm, bundle=FakeBundle())
    out = agent.step(history=[], user_msg="帮我生成一份关于锂电池行业的报告", model="auto-chat")
    assert out["intent"] == "generate_report"
    assert out["report"]["topic"] == "锂电池行业研究报告"


def test_chat_mcp_tool_in_schema(monkeypatch):
    # mcp 客户端返回工具 → schema 含 mcp: 前缀工具，fc-loop 能调
    class FakeMCP:
        def list_tools(self):
            return [{"server": "deepwiki", "name": "ask_question",
                     "description": "问 deepwiki", "input_schema": {}}]

        def call_tool(self, server, name, args):
            return {"ok": True, "result": "deepwiki 答案", "source": f"mcp:{server}:{name}"}

    bundle = FakeBundle()
    bundle.mcp = FakeMCP()
    llm = FakeLLM(final_text="deepwiki 说该仓库用 LangGraph。", first_tool="mcp:deepwiki:ask_question")
    agent = ChatAgent(llm=llm, bundle=bundle)
    out = agent.step(history=[], user_msg="用 deepwiki 查 langgraph 架构", model="auto-chat")
    assert "mcp:deepwiki:ask_question" in out["tool_calls"]
    assert out["intent"] == "chat"


def test_chat_mcp_only_answer_has_sources():
    # R1 修复：MCP-only 回答的来源须进入 sources（此前 extra_search 只收 web_search）
    class FakeMCP:
        def list_tools(self):
            return [{"server": "deepwiki", "name": "ask_question",
                     "description": "问 deepwiki", "input_schema": {}}]

        def call_tool(self, server, name, args):
            return {"ok": True, "result": "deepwiki 答案", "source": f"mcp:{server}:{name}"}

    bundle = FakeBundle()
    bundle.mcp = FakeMCP()
    llm = FakeLLM(final_text="deepwiki 说该仓库用 LangGraph。", first_tool="mcp:deepwiki:ask_question")
    agent = ChatAgent(llm=llm, bundle=bundle)
    out = agent.step(history=[], user_msg="用 deepwiki 查 langgraph 架构", model="auto-chat")
    assert out["intent"] == "chat"
    assert len(out["sources"]) == 1
    src = out["sources"][0]
    assert src["tool"] == "mcp:deepwiki:ask_question"
    assert "deepwiki 答案" in (src.get("content") or "")


def test_chat_mcp_only_answer_is_learned(monkeypatch):
    # R1 修复：MCP-only 回答须触发知识沉淀（门槛从 extra_search 改为「任一 tool ok」）
    captured = {}

    async def fake_learn(user_msg, reply, tool_entries, extra_search):
        captured["called"] = True
        captured["tool_names"] = [t.get("tool") for t in tool_entries if isinstance(t, dict)]
        return 1

    monkeypatch.setattr("tools.chat_learning.chat_learn_write", fake_learn)

    class FakeMCP:
        def list_tools(self):
            return [{"server": "deepwiki", "name": "ask_question",
                     "description": "问 deepwiki", "input_schema": {}}]

        def call_tool(self, server, name, args):
            return {"ok": True, "result": "deepwiki 答案", "source": f"mcp:{server}:{name}"}

    bundle = FakeBundle()
    bundle.mcp = FakeMCP()
    llm = FakeLLM(final_text="deepwiki 说该仓库用 LangGraph。", first_tool="mcp:deepwiki:ask_question")
    agent = ChatAgent(llm=llm, bundle=bundle)
    out = agent.step(history=[], user_msg="用 deepwiki 查 langgraph 架构", model="auto-chat")
    assert out["intent"] == "chat"
    assert captured.get("called") is True
    assert "mcp:deepwiki:ask_question" in captured["tool_names"]


def test_chat_agent_resolves_auto_chat_to_tool_model(monkeypatch):
    # auto-chat 等 universal alias 可能不支持 tools / 配额耗尽，应 fallback 到 chat.tool_model
    captured = {}

    class SpyLLM(FakeLLM):
        def complete_with_tools(self, model, system, user, tools=None, role=None, shape=None, kind=None):
            captured["model"] = model
            return super().complete_with_tools(model, system, user, tools, role, shape, kind)

    monkeypatch.setattr("chat_agent._load_chat_tool_model", lambda: "LongCat-2.0")
    agent = ChatAgent(llm=SpyLLM(final_text="根据检索，答案是 42。"), bundle=FakeBundle())
    out = agent.step(history=[], user_msg="天空为什么是蓝的", model="auto-chat")
    assert out["intent"] == "chat"
    assert captured.get("model") == "LongCat-2.0"


def test_chat_agent_respects_explicit_model(monkeypatch):
    # 用户显式选具体模型时，应尊重选择（不强制 fallback）
    captured = {}

    class SpyLLM(FakeLLM):
        def complete_with_tools(self, model, system, user, tools=None, role=None, shape=None, kind=None):
            captured["model"] = model
            return super().complete_with_tools(model, system, user, tools, role, shape, kind)

    monkeypatch.setattr("chat_agent._load_chat_tool_model", lambda: "LongCat-2.0")
    agent = ChatAgent(llm=SpyLLM(final_text="根据检索，答案是 42。"), bundle=FakeBundle())
    out = agent.step(history=[], user_msg="天空为什么是蓝的", model="Intern-S2-Preview-397B")
    assert out["intent"] == "chat"
    assert captured.get("model") == "Intern-S2-Preview-397B"
