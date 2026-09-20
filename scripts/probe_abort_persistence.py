"""探针：客户端中途断开（模拟前端「停止生成」）后，服务端到底发生了什么？

要回答三个问题（决定 UI 的诚实文案与是否需要服务端截断）：
  Q1 断连后服务端的 agent.step 还会跑完吗？
  Q2 跑完的话，user/assistant 两条消息会被写进 DB 吗？
  Q3 若被写进 DB，前端「停止」后 UI 与 DB 是否就不一致了？

做法：建会话 → 发 /chat → 2s 后硬断开 socket → 轮询 DB 90s 看是否落盘。
只读观测，不改任何生产数据（除自己新建的探针会话，结尾删除）。
"""
from __future__ import annotations

import json
import socket
import sys
import time
import uuid
import urllib.request

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from server.auth import create_access_token  # noqa: E402
from server.database import get_sync_session  # noqa: E402
from server.models import Account, ChatMessage, ChatSession  # noqa: E402


def main() -> int:
    with get_sync_session() as s:
        acc = s.execute(
            select(Account).where(Account.is_system_main.is_(True)).limit(1)
        ).scalars().first()
        if acc is None:
            print("FAIL 找不到主账号")
            return 1
        token = create_access_token(acc)
        print(f"主账号: {acc.username} id={acc.id}")

    # 建一个探针会话，拿到确定的 session_id（否则 session 只在末尾才建，无从观测）
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1/chat/sessions", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, data=json.dumps({"model": "auto-chat"}).encode(), timeout=30) as r:
        sess = json.loads(r.read().decode())
    sid = sess["id"]
    print(f"探针会话: {sid}")

    body = json.dumps({
        "message": "请用不少于 800 字详细介绍多智能体研报系统的架构设计，分章节展开。",
        "history": [],
        "session_id": sid,
    }).encode()

    # 用裸 socket 发 HTTP，2s 后 RST 断开 —— 模拟浏览器 AbortController
    host, port = "127.0.0.1", 8000
    raw = (
        b"POST /api/v1/chat HTTP/1.1\r\n"
        b"Host: 127.0.0.1:8000\r\n"
        b"Content-Type: application/json\r\n"
        + f"Authorization: Bearer {token}\r\n".encode()
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    sk = socket.create_connection((host, port), timeout=20)
    sk.sendall(raw)
    t0 = time.time()

    # 等到看到服务端开始处理（读一点响应头或直接等）——2s 足以让 agent 进入 LLM 阶段
    time.sleep(2.0)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")  # RST
    sk.close()
    print(f"[{time.time()-t0:.1f}s] 客户端已硬断开（RST）")

    # 轮询 DB 90s
    seen_user = seen_asst = False
    deadline = time.time() + 90
    last = ""
    while time.time() < deadline:
        time.sleep(3)
        with get_sync_session() as s:
            rows = s.execute(
                select(ChatMessage.session_id, ChatMessage.role, ChatMessage.content)
                .where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.created_at)
            ).all()
        # 必须在 with 内取属性；出了上下文 ORM 对象已 detach。
        # 这里直接 select 列而不是实体，彻底避开 detach 问题。
        state = ", ".join(f"{r.role}({len(r.content)}字)" for r in rows) or "(空)"
        if state != last:
            print(f"[{time.time()-t0:5.1f}s] DB: {state}")
            last = state
        seen_user = any(r.role == "user" for r in rows)
        seen_asst = any(r.role == "assistant" for r in rows)
        if seen_asst:
            break

    print()
    print("=" * 60)
    print(f"Q1 断连后服务端是否跑完：{'是（消息已落盘）' if seen_asst else ('部分（仅 user 落盘）' if seen_user else '否（未落盘）')}")
    print(f"Q2 user 消息落盘：{'是' if seen_user else '否'}")
    print(f"Q3 assistant 消息落盘：{'是' if seen_asst else '否'}")
    print("=" * 60)

    # 清理探针会话
    try:
        with get_sync_session() as s:
            obj = s.get(ChatSession, sid)
            if obj is not None:
                s.delete(obj)
                s.commit()
        print(f"已清理探针会话 {sid}")
    except Exception as e:  # noqa: BLE001
        print(f"清理失败（需手工删）：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
