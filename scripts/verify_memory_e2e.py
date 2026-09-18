# scripts/verify_memory_e2e.py · 跨会话记忆端到端验证
"""证明「会话1 写入 → 会话2（空 history）召回 → 注入模型 system prompt」全链路。

用法（容器内）：docker exec -w /app report-api python scripts/verify_memory_e2e.py
判定：末行打印 RESULT: PASS / FAIL，退出码 0 / 1。

背景：嵌入端点欠费（402）时语义召回不可用；本验证证明关键词兜底仍能让记忆跨会话生效。
"""
import asyncio
import json
import sys
import uuid

sys.path.insert(0, "/app")

from chat_agent import ChatAgent  # noqa: E402
from tools.kb_store import kb_write  # noqa: E402


class _FakeWebSearch:
    def search_many(self, queries, agent=None, route_by_intent=False):
        return [], [{"agent": agent, "tool": "web_search", "ok": True}]


class _FakeBundle:
    def __init__(self):
        self.web_search = _FakeWebSearch()
        self.data_proc = None
        self.mcp = None


class _SpyLLM:
    """记录收到的 system prompt，直接返回终态（不调工具）。"""

    def __init__(self):
        self.systems: list[str] = []

    def complete_with_tools(self, model, system, user, tools=None, role=None, shape=None, kind=None):
        self.systems.append(system)
        return {"content": "好的，我已参考你之前的相关记录。", "tool_calls": []}


def main():
    marker = "E2E-MEM-" + uuid.uuid4().hex[:8]
    ok = True

    # ---- 会话 1：写入一条带唯一标记的「事实」 ----
    n = asyncio.run(kb_write([{
        "kind": "chat_fact",
        "topic": "跨会话记忆E2E",
        "title": "跨境电商关税政策结论",
        "content": f"用户此前问过跨境电商关税政策，结论：东南亚关税下调 3 个百分点。{marker}",
        "source": "verify_memory_e2e",
        "metadata": {"auto": True},
    }]))
    print(f"[session1] kb_write -> {n} (marker={marker})", flush=True)
    if n < 1:
        print("RESULT: FAIL (写入未成功)", flush=True)
        return 1

    # ---- 会话 2：全新会话（history 为空），用不同措辞提问 ----
    llm = _SpyLLM()
    agent = ChatAgent(llm=llm, bundle=_FakeBundle())
    out = agent.step(history=[], user_msg="跨境电商的关税政策是怎么说的？", model="auto-chat")
    system = llm.systems[-1] if llm.systems else ""

    injected = marker in system
    has_header = "你之前积累的相关知识" in system
    print(f"[session2] intent={out.get('intent')} system_len={len(system)}", flush=True)
    print(f"[session2] 记忆注入标题出现: {has_header}", flush=True)
    print(f"[session2] 会话1标记出现在 system prompt: {injected}", flush=True)
    ok = ok and injected and has_header

    # ---- 负对照：无关问题不应注入该记忆 ----
    llm2 = _SpyLLM()
    agent2 = ChatAgent(llm=llm2, bundle=_FakeBundle())
    agent2.step(history=[], user_msg="zzzq 完全无关的问题 xyzq", model="auto-chat")
    neg_injected = marker in (llm2.systems[-1] if llm2.systems else "")
    print(f"[negctrl] 无关问题是否误注入: {neg_injected} (期望 False)", flush=True)
    if neg_injected:
        ok = False

    print("RESULT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
