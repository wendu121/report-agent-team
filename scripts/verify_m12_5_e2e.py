"""M12-5 真实环境端到端验证（不触网）。

DESIGN_M12-5 §6：单测用一次性租户根隔离，本脚本再用一条**真实文件系统**链路证明
build_pipeline_system 在研报流水线侧确实能把 shape 匹配的专家 persona 注入对应节点、
且 shape 错配不污染其它节点、模型优先级正确。

诚实原则：
- 任何一步失败打印 FAIL，exit 1，绝不没跑通假装通过。
- 本脚本不打真网、不调真 LLM，所有断言在一次性租户根内闭环，**绝不污染** boss 真实 config。

用法（宿主 venv）：
    cd report-agent-team
    PYTHONPATH=. <venv>/python scripts/verify_m12_5_e2e.py
"""
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []
ok_all = True


def _bootstrap_temp_tenant():
    from server import tenancy

    root = Path(tempfile.mkdtemp(prefix="rat-m12-5-e2e-"))
    tenancy.TENANTS_ROOT = root
    tenancy.set_current_account("m12_5")
    r = root / "m12_5"
    for d in ("agents", "config", "experts", "skills", ".audit"):
        (r / d).mkdir(parents=True, exist_ok=True)
    base = Path(__file__).resolve().parent.parent
    if (base / "agents").exists():
        shutil.copytree(base / "agents", r / "agents", dirs_exist_ok=True)
    if (base / "config" / "agents_library.yaml").exists():
        shutil.copy2(base / "config" / "agents_library.yaml",
                     r / "config" / "agents_library.yaml")
    print(f"[bootstrap] 一次性租户根 = {r}")
    return r


def check(name, fn):
    global ok_all
    try:
        detail = fn()
        print(f"[PASS] {name}: {detail}")
        results.append((name, "PASS", detail))
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)
        results.append((name, "FAIL", str(e)))
        ok_all = False


def step_expert_injected_into_matching_node(root):
    """shape 匹配注入 + shape 错配隔离 + 模型优先级，三验于真实租户根。"""
    from orchestrator import (
        build_pipeline_system,
        _resolve_agent_model,
        load_agent_registry,
    )
    from tools import experts as ex

    pkg = root / "experts" / "fin-expert"
    (pkg / "agents").mkdir(parents=True, exist_ok=True)
    plugin = {
        "name": "fin-expert", "expertType": "agent", "agentName": "fin-expert",
        "displayName": {"en": "fin-expert", "zh": "财报分析师"},
        "profession": {"en": "fin", "zh": "财务"}, "categoryId": "09-FinanceAccounting",
        "tags": ["fin-expert"],
    }
    (pkg / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False), encoding="utf-8")
    (pkg / "agents" / "fin-expert.md").write_text(
        "---\nname: fin-expert\n---\n杜邦分解三步法。\n", encoding="utf-8")
    ex.register_expert("fin-expert", "experts/fin-expert", shape="analyst", model="longcat-foo")

    reg = load_agent_registry()
    sys_a, meta_a = build_pipeline_system("Analyst", reg, user_text="@fin-expert 分析这份财报")
    sys_r, meta_r = build_pipeline_system("Researcher", reg, user_text="@fin-expert 分析这份财报")
    assert meta_a and meta_a["shape"] == "analyst", f"meta_a={meta_a}"
    assert "【专家：" in sys_a and "杜邦分解三步法" in sys_a, "Analyst 节点未注入专家"
    assert "【专家：" not in sys_r, "shape 过滤器失效：analyst 专家漏进 Researcher"
    # 模型优先级：专家 model 生效（shape 匹配）
    models = {"roles": {"Analyst": {"model": "role-default"}}}
    assert _resolve_agent_model(None, meta_a, "analyst", models, "Analyst") == "longcat-foo"
    # 模型优先级：model_override 优先
    assert _resolve_agent_model("user-override", meta_a, "analyst", models, "Analyst") == "user-override"
    return "Analyst 注入 + Researcher 隔离 + 模型消费 三验通过"


root = _bootstrap_temp_tenant()
check("专家 shape 接入研报流水线（V1/V4/V5）",
      lambda: step_expert_injected_into_matching_node(root))

print("\n--- summary ---")
for n, s, d in results:
    print(f"{s:5} {n}")
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
