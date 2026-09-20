# tests/test_api_report_download.py · 研报下载端点（OR-3）测试
# 只挂 server.api.router，monkeypatch 掉 _owned_task（不碰 DB/引擎/网络）。

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import api as api_mod  # noqa: E402
from server.api import get_current_account  # noqa: E402
from tools.doc_render import RendererUnavailable  # noqa: E402

MD = """# 锂电池行业研报

## 摘要

固态电池产业化提速。

## 引用 / 来源

- [rec-1](https://a.com/1) — 标题一（可信度: high）
"""


class _FakeUserTask:
    topic = "锂电池"


class _FakeTask:
    def __init__(self, st, md):
        self.status = st
        self.report_markdown = md
        self.user_task = _FakeUserTask()
        self.owner_id = "acc-1"


app = FastAPI()
app.include_router(api_mod.router, prefix="/api/v1")
app.dependency_overrides[get_current_account] = lambda: {"id": "acc-1"}
client = TestClient(app)


@pytest.fixture
def done_task(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task", lambda tid: _FakeTask(api_mod.TaskStatus.DONE, MD))
    return "t-1"


# ---------------------------------------------------------------- 正常路径
def test_download_md_returns_markdown(done_task):
    r = client.get(f"/api/v1/tasks/{done_task}/report?format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text == MD


@pytest.mark.parametrize("fmt,magic,mime", [
    ("docx", b"PK", "officedocument.wordprocessingml"),
    ("pptx", b"PK", "officedocument.presentationml"),
    ("pdf", b"%PDF", "application/pdf"),
])
def test_download_rendered_formats(done_task, fmt, magic, mime):
    r = client.get(f"/api/v1/tasks/{done_task}/report?format={fmt}")
    assert r.status_code == 200, r.text
    assert mime in r.headers["content-type"]
    assert r.content[:4].startswith(magic), f"{fmt} 产出不是真文件"


def test_default_format_is_md(done_task):
    r = client.get(f"/api/v1/tasks/{done_task}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")


def test_content_disposition_carries_utf8_filename(done_task):
    r = client.get("/api/v1/tasks/t-1/report?format=pdf")
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert "filename*=" in cd, "中文主题须走 RFC 5987 UTF-8 名，否则乱码"
    assert "%E9%94%82" in cd or "UTF-8''" in cd


# ---------------------------------------------------------------- 错误路径
def test_unsupported_format_returns_400(done_task):
    r = client.get("/api/v1/tasks/t-1/report?format=rtf")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "UNSUPPORTED_FORMAT"


def test_running_task_returns_409(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task",
                        lambda tid: _FakeTask(api_mod.TaskStatus.RUNNING, MD))
    r = client.get("/api/v1/tasks/t-1/report?format=docx")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "TASK_NOT_COMPLETED"


def test_escalated_task_can_download(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task",
                        lambda tid: _FakeTask(api_mod.TaskStatus.ESCALATED, MD))
    r = client.get("/api/v1/tasks/t-1/report?format=md")
    assert r.status_code == 200


def test_foreign_task_returns_404(monkeypatch):
    def boom(tid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"error": "TASK_NOT_FOUND", "message": "任务不存在"})
    monkeypatch.setattr(api_mod, "_owned_task", boom)
    r = client.get("/api/v1/tasks/t-1/report?format=docx")
    assert r.status_code == 404


# ---------------------------------------------------------------- 审计包（OR-4）
def _audit_task(st):
    from datetime import datetime
    from types import SimpleNamespace
    ut = api_mod.UserTask(topic="锂电池", scope=["供需"], output_format_spec="Markdown")
    gr = api_mod.GateReview(decision="advance", reason="ok", eval_score=0.9,
                            problem_points=[], gate="GateA", round=1,
                            timestamp="2026-09-17T20:00:00")
    ee = api_mod.EngineEvent(event="researcher_records_synthesized", agent="Researcher",
                             reason="ok", round=1, timestamp="2026-09-17T20:01:00")
    rs = SimpleNamespace(gate_review_history=[gr], engine_events=[ee])
    return SimpleNamespace(
        task_id="t-1", status=st, created_at=datetime(2026, 9, 17, 20, 0),
        updated_at=datetime(2026, 9, 17, 20, 5), user_task=ut, routing_state=rs,
        report_markdown=MD, prior_versions={"v1": "old"}, retrieval_records=[{"id": "rec-1"}],
        analysis_conclusions=[], draft_segments=[], owner_id="acc-1",
    )


def test_audit_export_returns_real_zip(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task",
                        lambda tid: _audit_task(api_mod.TaskStatus.DONE))
    r = client.get("/api/v1/tasks/t-1/audit-export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    import io, zipfile, json
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    for must in ("report.md", "gate_review_history.json", "prior_versions.json",
                 "user_task.json", "engine_events.json", "metadata.json"):
        assert must in names, f"审计包缺 {must}"
    assert zf.read("report.md").decode("utf-8") == MD
    meta = json.loads(zf.read("metadata.json"))
    assert meta["task_id"] == "t-1"
    # status 必须是可序列化字符串（枚举会 TypeError）
    assert isinstance(meta["status"], str)


def test_audit_export_gate_history_serialized(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task",
                        lambda tid: _audit_task(api_mod.TaskStatus.ESCALATED))
    r = client.get("/api/v1/tasks/t-1/audit-export")
    import io, zipfile, json
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    hist = json.loads(zf.read("gate_review_history.json"))
    assert hist[0]["gate"] == "GateA"
    # 时间戳是字符串（TaskResponse 侧）也必须能进 JSON，不能 AttributeError
    assert "timestamp" in hist[0]


def test_audit_export_running_task_409(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task",
                        lambda tid: _audit_task(api_mod.TaskStatus.RUNNING))
    r = client.get("/api/v1/tasks/t-1/audit-export")
    assert r.status_code == 409


def test_renderer_unavailable_returns_503_not_empty_file(monkeypatch):
    monkeypatch.setattr(api_mod, "_owned_task", lambda tid: _FakeTask(api_mod.TaskStatus.DONE, MD))
    def boom(blocks, meta):
        raise RendererUnavailable("pdf 渲染不可用（缺 reportlab：模拟）")
    monkeypatch.setitem(api_mod._REPORT_RENDERERS, "pdf", boom)
    r = client.get("/api/v1/tasks/t-1/report?format=pdf")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "RENDERER_UNAVAILABLE"
    assert len(r.content) and r.json()  # 必须是有内容的错误体，绝不是空字节冒充成功
