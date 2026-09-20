"""tools/review_queue 单测（纯文件 IO，不依赖 web 上下文，宿主 pytest 可跑）。

租户隔离通过 monkeypatch review_queue._tenant_root 实现。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tools.review_queue as rq


@pytest.fixture
def tenant_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "_tenant_root", lambda: tmp_path)
    return tmp_path


def test_upsert_new_and_preserves_decision_on_rerun(tenant_tmp):
    rq.upsert_pending_push({"id": "cs-1", "title": "x", "commits": ["a"],
                            "gate": {"status": "PASS_WITH_NOTES"}})
    rq.approve("cs-1", "boss")  # 老板已批准
    # gate 重跑触发再次 upsert（同 id，新内容）——决策字段必须保留
    rq.upsert_pending_push({"id": "cs-1", "title": "x2", "commits": ["a", "b"]})
    got = rq.get_item("cs-1")
    assert got["decision"] == "approved"
    assert got["approved_by"] == "boss"
    assert got["title"] == "x2"        # 非决策字段被新值覆盖
    assert got["commits"] == ["a", "b"]


def test_flow_approve_reject_markpushed(tenant_tmp):
    rq.upsert_pending_push({"id": "cs-2", "title": "y"})
    rq.approve("cs-2", "boss")
    assert rq.get_item("cs-2")["decision"] == "approved"
    rq.reject("cs-2", "not now")
    assert rq.get_item("cs-2")["decision"] == "rejected"
    assert rq.get_item("cs-2")["reject_reason"] == "not now"
    rq.mark_pushed("cs-2", "origin/master")
    assert rq.get_item("cs-2")["decision"] == "pushered"
    assert rq.get_item("cs-2")["pushed_ref"] == "origin/master"


def test_missing_item_raises(tenant_tmp):
    with pytest.raises(rq.ReviewQueueError):
        rq.approve("nope", "boss")
    with pytest.raises(rq.ReviewQueueError):
        rq.reject("nope", "r")
    with pytest.raises(rq.ReviewQueueError):
        rq.mark_pushed("nope", "ref")


def test_list_and_get(tenant_tmp):
    rq.upsert_pending_push({"id": "a", "title": "A"})
    rq.upsert_pending_push({"id": "b", "title": "B"})
    assert [i["id"] for i in rq.list_items()] == ["a", "b"]
    assert rq.get_item("b")["title"] == "B"
    assert rq.get_item("missing") is None


def test_tenant_isolation(tmp_path, monkeypatch):
    ra = tmp_path / "ta"
    rb = tmp_path / "tb"
    monkeypatch.setattr(rq, "_tenant_root", lambda: ra)
    rq.upsert_pending_push({"id": "cs-x", "title": "A"})
    monkeypatch.setattr(rq, "_tenant_root", lambda: rb)
    assert rq.list_items() == []
    assert rq.get_item("cs-x") is None
    rq.upsert_pending_push({"id": "cs-y", "title": "B"})
    assert [i["id"] for i in rq.list_items()] == ["cs-y"]


def test_persists_across_reloads(tenant_tmp):
    rq.upsert_pending_push({"id": "cs-z", "title": "Z", "commits": ["c1"]})
    # 重新读盘（模拟新进程）
    data = rq._load()
    assert data["items"][0]["id"] == "cs-z"
    assert data["items"][0]["commits"] == ["c1"]
