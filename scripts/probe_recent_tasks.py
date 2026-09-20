"""查最近的任务实况：boss 16:49 那条外卖研报请求到底跑到哪一步了。"""
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select, desc  # noqa: E402

from server.database import get_sync_session  # noqa: E402
from server.models import Account, ChatSession  # noqa: E402

# 任务表名不一定叫 Task，先探测
from server.database import Base  # noqa: E402

print("=== 所有表 ===")
names = sorted(Base.metadata.tables.keys())
print(", ".join(names))
print()

with get_sync_session() as s:
    # 账号
    for acc in s.execute(select(Account.id, Account.username, Account.is_system_main)).all():
        print(f"账号: {acc.username} id={acc.id} main={acc.is_system_main}")
    print()

    # 最近会话
    print("=== 最近 6 个会话 ===")
    rows = s.execute(
        select(ChatSession.id, ChatSession.topic, ChatSession.model,
               ChatSession.owner_id, ChatSession.created_at, ChatSession.updated_at)
        .order_by(desc(ChatSession.updated_at)).limit(6)
    ).all()
    for r in rows:
        print(f"{r.created_at} | {r.id[:8]} | model={r.model!r} | topic={(r.topic or '')[:40]!r}")

print()
print("=== 任务表内容（若有）===")
for tname in names:
    if "task" in tname.lower():
        tbl = Base.metadata.tables[tname]
        cols = [c.name for c in tbl.columns]
        print(f"-- {tname} cols={cols}")
        with get_sync_session() as s:
            rows = s.execute(select(tbl).order_by(desc(tbl.c.created_at)).limit(8)).mappings().all()
            for r in rows:
                d = dict(r)
                keep = {k: (str(v)[:60] if v is not None else None)
                        for k, v in d.items()
                        if k in ("id", "status", "topic", "user_task", "error", "created_at",
                                 "updated_at", "model", "owner_id", "current_node", "round")}
                print("   ", keep)
