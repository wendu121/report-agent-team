"""直查：tasks 表 + dacc2bab 会话的消息（boss 那条外卖研报请求的最终落盘）。"""
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select, desc, func  # noqa: E402

from server.database import get_sync_session  # noqa: E402
from server.models import ChatMessage  # noqa: E402
from server.database import Base  # noqa: E402

tasks = Base.metadata.tables["tasks"]

with get_sync_session() as s:
    n = s.execute(select(func.count()).select_from(tasks)).scalar()
    print(f"tasks 总行数 = {n}")
    rows = s.execute(select(tasks).order_by(desc(tasks.c.created_at)).limit(10)).mappings().all()
    for r in rows:
        d = dict(r)
        print("-" * 60)
        print("task_id:", d.get("task_id"))
        print("status :", d.get("status"))
        print("created:", d.get("created_at"), "updated:", d.get("updated_at"))
        print("topic  :", str(d.get("topic"))[:120])
        print("escalate_reason:", str(d.get("escalate_reason"))[:200])
        print("report_markdown 长度:", len(str(d.get("report_markdown") or "")))
        ts = d.get("tool_status")
        print("tool_status:", str(ts)[:220])

print()
print("=== dacc2bab 会话消息 ===")
with get_sync_session() as s:
    rows = s.execute(
        select(ChatMessage.role, ChatMessage.content, ChatMessage.task_id, ChatMessage.created_at)
        .where(ChatMessage.session_id.like("dacc2bab%"))
        .order_by(ChatMessage.created_at)
    ).all()
    for r in rows:
        print(f"[{r.created_at}] {r.role} task_id={r.task_id}")
        print("   ", (r.content or "")[:400].replace("\n", " | "))
