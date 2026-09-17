"""M12-1 Skill Importer 单测（验收 V1/V4/V5/V6）。

全部用 tmp_path 接管模块级路径常量，零污染真实 config/skills.yaml。
网络用 monkeypatch 替换 fetch_source，不触网。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import skill_importer as si  # noqa: E402
from tools import skills as sk  # noqa: E402


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
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
    # 让引擎层 build_skill_context 也读 tmp（V1 真实可读验证）
    monkeypatch.setattr(sk, "BASE", tmp_path)
    monkeypatch.setattr(sk, "CONFIG_DIR", cfg)
    monkeypatch.setattr(sk, "SKILLS_PATH", cfg / "skills.yaml")
    return tmp_path


def _fake_source(text="# Demo\n\n纯指令技能，无代码无外链。\n",
                 url="https://example.com/skill.md"):
    return {"text": text, "source_type": "raw_md", "final_url": url,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def test_classify_levels():
    assert si.classify("纯指令文本，无代码无 URL。", "raw_md")[0] == "L0"
    lvl, reasons = si.classify("见 https://api.x.com/docs 调用。", "raw_md")
    assert lvl == "L1"
    assert any("外部 URL" in r for r in reasons)
    # 真实执行意图 → L2（硬拒）
    lvl2, _ = si.classify("用 subprocess([\"rm\"]) 清理。", "raw_md")
    assert lvl2 == "L2"


def test_code_block_downgraded_to_l1():
    """2026-09-15 修正：惰性代码块不再一律 L2。

    本引擎只把技能 md 拼进 system prompt、从不执行其中代码，故代码块降到 L1
    （仍须人工审批才落盘），而不是拒收——旧策略导致第三方 agent 定义一概装不进来。
    """
    lvl, reasons = si.classify("运行脚本：\n```bash\nrm -rf /\n```\n", "raw_md")
    assert lvl == "L1"
    assert any("代码块" in r for r in reasons)
    assert any("不执行外部代码" in r for r in reasons)
    # 语言标签不在检测列表里的围栏代码块：保持既不升级也不静默
    lvl2, _ = si.classify("示例：\n```\nSELECT * FROM t;\n```\n", "raw_md")
    assert lvl2 in ("L0", "L1")


def test_validate_spec_rejects_bad():
    good = {"id": "my-skill", "level": "L0", "target": "skill",
            "target_roles": ["researcher"], "name": "x",
            "payload": {"markdown": "body"}}
    assert si.validate_spec(good) == []
    assert si.validate_spec(dict(good, id="Bad Id"))          # 非法 id
    assert si.validate_spec(dict(good, target="nope"))        # 非法 target
    assert si.validate_spec(dict(good, target_roles=["ceo"])) # 非法 role
    assert si.validate_spec(dict(good, target="tool",
                                 payload={"endpoint": "ftp://x"}))  # 非法 endpoint


def test_l0_install_and_engine_reads(tmp_paths):
    src = _fake_source()
    lvl, reasons = si.classify(src["text"], src["source_type"])
    spec = si.build_spec(src, lvl, reasons, sid="demo-skill", target_roles=["researcher"])
    res = si.install_spec(spec, operator="test")
    assert res["status"] == "installed"
    # V1：引擎 build_skill_context 真实读到
    ctx = sk.build_skill_context("researcher")
    assert "【已启用技能" in ctx and "纯指令技能" in ctx
    # V4：回滚后 prompt 中消失
    rb = si.rollback("demo-skill", operator="test")
    assert rb["status"] == "rolled_back"
    assert "【已启用技能" not in sk.build_skill_context("researcher")


def test_l1_proposal_accept_reject(tmp_paths):
    src = _fake_source("见 https://api.x.com/docs 调用外部。\n")
    lvl, reasons = si.classify(src["text"], src["source_type"])
    assert lvl == "L1"
    spec = si.build_spec(src, lvl, reasons, sid="l1-skill", target_roles=["analyst"])
    pid = si.save_proposal(spec)
    assert any(p["id"] == pid for p in si.list_proposals())
    acc = si.accept_proposal(pid, operator="test")
    assert acc["status"] == "installed"
    assert not si.list_proposals()  # 采纳后提案消失

    pid2 = si.save_proposal(spec)
    rej = si.reject_proposal(pid2, operator="test")
    assert rej["status"] == "rejected"
    assert not (tmp_paths / ".audit" / "skill_proposals" / f"{pid2}.json").exists()


def test_idempotent(tmp_paths):
    src = _fake_source()
    lvl, reasons = si.classify(src["text"], src["source_type"])
    spec = si.build_spec(src, lvl, reasons, sid="idem", target_roles=["writer"])
    assert si.install_spec(spec)["status"] == "installed"
    assert si.install_spec(spec)["status"] == "unchanged"  # V6 幂等


def test_import_l0_installs_via_api(monkeypatch, tmp_paths):
    monkeypatch.setattr(si, "fetch_source", lambda url: _fake_source())
    res = si.import_skill("https://example.com/skill.md", operator="test",
                          target_roles=["researcher"])
    assert res["status"] == "installed"
    assert res["level"] == "L0"


def test_import_l2_rejected_zero_disk(monkeypatch, tmp_paths):
    # 真实执行意图（shebang + subprocess）→ 硬拒且零落盘
    monkeypatch.setattr(si, "fetch_source",
                        lambda url: _fake_source("#!/bin/bash\n调用 os.system(\"ls\")。\n"))
    res = si.import_skill("https://example.com/x.md", operator="test")
    assert res["status"] == "rejected"
    assert res["level"] == "L2"
    # 零落盘：skills.yaml 未被创建
    assert not (tmp_paths / "config" / "skills.yaml").exists()


def test_code_only_import_goes_to_proposal(monkeypatch, tmp_paths):
    """含代码块的第三方 agent 定义：不再被拒收，而是落 L1 提案等审批，审批后可安装。"""
    agent_md = (
        "---\nname: Investment Researcher\ndescription: 深度财务分析\ncolor: green\n---\n"
        "# Investment Researcher\n\n先跑数据抓取：\n```bash\npip install pandas\n```\n"
        "再建估值模型。\n"
    )
    monkeypatch.setattr(si, "fetch_source",
                        lambda url: _fake_source(agent_md,
                                                 "https://raw.githubusercontent.com/o/r/main/f.md"))
    res = si.import_skill("https://raw.githubusercontent.com/o/r/main/f.md",
                          operator="test", sid="investment-researcher")
    assert res["status"] == "pending_approval", res
    assert res["level"] == "L1"
    # 打架点的安装路径：采纳后引擎真能读到
    acc = si.accept_proposal(res["id"], operator="test")
    assert acc["status"] == "installed"
    ctx = sk.build_skill_context("researcher")
    assert "【已启用技能" in ctx and "Investment Researcher" in ctx


def test_inline_command_now_l1():
    """行首命令式代码同样降为 L1；真实执行意图仍 L2。"""
    lvl, reasons = si.classify("运行：\n```\npython train.py --epochs 5\n```\n", "raw_md")
    assert lvl == "L1"
    assert any("不执行外部代码" in r for r in reasons)
    lvl2, _ = si.classify("bash ./setup.sh 初始化。", "raw_md")
    assert lvl2 == "L1"
    # 真实执行信号 → 仍 L2，不可放宽
    assert si.classify("请 eval(\"1+1\") 求值。", "raw_md")[0] == "L2"
    assert si.classify("allowed-tools: Bash(rm)", "raw_md")[0] == "L2"
    # 普通散文里的「python」不当行首命令 → 仍 L0/L1
    lvl3, _ = si.classify("我们用 python 写脚本，详见 https://x.com/docs。", "raw_md")
    assert lvl3 != "L2"


def test_discover_candidates(monkeypatch):
    """GitHub Trees API 列目录：过滤目录类文件、去重排序、限流/截断即返回空。"""
    tree = {"truncated": False, "tree": [
        {"path": "README.md", "type": "blob"},
        {"path": ".github/workflows/ci.md", "type": "blob"},
        {"path": "docs/guide.md", "type": "tree"},
        {"path": "finance/investment-researcher.md", "type": "blob"},
        {"path": "engineering/backend.md", "type": "blob"},
        {"path": "LICENSE", "type": "blob"},
    ]}
    payload = json.dumps(tree).encode("utf-8")

    calls = []

    def fake_get(url, timeout=15):
        calls.append(url)
        return payload, "application/json"

    monkeypatch.setattr(si, "_http_get", fake_get)
    out = si.discover_candidates("https://github.com/msitarzewski/agency-agents.git")
    paths = [c["path"] for c in out]
    assert paths == ["engineering/backend.md", "finance/investment-researcher.md"]
    assert out[0]["raw_url"].startswith(
        "https://raw.githubusercontent.com/msitarzewski/agency-agents/")
    assert "agency-agents.git" not in out[0]["raw_url"]  # .git 必须被剥离
    assert out[0]["branch"] == "HEAD"

    # limit 生效
    assert len(si.discover_candidates("https://github.com/o/r", limit=1)) == 1

    # 截断 → 宁缺毋滥，返回空
    monkeypatch.setattr(si, "_http_get",
                        lambda url, timeout=15: (json.dumps(
                            {"truncated": True, "tree": []}).encode(), "application/json"))
    assert si.discover_candidates("https://github.com/o/r") == []

    # API 不可达 → 返回空且**不抛**（重试结束后）
    slept = []
    monkeypatch.setattr(si.time, "sleep", lambda s: slept.append(s))

    def boom(url, timeout=15):
        raise si.SkillImportError("HTTP 403")

    monkeypatch.setattr(si, "_http_get", boom)
    assert si.discover_candidates("https://github.com/o/r") == []
    assert len(slept) > 0  # 出网抖动要重试，单次失败不得一棍子打死


def test_malformed_id_path_traversal_blocked(tmp_paths):
    # M1/M2 回归：任意恶意 id 经 _norm_id 后路径必在 PROPOSAL_DIR 内
    # 注意用 resolve()：is_relative_to 是词法的，对 ../../ 会假通过
    for evil in ("../../evil", "../evil", "a/../../b", "..\\..\\evil"):
        p = si._proposal_path(evil)
        assert p.resolve().is_relative_to(si.PROPOSAL_DIR.resolve()), \
            f"{evil} 逃出提案目录: {p}"
    # 真实越界读验证：在 BASE 根放陷阱文件，get_proposal('../../escaped') 不应读到它
    trap = tmp_paths / "escaped.json"
    trap.write_text("SECRET-OUTSIDE", encoding="utf-8")
    assert si.get_proposal("../../escaped") is None  # 读的是白名单内，读不到陷阱
    trap.unlink()
    # save_proposal 落盘文件名也被规范化
    spec = {"id": "../../evil", "level": "L1", "target": "skill",
            "target_roles": ["researcher"], "name": "x",
            "payload": {"markdown": "body"}}
    pid = si.save_proposal(spec)
    assert pid == "evil"  # ../ 被剥离
    assert (tmp_paths / ".audit" / "skill_proposals" / "evil.json").exists()


def test_install_spec_malformed_id_contained(tmp_paths):
    # M1/M2 回归：build_spec/install_spec 对恶意 sid 规范化，md 不逃出 SKILLS_DIR
    src = _fake_source()
    lvl, reasons = si.classify(src["text"], src["source_type"])
    spec = si.build_spec(src, lvl, reasons, sid="../../evil", target_roles=["researcher"])
    assert spec["id"] == "evil"
    res = si.install_spec(spec, operator="test")
    assert res["status"] == "installed"
    assert (tmp_paths / "skills" / "evil.md").exists()  # 落在白名单内
    rb = si.rollback("../../evil", operator="test")     # rollback 同样规范化
    assert rb["status"] == "rolled_back"
