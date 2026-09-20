#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DESIGN_source_honesty §5 验收（接口/对话层）：占位数据不得冒充「来源」。

背景：2026-09-19 子账号跑一次对话，前端显示「来源（8）」，8 条全是
`[MOCK] Tavily 搜索…` 占位；同一次检索的真实结果（66 条）被挤出展示位。
本脚本把「占位不是来源」「检索面不完整必须显式告警」钉成断言。

运行（容器内）：
  docker compose -f docker-compose.yml exec -T -e PYTHONPATH=/app \\
      api python -u /app/scripts/verify_source_honesty_api.py

A1-A4 为确定性离线用例；A5 为真·HTTP 端到端（依赖 LLM 网关，可能因限流 SKIP）。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

API = "http://127.0.0.1:8000/api/v1"

_results: list[tuple[str, str, str]] = []   # (tag, PASS|FAIL|SKIP, detail)


def check(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}" + (f"  —— {detail}" if detail else ""))


def skip(tag: str, detail: str) -> None:
    _results.append((tag, "SKIP", detail))
    print(f"[SKIP] {tag}  —— {detail}")


class _FakeWS:
    """最小的 web_search 替身：只暴露 degraded，供 _source_warnings 读取。"""
    def __init__(self, degraded):
        self.degraded = degraded


class _FakeBundle:
    def __init__(self, degraded):
        self.web_search = _FakeWS(degraded)


