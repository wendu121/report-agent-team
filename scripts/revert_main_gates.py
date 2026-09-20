"""一次性运维脚本：把主账号三道闸恢复为改动前的值（cloudflare / llama-3.3-70b）。

背景（2026-09-19 夜 inspect 坐实）：
  审计 `2026-09-19T15-19-56-022411Z-update.json` 把主账号三闸从
  `cloudflare / llama-3.3-70b` 改成 `new-api / Intern-S2-Preview`，后者**未命中接口**
  且 **400「您已超过输入 tokens 配额」** → 主账号三道闸 100% 降级放行（等于无审核）。

为什么不用 HTTP：
  `PUT /admin/models` 走 `_require_admin(x_admin_token)`（request=None）→ Bearer JWT 恒 401；
  `.env` 未配 `ADMIN_TOKEN` → X-Admin-Token 后门通道不存在。**HTTP 路径当前不可用**
  （即 MEMORY §7.2 那条"admin Bearer 全失效"缺陷的又一实证）。
  故在容器内直接复用 admin.py 的同一套保存逻辑：**保留注释 + 原子写 + 写审计 + 回读校验**，
  operator 记为主账号，与既有审计条目格式一致。

用法（容器内）：
  docker compose exec -T api python /app/scripts/revert_main_gates.py
加 `--dry` 只打印预览不落盘。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY = "--dry" in sys.argv
MAIN_ACCOUNT = "9910ebbc-112f-4ef2-ad7b-ed2582999382"   # wendy（主账号）

from server import tenancy  # noqa: E402

tenancy.set_current_account(MAIN_ACCOUNT)

from server import admin  # noqa: E402

TARGET = {"GateA": "Researcher", "GateB": "Analyst", "GateC": "Writer"}
NEW_BASE = "cloudflare"
NEW_MODEL = "llama-3.3-70b"

path = admin._mapping_path()
print(f"配置文件: {path}")
print(f"账号: {MAIN_ACCOUNT}")

data = admin._load_commented()
before = admin._to_plain(data)
print("\n=== BEFORE（三闸）===")
for g in TARGET:
    print(f"  {g}: {before['gates'].get(g)}")

for g in TARGET:
    data["gates"][g]["base"] = NEW_BASE
    data["gates"][g]["model"] = NEW_MODEL

errors = admin._validate_mapping(data)
print(f"\n校验: {'❌ ' + str(errors) if errors else '✅ 通过'}")
if errors:
    sys.exit(1)

after_guess = admin._to_plain(data)
print("\n=== AFTER（待落盘，三闸）===")
for g in TARGET:
    print(f"  {g}: {after_guess['gates'].get(g)}")

if DRY:
    print("\n--dry：未落盘")
    sys.exit(0)

admin._atomic_write(path, admin._dump_commented(data))

import yaml as pyyaml  # noqa: E402

reparsed = pyyaml.safe_load(path.read_text(encoding="utf-8"))
after = admin._to_plain(reparsed)
ts = admin._write_audit("update", before, after, f"account:{MAIN_ACCOUNT}", "pass")

print(f"\n✅ 已落盘并写审计：{ts}")
print("=== 回读确认（三闸）===")
for g in TARGET:
    print(f"  {g}: {after['gates'].get(g)}")
print("\n注：orchestrator 每次 run_report 重新 load_model_mapping → 下一个任务即生效，无需重启。")
