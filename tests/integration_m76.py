"""
M7-6 前后端联调集成探针（契约级真机验证）
================================================
server 以独立子进程起服（与前端 dev server 真实连接架构一致）。本脚本作为外部客户端做三层验证：
  A) REST 契约：真实 httpx POST /tasks（201）→ GET /tasks/{id}（200），形状对齐 api.ts
  B) WS 握手：真实 websockets 客户端连 server，收真实 connection_established，形状对齐 engine.ts
  C) WS 事件形态：直接实例化 server 自带的 Event 模型并 .dict()（正是 emit_* 广播的字节），
     逐字段比对 engine.ts（零编造、零改 server 代码）

不依赖真实 LLM 引擎。运行：python tests/integration_m76.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# TD-006：原硬编码宿主绝对路径且目录名有误（workbuddy，实为 .workbuddy，路径已失效）。
# 该常量当前未被使用（client-only 探针），改用 sys.executable 消除硬编码与失效路径。
PY = sys.executable
PORT = int(os.environ.get("M76_PORT", "8000"))
BASE = f"http://127.0.0.1:{PORT}"
WS_TASK_ID = "m76-ws-probe-0001"

# 前端 engine.ts 期望的顶层字段（每个事件类型）
EXPECTED = {
    "connection_established": ["event_type", "task_id", "debug_mode", "timestamp", "message"],
    "agent_complete": ["event_type", "task_id", "timestamp", "round", "agent", "tool_status", "engine_events"],
    "gate_complete": ["event_type", "task_id", "timestamp", "round", "gate", "decision", "reason", "eval_score", "problem_points"],
    "rework_trigger": ["event_type", "task_id", "timestamp", "round", "rework_target_agent", "rework_reason", "last_gate"],
    "round_update": ["event_type", "task_id", "timestamp", "round", "max_rounds"],
    "tool_error": ["event_type", "task_id", "timestamp", "round", "agent", "tool", "error"],
    "task_done": ["event_type", "task_id", "timestamp", "round", "status", "report_markdown", "audit_url"],
    "task_escalated": ["event_type", "task_id", "timestamp", "round", "status", "escalate_reason", "last_gate", "audit_url"],
}

RESULTS: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "pass": cond, "detail": detail})
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


async def part_a_rest() -> None:
    import httpx

    async with httpx.AsyncClient(base_url=BASE, timeout=15, trust_env=False) as client:
        r = await client.post(
            "/api/v1/tasks",
            json={
                "user_task": {
                    "topic": "M7-6 联调测试主题",
                    "scope": ["技术可行性", "市场格局"],
                    "output_format_spec": "结构化 Markdown 研报",
                    "constraints": ["引用需可溯源"],
                }
            },
        )
        check("REST POST /tasks -> 201", r.status_code == 201, f"status={r.status_code}")
        body = r.json()
        for k in ("task_id", "status", "created_at", "user_task"):
            check(f"CreateTaskResponse 含字段 {k}", k in body, f"keys={list(body.keys())}")
        check("CreateTaskResponse.status == running", body.get("status") == "running", f"status={body.get('status')}")
        task_id = body["task_id"]

        g = await client.get(f"/api/v1/tasks/{task_id}")
        check("REST GET /tasks/{id} -> 200", g.status_code == 200, f"status={g.status_code}")
        t = g.json()
        needed = [
            "task_id", "status", "created_at", "updated_at", "user_task",
            "routing_state", "retrieval_records", "analysis_conclusions",
            "draft_segments", "tool_status", "prior_versions",
            "report_markdown", "escalate_reason",
        ]
        missing = [k for k in needed if k not in t]
        check("TaskResponse 含全部 13 个投影字段", not missing, f"missing={missing}")
        rs = t.get("routing_state", {})
        for k in ("round", "max_rounds", "last_gate", "status", "rework_target_agent", "rework_reason", "gate_review_history", "engine_events"):
            check(f"routing_state 含字段 {k}", k in rs, f"keys={list(rs.keys())}")
        nf = await client.get("/api/v1/tasks/does-not-exist")
        check("REST GET 未知任务 -> 404", nf.status_code == 404, f"status={nf.status_code}")


async def part_b_ws_handshake() -> None:
    from websockets.asyncio.client import connect as ws_connect

    uri = f"ws://127.0.0.1:{PORT}/api/v1/tasks/{WS_TASK_ID}/stream?debug_mode=0"
    async with ws_connect(uri) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    check("WS 首包为 connection_established", first.get("event_type") == "connection_established",
          f"event_type={first.get('event_type')}")
    missing = [k for k in EXPECTED["connection_established"] if k not in first]
    check("WS connection_established 顶层字段完整", not missing, f"missing={missing} | keys={list(first.keys())}")
    check("WS connection_established.debug_mode == 0", first.get("debug_mode") == 0, f"debug_mode={first.get('debug_mode')}")
    print("\n--- 真实 connection_established payload ---")
    print(json.dumps(first, ensure_ascii=False, indent=2))


def part_c_ws_event_shapes() -> None:
    """实例化 server 自带 Event 模型，验证 .dict() 顶层字段对齐 engine.ts（emit_* 广播的正是此字节）。"""
    from datetime import datetime

    sys.path.insert(0, str(PROJECT_ROOT))
    import server.websocket as wsmod

    ts = datetime.utcnow().isoformat()
    samples = {
        "agent_complete": wsmod.AgentCompleteEvent(task_id=WS_TASK_ID, timestamp=ts, round=1, agent="Researcher"),
        "gate_complete": wsmod.GateCompleteEvent(task_id=WS_TASK_ID, timestamp=ts, round=1, gate="GateA", decision="advance", reason="ok", eval_score=0.9),
        "rework_trigger": wsmod.ReworkTriggerEvent(task_id=WS_TASK_ID, timestamp=ts, round=1, rework_target_agent="Writer", rework_reason="r", last_gate="GateB"),
        "round_update": wsmod.RoundUpdateEvent(task_id=WS_TASK_ID, timestamp=ts, round=2, max_rounds=2),
        "tool_error": wsmod.ToolErrorEvent(task_id=WS_TASK_ID, timestamp=ts, round=2, agent="Writer", tool="web_search", error="503"),
        "task_done": wsmod.TaskDoneEvent(task_id=WS_TASK_ID, timestamp=ts, round=2, status="done", report_markdown="# x", audit_url="/api/v1/tasks/x/audit-export"),
        "task_escalated": wsmod.TaskEscalatedEvent(task_id=WS_TASK_ID, timestamp=ts, round=2, status="escalated", escalate_reason="e", last_gate="GateC", audit_url="/api/v1/tasks/x/audit-export"),
    }
    for et, model in samples.items():
        d = model.dict()
        missing = [k for k in EXPECTED[et] if k not in d]
        check(f"WS 事件 {et} 顶层字段对齐 engine.ts", not missing,
              f"missing={missing} | 实际keys={list(d.keys())}")


def wait_server_ready(timeout: float = 20.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(base_url=BASE, timeout=2, trust_env=False) as c:
                if c.get("/health").status_code == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


async def main() -> None:
    # server 由外部以独立进程起服（Bash 后台：python -m uvicorn server.main:app --port 8000）
    if not wait_server_ready():
        check("server 启动并 /health 就绪", False, "server 未在超时内就绪（请先起服）")
        return
    check("server 启动并 /health 就绪", True)

    await part_a_rest()
    await part_b_ws_handshake()
    part_c_ws_event_shapes()

    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    print(f"\n==== M7-6 联调结果：{passed}/{total} 通过 ====")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
