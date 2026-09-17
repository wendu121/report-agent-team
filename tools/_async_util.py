# tools/_async_util.py · 同步上下文安全跑协程（M11-1/M11-3 共用）
#
# 为什么需要它：
#   - 引擎正式路径跑在**独立子进程**（server/engine_client.py 用 subprocess 起
#     engine_runner.py → orchestrator.run_report），该进程内**无运行中的 event loop**
#     → 直接 asyncio.run 即可（主路径）。
#   - 兜底：若将来有人从 async 上下文直接调用（本线程已有运行中的 loop），
#     再 new_event_loop + run_until_complete 会抛
#     RuntimeError("Cannot run the event loop while another loop is running")。
#     故改为**在独立线程内 asyncio.run**，彻底规避嵌套 loop（不引入 nest_asyncio 依赖）。
#
# 单一实现，避免 orchestrator 与 tools/mcp_client 各写一份导致修复不同步
# （第 2 轮独立审议 MINOR：重复实现）。
from __future__ import annotations

import asyncio
import threading


def run_async(coro):
    """同步上下文跑协程；已有运行中的 loop 时改用独立线程兜底。异常原样回抛调用方。

    同步路径不再用 asyncio.run：mcp streamable_http 客户端连接失败时，其遗留的
    异步生成器会在 asyncio.run 的 loop 关闭阶段抛
    "RuntimeError: aclose(): asynchronous generator is already running"（anyio
    cancel-scope 清理错误），该异常逃逸出调用方 try → 打穿 /chat 端点 500。
    改为自建 loop + 显式清理并吞掉清理异常，杜绝逃逸。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(lambda *a: None)
        try:
            return loop.run_until_complete(coro)
        except asyncio.CancelledError as e:
            # 根因修复：MCP server 不可达/超时 → anyio cancel scope 取消 coro →
            # 抛 CancelledError。它是 BaseException 子类，调用方 except Exception
            # 抓不住 → 逃逸成 Starlette 500 纯文本页。此处转普通 RuntimeError，
            # 让 MCP 边界的降级逻辑（list_tools 返回 [] / call_tool 返回 ok=False）
            # 正常生效。注意：这是 MCP 连接被取消，不是整个 /chat 请求被取消。
            raise RuntimeError(f"异步任务被取消（MCP server 不可达/超时？）: {e}") from e
        finally:
            _drain_loop(loop)

    box: dict = {}

    def _worker():
        try:
            box["v"] = asyncio.run(coro)
        except asyncio.CancelledError as e:  # noqa: BLE001 — 转普通异常，避免逃逸 except Exception
            box["e"] = RuntimeError(f"异步任务被取消（MCP server 不可达/超时？）: {e}")
        except BaseException as e:  # noqa: BLE001 — 原样回抛
            box["e"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _drain_loop(loop):
    """尽力关闭挂起的异步生成器并关 loop，吞掉任何清理异常（anyio cancel-scope 错误）。"""
    try:
        for ag in list(getattr(loop, "_asyncgens", set())):
            try:
                loop.run_until_complete(ag.aclose())
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        loop.close()
    except Exception:  # noqa: BLE001
        pass
