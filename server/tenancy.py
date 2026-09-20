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
#
# ⚠️ `.engine_state` 必须在这里「建」而不是「拷」：
# 它是引擎状态目录（`{id}_input.json` / `{id}_output.json` / `{id}_events.jsonl`），
# 在 COPY_EXCLUDE 里（运行时产物绝不继承别人的），但**必须存在**——
# 引擎启动的第一步就是往它里面写 input.json，目录不存在会直接
# `[Errno 2] No such file or directory` → 任务创建后 5ms 内 escalated，
# 用户看到「任务已启动」但永远没有报告（2026-09-19 实测事故，见
# VERIFICATION_ENGINE_STATE_DIR.md）。所以此处只建目录（mkdir，
# parents+exist_ok 幂等），不放任何内容。
RESOURCE_DIRS: List[str] = [
    "config",
    "agents",
    "gates",
    "templates",
    "skills",
    "experts",
    ".secrets",
    ".audit",
    ".engine_state",
]

# 从模板拷贝时**必须排除**的目录/文件：
# - .secrets：密钥不继承。子账号必须自己配自己的 key（否则就是共享凭据，等于没隔离）
# - .audit / outputs / .engine_state：运行时产物，拷过去会串别人的日志与任务
COPY_EXCLUDE: List[str] = [".secrets", ".audit", "outputs", ".engine_state"]

# ---------------------------------------------------------------------------
# 子账号中性模板（DESIGN_subaccount_model_isolation.md §3-D1）
#
# 为什么需要：`models.yaml` / `model_mapping.yaml` / `custom_providers.yaml` 三份文件承载的是
# **主账号自己的模型归属物**——端点定义（base_url + `${NEWAPI_API_KEY}`）与模型名。密钥虽然
# 走全局 env 展开（不落盘），但**占位符本身就等于密钥跟着来了**：子账号一旦继承，每次 LLM 调用
# 都打在主账号的 new-api 上、烧主账号的额度。这正是"假隔离"。
#
# 故子账号首次激活时**不继承**这三份，改写入下述中性骨架：端点清空、映射留空、provider 清空、
# 并关闭网关动态拉取（否则子账号的模型下拉会去拉主账号网关的真实模型列表）。
# 骨架里刻意不填任何模型名——填了就是"能跑但跑在别人身上"的假配置（禁假配置铁律）。
# ---------------------------------------------------------------------------
NEUTRAL_MODELS_YAML = """# config/models.yaml · 子账号模板（注册时自动生成，不继承主账号）
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

NEUTRAL_MODEL_MAPPING_YAML = """# config/model_mapping.yaml · 子账号模板（注册时自动生成）
#
# 子账号需**自己**把角色 / 审核闸映射到你所配置的模型。
# 最快路径：打开「设置 → 模型映射」，base / model 都是下拉框，直接选即可（无需手填）。
#
# 若手写本文件，务必注意（2026-09-19 实测事故：照旧指引手填裸模型名 → 请求被打到
# 错误的厂商 → HTTP 503 model_not_found → 三道审核闸全部静默降级放行、报告仍标 done）：
#   1) 先到「设置 → 自定义 API」添加 Provider（base_url + API Key）与模型 id；
#   2) model 必须填**接口 id**，格式 custom:<provider id>:<模型 id>
#      例：custom:agent:agnes-3.0-flash
#      只写裸名（如 agnes-3.0-flash）会因命中不了接口而被**回落到默认端点**，
#      可能把模型名发给完全不相干的厂商并报 503；
#   3) base 填该 Provider 的 id（与 custom_providers.yaml 的 id 一致）。它是「来源标签」，
#      **不参与路由**，仅用于硬约束校验：gates.X.base 必须 != roles[reviews].base（防自审包庇）
#      → 至少需要两个不同来源的 Provider 才能满足硬约束；
#   4) chat.tool_model 同样填接口 id（对话入口自主调工具用；留空会回落网关别名
#      auto-chat，子账号没有该别名 → 必然 503）。
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

NEUTRAL_CUSTOM_PROVIDERS_YAML = "providers: []\n"

# key = 相对租户根的路径
NEUTRAL_SEED_FILES: dict = {
    "config/models.yaml": NEUTRAL_MODELS_YAML,
    "config/model_mapping.yaml": NEUTRAL_MODEL_MAPPING_YAML,
    "config/custom_providers.yaml": NEUTRAL_CUSTOM_PROVIDERS_YAML,
}


def _write_neutral_seed_files(root: Path, seed_root: Optional[Path] = None) -> None:
    """给子账号写入中性模型模板（原子写）。

    **覆盖判定刻意保守**：仅当目标文件「不存在」或「内容与主账号原样副本逐字节相同」时才写。
    即——只要用户动过（自配了 provider / 填了映射），就绝不覆盖。这样重复 approve 也不会
    把子账号已配好的模型抹掉（`POST /accounts/{id}/approve` 对已激活账号可被重复调用）。
    """
    for rel, text in NEUTRAL_SEED_FILES.items():
        dst = root / rel
        if dst.exists():
            src = (seed_root / rel) if seed_root else None
            if src is None or not src.exists():
                continue  # 无法判定"是否原样继承" → 保守不动
            try:
                if dst.read_bytes() != src.read_bytes():
                    continue  # 内容已不同于主账号 = 用户改过 → 保留
            except OSError:
                continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, dst)
        except OSError:
            # 播种失败不阻断注册/审批主链路；缺文件时加载器会"诚实报错"而非借别人的端点
            pass

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
        # 子账号：**不继承**主账号的模型/密钥归属物（models.yaml / model_mapping.yaml /
        # custom_providers.yaml），改写入中性骨架。拷贝之后再做，用"是否仍是主账号原样副本"
        # 判定是否覆盖（见 _write_neutral_seed_files），因此不会抹掉用户已配好的模型。
        # 设计依据：DESIGN_subaccount_model_isolation.md §3-D1
        _write_neutral_seed_files(root, seed_root)
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
