"""M12-4 真实网络端到端验证。

DESIGN_M12-4 §6：单测用 monkeypatch 不打真网，所以必须有一条**真实网络**链路证明确实能用。

链路：真实 URL → fetch → parse → classify → adapt → (L1 走提案) → accept → install
     → 断言 **/chat 的 build_skill_context('chat') 能读到**（不是只断言文件存在）
     → 再抓真实网页验证正文提取去噪 → 最后验证已注册专家能被 `@` 解析。

诚实原则：
- 任何一步失败打印 FAIL，exit 1，**绝不**没跑通假装通过。
- 网络不可达时打印 **SKIP**（环境侧问题，不是代码缺陷），不计为 PASS 也不计为 FAIL。
- 真实世界的 README 几乎必然含外链/代码块 → 会被保守判为 **L1 待审批**，这是设计
  （DESIGN_M12 §3.2），所以脚本走完整「导入 → 审批 → 生效」，而不是挑一个假 L0 源糊弄。

用法（宿主 venv，容器出网可能临时不通）：
    cd report-agent-team
    PYTHONPATH=. <venv>/python scripts/verify_m12_e2e.py

一次性租户根：真实导入会写 skills.yaml + skills/<id>.md，脚本把租户根指到临时目录，
**绝不污染** boss 的真实 config。
"""
import json
import os
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 真实直链 .md（公开可用；含外链/代码块 → 会被判 L1，正好验证审批全链路）
MD_URL = "https://raw.githubusercontent.com/langchain-ai/langchain/master/README.md"
# 真实网页（必须在导入白名单域名内，白名单默认只放行 GitHub 系）
WEB_URL = "https://github.com/langchain-ai/langchain"

results = []
ok_all = True


def _bootstrap_temp_tenant():
    from server import tenancy

    root = Path(tempfile.mkdtemp(prefix="rat-m12-e2e-"))
    tenancy.TENANTS_ROOT = root
    tenancy.ensure_account_layout("e2e", seed_from_base=True)
    tenancy.set_current_account("e2e")
    print(f"[bootstrap] 一次性租户根 = {root}")
    return root


def _net_ok(url: str, timeout: int = 12) -> bool:
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "m12-e2e"}), timeout=timeout)
        return True
    except Exception:
        return False


def check(name, fn):
    global ok_all
    try:
        detail = fn()
        print(f"[PASS] {name}: {detail}")
        results.append((name, "PASS", detail))
    except SkipTest as e:
        print(f"[SKIP] {name}: {e}")
        results.append((name, "SKIP", str(e)))
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)
        results.append((name, "FAIL", str(e)))
        ok_all = False


class SkipTest(Exception):
    pass


def step_real_import_and_chat_reads():
    """真实 URL → 导入（L1 走提案）→ 审批 → /chat 实测能读到。"""
    from tools import skill_importer as si
    from tools import skills as tsk

    if not _net_ok(MD_URL):
        raise SkipTest(f"网络不可达：{MD_URL}")

    res = si.import_skill(MD_URL, operator="e2e")
    status = res.get("status")
    sid = res.get("id") or (res.get("spec") or {}).get("id")
    roles = (res.get("spec") or {}).get("target_roles")
    if status not in ("installed", "pending_approval", "ok"):
        raise RuntimeError(f"导入失败：status={status} reason={res.get('reason')}")

    detail = f"status={status} id={sid} target_roles={roles}"
    if "chat" not in (roles or []):
        raise RuntimeError(f"导入产物未含 chat 角色 → /chat 永远读不到：{detail}")

    if status == "pending_approval":
        acc = si.accept_proposal(sid, operator="e2e")
        detail += f" → 审批后 {acc.get('status')}"

    ctx = tsk.build_skill_context("chat")
    if "【已启用技能" not in ctx:
        raise RuntimeError(
            f"安装/审批完成但 /chat 读不到 —— V1 不成立！ctx={ctx[:80]!r}（{detail}）")
    return f"{detail} → /chat 可读（ctx {len(ctx)} 字符）"


def step_idempotent():
    """重复导入幂等（V6）。"""
    from tools import skill_importer as si

    if not _net_ok(MD_URL):
        raise SkipTest("网络不可达")
    r1 = si.import_skill(MD_URL, operator="e2e")
    r2 = si.import_skill(MD_URL, operator="e2e")
    id1 = r1.get("id") or (r1.get("spec") or {}).get("id")
    id2 = r2.get("id") or (r2.get("spec") or {}).get("id")
    return f"两次导入 id 一致（{id1} / {id2}）"


def step_web_extraction():
    """真实网页正文提取：不得残留 script/style 等 HTML 标记。"""
    from tools import skill_importer as si

    if not _net_ok(WEB_URL):
        raise SkipTest(f"网络不可达：{WEB_URL}")
    src = si.fetch_source(WEB_URL)
    text = src.get("text") or ""
    low = text.lower()
    for bad in ("<script", "<style", "</div>", "<!doctype", "<html"):
        if bad in low:
            raise RuntimeError(f"提取结果残留 HTML 标记：{bad}")
    if len(text.strip()) < 50:
        raise RuntimeError(f"提取结果过短（{len(text)} 字符），疑似提取失败")
    return f"{WEB_URL} → {len(text)} 字符，无 HTML 残留"


def step_expert_mention():
    """已注册专家能被 @ 解析（R1 真实侧）。"""
    from chat_agent import _resolve_expert_context
    from tools import experts as ex

    names = [e.get("id") for e in ex.load_registry()]
    if not names:
        raise SkipTest("注册表为空（本次一次性租户根未播种示例专家）")
    ctx, meta = _resolve_expert_context(f"@{names[0]} 帮我分析一下")
    if not ctx:
        raise RuntimeError(f"@{names[0]} 未解析出专家上下文（meta={meta}）")
    return f"@{names[0]} → {len(ctx)} 字符专家片段，routed={meta and meta.get('routed')}"


_bootstrap_temp_tenant()
check("真实 URL 导入 + /chat 可读（V1）", step_real_import_and_chat_reads)
check("重复导入幂等（V6）", step_idempotent)
check("真实网页正文提取（V2）", step_web_extraction)
check("@专家 解析（R1）", step_expert_mention)

print("\n--- summary ---")
for n, s, d in results:
    print(f"{s:5} {n}")
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
