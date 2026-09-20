"""验证：对话可中断（真停 + 不落库）与编辑截断。

对照 DESIGN_chat_interrupt_edit.md §4 验收表。**只报事实，不做美化**：
每步打印真实 HTTP 码 / 真实 DB 计数 / 真实耗时。

修复前的实测基线（scripts/probe_abort_persistence.py）：
客户端 2s 断开 → 服务端照跑到 35s 并**把 user+assistant 都写进库**。
所以 A1/A2 的判据是：断开后 DB **永远**不出现这一轮的两行。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from sqlalchemy import select, func  # noqa: E402

from server.auth import create_access_token  # noqa: E402
from server.database import get_sync_session  # noqa: E402
from server.models import Account, ChatMessage, ChatSession  # noqa: E402

API = "http://127.0.0.1:8000/api/v1"
NGINX_HOST = os.getenv("NGINX_HOST", "web")  # compose 服务名；测「浏览器真实链路」用
LONG_PROMPT = "请检索并详细比较2026年中国外卖市场的主要平台（美团、淘宝闪购/饿了么、京东），列出来源。"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'  PASS' if ok else '  FAIL'} | {name}" + (f"  —— {detail}" if detail else ""))


def call(path: str, token: str, method: str = "GET", body=None, timeout: int = 600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, method=method, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:300]}


def tokens() -> dict[str, str]:
    with get_sync_session() as s:
        return {a.username: create_access_token(a)
                for a in s.execute(select(Account)).scalars().all()}


def msg_count(sid: str) -> int:
    with get_sync_session() as s:
        return s.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == sid)
        ).scalar() or 0


def make_session(tok: str, topic: str = "verify-interrupt") -> str:
    st, r = call("/chat/sessions", tok, "POST", {"topic": topic, "model": "auto-chat"})
    return r["id"]


def cleanup(sid: str) -> None:
    try:
        with get_sync_session() as s:
            obj = s.get(ChatSession, sid)
            if obj is not None:
                s.delete(obj)
                s.commit()
    except Exception as e:  # noqa: BLE001
        print(f"    清理 {sid} 失败: {e}")


def abort_midflight(tok: str, sid: str, prompt: str, after: float = 3.0,
                    host: str = "127.0.0.1", port: int = 8000,
                    path: str = "/api/v1/chat", label: str = "") -> None:
    """裸 socket 发 /chat，after 秒后 RST —— 等价于浏览器 AbortController.abort()。

    host/port/path 可切换「直连 api:8000」与「经 nginx（web:80）」两条链路：
    浏览器走的是后者，只测前者会漏掉反代是否吞掉断连。
    """
    body = json.dumps({"message": prompt, "history": [], "session_id": sid}).encode()
    raw = (
        f"POST {path} HTTP/1.1\r\n".encode()
        + f"Host: {host}:{port}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + f"Authorization: Bearer {tok}\r\n".encode()
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    sk = socket.create_connection((host, port), timeout=20)
    sk.sendall(raw)
    time.sleep(after)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    sk.close()
    if label:
        print(f"  [{label}] 已硬断开（RST）→ {host}:{port}{path}")


def wait_no_write(sid: str, t0: float, window: float, label: str) -> int:
    """在 window 秒内盯住 DB：一旦出现消息即为泄漏（基线在 78s 写入）。"""
    deadline = time.time() + window
    while time.time() < deadline:
        time.sleep(5)
        n = msg_count(sid)
        if n:
            print(f"  [{time.time()-t0:6.1f}s] ⚠️ DB 出现 {n} 条（服务端没有丢弃本轮）")
            return n
    n = msg_count(sid)
    print(f"  [{time.time()-t0:6.1f}s] 等满 {window:.0f}s，最终消息数 = {n}")
    return n


def main() -> int:
    # ============ A0：中间件体检（防「停止」再次被静默废掉）============
    # BaseHTTPMiddleware 会在 app 外再包一层 receive/send，吞掉 http.disconnect，
    # 使 is_disconnected() 恒为 False —— 不报错、不告警，只让「停止」变成假按钮。
    # 这条断言把「别再顺手加回去」钉死在测试里（详见 DESIGN §6 与 probe_disconnect_middleware.py）。
    print("[A0] 中间件体检：不得存在 BaseHTTPMiddleware（它会吞掉断连信号）")
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from server.main import app as _app

        offenders = [
            f"{m.cls.__module__}.{m.cls.__name__}"
            for m in _app.user_middleware
            if isinstance(m.cls, type) and issubclass(m.cls, BaseHTTPMiddleware)
        ]
        print(f"  已注册中间件 {len(_app.user_middleware)} 个，其中 BaseHTTPMiddleware 派生类 {len(offenders)} 个")
        check("A0 无 BaseHTTPMiddleware（否则 is_disconnected 失效 → 停止是假按钮）",
              not offenders, ", ".join(offenders) or "clean")
    except Exception as e:  # noqa: BLE001
        check("A0 无 BaseHTTPMiddleware（否则 is_disconnected 失效 → 停止是假按钮）", False, str(e))

    toks = tokens()
    if "wendy" not in toks:
        print("FAIL 找不到主账号 wendy")
        return 1
    main_tok = toks["wendy"]
    print(f"账号: {list(toks)}")
    print("=" * 72)

    # ============ A1/A2：中途停止 → 服务端丢弃本轮，且整轮不落库 ============
    print("[A1/A2] 中途断开（模拟点「停止」）—— 直连 api:8000")
    sid = make_session(main_tok, "verify-interrupt-abort")
    print(f"  探针会话 {sid}，初始消息数 {msg_count(sid)}")
    t0 = time.time()
    abort_midflight(main_tok, sid, LONG_PROMPT, after=3.0, label=f"{time.time()-t0:.1f}s")

    # 自然完成约 30~80s；等满 100s 仍为 0 才能证明「不是跑完才没写」，而是真被丢弃
    n_final = wait_no_write(sid, t0, 100, "A1/A2")
    check("A1/A2 断开后整轮零落库（等满 100s）", n_final == 0,
          f"{n_final} 条（0 = 丢弃成功；非 0 = 与修复前一样照跑照写）")
    cleanup(sid)

    # ============ B1：经 nginx（浏览器真实链路）============
    # 浏览器不走 8000，走 web:80 → nginx → api:8000。只测直连会漏掉「反代是否吞掉断连」。
    print()
    print(f"[B1] 中途断开 —— 经 nginx（{NGINX_HOST}:80，浏览器走的就是这条）")
    sid_b = make_session(main_tok, "verify-interrupt-nginx")
    t0b = time.time()
    try:
        abort_midflight(main_tok, sid_b, LONG_PROMPT, after=3.0,
                        host=NGINX_HOST, port=80, label=f"{time.time()-t0b:.1f}s")
        nb = wait_no_write(sid_b, t0b, 100, "B1")
        check("B1 经反代断开同样整轮零落库", nb == 0,
              f"{nb} 条" if nb else "0 条")
    except OSError as e:
        check("B1 经反代断开同样整轮零落库", False, f"反代不可达：{e}")
    cleanup(sid_b)

    # ============ A3：正常一轮仍然落库（不能为了能停把正常路径弄坏）============
    # 必须用**全新会话**：A1/A2 与 B1 的探针会话上面已 cleanup 掉，
    # 复用会得到 404「会话不存在」，把后续 A4/A5 一起带成级联假失败（上一版就踩了）。
    print()
    print("[A3] 正常一轮（不中断）仍须落库 + 回传消息 id")
    sid3 = make_session(main_tok, "verify-interrupt-normal")
    before = msg_count(sid3)
    st, resp = call("/chat", main_tok, "POST", {"message": "一句话回答：1+1等于几？", "history": [], "session_id": sid3})
    after = msg_count(sid3)
    print(f"  HTTP {st} intent={resp.get('intent')} reply={str(resp.get('reply'))[:40]!r}")
    print(f"  user_message_id={resp.get('user_message_id')}")
    print(f"  assistant_message_id={resp.get('assistant_message_id')}")
    print(f"  消息数 {before} → {after}")
    check("A3 正常一轮照常落库", st == 200 and after == before + 2,
          f"{before} → {after}")
    check("A3 响应回传落库消息 id（编辑重发要用）",
          bool(resp.get("user_message_id")) and bool(resp.get("assistant_message_id")),
          f"user={str(resp.get('user_message_id'))[:8]} asst={str(resp.get('assistant_message_id'))[:8]}")

    uid = resp.get("user_message_id")

    # ============ A4：截断（编辑重发的前置动作）============
    print()
    print("[A4] 截断：删掉该条及其之后")
    # 注意：断言必须基于**截断前的真实条数**，不能写死 0。
    # 上一版写成 left == 0，在 A1/A2 泄漏了 2 条时会误报 FAIL（级联假失败），
    # 掩盖掉「截断本身其实是对的」——测试自身的 bug 也算 bug。
    before_cut = msg_count(sid3)
    st, r = call(f"/chat/sessions/{sid3}/truncate", main_tok, "POST", {"from_message_id": uid})
    left = msg_count(sid3)
    print(f"  截断前 {before_cut} 条 → HTTP {st} {r} → 剩余 {left} 条")
    check("A4 截断删除该条及其之后（deleted 与实际减少量一致）",
          st == 200 and r.get("deleted") == 2 and left == before_cut - 2,
          f"deleted={r.get('deleted')} {before_cut} → {left}")

    st2, r2 = call(f"/chat/sessions/{sid3}/truncate", main_tok, "POST", {"from_message_id": uid})
    print(f"  幂等复跑 HTTP {st2} {str(r2)[:100]}")
    check("A4 幂等：重复截断不误删（该消息已不存在 → 404 fail loud）", st2 == 404,
          f"HTTP {st2}")
    cleanup(sid3)

    # ============ A5：越权截断 ============
    # 关键：必须拿**仍然存在**的消息去试，否则 404 可能只是因为消息已不存在，
    # 证明不了「挡的是越权」——那样的 PASS 是假的。
    print()
    print("[A5] 越权：用 wendy1 的 token 截断 wendy 会话里**仍在**的消息")
    sid5 = make_session(main_tok, "verify-interrupt-authz")
    stx, respx = call("/chat", main_tok, "POST",
                      {"message": "只回一个字：好", "history": [], "session_id": sid5})
    uid5 = respx.get("user_message_id")
    cnt_before_authz = msg_count(sid5)
    print(f"  wendy 会话 {sid5} 现有 {cnt_before_authz} 条，目标消息 {str(uid5)[:8]}")
    if "wendy1" in toks and uid5:
        st3, r3 = call(f"/chat/sessions/{sid5}/truncate", toks["wendy1"], "POST",
                       {"from_message_id": uid5})
        still = msg_count(sid5)
        check("A5 越权截断被拒且未删任何东西", st3 == 404 and still == cnt_before_authz,
              f"HTTP {st3} deleted={r3.get('deleted')} {cnt_before_authz} → {still}")
    else:
        check("A5 越权截断被拒", False, f"前置不满足：wendy1={('wendy1' in toks)} uid={bool(uid5)}")
    cleanup(sid5)

    # ============ A6：无归属会话必须 fail closed ============
    # 回归护栏：`/chat` 的归属校验曾写成宽松式（owner_id 为 NULL 即放行），
    # 而库里真有 owner_id IS NULL 的存量会话 → 任何账号都能继续它 = 跨租户泄漏历史。
    # 这里人为造一个无归属会话来钉死这条：必须 404。
    print()
    print("[A6] 无归属会话（owner_id IS NULL）：继续对话必须 404（fail closed）")
    sid6 = make_session(main_tok, "verify-orphan-session")
    with get_sync_session() as s:
        obj = s.get(ChatSession, sid6)
        obj.owner_id = None
        s.commit()
    st6, r6 = call("/chat", main_tok, "POST",
                   {"message": "这条不该被受理", "history": [], "session_id": sid6})
    print(f"  人为置空 owner_id 后继续对话 → HTTP {st6} {str(r6)[:80]}")
    check("A6 owner_id 为空时继续对话被拒（不把他人/存量历史喂给模型）", st6 == 404, f"HTTP {st6}")
    cleanup(sid6)

    print()
    print("=" * 72)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"结果：{passed}/{len(results)} PASS")
    for name, ok, detail in results:
        if not ok:
            print(f"  ❌ {name} —— {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
