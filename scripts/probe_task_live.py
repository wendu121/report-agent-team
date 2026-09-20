"""问活的 API：boss 那条任务（4a849ca2）现在到底在不在、什么状态。"""
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from server.auth import create_access_token  # noqa: E402
from server.database import get_sync_session  # noqa: E402
from server.models import Account  # noqa: E402

TASK = "4a849ca2-99ab-4b73-8e95-ee204945838e"


def get(path, token):
    req = urllib.request.Request("http://127.0.0.1:8000" + path)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


with get_sync_session() as s:
    accs = s.execute(select(Account)).scalars().all()
    toks = {a.username: create_access_token(a) for a in accs}
    print("账号:", list(toks))

# 两个账号分别查同一条任务：任务归属决定谁能看见（owner 隔离）
for uname, tok in toks.items():
    print("=" * 60)
    print(f"--- 以 {uname} 身份查 ---")
    st, body = get(f"/api/v1/tasks/{TASK}", tok)
    print(f"GET /tasks/{TASK[:8]}… -> HTTP {st}")
    print("   ", body[:300])
    st, body = get("/api/v1/tasks", tok)
    try:
        items = json.loads(body)
        items = items.get("items", items) if isinstance(items, dict) else items
        print(f"GET /tasks -> {len(items)} 条")
        for t in items[:10]:
            print("   -", t.get("task_id", "")[:8], t.get("status"), str(t.get("topic"))[:50])
    except Exception as e:  # noqa: BLE001
        print("  解析失败:", e, body[:200])
