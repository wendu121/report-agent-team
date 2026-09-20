"""在真实子账号租户上下文里创建一次任务，并等待引擎跑完，输出三道闸的真实 eval_score。

用途：验证「模型映射改为接口 id」后，Gate 是否真的调用了审核 LLM（而非静默降级放行）。
运行（容器内）：docker compose exec -T api python /app/scripts/verify_gate_real.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# 以 `python /app/scripts/xxx.py` 方式运行时，sys.path[0] 是 scripts/ 而非 /app，
# 会导致 `import server` 失败 —— 显式补上项目根。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 默认子账号 wendy1；传 argv[1] 可换账号（如主账号 9910ebbc-...）。
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"  # wendy1
API = "http://127.0.0.1:8000"

from sqlalchemy import select  # noqa: E402

from server.auth import create_access_token  # noqa: E402
from server.database import get_sync_session  # noqa: E402
from server.models import Account  # noqa: E402


def main() -> int:
    with get_sync_session() as s:
        acc = s.execute(select(Account).where(Account.id == ACCOUNT)).scalar_one()
        tok = create_access_token(acc)

    payload = {
        "user_task": {
            "topic": "2026年中国即饮咖啡行业竞争格局",
            "scope": ["市场规模与增速", "主要品牌", "渠道结构"],
            "output_format_spec": "结构化研报：含引言、分节小标题、结论",
            "constraints": [],
        }
    }
    req = urllib.request.Request(
        API + "/api/v1/tasks",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("创建任务失败 HTTP", e.code)
        print(e.read().decode()[:800])
        return 1

    task_id = body["task_id"]
    print("任务已创建 task_id =", task_id, flush=True)

    # 轮询引擎产物文件
    path = f"/app/tenants/{ACCOUNT}/.engine_state/{task_id}_output.json"
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(10)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        rs = d.get("routing_state") or {}
        st = rs.get("status")
        if st in ("done", "escalated", "aborted", "failed"):
            gh = rs.get("gate_review_history") or []
            print("\n=== 引擎终态 ===")
            print("status      =", st)
            print("round       =", rs.get("round"), "/ max_rounds =", rs.get("max_rounds"))
            print("md 字数     =", len(d.get("report_markdown") or ""))
            print("escalate    =", str(rs.get("escalate_reason"))[:200])
            print("检索记录数  =", len(d.get("retrieval_records") or []))
            print("闸记录数    =", len(gh))
            print("\n=== 每道闸的真实判定 ===")
            llm_reviewed = 0
            degraded = 0
            for i, g in enumerate(gh):
                sc = g.get("eval_score")
                rs_status = g.get("review_status") or "(未标记)"
                src = g.get("eval_score_source") or "-"
                if rs_status == "llm_reviewed":
                    llm_reviewed += 1
                elif rs_status == "degraded_unavailable":
                    degraded += 1
                print(
                    "  [%d] gate=%-7s decision=%-8s review_status=%-20s score=%-6r src=%-19s reason=%s"
                    % (i, g.get("gate"), g.get("decision"), rs_status, sc, src,
                       str(g.get("reason"))[:70])
                )
            print("\n=== 判定 ===")
            # M13-gate-degradation：判据已从「eval_score 是否为 None」改为 review_status。
            # 理由：eval_score=None **不再是失败信号** —— 它可能是诚实的正确值
            #（闸降级且独立评分器也失败，或被可信性守卫拒绝）。真正要判的是
            #「审核 LLM 到底跑没跑起来」，这正是 review_status 的语义。
            if degraded:
                print("⚠️ %d / %d 道闸为「审核 LLM 不可用·降级放行」"
                      "——报告已产出，但质量未经 AI 审核" % (degraded, len(gh)))
                print("   （降级本身不阻断任务；前端结果页会显示警示横幅并标注各闸依据来源）")
            if gh and llm_reviewed == len(gh):
                print("✅ 全部 %d 道闸均由审核 LLM 真实评审" % len(gh))
            elif gh and llm_reviewed:
                print("ℹ️ %d / %d 道闸由审核 LLM 真实评审，%d 道降级放行"
                      % (llm_reviewed, len(gh), degraded))
            return 0
        print("... status =", st, flush=True)

    print("超时未完成")
    return 2


if __name__ == "__main__":
    sys.exit(main())