def main() -> int:
    from chat_agent import _build_sources, _source_warnings

    # ---- A1：_build_sources 丢弃占位记录，保留真实记录 ----
    entries = [{"agent": "ChatAgent", "tool": "web_search", "ok": True, "result": [
        {"source": "tavily", "url": "https://mock.local/x/1", "title": "[MOCK] Tavily 搜索：x · 结果 1",
         "snippet": "[MOCK] 占位内容", "is_mock": True},
        {"source": "wikipedia", "url": "https://zh.wikipedia.org/y", "title": "真实条目",
         "snippet": "真实摘要"},
    ]}]
    srcs = _build_sources(entries)
    check("A1 占位记录不进 sources、真实记录保留",
          len(srcs) == 1 and srcs[0]["source"] == "wikipedia",
          f"sources={[(s.get('source'), s.get('title')) for s in srcs]}")

    # ---- A2：纯占位 → sources 为空（前端不会渲染「来源（N）」） ----
    only_mock = [{"agent": "ChatAgent", "tool": "web_search", "ok": True, "result": [
        {"source": "tavily", "url": f"https://mock.local/{i}", "title": f"[MOCK] … 结果 {i}",
         "is_mock": True} for i in range(5)]}]
    srcs2 = _build_sources(only_mock)
    check("A2 纯占位 → sources 为空", srcs2 == [], f"sources={srcs2}")

    # ---- A3：响应模型暴露两个新字段且默认值正确 ----
    try:
        from server.api import ChatResponse
        m = ChatResponse(reply="x", intent="chat")
        ok3 = (m.source_warnings == [] and m.sources_are_real is True)
        m2 = ChatResponse(reply="x", intent="chat", source_warnings=["w"], sources_are_real=False)
        ok3 = ok3 and m2.source_warnings == ["w"] and m2.sources_are_real is False
        check("A3 ChatResponse 暴露 source_warnings / sources_are_real",
              ok3, f"default={m.source_warnings},{m.sources_are_real} set={m2.source_warnings},{m2.sources_are_real}")
    except Exception as e:  # noqa: BLE001
        check("A3 ChatResponse 暴露 source_warnings / sources_are_real", False,
              f"{type(e).__name__}: {e}")

    # 纯真实（无占位）fixture：A1/A4a/A4d 需要「没被剔除过」的干净结果
    entries_real = [{"agent": "ChatAgent", "tool": "web_search", "ok": True, "result": [
        {"source": "wikipedia", "url": "https://zh.wikipedia.org/y", "title": "真实条目",
         "snippet": "真实摘要"},
    ]}]

    # ---- A4：降级源 + 真发起过检索 → 有告警且 sources_are_real=False ----
    degraded = [{"id": "tavily", "name": "Tavily 搜索", "reason": "missing_key",
                 "fix": "在「设置 → 数据源」中为「Tavily 搜索」填写 API Key"}]
    w4a, ok4a = _source_warnings(_FakeBundle(degraded), entries_real)
    check("A4a 发起过检索 + 有降级源 → 告警非空且 sources_are_real=False",
          bool(w4a) and ok4a is False and any("Tavily 搜索" in w for w in w4a),
          f"warnings={w4a} sources_are_real={ok4a}")
    # 占位被剔除也要报出来
    w4b, ok4b = _source_warnings(_FakeBundle([]), only_mock)
    check("A4b 占位被剔除 → 显式告警且 sources_are_real=False",
          bool(w4b) and ok4b is False and "占位" in w4b[0],
          f"warnings={w4b} sources_are_real={ok4b}")
    # 未发起检索（闲聊）→ 不打扰用户
    w4c, ok4c = _source_warnings(_FakeBundle(degraded), [{"tool": "data_proc", "ok": True, "result": {}}])
    check("A4c 未发起检索时不要弹降级告警（避免噪声）",
          w4c == [] and ok4c is True, f"warnings={w4c} sources_are_real={ok4c}")
    # 一切正常
    w4d, ok4d = _source_warnings(_FakeBundle([]), entries_real)
    check("A4d 无降级、无占位 → 无告警且 sources_are_real=True",
          w4d == [] and ok4d is True, f"warnings={w4d} sources_are_real={ok4d}")

    # ---- A6：知识沉淀不得把占位当来源（chat_learn_write 用 kb_write，需打桩捕获） ----
    tag6 = "A6 占位记录不得被写进知识库当来源"
    try:
        import asyncio
        import tools.kb_store as kb_store
        import tools.chat_learning as cl

        captured = {}

        async def _fake_kb_write(entries):
            captured["entries"] = entries
            return len(entries)

        orig = kb_store.kb_write
        kb_store.kb_write = _fake_kb_write
        try:
            poisoned = [{"agent": "ChatAgent", "tool": "web_search", "ok": True, "result": [
                {"source": "tavily", "url": "https://mock.local/tavily/1", "title": "[MOCK] …",
                 "is_mock": True},
                {"source": "wikipedia", "url": "https://zh.wikipedia.org/z", "title": "真条目"},
            ]}]
            n = asyncio.get_event_loop().run_until_complete(
                cl.chat_learn_write("测试问题", "测试回答", poisoned,
                                    extra_search=[{"source": "tavily",
                                                   "url": "https://mock.local/tavily/9",
                                                   "is_mock": True}]))
        finally:
            kb_store.kb_write = orig
        ent = (captured.get("entries") or [{}])[0]
        srcs = (ent.get("metadata") or {}).get("tool_sources") or []
        leaked = [x for x in srcs if "mock.local" in str(x)]
        check(tag6, n == 1 and not leaked and any("wikipedia" in str(x) for x in srcs),
              f"n={n} tool_sources={srcs} 泄漏占位={leaked}")
    except Exception as e:  # noqa: BLE001
        check(tag6, False, f"{type(e).__name__}: {str(e)[:160]}")

    # ---- A5：真·HTTP 端到端（依赖 LLM 网关；失败则如实 SKIP，绝不伪造） ----
    tag5 = "A5 真机 /chat 响应含新字段且 sources 无占位"
    try:
        from sqlalchemy import select
        from server.auth import create_access_token
        from server.database import get_sync_session
        from server.models import Account
        token = None
        with get_sync_session() as s:
            acc = s.execute(select(Account).where(Account.username == "wendy")).scalars().first()
            if acc is not None:
                token = create_access_token(acc)   # 必须在 session 内铸造：出去即 detached
        if token is None:
            skip(tag5, "库里没有 wendy 账号")
        else:
            body = json.dumps({"message": "1+1等于几？只回数字"}).encode()
            req = urllib.request.Request(API + "/chat", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read().decode())
            has_keys = "source_warnings" in resp and "sources_are_real" in resp
            bad = [s for s in (resp.get("sources") or [])
                   if s.get("is_mock") or str(s.get("title") or "").startswith("[MOCK]")]
            check(tag5, bool(has_keys) and not bad,
                  f"keys={has_keys} sources={len(resp.get('sources') or [])} "
                  f"warnings={resp.get('source_warnings')} are_real={resp.get('sources_are_real')} "
                  f"mock_leak={len(bad)}")
    except urllib.error.HTTPError as e:
        skip(tag5, f"HTTP {e.code}: {e.read().decode()[:160]}")
    except Exception as e:  # noqa: BLE001
        skip(tag5, f"{type(e).__name__}: {str(e)[:160]}")

    total = len(_results)
    npass = sum(1 for _t, st, _d in _results if st == "PASS")
    nfail = sum(1 for _t, st, _d in _results if st == "FAIL")
    nskip = sum(1 for _t, st, _d in _results if st == "SKIP")
    print("\n" + "=" * 62)
    print(f"结果：{npass}/{total} PASS，{nfail} FAIL，{nskip} SKIP")
    print("=" * 62)
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
