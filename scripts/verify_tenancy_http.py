"""Phase 1 多租户隔离 · 真实 HTTP/DB 端到端实证（关门③盲区补充）。

覆盖独立审议抓出的三个 MAJOR 的「真实跑」证据：
  H-1 路由级鉴权依赖生效（无 token → 401；A/B 跨账号访问 → 404）
  H-2 /chat 会话归属 + 跨会话记忆隔离（B 看不到 A 的记忆；自动建会话带 owner_id）
  H-3 skill_importer 按账号写盘（落到 tenants/<A>，绝不写全局；无上下文 fail loud）

不跑真实 LLM：monkeypatch server.api._get_chat_agent 返回桩 agent。
不连 Postgres：DATABASE_URL / ASYNC_DATABASE_URL 指向临时文件 sqlite。

用法：python scripts/verify_tenancy_http.py
"""
import os
import sys
import asyncio
import tempfile
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

tmp = tempfile.mkdtemp(prefix="rat-tenancy-http-")
os.environ["TENANTS_ROOT"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/rat.db?check_same_thread=False"
os.environ["ASYNC_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/rat.db"
os.environ["AUTH_SECRET"] = "test-secret-do-not-use-in-prod"
os.environ.pop("RAT_LEGACY_GLOBAL", None)
os.environ.pop("RAT_ACCOUNT_ID", None)

# 必须在 import server.database 之前设好 env（引擎 URL 在模块加载时读取）
import server.database  # noqa: E402
from server.database import SyncSessionLocal, init_database  # noqa: E402
from server import models as M  # noqa: E402
from server import tenancy  # noqa: E402
import tools.skill_importer as skill_importer  # noqa: E402

init_database()  # 建表（同步引擎，文件 sqlite）

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.api import router as api_router  # noqa: E402
from server.auth import router as auth_router  # noqa: E402

# ---- 桩 agent：避免真实 LLM 调用 ----
class FakeAgent:
    def step(self, history, user_msg, model=None):
        return {"reply": "stub", "intent": "chat", "tool_calls": [], "sources": []}

import server.api as api_mod  # noqa: E402
api_mod._get_chat_agent = lambda: FakeAgent()

app = FastAPI()
app.include_router(api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

failures = []
def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)

client = TestClient(app)

# ============================================================
# H-1 路由级鉴权：无 token → 401
# ============================================================
r = client.get("/api/v1/chat/sessions")
check("H-1 无 token 访问受保护路由 → 401", r.status_code == 401, f"status={r.status_code}")

# ============================================================
# 注册 A(主/激活) 与 B(子/待审)，审批后登录拿 JWT
# ============================================================
ra = client.post("/api/v1/auth/register", json={"username": "alice", "password": "password123"})
check("注册 A 成功", ra.status_code == 200, ra.text[:120])
rb = client.post("/api/v1/auth/register", json={"username": "bob", "password": "password123"})
check("注册 B 成功(待审)", rb.status_code == 200, rb.text[:120])

# 已知产品缺口（独立于本 Phase 隔离 MAJOR）：register 把 sub 的 parent_id 留空，
# 而 _approve_impl 要求 parent_id==main.id，导致主账号无法审批「开放注册」的子账号。
# 此处在测试桩里补上父子归属，模拟预期层级，使 B 成为可审批的独立账号。
b_id = rb.json()["id"]
with SyncSessionLocal() as s:
    b = s.get(M.Account, b_id)
    b.parent_id = ra.json()["id"]
    s.commit()

la = client.post("/api/v1/auth/login", json={"username": "alice", "password": "password123"})
check("登录 A 成功", la.status_code == 200, la.text[:120])
token_a = la.json()["access_token"]
hdr_a = {"Authorization": f"Bearer {token_a}"}

# B 此时 pending，登录应被拒
lb_pending = client.post("/api/v1/auth/login", json={"username": "bob", "password": "password123"})
check("B 待审时登录被拒(403)", lb_pending.status_code in (403, 401), f"status={lb_pending.status_code}")

# A 审批 B
approve = client.post(f"/api/v1/auth/accounts/{b_id}/approve", headers=hdr_a)
check("A 审批 B 成功", approve.status_code == 200, approve.text[:120])

lb = client.post("/api/v1/auth/login", json={"username": "bob", "password": "password123"})
check("B 审批后登录成功", lb.status_code == 200, lb.text[:120])
token_b = lb.json()["access_token"]
hdr_b = {"Authorization": f"Bearer {token_b}"}

# ============================================================
# H-1 跨账号会话隔离：A 建会话，B 访问 → 404
# ============================================================
sa = client.post("/api/v1/chat/sessions", headers=hdr_a, json={"topic": "A 的会话"})
check("A 建会话成功", sa.status_code == 200, sa.text[:120])
sid_a = sa.json()["id"]

rb_get = client.get(f"/api/v1/chat/sessions/{sid_a}", headers=hdr_b)
check("H-1 B 访问 A 的会话 → 404(越权)", rb_get.status_code == 404, f"status={rb_get.status_code}")
ra_get = client.get(f"/api/v1/chat/sessions/{sid_a}", headers=hdr_a)
check("H-1 A 访问自己的会话 → 200", ra_get.status_code == 200, f"status={ra_get.status_code}")

# 调试探针：确认 A 建的会话确实带 owner_id（否则隔离形同虚设）
with SyncSessionLocal() as s:
    _owner = s.get(M.ChatSession, sid_a)
    print(f"[debug] sid_a owner_id = {getattr(_owner, 'owner_id', None)}")

# ============================================================
# H-2 /chat 会话归属：B 拿 A 的 session_id 调 /chat → 404（在调用 LLM 前拦截）
# ============================================================
rc = client.post("/api/v1/chat", headers=hdr_b, json={"message": "hi", "session_id": sid_a})
check("H-2 B 用 A 的 session 调 /chat → 404", rc.status_code == 404, f"status={rc.status_code}")

# ============================================================
# H-2 /chat 自动建会话归属当前账号（owner_id 注入）
# ============================================================
rc2 = client.post("/api/v1/chat", headers=hdr_a, json={"message": "hello from A"})
check("H-2 A 无 session 调 /chat → 200", rc2.status_code == 200, rc2.text[:160])
sid_new = rc2.json().get("session_id")
check("H-2 返回了新建 session_id", bool(sid_new), f"sid={sid_new}")
if sid_new:
    with SyncSessionLocal() as s:
        row = s.get(M.ChatSession, sid_new)
        check("H-2 自动建会话 owner_id == A", row is not None and row.owner_id == "alice" or row is not None and row.owner_id == ra.json().get("id"),
              f"owner={getattr(row, 'owner_id', None)}")

# ============================================================
# H-2 跨会话记忆隔离：A 的会话记忆，B 的 /chat 上下文不得注入
# ============================================================
# 另建 A 的「第二个会话」并写入记忆；查询时排除 sid_a（当前会话），故应命中第二个会话。
# 这样才真正检验跨会话召回是否按 owner 过滤。
a_id = ra.json()["id"]
sid_a2 = "sess-a2-" + a_id
with SyncSessionLocal() as s:
    s.add(M.ChatSession(id=sid_a2, topic="A2", model="auto-chat", owner_id=a_id))
    s.add(M.ChatSessionMemory(id="mem-a2-" + a_id, session_id=sid_a2, key="conclusion", value="SECRET-A-MEMORY"))
    s.commit()

def _mem_exclude(exclude_sid, aid):
    tenancy.reset_current_account()
    if aid:
        tenancy.set_current_account(aid)
    return asyncio.run(api_mod._fetch_recent_session_memories(exclude_sid))

mem_a = _mem_exclude(sid_a, a_id)
check("H-2 A 能读到自己的跨会话记忆", any("SECRET-A-MEMORY" in m.get("value", "") for m in mem_a), f"n={len(mem_a)}")
mem_b = _mem_exclude(sid_a, b_id)
check("H-2 B 读不到 A 的跨会话记忆(隔离)", not any("SECRET-A-MEMORY" in m.get("value", "") for m in mem_b), f"n={len(mem_b)}")

# ============================================================
# H-3 skill_importer 按账号写盘（真实落盘，非纸面）
# ============================================================
tenancy.reset_current_account()
tenancy.set_current_account(ra.json()["id"])
skill_importer._audit_dir().mkdir(parents=True, exist_ok=True)
skill_importer._atomic_write(skill_importer._skills_yaml(), "skills:\n  - id: demo\n  name: demo\n")
skills_a = tenancy.account_root(ra.json()["id"]) / "config" / "skills.yaml"
skills_b = tenancy.account_root(rb.json()["id"]) / "config" / "skills.yaml"
check("H-3 技能落到 tenants/A/config/skills.yaml", skills_a.exists() and "id: demo" in skills_a.read_text(encoding="utf-8"), str(skills_a))
# B 的 skills.yaml 是「审批时从 A 模板播种」的合法副本；关键是它绝不能含 A 本次写入的 demo
# —— 证明 skill_importer 的写盘只作用于当前账号，没有污染他人命名空间。
check(
    "H-3 A 的写入未污染 tenants/B（隔离）",
    (not skills_b.exists()) or ("id: demo" not in skills_b.read_text(encoding="utf-8")),
    str(skills_b),
)

# 无上下文 fail loud（不静默回落全局）
tenancy.reset_current_account()
try:
    skill_importer._skills_yaml()
    check("H-3 无上下文 _skills_yaml 抛错(不假隔离)", False, "未抛错")
except tenancy.TenancyError:
    check("H-3 无上下文 _skills_yaml 抛错(不假隔离)", True)

print()
print("FAILED:" if failures else "ALL PASS", ", ".join(failures) if failures else "")
sys.exit(1 if failures else 0)
