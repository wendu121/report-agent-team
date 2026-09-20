#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清洗子账号从主账号继承来的「模型归属物」配置（一次性数据修复）。

## 为什么需要它
设计依据：`DESIGN_subaccount_model_isolation.md` §1（三处泄漏点 L1/L3）与 §3-D4。

历史上子账号首次激活（approve）时，`tenancy.ensure_account_layout(child, seed_from=main)`
会把主账号的整份 `config/` 拷进子账号租户目录。其中三份文件承载的是**主账号自己的模型
归属物**，本不该继承：

  - `config/models.yaml`          —— 含 `endpoints[0].id = new-api`（base_url 指向主账号
                                     网关、api_key 是 `${NEWAPI_API_KEY}`）。密钥虽走全局 env
                                     展开（不落盘），但**占位符跟着来了就等于密钥跟着来了**，
                                     子账号每次 LLM 调用都烧主账号的额度。
  - `config/model_mapping.yaml`   —— 含主账号网关的模型名（LongCat-2.0 / llama-3.3-70b）
                                     与来源标签（custom / cloudflare）。
  - `config/custom_providers.yaml`—— 含主账号自定义 provider 的定义。

代码侧已修（`tenancy._write_neutral_seed_files` 播种中性模板 + `orchestrator._has_tenant_context`
禁止为有账号上下文的请求回落 `NEWAPI_*`），本脚本负责把**已经落盘的存量污染**洗干净。

## 用法
    # 先看会改什么（不写盘）
    PYTHONPATH=/app python /app/scripts/purge_inherited_gateway.py --dry-run
    # 真洗
    PYTHONPATH=/app python /app/scripts/purge_inherited_gateway.py

## 幂等性
中性模板里 `endpoints: []`、`providers: []`、映射值全为空，均不再命中污染特征，
故重复执行第二遍会报告「修复 0 处」。可安全反复跑。

## 边界
- **系统主账号的租户目录被完全跳过**（它本来就是网关的主人，不能洗）。
- 只在命中污染特征时才覆盖；内容已是被改过的（非主账号原样）不动——避免误抹用户自配内容。
- 单文件解析失败只记 SKIP 不中断；写盘走「临时文件 + os.replace」原子替换。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 主账号网关的模型/来源特征（DESIGN_subaccount_model_isolation.md §3-D4）
MAIN_GATEWAY_MODELS = {"LongCat-2.0", "llama-3.3-70b", "glm-4.7"}
MAIN_GATEWAY_BASES = {"custom", "cloudflare", "zhipu", "google"}

NEUTRAL_MODELS = """# config/models.yaml · 子账号模板（注册时自动生成，不继承主账号）
#
# 子账号**自备模型 API**：请到「设置 → 自定义 API」添加你自己的 OpenAI 兼容
# Provider（base_url + API Key）。添加后其模型会自动出现在 interfaces 列表中。
# 子账号不共享主账号的 new-api 网关。
endpoints: []
interfaces:
  - id: auto
    name: 自动
    description: 使用 model_mapping.yaml 的映射（未配置前不可用）
    kind: auto
    default: true
gateway:
  fetch_from_gateway: false
"""

NEUTRAL_MAPPING = """# config/model_mapping.yaml · 子账号模板（注册时自动生成）
#
# 子账号需**自己**把角色 / 审核闸映射到你所配置的模型：
#   1) 先到「设置 → 自定义 API」添加 Provider 与模型 id
#   2) 再把下面的 model 填成该 Provider 上的真实模型 id
#   3) base 是「来源标签」：gates.X.base 必须 != roles[reviews].base（防自审包庇）
#      → 至少需要两个不同来源的 Provider 才能满足硬约束
roles:
  Researcher:
    base: ""
    model: ""
  Analyst:
    base: ""
    model: ""
  Writer:
    base: ""
    model: ""
gates:
  GateA:
    reviews: Researcher
    base: ""
    model: ""
  GateB:
    reviews: Analyst
    base: ""
    model: ""
  GateC:
    reviews: Writer
    base: ""
    model: ""
eval:
  base: ""
  model: ""
chat:
  tool_model: ""
"""

NEUTRAL_PROVIDERS = "providers: []\n"


# --------------------------------------------------------------------------- IO


