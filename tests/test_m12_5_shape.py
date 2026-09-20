"""M12-5 专家 shape 接入研报流水线单测（DESIGN_M12-5 §4 验收 V1-V6）。

不触网、不打真 LLM：一次性租户根隔离（mirror M12-4 fixtures），
直接调 build_pipeline_system / _resolve_agent_model 验证 shape 过滤 + 模型消费。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import (  # noqa: E402
    build_pipeline_system,
    _resolve_agent_model,
    load_agent_registry,
)
from tools import experts as ex  # noqa: E402


@pytest.fixture
def m12_5_env(tmp_path, monkeypatch):
    """一次性租户根：只拷 agent 提示词 + 智能体库（不拷 experts/，零真实专家干扰）。"""
    import server.tenancy as t

    monkeypatch.setattr(t, "TENANTS_ROOT", tmp_path)
    t.set_current_account("m12_5")
    root = tmp_path / "m12_5"
    for d in ("agents", "config", "experts", "skills", ".audit"):
        (root / d).mkdir(parents=True, exist_ok=True)
    if (ROOT / "agents").exists():
        shutil.copytree(ROOT / "agents", root / "agents", dirs_exist_ok=True)
    if (ROOT / "config" / "agents_library.yaml").exists():
        shutil.copy2(ROOT / "config" / "agents_library.yaml",
                     root / "config" / "agents_library.yaml")
    yield root
    t.reset_current_account()


def _write_pkg(root: Path, eid="fin-expert", body="财务分析。\n", expert_type="agent",
               display_zh=None):
    (root / "agents").mkdir(parents=True, exist_ok=True)
    plugin = {
        "name": eid,
        "expertType": expert_type,
        "agentName": eid,
        "displayName": {"en": eid, "zh": display_zh or eid},
        "profession": {"en": "fin", "zh": "财务"},
        "categoryId": "09-FinanceAccounting",
        "tags": [eid],
    }
    (root / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False), encoding="utf-8")
    (root / "agents" / f"{eid}.md").write_text(
        f"---\nname: {eid}\n---\n{body}", encoding="utf-8")


# --------------------------------------------------------------------------
# V1：显式 @ + shape 过滤
# --------------------------------------------------------------------------
def test_v1_explicit_mention_shape_filter(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "fin-expert", body="杜邦分解三步法。\n")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")
    reg = load_agent_registry()
    sys_a, meta_a = build_pipeline_system("Analyst", reg, user_text="@fin-expert 分析这份财报")
    sys_r, meta_r = build_pipeline_system("Researcher", reg, user_text="@fin-expert 分析这份财报")
    assert meta_a is not None and meta_a["shape"] == "analyst"
    assert "【专家：" in sys_a
    assert "杜邦分解三步法" in sys_a
    # shape 过滤器：analyst 专家绝不进 Researcher 节点
    assert meta_r is not None and meta_r["shape"] == "analyst"
    assert "【专家：" not in sys_r


# --------------------------------------------------------------------------
# V2：隐式 route（高置信）
# --------------------------------------------------------------------------
def test_v2_implicit_route(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "fin-expert",
               body="财报 利润 负债 现金流 三大报表 杜邦分析")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")
    reg = load_agent_registry()
    sys_a, meta_a = build_pipeline_system(
        "Analyst", reg, user_text="分析这份财报的现金流和利润")
    assert meta_a is not None and meta_a["routed"] is True
    assert "【专家：" in sys_a


# --------------------------------------------------------------------------
# V3：低置信 → 无专家注入，与基线逐字节一致（守回归）
# --------------------------------------------------------------------------
def test_v3_low_confidence_no_regression(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "fin-expert", body="财报 利润 负债")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")
    reg = load_agent_registry()
    baseline, _ = build_pipeline_system("Analyst", reg, user_text=None)
    sys_, meta = build_pipeline_system("Analyst", reg, user_text="如何做一顿饭")
    assert meta is None
    assert "【专家：" not in sys_
    # 守住「无专家时行为不变」：含注册专家但低置信文本 → 仍不注入，等于基线
    assert sys_ == baseline


# --------------------------------------------------------------------------
# V4：shape 错配 → 不注入（writer 专家不污染 Researcher 节点）
# --------------------------------------------------------------------------
def test_v4_shape_mismatch_not_injected(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "writer-expert", body="创意写作方法论。\n")
    ex.register_expert("writer-expert", "experts/writer-expert", shape="writer")
    reg = load_agent_registry()
    sys_r, meta_r = build_pipeline_system(
        "Researcher", reg, user_text="@writer-expert 写个大纲")
    assert meta_r is not None and meta_r["shape"] == "writer"
    assert "【专家：" not in sys_r


# --------------------------------------------------------------------------
# V5：模型消费（model_override > 专家 model > role→model）
# --------------------------------------------------------------------------
def test_v5_model_priority():
    models = {"roles": {
        "Analyst": {"model": "role-default"},
        "Researcher": {"model": "researcher-default"},
    }}
    meta = {"shape": "analyst", "model": "longcat-foo"}
    # 专家 model 生效（shape 匹配）
    assert _resolve_agent_model(None, meta, "analyst", models, "Analyst") == "longcat-foo"
    # model_override 优先于专家 model
    assert _resolve_agent_model("user-override", meta, "analyst", models, "Analyst") == "user-override"
    # shape 不匹配 → 用 role 默认
    assert _resolve_agent_model(None, meta, "researcher", models, "Researcher") == "researcher-default"
    # 无专家 → role 默认
    assert _resolve_agent_model(None, None, "analyst", models, "Analyst") == "role-default"


def test_v5_expert_model_in_pipeline(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "fin-expert", body="财报分析。\n")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst", model="longcat-foo")
    reg = load_agent_registry()
    _sys_a, meta_a = build_pipeline_system("Analyst", reg, user_text="@fin-expert 分析财报")
    assert meta_a is not None and meta_a["model"] == "longcat-foo"


# --------------------------------------------------------------------------
# V6：包损坏 → 降级不注入、不抛
# --------------------------------------------------------------------------
def test_v6_broken_package_degrades(m12_5_env):
    pkg = m12_5_env / "experts" / "fin-expert"
    _write_pkg(pkg)
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")
    (pkg / "plugin.json").unlink()  # 包损坏
    reg = load_agent_registry()
    sys_a, meta_a = build_pipeline_system("Analyst", reg, user_text="@fin-expert 分析财报")
    assert meta_a is None
    assert "【专家：" not in sys_a


# --------------------------------------------------------------------------
# V7：team 型专家在流水线侧显式拒绝（默认拒绝，不冒充可用）
# --------------------------------------------------------------------------
def test_v7_team_expert_rejected(m12_5_env):
    _write_pkg(m12_5_env / "experts" / "team-expert", expert_type="team")
    ex.register_expert("team-expert", "experts/team-expert")
    reg = load_agent_registry()
    sys_a, meta_a = build_pipeline_system("Analyst", reg, user_text="@team-expert 帮我协作分析")
    assert meta_a is None
    assert "【专家：" not in sys_a
    # 降级不抛、对话/流水线照常（与 M12-4 V8 一致）
    assert build_pipeline_system("Analyst", reg, user_text="普通任务")[1] is None
