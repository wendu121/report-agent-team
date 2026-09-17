"""M12-2 管控 API 端到端（FastAPI TestClient，不触网）。

只挂 server.admin.router（不拉起 orchestrator），用 monkeypatch 接管
skill_importer / experts / skills 的模块级路径与 fetch_source。
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import admin as admin_mod  # noqa: E402
from tools import skill_importer as si  # noqa: E402
from tools import experts as ex  # noqa: E402
from tools import skills as sk  # noqa: E402

app = FastAPI()
app.include_router(admin_mod.router, prefix="/api/v1")
client = TestClient(app)


@pytest.fixture
def api_paths(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    audit = tmp_path / ".audit"
    monkeypatch.setattr(si, "BASE", tmp_path)
    monkeypatch.setattr(si, "CONFIG_DIR", cfg)
    monkeypatch.setattr(si, "SKILLS_PATH", cfg / "skills.yaml")
    monkeypatch.setattr(si, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(si, "AUDIT_DIR", audit)
    monkeypatch.setattr(si, "PROPOSAL_DIR", audit / "skill_proposals")
    monkeypatch.setattr(si, "BACKUP_DIR", audit / "skill_backups")
    monkeypatch.setattr(si, "IMPORT_LOG", audit / "skill_imports.jsonl")
    monkeypatch.setattr(si, "ALLOWLIST_PATH", cfg / "skill_import_allowlist.yaml")
    monkeypatch.setattr(ex, "BASE", tmp_path)
    monkeypatch.setattr(ex, "CONFIG_DIR", cfg)
    monkeypatch.setattr(ex, "EXPERT_DIR", tmp_path / "experts")
    monkeypatch.setattr(ex, "REGISTRY_PATH", cfg / "experts.yaml")
    monkeypatch.setattr(ex, "AUDIT_DIR", audit)
    monkeypatch.setattr(ex, "EXPERT_LOG", audit / "experts.jsonl")
    monkeypatch.setattr(ex, "PROPOSAL_DIR", audit / "expert_proposals")
    # 让 admin 端点里的 load_skills / build_skill_context 也读 tmp
    monkeypatch.setattr(sk, "BASE", tmp_path)
    monkeypatch.setattr(sk, "CONFIG_DIR", cfg)
    monkeypatch.setattr(sk, "SKILLS_PATH", cfg / "skills.yaml")
    return tmp_path


def test_import_l0_via_api(api_paths, monkeypatch):
    monkeypatch.setattr(
        si, "fetch_source",
        lambda url: {"text": "# API Skill\n\n纯指令。\n", "source_type": "raw_md",
                     "final_url": url, "sha256": "a"})
    r = client.post("/api/v1/admin/skills/import",
                    json={"url": "https://x/skill.md", "target_roles": ["researcher"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["status"] == "installed"
    sid = body["id"]
    # 列表里有
    lst = client.get("/api/v1/admin/skills")
    assert lst.status_code == 200
    assert any(s["id"] == sid for s in lst.json()["items"])
    # 回滚（V4）
    rb = client.post(f"/api/v1/admin/skills/{sid}/rollback")
    assert rb.status_code == 200 and rb.json()["status"] == "rolled_back"
    lst2 = client.get("/api/v1/admin/skills")
    assert not any(s["id"] == sid for s in lst2.json()["items"])


def test_experts_endpoints(api_paths, monkeypatch):
    g = client.get("/api/v1/admin/experts")
    assert g.status_code == 200 and g.json()["count"] == 0
    # toggle 不存在 → 400
    t = client.post("/api/v1/admin/experts/nope/toggle", json={"enabled": True})
    assert t.status_code == 400
    # convert 提案
    monkeypatch.setattr(
        si, "fetch_source",
        lambda url: {"text": "# 合同专家\n\n审查合同条款。\n", "source_type": "raw_md",
                     "final_url": url, "sha256": "b"})
    c = client.post("/api/v1/admin/experts/convert",
                    json={"url": "https://x/c.md", "id": "contract-expert", "shape": "analyst"})
    assert c.status_code == 200 and c.json()["status"] == "pending_approval"
    pid = c.json()["id"]
    pl = client.get("/api/v1/admin/experts/proposals")
    assert pl.status_code == 200 and any(p["id"] == pid for p in pl.json()["items"])
    # 采纳（V3）
    acc = client.post(f"/api/v1/admin/experts/proposals/{pid}/accept")
    assert acc.status_code == 200 and acc.json()["status"] == "installed"
    g2 = client.get("/api/v1/admin/experts")
    assert any(i["id"] == "contract-expert" for i in g2.json()["items"])
    # 启停
    tg = client.post("/api/v1/admin/experts/contract-expert/toggle", json={"enabled": False})
    assert tg.status_code == 200 and tg.json()["enabled"] is False
