"""
多租户资源根解析（Phase 1 · 账号体系）

设计来源：DESIGN_account_hierarchy.md §3 / §6.3

核心契约（务必守住，破一条就是越权读）：
1. 所有可配置资源（模型映射 / 密钥 / MCP / 自定义 Provider / 技能 / 专家 /
   Agent-Gate 提示词 / 任务模板 / 审计）都必须经解析本模块，禁止再出现
   `BASE / "config"` 之类的写死路径。
2. 当前账号由 ContextVar 承载，由 FastAPI 鉴权依赖注入；**没有上下文就抛错**
   （fail loud），绝不静默回落全局根目录——静默回落 = 假隔离。
3. 引擎是独立子进程，租户根必须经 input.json 透传后在子进程 `bind_account`。
"""

from __future__ import annotations

import os
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Iterable, List, Optional

# 项目根：server/ 的上一级
BASE = Path(__file__).resolve().parent.parent

# 租户根（compose 挂载 ./tenants:/app/tenants）
TENANTS_ROOT = Path(os.getenv("TENANTS_ROOT", str(BASE / "tenants")))

# 每个租户内部的资源目录
RESOURCE_DIRS: List[str] = [
    "config",
    "agents",
    "gates",
    "templates",
    "skills",
    "experts",
    ".secrets",
    ".audit",
]

# 从模板拷贝时**必须排除**的目录/文件：
# - .secrets：密钥不继承。子账号必须自己配自己的 key（否则就是共享凭据，等于没隔离）
# - .audit / outputs / .engine_state：运行时产物，拷过去会串别人的日志与任务
COPY_EXCLUDE: List[str] = [".secrets", ".audit", "outputs", ".engine_state"]

_current_account_id: ContextVar[Optional[str]] = ContextVar("current_account_id", default=None)


class TenancyError(RuntimeError):
    """租户上下文缺失 / 越权。刻意显式，禁止静默降级。"""


# ---------------------------------------------------------------- 上下文管理


def set_current_account(account_id: str) -> None:
    """绑定当前请求的租户。由鉴权依赖调用。"""
    if not account_id:
        raise TenancyError("account_id 不可为空")
    _current_account_id.set(account_id)


def reset_current_account(token=None) -> None:
    """重置上下文（测试用）。"""
    _current_account_id.reset(token) if token is not None else _current_account_id.set(None)


# 迁移期显式逃生阀：仅当环境变量 RAT_LEGACY_GLOBAL=1 时，资源根回落到仓库全局目录。
# 这是「明确打开」而不是「无人说话就回落」，且每次解析都会 WARN 日志，便于抓出漏改点。
# Phase 2 前必须删除（留着就是假隔离）。
LEGACY_GLOBAL = os.getenv("RAT_LEGACY_GLOBAL", "").strip() == "1"


def _warn_legacy_once() -> None:
    if not hasattr(_warn_legacy_once, "_done"):
        import logging
        logging.getLogger("tenancy").warning(
            "[MIGRATION] RAT_LEGACY_GLOBAL=1 生效中：资源根回落到全局目录，隔离未真正启用。请在补完认证上下文后关闭。"
        )
        setattr(_warn_legacy_once, "_done", True)


def current_account_id(allow_none: bool = False) -> Optional[str]:
    aid = _current_account_id.get()
    if aid is None:
        # 引擎子进程 / CLI：允许用显式环境变量提供账号（仍然是有意为之，不是隐式回落）
        aid = os.getenv("RAT_ACCOUNT_ID", "").strip() or None
        if aid:
            set_current_account(aid)
    if aid is None:
        if LEGACY_GLOBAL:
            _warn_legacy_once()
            return None
        if allow_none:
            return None
        raise TenancyError(
            "当前线程没有租户上下文——资源路径无法确定。"
            "请确保在请求链路（FastAPI 依赖已注入 set_current_account）或引擎子进程的 bind_account 之后调用。"
        )
    return aid


def bind_account(account_id: str) -> None:
    """引擎子进程入口：input.json 透传来 tenant_root/account_id 后立即调用。"""
    set_current_account(account_id)


# -------------------------------------------------------------------- 路径


def account_root(account_id: Optional[str] = None) -> Path:
    # 严格解析：无上下文即抛 TenancyError（绝不静默回落全局）。
    # 仅当显式打开 RAT_LEGACY_GLOBAL 时 current_account_id 才返回 None。
    aid = account_id or current_account_id()
    if aid is None:
        return Path(BASE)  # 仅 LEGACY_GLOBAL 可达，且上游已 WARN
    return Path(TENANTS_ROOT) / aid


def tenant_path(*parts: str, account_id: Optional[str] = None) -> Path:
    """在当前（或指定）租户根下拼路径。"""
    root = account_root(account_id)
    p = root
    for part in parts:
        p = p / part
    return p


def config_path(filename: str, account_id: Optional[str] = None) -> Path:
    return tenant_path("config", filename, account_id=account_id)


def secrets_path(filename: str, account_id: Optional[str] = None) -> Path:
    return tenant_path(".secrets", filename, account_id=account_id)


def audit_path(*sub: str, account_id: Optional[str] = None) -> Path:
    return tenant_path(".audit", *sub, account_id=account_id)


# ---------------------------------------------------------------- 目录供应


def ensure_account_layout(
    account_id: str,
    seed_from: Optional[str] = None,
    copy_payload: bool = True,
    seed_from_base: bool = False,
) -> Path:
    """
    确保某账号的资源目录齐备。

    seed_from: 模板账号 id（通常是主账号）。仅拷贝「配置类」内容，
               不拷密钥/审计/运行时产物（见 COPY_EXCLUDE）。
    seed_from_base: 用仓库全局 BASE（镜像内置默认 config/agents/gates/templates/skills/experts）
               做初始播种——**仅主账号（系统首个账号）** 使用，使其拥有默认资源。
    """
    root = account_root(account_id)
    for d in RESOURCE_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    if seed_from_base:
        if copy_payload and BASE.exists():
            # 只拷「配置类」资源目录；密钥/审计/运行时产物（.secrets/.audit/.engine_state/
            # outputs）即使 BASE 里存在也绝不继承（主账号必须自己配 key，否则等于没隔离）。
            for d in ("config", "agents", "gates", "templates", "skills", "experts"):
                src = BASE / d
                if not src.exists():
                    continue
                _copy_tree_filtered(src, root / d)
    elif seed_from and seed_from != account_id and copy_payload:
        seed_root = account_root(seed_from)
        if seed_root.exists():
            _copy_tree_filtered(seed_root, root)
    return root


def _copy_tree_filtered(src: Path, dst: Path) -> None:
    for item in sorted(src.iterdir()):
        if item.name in COPY_EXCLUDE:
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def archive_account_layout(account_id: str) -> Optional[Path]:
    """停用/删除账号：资源目录归档而非级联删除（避免误删数据）。"""
    root = account_root(account_id)
    if not root.exists():
        return None
    archived = Path(str(root) + ".deleted")
    n = 1
    while archived.exists():
        archived = Path(f"{root}.deleted.{n}")
        n += 1
    root.rename(archived)
    return archived


def list_account_layouts() -> Iterable[str]:
    if not TENANTS_ROOT.exists():
        return []
    return [p.name for p in TENANTS_ROOT.iterdir() if p.is_dir()]
