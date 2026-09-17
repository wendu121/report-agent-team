# tests/e2e_real_run.py · 真实引擎端到端回归（M7 收尾 / 引擎真实跑通）
#
# 目的：验证 server 经 new-api 网关真实驱动引擎 → WebSocket 推送 → 事件形状
#       对齐 API_SPEC §3.2 / 前端 engine.ts 判别联合契约。
#
# 用法：
#   python tests/e2e_real_run.py ["主题"] ["scope,逗号分隔"]
# 前置：server 已在 :8000 运行，且 NEWAPI_API_KEY 已注入其环境。
#
# 说明：
#   - 检索走 MOCK（无 TAVILY_API_KEY）；仅验证链路与契约，不验证检索内容真实性。
#   - 中间事件（agent_complete/gate_complete/round_update）当前真实引擎路径不推送
#     （server 仅收尾 emit task_done/escalated），本脚本如实记录并标注，不作失败判。

import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime
from websockets.asyncio.client import connect as ws_connect

BASE = "http://127.0.0.1:8000"
WS_BASE = "ws://127.0.0.1:8000"

# 8 事件判别联合字段契约（API_SPEC §3.2 / engine.ts）
SCHEMA = {
    "connection_established": ["event_type", "task_id", "debug_mode", "timestamp", "message"],
    "agent_complete": ["event_type", "task_id", "timestamp", "round", "agent", "tool_status", "engine_events"],
    "gate_complete": ["event_type", "task_id", "timestamp", "round", "gate", "decision", "reason", "eval_score", "problem_points"],
    "rework_trigger": ["event_type", "task_id", "timestamp", "round", "rework_target_agent", "rework_reason", "last_gate"],
    "round_update": ["event_type", "task_id", "timestamp", "round", "max_rounds"],
    "tool_error": ["event_type", "task_id", "timestamp", "round", "agent", "tool", "error"],
    "task_done": ["event_type", "task_id", "timestamp", "round", "status", "report_markdown", "audit_url"],
    "task_escalated": ["event_type", "task_id", "timestamp", "round", "status", "escalate_reason", "last_gate", "audit_url"],
}
TERMINAL = {"task_done", "task_escalated"}


def post_task(topic: str, scope: list) -> dict:
    body = json.dumps({"user_task": {
        "topic": topic,
        "scope": scope,
        "output_format_spec": "markdown",
        "constraints": ["中文输出", "结论需标注来源"],
    }}).encode()
    req = urllib.request.Request(
        BASE + "/api/v1/tasks", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


async def capture(task_id: str, timeout: int = 1200) -> list:
    uri = f"{WS_BASE}/api/v1/tasks/{task_id}/stream?debug_mode=0"
    events: list = []
    t0 = time.time()
    try:
        async with ws_connect(uri) as ws:
            while time.time() - t0 < timeout:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue  # 引擎长跑中，继续等
                try:
                    ev = json.loads(msg)
                except json.JSONDecodeError:
                    events.append({"_parse_error": msg})
                    continue
                events.append(ev)
                if ev.get("event_type") in TERMINAL:
                    break
    except Exception as e:  # WS 断开/异常
        events.append({"_ws_error": str(e)})
    return events


def validate(events: list) -> list:
    out = []
    for ev in events:
        et = ev.get("event_type")
        if et not in SCHEMA:
            out.append({"event_type": et, "ok": False,
                        "reason": "未知 event_type（非判别联合成员）" if et else "缺 event_type"})
            continue
        missing = [k for k in SCHEMA[et] if k not in ev]
        out.append({"event_type": et, "ok": not missing, "missing": missing})
    return out


async def main():
    topic = sys.argv[1] if len(sys.argv) > 1 else "2026 年 AI 芯片市场研报"
    scope = sys.argv[2].split(",") if len(sys.argv) > 2 else ["供给", "需求", "竞争格局"]
    print(f"[e2e] POST 真实任务 topic={topic} scope={scope}", flush=True)
    task = post_task(topic, scope)
    tid = task["task_id"]
    print(f"[e2e] task_id={tid} status={task['status']}", flush=True)
    print(f"[e2e] 连接 WS 捕获真实事件 (timeout=1200s)...", flush=True)
    events = await capture(tid, timeout=1200)
    print(f"[e2e] 捕获 {len(events)} 个事件", flush=True)

    val = validate(events)
    # 终态查询
    try:
        with urllib.request.urlopen(BASE + f"/api/v1/tasks/{tid}", timeout=30) as r:
            final = json.loads(r.read().decode())
    except Exception as e:
        final = {"_error": str(e)}

    out = {
        "task_id": tid, "topic": topic, "scope": scope,
        "events": events, "validation": val,
        "final_state": final, "captured_at": datetime.now().isoformat(),
    }
    with open("tests/e2e_real_run_report.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    ok = sum(1 for v in val if v["ok"])
    print("=== 事件契约校验 ===", flush=True)
    for v in val:
        flag = "OK " if v["ok"] else "FAIL"
        extra = f"  missing={v['missing']}" if not v["ok"] else ""
        print(f"  {flag} {v['event_type']}{extra}", flush=True)
    print(f"[e2e] 契约通过 {ok}/{len(val)}；报告→ tests/e2e_real_run_report.json", flush=True)
    print(f"[e2e] 终态: status={final.get('status')} "
          f"round={final.get('routing_state', {}).get('round')}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
