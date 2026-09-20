"""一次性数据修复：把多租户改造前遗留的全局数据源密钥，搬进**系统主账号**自己的命名空间。

## 为什么必须修（已在真机证实）

多租户 commit `0f8511a`（2026-09-17）把密钥读取路径改成了「按账号命名空间」：

    tools/data_sources.py::_secrets_file() -> tenancy.secrets_path("plugins.env")
                                            = tenants/<account_id>/.secrets/plugins.env

但**没有人把改造前那份全局 `/app/.secrets/plugins.env` 搬进主账号自己的租户目录**。
结果（容器内实测）：

    legacy /app/.secrets/plugins.env 存在=True | 含 DS_TAVILY_API_KEY=True
    wendy(主)  -> 读 tenants/9910ebbc…/.secrets/plugins.env  exists=False -> 读不到
    wendy1(子) -> 读 tenants/af28b675…/.secrets/plugins.env  exists=False -> 读不到

`load_secrets()` 读不到 key → `build_search_tool()` 走
「provider=='tavily' 且无 key → MockProvider」分支 → **返回 `[MOCK] … 占位内容，非真实检索结果`**，
而且**全程不报错**：`/plugins` 里 tavily 仍是 `enabled=True`，日志把 mock 的 5 条记成
「tavily 命中 5 条」。于是从 09-17 起，**所有人（含主账号）的 tavily 都是假的**，
用户的研报「来源」里出现 8 条 `[MOCK] Tavily 搜索…`。

## 本脚本做什么 / 不做什么

- **只处理系统主账号**：那份 legacy 密钥本来就是主账号的（改造前是它的事实密钥库）。
- **绝不碰子账号**：设计上「各自一套 key，绝不跨账号共享」（`_secrets_file` 注释原文），
  子账号要用就自己在 UI 里填（设置 → 插件 → 填入 key → 会写进它自己的租户 `.secrets/`）。
- **合并不覆盖**：只补 legacy 里有、租户里没有的 KEY；已存在的键原样保留。
- **幂等**：重复执行第二次会报「无需搬移」。

## 用法

    python scripts/migrate_legacy_secrets_to_tenant.py --dry-run
    python scripts/migrate_legacy_secrets_to_tenant.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from server import tenancy  # noqa: E402

LEGACY = Path(__file__).resolve().parent.parent / ".secrets" / "plugins.env"


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return out


def _resolve_main():
    from scripts.purge_inherited_gateway import _resolve_main_account

    return _resolve_main_account()


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv

    if not LEGACY.exists():
        print(f"ℹ️  没有遗留全局密钥文件 {LEGACY} —— 无需搬移")
        return 0
    legacy = _parse(LEGACY.read_text(encoding="utf-8"))
    if not legacy:
        print(f"ℹ️  {LEGACY} 存在但没有任何 KEY=VALUE —— 无需搬移")
        return 0
    print(f"遗留全局密钥 {LEGACY}\n  含 {len(legacy)} 个键：{sorted(legacy)}\n")

    main_id, main_name, err = _resolve_main()
    if err or not main_id:
        print(f"❌ 无法确定系统主账号：{err}")
        return 1

    tenancy.set_current_account(main_id)
    target = tenancy.secrets_path("plugins.env")
    print(f"系统主账号：{main_name} ({main_id})\n  目标：{target}")

    current = _parse(target.read_text(encoding="utf-8")) if target.exists() else {}
    missing = {k: v for k, v in legacy.items() if k not in current}

    if not missing:
        print(f"✅ 租户密钥已包含全部键（现有 {len(current)} 个）—— 幂等复跑，无需搬移")
        return 0

    print(f"  租户现有 {len(current)} 个键；需要补 {len(missing)} 个：{sorted(missing)}")
    if dry:
        print("\n[DRY-RUN] 未改动任何数据。去掉 --dry-run 即执行。")
        return 0

    merged = dict(current)
    merged.update(missing)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"{k}={v}" for k, v in sorted(merged.items())) + "\n",
                      encoding="utf-8")
    target.chmod(0o600)  # 密钥文件不给 group/other 读
    print(f"\n✅ 已写入 {target}（{len(merged)} 个键，权限 600）")

    # 复核：必须能真的读到，否则等于没修
    fresh = _parse(target.read_text(encoding="utf-8"))
    got = [k for k in missing if k in fresh]
    print(f"复核：新增键可读 {len(got)}/{len(missing)} -> {sorted(got)}")
    return 0 if len(got) == len(missing) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