def _atomic_write(path: Path, text: str) -> None:
    """临时文件 + os.replace 原子替换（与 server/admin.py 的 _atomic_write 同思路）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_yaml(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """返回 (数据, 错误信息)。解析失败返回 (None, err) 由调用方记 SKIP。"""
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 污染判定


def _models_polluted(data: Any) -> Optional[str]:
    """models.yaml 是否含主账号网关端点。返回人类可读的原因，未污染返回 None。"""
    if not isinstance(data, dict):
        return None
    for e in data.get("endpoints") or []:
        if isinstance(e, dict) and e.get("id") == "new-api":
            return "含 new-api 端点（指向主账号网关）"
    gw = data.get("gateway") or {}
    if isinstance(gw, dict) and gw.get("fetch_from_gateway"):
        return "gateway.fetch_from_gateway=true（会去拉主账号网关的模型列表）"
    return None


def _mapping_polluted(data: Any) -> Optional[str]:
    """model_mapping.yaml 的 model/base 是否指向主账号网关。"""
    if not isinstance(data, dict):
        return None
    hits: List[str] = []

    def _scan(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        base = node.get("base")
        model = node.get("model")
        if isinstance(model, str) and model in MAIN_GATEWAY_MODELS:
            hits.append(f"{prefix}.model={model}")
        if isinstance(base, str) and base in MAIN_GATEWAY_BASES:
            hits.append(f"{prefix}.base={base}")

    roles = data.get("roles") or {}
    if isinstance(roles, dict):
        for name, cfg in roles.items():
            _scan(cfg, f"roles.{name}")
    gates = data.get("gates") or {}
    if isinstance(gates, dict):
        for name, cfg in gates.items():
            _scan(cfg, f"gates.{name}")
    _scan(data.get("eval"), "eval")

    chat = data.get("chat") or {}
    if isinstance(chat, dict) and chat.get("tool_model") in MAIN_GATEWAY_MODELS:
        hits.append(f"chat.tool_model={chat['tool_model']}")

    return ("指向主账号网关模型：" + "、".join(hits)) if hits else None


def _providers_polluted(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    provs = data.get("providers") or []
    if provs:
        names = [p.get("id") for p in provs if isinstance(p, dict) and p.get("id")]
        return f"含主账号自定义 provider：{', '.join(names) or len(provs)}"
    return None


# ------------------------------------------------------------------------- 主流程


def _resolve_tenants_root() -> Path:
    """租户根。刻意不用 tenancy.account_root()（缺上下文会抛错）。"""
    try:
        from server import tenancy  # noqa: PLC0415

        return Path(tenancy.TENANTS_ROOT)
    except Exception:  # noqa: BLE001
        base = Path(__file__).resolve().parent.parent
        return Path(os.getenv("TENANTS_ROOT", str(base / "tenants")))


def _resolve_main_account() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """返回 (主账号 id, 主账号用户名, 错误)。"""
    try:
        from sqlalchemy import select

        from server.database import get_sync_session
        from server.models import Account
    except Exception as exc:  # noqa: BLE001
        return None, None, f"无法导入数据库模块：{type(exc).__name__}: {exc}"

    try:
        with get_sync_session() as session:
            main = (
                session.execute(
                    select(Account).where(Account.is_system_main.is_(True)).limit(1)
                )
            ).scalars().first()
            if main is None:  # 兜底：首个 role=main
                main = (
                    session.execute(
                        select(Account).where(Account.role == "main").limit(1)
                    )
                ).scalars().first()
            if main is None:
                return None, None, "数据库中找不到系统主账号（is_system_main / role=main）"
            return main.id, getattr(main, "username", None), None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"查询账号失败：{type(exc).__name__}: {exc}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="清洗子账号继承的主账号模型配置（幂等）")
    ap.add_argument("--dry-run", action="store_true", help="只报告将要做的改动，不写盘")
    args = ap.parse_args(argv)

    tag = "[DRY]" if args.dry_run else "[FIX]"

    tenants_root = _resolve_tenants_root()
    if not tenants_root.exists():
        print(f"❌ 租户根不存在：{tenants_root}")
        return 2

    main_id, main_name, err = _resolve_main_account()
    if err:
        print(f"❌ {err}")
        return 2

    # 账号名映射（仅用于日志可读性；查不到就显示 id）
    user_map: Dict[str, str] = {}
    try:
        from sqlalchemy import select

        from server.database import get_sync_session
        from server.models import Account

        with get_sync_session() as session:
            for a in session.execute(select(Account)).scalars().all():
                user_map[a.id] = a.username
    except Exception:  # noqa: BLE001
        pass

    scanned = 0
    fixed = 0

    for tenant_dir in sorted(p for p in tenants_root.iterdir() if p.is_dir()):
        scanned += 1
        aid = tenant_dir.name
        label = f"{aid} ({user_map.get(aid, '?')})"

        if aid == main_id:
            print(f"[SKIP] {label} : 系统主账号，跳过（网关主人）")
            continue

        cfg_dir = tenant_dir / "config"
        if not cfg_dir.exists():
            continue

        checks = [
            ("config/models.yaml", NEUTRAL_MODELS, _models_polluted),
            ("config/model_mapping.yaml", NEUTRAL_MAPPING, _mapping_polluted),
            ("config/custom_providers.yaml", NEUTRAL_PROVIDERS, _providers_polluted),
        ]

        for rel, neutral, detector in checks:
            path = tenant_dir / rel
            if not path.exists():
                continue
            data, perr = _load_yaml(path)
            if perr is not None:
                print(f"[SKIP] {label} {rel} : 解析失败 {perr}")
                continue
            reason = detector(data)
            if not reason:
                continue
            print(f"{tag} {label} {rel} : {reason} → 换中性模板")
            fixed += 1
            if not args.dry_run:
                try:
                    _atomic_write(path, neutral)
                except Exception as exc:  # noqa: BLE001
                    print(f"[ERR ] {label} {rel} : 写盘失败 {type(exc).__name__}: {exc}")

    print(f"\n共扫描 {scanned} 个租户，{'将要修复' if args.dry_run else '已修复'} {fixed} 处"
          f"（跳主账号 {main_id}{f' / {main_name}' if main_name else ''}）")
    if args.dry_run:
        print("（--dry-run：未写盘。去掉 --dry-run 才会真正修改）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
