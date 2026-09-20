"""证伪实验：BaseHTTPMiddleware 是否吞掉 http.disconnect → is_disconnected() 永假。

背景：/chat 的「用户停止」依赖 `await http_request.is_disconnected()`。
真机实测（2026-09-19）客户端 RST 后，服务端**没有**打出「检测到客户端断开」，
且工具循环继续跑到第二、三轮 —— 说明取消信号压根没置位。

server/main.py:87 注册了 `_RequestContextMiddleware(BaseHTTPMiddleware)`。
本脚本用两个**除了中间件外完全相同**的 app，各自起 uvicorn（同进程内线程），
用同一种裸 socket RST 断开，对比「服务端是否感知断开」。对照实验，不是推理。

判据：
  - 无中间件 → 应打出 DISCONNECT_DETECTED
  - 有中间件 → 若打不出，则 BaseHTTPMiddleware 即根因；同时看是否 HANDLER_CANCELLED
    （若 handler 被取消但 cancel_evt 未置位 → 「整轮不落库」为真但「停止烧 token」为假，
     两条验收必须分开看，不能混着报 PASS）
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

LOG: list[str] = []


def _emit(msg: str) -> None:
    LOG.append(msg)
    print(msg, flush=True)


class _MW(BaseHTTPMiddleware):
    """复刻 server/main.py 的 _RequestContextMiddleware（只加 header，不碰 body）。"""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        resp.headers["X-Probe"] = "1"
        return resp


def _work() -> str:
    """模拟同步阻塞的 agent.step（内含 LLM HTTP + 工具循环）。"""
    time.sleep(15)
    return "done"


def build_app(with_mw: bool) -> FastAPI:
    app = FastAPI()
    if with_mw:
        app.add_middleware(_MW)

    @app.post("/slow")
    async def slow(request: Request):
        evt = threading.Event()

        async def watch():
            while not evt.is_set():
                try:
                    if await request.is_disconnected():
                        evt.set()
                        _emit("DISCONNECT_DETECTED")
                        return
                except Exception as e:  # noqa: BLE001
                    _emit(f"WATCH_EXC {type(e).__name__}: {e}")
                    return
                await asyncio.sleep(0.5)

        task = asyncio.create_task(watch())
        outcome = "completed"
        try:
            await asyncio.wait_for(asyncio.to_thread(_work), timeout=30)
        except asyncio.CancelledError:
            outcome = "cancelled"
            _emit("HANDLER_CANCELLED")
            raise
        finally:
            _emit(f"OUTCOME={outcome} cancel_evt_set={evt.is_set()}")
        task.cancel()
        return {"ok": True}

    return app


def serve(app: FastAPI, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    return server, th


def rst_post(port: int, after: float = 2.0) -> None:
    """裸 socket 发 POST，after 秒后 SO_LINGER=0 close → RST（等价 AbortController.abort）。"""
    body = b'{"message":"hi"}'
    raw = (
        b"POST /slow HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    sk = socket.create_connection(("127.0.0.1", port), timeout=10)
    sk.sendall(raw)
    time.sleep(after)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    sk.close()


def run_case(with_mw: bool, port: int) -> list[str]:
    LOG.clear()
    server, _th = serve(build_app(with_mw), port)
    for _ in range(80):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        _emit("SERVER_NOT_STARTED")
        return list(LOG)
    rst_post(port, after=2.0)
    time.sleep(9)  # 给服务端打印的机会
    server.should_exit = True
    time.sleep(1)
    return list(LOG)


def main() -> int:
    print("=" * 72)
    print("[无 BaseHTTPMiddleware]  port=8891")
    nomw = run_case(False, 8891)
    for line in nomw:
        print("   ", line)

    print("=" * 72)
    print("[有 BaseHTTPMiddleware]  port=8892")
    mw = run_case(True, 8892)
    for line in mw:
        print("   ", line)

    print()
    print("=" * 72)
    print("结论：")
    print(f"  无中间件 → 感知断开: {'是' if 'DISCONNECT_DETECTED' in nomw else '否'}"
          f" | handler 被取消: {'是' if 'HANDLER_CANCELLED' in nomw else '否'}")
    print(f"  有中间件 → 感知断开: {'是' if 'DISCONNECT_DETECTED' in mw else '否'}"
          f" | handler 被取消: {'是' if 'HANDLER_CANCELLED' in mw else '否'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
