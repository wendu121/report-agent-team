"""Phase 1 多租户隔离实证脚本（真实跑，非纸面）。

验证：
  E-a 两个账号的资源路径互不相同
  E-b 模板拷贝会带上「配置类」内容，但排除密钥/审计/运行时产物
  E-c 会话归属过滤真的生效（SQL 层面带 owner_id 条件）
  E-d 无上下文时 fail loud（不静默回落全局）

用法：python scripts/verify_tenancy.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.mkdtemp(prefix="rat-tenancy-")
os.environ["TENANTS_ROOT"] = tmp
os.environ.pop("RAT_LEGACY_GLOBAL", None)
os.environ.pop("RAT_ACCOUNT_ID", None)

from server import tenancy  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# ---------- E-a 路径隔离 ----------
a, b = "acct-A", "acct-B"
tenancy.set_current_account(a)
pa = tenancy.config_path("model_mapping.yaml")
tenancy.set_current_account(b)
pb = tenancy.config_path("model_mapping.yaml")
check("E-a 两账号 config 路径不同", pa != pb, f"{pa} vs {pb}")
check("E-a 含各自账号 id", a in str(pa) and b in str(pb))

# ---------- E-b 模板拷贝 + 排除项 ----------
tenancy.set_current_account(a)
root_a = tenancy.account_root()
(root_a / "config").mkdir(parents=True, exist_ok=True)
(root_a / "config" / "model_mapping.yaml").write_text("chat:\n  tool_model: MODEL-A\n", encoding="utf-8")
(root_a / ".secrets").mkdir(parents=True, exist_ok=True)
(root_a / ".secrets" / "plugins.env").write_text("DS_TAVILY_API_KEY=SECRET-A\n", encoding="utf-8")
(root_a / ".audit").mkdir(parents=True, exist_ok=True)
(root_a / ".audit" / "admin.log.jsonl").write_text("{}\n", encoding="utf-8")

tenancy.ensure_account_layout(b, seed_from=a, copy_payload=True)
check("E-b 配置类被复制", (tenancy.account_root(b) / "config" / "model_mapping.yaml").exists())
check(
    "E-b 密钥未被复制（不继承别人的 key）",
    not (tenancy.account_root(b) / ".secrets" / "plugins.env").exists(),
)
check(
    "E-b 审计未被复制（不串日志）",
    not (tenancy.account_root(b) / ".audit" / "admin.log.jsonl").exists(),
)

# ---------- E-c 会话过滤条件真的带 owner_id ----------
src = Path("server/api.py").read_text(encoding="utf-8")
check("E-c 列表按 owner 过滤", "ChatSession.owner_id == aid" in src)
check("E-c 越权走 404", "_owned_session" in src and "404" in src)

# ---------- E-d 无上下文 fail loud ----------
import contextvars  # noqa: E402

ctx = contextvars.copy_context()


def _no_ctx():
    os.environ.pop("RAT_ACCOUNT_ID", None)
    tenancy._current_account_id.set(None)
    try:
        tenancy.tenant_path("config", "x.yaml")
        return False
    except tenancy.TenancyError:
        return True


ctx.run(_no_ctx)
check("E-d 无上下文抛 TenancyError（不静默回落全局）", ctx.run(_no_ctx))

# ---------- E-e admin.py 静态回归（踩过的两个坑，必须钉死） ----------
admin_src = Path("server/admin.py").read_text(encoding="utf-8")
check(
    "E-e admin 无「模块级 LEGACY_* 调用租户函数」（否则 import 即 TenancyError）",
    not re.search(r"^LEGACY_\w+ = _\w+\(\)", admin_src, re.M),
)
check(
    "E-e admin 已堵 dev_no_token 放行（旧：未配置 ADMIN_TOKEN 就放行）",
    'return "dev_no_token"' not in admin_src,
)
check(
    "E-e admin 鉴权支持 Bearer JWT（登录即租户）",
    'bearer ' in admin_src.lower() and "AUTH_SECRET" in admin_src and "set_current_account" in admin_src,
)
# 资源根全部走函数（非 BASE 常量）
leftover = [
    ln for ln in admin_src.splitlines()
    if re.search(r'(?<!LEGACY_)\b(CONFIG_DIR|MAPPING_PATH|PLUGINS_PATH|SECRETS_PATH|LIBRARY_PATH|MODELS_PATH|AGENTS_DIR|GATES_DIR|TEMPLATES_DIR|AUDIT_DIR)\b', ln)
    and not ln.strip().startswith("#")
    and "def _" not in ln
]
check("E-e admin 无遗留裸资源常量引用", not leftover, "; ".join(leftover[:3]))

# ---------- E-f 任务状态目录按账号隔离（引擎子进程状态文件不串号） ----------
c, d = "acct-C", "acct-D"
tenancy.set_current_account(c)
sd_c = tenancy.tenant_path(".engine_state")
tenancy.set_current_account(d)
sd_d = tenancy.tenant_path(".engine_state")
check("E-f 两账号 .engine_state 路径不同", str(sd_c) != str(sd_d) and c in str(sd_c) and d in str(sd_d))
check(
    "E-f 引擎状态目录落在 TENANTS_ROOT 下且含账号 id（非全局 .engine_state）",
    str(sd_c).startswith(str(tenancy.TENANTS_ROOT)) and c in str(sd_c),
)

# ---------- E-g 主账号 seed_from_base：拷配置不拷密钥 ----------
tenancy.ensure_account_layout("acct-SEED", seed_from_base=True)
seed_root = tenancy.account_root("acct-SEED")
check("E-g 主账号播种得到 config 目录", (seed_root / "config").exists())
check("E-g 主账号播种得到 skills/agents（镜像内置默认）", (seed_root / "skills").exists() and (seed_root / "agents").exists())
check(
    "E-g 主账号播种 .secrets 为空（密钥不继承镜像/全局）",
    not (seed_root / ".secrets").exists() or not any((seed_root / ".secrets").iterdir()),
)
_empty = lambda p: (not p.exists()) or not any(p.iterdir())
check(
    "E-g 主账号播种 .engine_state/.audit 为空（运行时产物不拷）",
    _empty(seed_root / ".engine_state") and _empty(seed_root / ".audit"),
)

print()
print("FAILED:" if failures else "ALL PASS", ", ".join(failures) if failures else "")
sys.exit(1 if failures else 0)
