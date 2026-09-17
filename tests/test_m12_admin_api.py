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
    # 多租户改造后路径解析改走惰性函数（旧模块级常量已删除或已无人消费），
    # monkeypatch 必须打在函数上，否则 AttributeError 或「改了个寂寞」。
    monkeypatch.setattr(si, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(si, "_config_dir", lambda: cfg)
    monkeypatch.setattr(si, "_skills_yaml", lambda: cfg / "skills.yaml")
    monkeypatch.setattr(si, "_skills_dir", lambda: tmp_path / "skills")
    monkeypatch.setattr(si, "_audit_dir", lambda: audit)
    monkeypatch.setattr(si, "_proposal_dir", lambda: audit / "skill_proposals")
    monkeypatch.setattr(si, "_backup_dir", lambda: audit / "skill_backups")
    monkeypatch.setattr(si, "_import_log", lambda: audit / "skill_imports.jsonl")
    monkeypatch.setattr(si, "_allowlist_path", lambda: cfg / "skill_import_allowlist.yaml")
    monkeypatch.setattr(ex, "_root", lambda: tmp_path)
    monkeypatch.setattr(ex, "_registry_path", lambda: cfg / "experts.yaml")
    monkeypatch.setattr(ex, "_expert_dir", lambda: tmp_path / "experts")
    monkeypatch.setattr(ex, "_audit_dir", lambda: audit)
    monkeypatch.setattr(ex, "_expert_log", lambda: audit / "experts.jsonl")
    monkeypatch.setattr(ex, "_proposal_dir", lambda: audit / "expert_proposals")
    # 让 admin 端点里的 load_skills / build_skill_context 也读 tmp
    monkeypatch.setattr(sk, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(sk, "_config_dir", lambda: cfg)
    monkeypatch.setattr(sk, "_skills_dir", lambda: tmp_path / "skills")
    # 多租户改造堵掉了 dev_no_token 放行缺口（R7）：admin 端点现在必须带凭据，
    # 否则 401。测试用显式后门令牌（ADMIN_TOKEN 是模块级常量，打常量即可生效）。
    monkeypatch.setattr(admin_mod, "ADMIN_TOKEN", "test-admin-token")
    client.headers.update({"X-Admin-Token": "test-admin-token"})
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
