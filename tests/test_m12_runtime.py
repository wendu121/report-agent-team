"""M12-4 运行时接入单测（DESIGN_M12-4 §5 验收 R1-R6 / V1 / V8）。

盘点发现的真缺口：M12-1/2/3 代码齐了，但 chat_agent.py / orchestrator.py **从未 import
tools.experts** —— 专家团零调用，`@专家` 在对话里不生效；且 /chat 的 system 不含
`build_skill_context`，DESIGN_M12 §7 的 V1（「下一个 /chat 能读到该技能」）实际是
打折通过的（只验了 build_skill_context("researcher")）。本档把这两处接上并实测。

不触网、不打真 LLM：FakeBundle 注入（保 DI 契约）+ RecordingLLM 记录 system。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chat_agent import ChatAgent, _resolve_expert_context  # noqa: E402
from tools import experts as ex  # noqa: E402
from tools import skills as sk  # noqa: E402


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class FakeBundle:
    """注入型 bundle：不触发 build_tools()（不打真网）。"""

    def __init__(self):
        self.web_search = None
        self.data_proc = None
        self.mcp = None


class RecordingLLM:
    """记录收到的 system / model，直接返回终态文本（不产生 tool_calls）。"""

    def __init__(self, final_text="好的，这是回答。"):
        self.final_text = final_text
        self.system = None
        self.model = None

    def complete_with_tools(self, model, system, user, tools=None, role=None,
                            shape=None, kind=None):
        self.model = model
        self.system = system
        return {"content": self.final_text, "tool_calls": []}


def _run(user_msg, model=None):
    llm = RecordingLLM()
    agent = ChatAgent(llm=llm, bundle=FakeBundle())
    out = agent.step(history=[], user_msg=user_msg, model=model)
    return out, llm


# --------------------------------------------------------------------------
# fixtures：专家包 + 技能库都落在 tmp
# --------------------------------------------------------------------------
@pytest.fixture
def exp_paths(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    audit = tmp_path / ".audit"
    monkeypatch.setattr(ex, "_root", lambda: tmp_path)
    monkeypatch.setattr(ex, "_registry_path", lambda: cfg / "experts.yaml")
    monkeypatch.setattr(ex, "_expert_dir", lambda: tmp_path / "experts")
    monkeypatch.setattr(ex, "_audit_dir", lambda: audit)
    monkeypatch.setattr(ex, "_expert_log", lambda: audit / "experts.jsonl")
    monkeypatch.setattr(ex, "_proposal_dir", lambda: audit / "expert_proposals")
    return tmp_path


@pytest.fixture
def skill_paths(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    sdir = tmp_path / "skills"
    sdir.mkdir(exist_ok=True)
    monkeypatch.setattr(sk, "_tenant_root", lambda: tmp_path)
    monkeypatch.setattr(sk, "_config_dir", lambda: cfg)
    monkeypatch.setattr(sk, "_skills_path", lambda: cfg / "skills.yaml")
    monkeypatch.setattr(sk, "_skills_dir", lambda: sdir)
    return tmp_path


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
# R1 / R2 / R3 / R4：专家调度
# --------------------------------------------------------------------------
def test_mention_injects_expert(exp_paths):
    """R1：显式 @ 命中 → system 含专家片段，routed=False。"""
    _write_pkg(exp_paths / "experts" / "fin-expert", body="杜邦分解三步法。\n")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")

    out, llm = _run("@fin-expert 帮我看这份财报")
    assert out["expert"] is not None
    assert out["expert"]["id"] == "fin-expert"
    assert out["expert"]["routed"] is False
    assert "【专家：" in llm.system
    assert "杜邦分解三步法" in llm.system


def test_unknown_mention_not_injected(exp_paths):
    """R2：@ 了不存在的专家 → 不注入、不报错（用户可能只是在写引用）。"""
    _write_pkg(exp_paths / "experts" / "fin-expert")
    ex.register_expert("fin-expert", "experts/fin-expert")

    out, llm = _run("@nosuch-expert 你好")
    assert out["expert"] is None
    assert "【专家：" not in llm.system
    assert out["reply"]


def test_implicit_route_injects_expert(exp_paths):
    """R3：意图明确但无 @ → 高置信才注入，routed=True。"""
    _write_pkg(exp_paths / "experts" / "fin-expert",
               body="财务报表分析 现金流 利润表 资产负债表")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")

    out, _llm = _run("分析财务报表的现金流")
    assert out["expert"] is not None
    assert out["expert"]["routed"] is True


def test_low_confidence_fallback(exp_paths):
    """R4/V7：低置信或并列 → 回退通用 ChatAgent，绝不强行套专家。"""
    _write_pkg(exp_paths / "experts" / "fin-expert",
               body="财务报表分析 现金流 利润表 资产负债表")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst")

    out, llm = _run("如何做一顿饭")
    assert out["expert"] is None
    assert "【专家：" not in llm.system


# --------------------------------------------------------------------------
# V8：任何专家故障都降级，/chat 绝不 500
# --------------------------------------------------------------------------
def test_broken_package_degrades(exp_paths):
    """V8：包体损坏（plugin.json 被删）→ 不注入专家，对话照常返回。"""
    pkg = exp_paths / "experts" / "fin-expert"
    _write_pkg(pkg)
    ex.register_expert("fin-expert", "experts/fin-expert")
    (pkg / "plugin.json").unlink()

    out, llm = _run("@fin-expert 看看")
    assert out["expert"] is None
    assert "【专家：" not in llm.system
    assert out["reply"]  # 没有 500，正常作答


def test_team_expert_degrades(exp_paths):
    """V8：expertType=team 默认拒绝 → 降级不注入（不冒充可用）。"""
    _write_pkg(exp_paths / "experts" / "team-expert", expert_type="team")
    ex.register_expert("team-expert", "experts/team-expert")

    out, llm = _run("@team-expert 帮我协作分析")
    assert out["expert"] is None
    assert "【专家：" not in llm.system
    assert out["reply"]


def test_resolve_expert_context_never_raises(exp_paths, monkeypatch):
    """V8：list_experts 本身炸了也不许冒泡（专家是增强项）。"""
    monkeypatch.setattr(ex, "list_experts", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("注册表炸了")))
    ctx, meta = _resolve_expert_context("随便问点什么")
    assert ctx == ""
    assert meta is None


# --------------------------------------------------------------------------
# V1（补 /chat 侧实证）：技能上下文能进对话
# --------------------------------------------------------------------------
def test_skill_context_reaches_chat(skill_paths):
    """V1：target_roles 含 chat 的已启用技能 → /chat 的 system 实测能读到。"""
    (skill_paths / "skills" / "demo.md").write_text("这是对话演示技能正文。", encoding="utf-8")
    (skill_paths / "config" / "skills.yaml").write_text(
        "skills:\n"
        "- id: demo\n"
        "  name: 对话演示技能\n"
        "  target_roles: [chat]\n"
        "  prompt_file: skills/demo.md\n"
        "  enabled: true\n"
        "  installed: true\n",
        encoding="utf-8")

    out, llm = _run("你好")
    assert "【已启用技能：对话演示技能】" in llm.system
    assert "这是对话演示技能正文。" in llm.system


def test_skill_not_for_chat_not_injected(skill_paths):
    """反向：只给 researcher 的技能不应污染对话（作用域要生效）。"""
    (skill_paths / "skills" / "r.md").write_text("研究员专用。", encoding="utf-8")
    (skill_paths / "config" / "skills.yaml").write_text(
        "skills:\n"
        "- id: ronly\n"
        "  name: 仅研究员\n"
        "  target_roles: [researcher]\n"
        "  prompt_file: skills/r.md\n"
        "  enabled: true\n"
        "  installed: true\n",
        encoding="utf-8")

    _out, llm = _run("你好")
    assert "仅研究员" not in llm.system


# --------------------------------------------------------------------------
# R5：专家模型偏好（防「能填但不消费」的假配置）
# --------------------------------------------------------------------------
def test_expert_model_used_when_user_unspecified(exp_paths):
    _write_pkg(exp_paths / "experts" / "fin-expert")
    ex.register_expert("fin-expert", "experts/fin-expert", model="expert-model-x")

    _out, llm = _run("@fin-expert 看看")  # model=None
    assert llm.model == "expert-model-x"


def test_user_model_wins_over_expert(exp_paths):
    _write_pkg(exp_paths / "experts" / "fin-expert")
    ex.register_expert("fin-expert", "experts/fin-expert", model="expert-model-x")

    _out, llm = _run("@fin-expert 看看", model="user-picked")
    assert llm.model == "user-picked"


# --------------------------------------------------------------------------
# R6：注册 id 规范化（REVIEW_M12.md 残留 MINOR-1 闭环）
# --------------------------------------------------------------------------
def test_register_expert_normalizes_id(exp_paths):
    pkg = exp_paths / "experts" / "evil"
    _write_pkg(pkg, eid="evil")
    ex.register_expert("../../evil", "experts/evil")

    ids = [e.get("id") for e in ex.load_registry()]
    assert "evil" in ids
    assert not any(".." in str(i) or "/" in str(i) for i in ids)
