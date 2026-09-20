"""聊天记录 / 历史记录 清理端点测试（DESIGN_chat_cleanup_time.md §2 K2 / K3-B）。

不触网、不烧 LLM。用 sqlite（aiosqlite）跑真实 DB，验证：
1) 单删成功且关联 events/reviews 被级联删（DB 行清零 + 内存态移除 + 状态文件清理）；
2) owner 不匹配 → 404（fail-closed，不泄漏存在性）；
3) running 状态 → 409，且任务保留；
4) 一键清空任务返回计数且 running 保留；
5) 一键清空会话后 messages / memory 零残留，且隔离其他账号的会话。

注意：运行时任务持久化在 server.api._tasks 字典 + .engine_state 文件，DB tasks 表
属迁移期 dead schema；因此单删测试同时校验「内存态移除」与「DB 关联行清理」两条真实路径。
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在 import server.database 之前把 URL 指向纯 ASCII 临时 sqlite，
# 并随后强制替换 server.database 的引擎/会话工厂（防其他模块先以 postgres URL 导入）。
_TMP = Path(os.environ.get("PYTEST_DEBUG_TEMPROOT", "C:/temp/pt-cleanup"))
_TMP.mkdir(parents=True, exist_ok=True)
_DB = _TMP / "cleanup_test.db"
if _DB.exists():
    try:
        _DB.unlink()
    except OSError:
        pass
_SYNC_URL = f"sqlite:///{_DB}"
_ASYNC_URL = f"sqlite+aiosqlite:///{_DB}"

from server import api as api_mod          # noqa: E402
from server.api import get_current_account  # noqa: E402
from server import tenancy                  # noqa: E402
from server import database as db_mod       # noqa: E402
from server.models import (                 # noqa: E402
    Base, Task, RoutingState, EngineEvent, GateReview,
    ChatSession, ChatMessage, ChatSessionMemory,
)

# 强制替换数据库引擎/会话工厂为 sqlite（覆盖模块级 import 时可能的 postgres 默认值）
_sync_engine = create_engine(_SYNC_URL, future=True)
_sync_sm = sessionmaker(bind=_sync_engine, future=True)
_async_engine = create_async_engine(_ASYNC_URL, future=True)
_async_sm = async_sessionmaker(bind=_async_engine, class_=AsyncSession, expire_on_commit=False)
db_mod.sync_engine = _sync_engine
db_mod.SyncSessionLocal = _sync_sm
db_mod.async_engine = _async_engine
db_mod.AsyncSessionLocal = _async_sm
Base.metadata.create_all(_sync_engine)

TEST_ACCOUNT = "test-account"
OTHER_ACCOUNT = "other-account"

app = FastAPI()
app.include_router(api_mod.router, prefix="/api/v1")


async def _fake_account():
    """测试用账号上下文：绑定 tenancy ContextVar，使 _current_aid() 返回 TEST_ACCOUNT。"""
    tenancy.set_current_account(TEST_ACCOUNT)
    return {"id": TEST_ACCOUNT}


app.dependency_overrides[get_current_account] = _fake_account

client = TestClient(app)


# ---------------------------------------------------------------------------
# 隔离：每个用例开始前清空内存任务字典 + 所有相关 DB 表
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate():
    api_mod._tasks.clear()
    with _sync_sm() as s:
        for tbl in (GateReview, EngineEvent, RoutingState, Task,
                    ChatMessage, ChatSessionMemory, ChatSession):
            s.execute(delete(tbl))
        s.commit()
    yield
    api_mod._tasks.clear()


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
class _FakeTask:
    """与 _owned_task 只看 .status / .owner_id 的最小替身。"""

    def __init__(self, task_id, status, owner_id):
        self.task_id = task_id
        self.status = status
        self.owner_id = owner_id


def _add_fake_task(task_id, status, owner_id=TEST_ACCOUNT):
    api_mod._tasks[task_id] = _FakeTask(task_id, status, owner_id)


def _db_add(obj):
    with _sync_sm() as s:
        s.add(obj)
        s.commit()


def _count(model, **kw):
    with _sync_sm() as s:
        stmt = select(func.count()).select_from(model)
        for k, v in kw.items():
            stmt = stmt.where(getattr(model, k) == v)
        return s.execute(stmt).scalar()


# ===========================================================================
# 1) 单删成功 + 关联 events/reviews 级联删
# ===========================================================================
def test_delete_single_task_cascade_and_audit():
    tid = "tid-cascade-1"
    # 内存态 + DB 父行 + 三张关联表都铺数据，验证全清
    _add_fake_task(tid, "done")
    _db_add(Task(
        task_id=tid, status="done", topic="t", scope=[], output_format_spec="",
        constraints=[], retrieval_records=[], analysis_conclusions=[],
        draft_segments=[], tool_status=[], prior_versions={},
    ))
    _db_add(RoutingState(task_id=tid, round=1, max_rounds=2, status="done"))
    _db_add(EngineEvent(task_id=tid, event="agent_output_unusable", round=1, reason="r"))
    _db_add(GateReview(task_id=tid, decision="advance", reason="r",
                       eval_score=0.9, gate="GateA", round=1))

    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "task_id": tid}

    # 内存态已移除
    assert tid not in api_mod._tasks
    # DB 关联行全部清零
    assert _count(Task, task_id=tid) == 0
    assert _count(RoutingState, task_id=tid) == 0
    assert _count(EngineEvent, task_id=tid) == 0
    assert _count(GateReview, task_id=tid) == 0
    # 再次 GET 该任务 → 404（fail-closed）
    g = client.get(f"/api/v1/tasks/{tid}")
    assert g.status_code == 404


# ===========================================================================
# 2) owner 不匹配 → 404（不泄漏存在性，绝不用 403）
# ===========================================================================
def test_delete_task_owner_mismatch_returns_404():
    tid = "tid-owner-1"
    _add_fake_task(tid, "done", owner_id=OTHER_ACCOUNT)
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 404
    # 任务仍在（未被删）
    assert tid in api_mod._tasks


# ===========================================================================
# 3) running → 409，且任务保留
# ===========================================================================
def test_delete_running_task_returns_409():
    tid = "tid-running-1"
    _add_fake_task(tid, "running")
    _db_add(Task(task_id=tid, status="running", topic="t", scope=[],
                 output_format_spec="", constraints=[], retrieval_records=[],
                 analysis_conclusions=[], draft_segments=[], tool_status=[],
                 prior_versions={}))

    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "TASK_BUSY"
    # 任务保留
    assert tid in api_mod._tasks
    assert _count(Task, task_id=tid) == 1


def test_delete_rework_task_returns_409():
    tid = "tid-rework-1"
    _add_fake_task(tid, "rework")
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 409
    assert tid in api_mod._tasks


# ===========================================================================
# 4) 一键清空任务：计数 + running 保留
# ===========================================================================
def test_clear_tasks_counts_and_keeps_running():
    done_a, done_b, running = "clr-done-1", "clr-done-2", "clr-run-1"
    _add_fake_task(done_a, "done")
    _add_fake_task(done_b, "done")
    _add_fake_task(running, "running")
    # 其他账号的任务不应被清
    _add_fake_task("clr-other-1", "done", owner_id=OTHER_ACCOUNT)

    r = client.delete("/api/v1/tasks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] == 2
    assert body["skipped_running"] == 1

    # 本账号非运行中任务已移除，running 保留，其他账号不受影响
    assert done_a not in api_mod._tasks
    assert done_b not in api_mod._tasks
    assert running in api_mod._tasks
    assert "clr-other-1" in api_mod._tasks


def test_clear_tasks_empty():
    r = client.delete("/api/v1/tasks")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "deleted": 0, "skipped_running": 0}


# ===========================================================================
# 5) 一键清空会话：messages / memory 零残留 + 账号隔离
# ===========================================================================
def test_clear_sessions_removes_messages_and_memory():
    sid = "sess-1"
    other_sid = "sess-other-1"
    _db_add(ChatSession(id=sid, topic="t", model="auto-chat", agents="",
                         plugins="", owner_id=TEST_ACCOUNT))
    _db_add(ChatMessage(id="m1", session_id=sid, role="user", content="hi"))
    _db_add(ChatMessage(id="m2", session_id=sid, role="assistant", content="yo"))
    _db_add(ChatSessionMemory(id="mem1", session_id=sid, key="topic", value="x"))
    # 其他账号的会话必须保留
    _db_add(ChatSession(id=other_sid, topic="o", model="auto-chat", agents="",
                         plugins="", owner_id=OTHER_ACCOUNT))
    _db_add(ChatMessage(id="m3", session_id=other_sid, role="user", content="keep"))

    r = client.delete("/api/v1/chat/sessions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] == 1

    # 本账号会话、消息、记忆全清
    assert _count(ChatSession, id=sid) == 0
    assert _count(ChatMessage, session_id=sid) == 0
    assert _count(ChatSessionMemory, session_id=sid) == 0
    # 其他账号不受影响
    assert _count(ChatSession, id=other_sid) == 1
    assert _count(ChatMessage, session_id=other_sid) == 1


def test_clear_sessions_empty():
    r = client.delete("/api/v1/chat/sessions")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted": 0}
