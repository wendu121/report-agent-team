"""M12-3 专家团单测（验收 V3/V4/V7/V8）。

tmp_path 接管 tools/experts 模块路径；convert 的网络用 monkeypatch 替换
skill_importer.fetch_source。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import experts as ex  # noqa: E402
from tools import skill_importer as si  # noqa: E402


@pytest.fixture
def exp_paths(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    audit = tmp_path / ".audit"
    # 同 test_m12_skill_importer：多租户改造后真实解析走惰性函数
    # （_root/_registry_path/_expert_dir/...），旧常量虽在但已无人消费——
    # 打常量会「改了个寂寞」（注册写进租户根，测试却读 tmp_path 下的包）。
    monkeypatch.setattr(ex, "_root", lambda: tmp_path)
    monkeypatch.setattr(ex, "_registry_path", lambda: cfg / "experts.yaml")
    monkeypatch.setattr(ex, "_expert_dir", lambda: tmp_path / "experts")
    monkeypatch.setattr(ex, "_audit_dir", lambda: audit)
    monkeypatch.setattr(ex, "_expert_log", lambda: audit / "experts.jsonl")
    monkeypatch.setattr(ex, "_proposal_dir", lambda: audit / "expert_proposals")
    return tmp_path


def _write_pkg(root: Path, eid="fin-expert", body="分析财报。\n"):
    (root / "agents").mkdir(parents=True, exist_ok=True)
    plugin = {"name": eid, "expertType": "agent", "agentName": eid,
              "displayName": {"en": eid, "zh": eid},
              "profession": {"en": "fin", "zh": "财务"},
              "categoryId": "09-FinanceAccounting", "tags": [eid]}
    (root / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False), encoding="utf-8")
    (root / "agents" / f"{eid}.md").write_text(
        f"---\nname: {eid}\n---\n{body}", encoding="utf-8")


def test_register_list_enable(exp_paths):
    _write_pkg(exp_paths / "experts" / "fin-expert")
    res = ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst", operator="test")
    assert res["status"] == "registered"
    items = ex.list_experts(enabled_only=False)
    assert any(i["id"] == "fin-expert" for i in items)
    ex.set_enabled("fin-expert", False, operator="test")
    assert not ex.list_experts(enabled_only=True)
    assert ex.list_experts(enabled_only=False)


def test_route_fallback_when_low_confidence(exp_paths):
    # 无专家 → 回退 None（V7）
    assert ex.route("今天天气怎么样") is None
    _write_pkg(exp_paths / "experts" / "fin-expert",
               body="财务报表分析 现金流 利润表 资产负债表")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")
    # 无关 query → 仍回退 None（保守：命中 < 2）
    assert ex.route("如何做一顿饭") is None
    # 相关 query 且 >= 2 命中 → 选中
    assert ex.route("分析财务报表的现金流") == "fin-expert"


def test_resolve_mention(exp_paths):
    _write_pkg(exp_paths / "experts" / "fin-expert")
    ex.register_expert("fin-expert", "experts/fin-expert")
    assert ex.resolve_mention("请 @fin-expert 看一下") == "fin-expert"
    assert ex.resolve_mention("没有提到专家") is None


def test_convert_proposal_accept(exp_paths, monkeypatch):
    monkeypatch.setattr(
        si, "fetch_source",
        lambda url: {"text": "# 财报专家\n\n分析三大报表。\n", "source_type": "raw_md",
                     "final_url": url, "sha256": "x"})
    prop = {"id": "conv-expert", "name": "财报专家", "source_url": "https://x/y.md",
            "shape": "analyst", "expert_type": "agent", "agent_body": "分析三大报表。",
            "allow_team_downgrade": False}
    pid = ex.save_expert_proposal(prop)
    assert pid == "conv-expert"
    # V3：审批 → 包体真实落盘 + 入团
    acc = ex.accept_expert_proposal(pid, operator="test")
    assert acc["status"] == "installed"
    assert (exp_paths / "experts" / "conv-expert" / "plugin.json").exists()
    assert (exp_paths / "experts" / "conv-expert" / "agents" / "conv-expert.md").exists()
    assert ex.get_expert("conv-expert") is not None
    assert not ex.list_expert_proposals()  # 采纳后清空


def test_team_expert_rejected_by_default(exp_paths):
    # expertType=team 且未 allow_team_downgrade → read_expert 抛错（诚实边界 §5.6）
    root = exp_paths / "experts" / "team-expert"
    (root / "agents").mkdir(parents=True, exist_ok=True)
    plugin = {"name": "team-expert", "expertType": "team", "agentName": "team-expert",
              "displayName": {"en": "t", "zh": "t"}, "profession": {"en": "t", "zh": "t"},
              "categoryId": "12-IndustryConsultant", "tags": ["t"]}
    (root / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False), encoding="utf-8")
    (root / "agents" / "team-expert.md").write_text(
        "---\nname: team-expert\n---\n多角色协作。\n", encoding="utf-8")
    ex.register_expert("team-expert", "experts/team-expert")
    with pytest.raises(ex.ExpertError):
        ex.get_expert("team-expert")


def test_malformed_id_proposal_path_traversal_blocked(exp_paths):
    # M1/M2 回归：专家提案 id 含 ../ 必须被规范化，不逃出 PROPOSAL_DIR
    for evil in ("../../evil", "../evil", "a/../../b", "..\\..\\evil"):
        p = ex._expert_proposal_path(evil)
        assert p.resolve().is_relative_to(ex._proposal_dir().resolve()), \
            f"{evil} 逃出提案目录: {p}"
    # 真实越界读验证：在 BASE 根放陷阱文件，get_expert_proposal('../../escaped') 不应读到
    trap = exp_paths / "escaped.json"
    trap.write_text("SECRET-OUTSIDE", encoding="utf-8")
    assert ex.get_expert_proposal("../../escaped") is None
    trap.unlink()
    prop = {"id": "../../evil", "name": "e", "source_url": "u",
            "shape": "analyst", "expert_type": "agent", "agent_body": "分析。"}
    pid = ex.save_expert_proposal(prop)
    assert pid == "evil"
    assert (exp_paths / ".audit" / "expert_proposals" / "evil.json").exists()


def test_write_package_malformed_id_contained(exp_paths):
    # M2 回归：恶意 id 经 _norm_id 后包目录必在 EXPERT_DIR 内
    pkg = ex._write_package({"id": "../../evil", "name": "e",
                             "expert_type": "agent", "agent_body": "分析三大报表。"})
    root = exp_paths / "experts" / "evil"
    assert (root / "plugin.json").exists()
    assert (root / "agents" / "evil.md").exists()
