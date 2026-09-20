"""
server/admin.py · 配置控制台后端（配置层）

  M8-1 · Models：config/model_mapping.yaml 的模型映射 UI 化
  M8-2 · Agents：agents/*.md 的角色提示词 UI 化

职责：把「原本只能 SSH 进容器改 yaml」的配置，暴露为受控的 HTTP API。

设计要点（对应 DESIGN_SETTINGS_CONSOLE.md §3.1 / §4 / §5.1 / §6）：
1. **保留 yaml 注释**：config/model_mapping.yaml 含 5 次变更历史与风险标注，
   普通 yaml.safe_dump 会全部丢弃 → 改用 ruamel.yaml round-trip 模式（CommentedMap），
   并**只更新变更字段值**而非整体替换，从而保留注释与键序。
2. **原子化写盘**：先写 .tmp 再 os.replace()，避免半写状态。
3. **写前备份**：把改动前快照复制到 .audit/models/<ts>-model_mapping.yaml.bak
4. **校验前置**：写盘前跑硬约束（Gate.base != 所审 Agent.base），违规返回 400 不落盘。
5. **审计日志**：每次改动写 .audit/models/<ts>-<action>.json，含 before/after diff。

热加载说明（实测结论，勿过度工程）：
   orchestrator.run_report() 每次执行都会重新调用 load_model_mapping()
   （orchestrator.py:1004 → :425），因此**改完 yaml 立即对下一个任务生效**，
   无需引入 watchfiles 监听或手动触发 reload。正在执行的任务不受影响（预期行为）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import contextvars
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

BASE = Path(__file__).resolve().parent.parent          # /app
LEGACY_CONFIG_DIR = BASE / "config"


def _audit_sub(sub: str) -> Path:
    """按当前账号命名空间解析审计子目录（各自一份审计，不串日志）。"""
    try:
        from server import tenancy
        return tenancy.audit_path(sub)
    except ImportError:
        return BASE / ".audit" / sub



def _cfg_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.tenant_path("config")
    except ImportError:
        return LEGACY_CONFIG_DIR

LEGACY_MAPPING_PATH = LEGACY_CONFIG_DIR / "model_mapping.yaml"


def _mapping_path() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.config_path("model_mapping.yaml")
    except ImportError:
        return LEGACY_MAPPING_PATH

LEGACY_AUDIT_DIR = BASE / ".audit" / "models"


def _models_audit_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.audit_path("models")
    except ImportError:
        return LEGACY_AUDIT_DIR


router = APIRouter()

# 应急后门口令：**未配置即该通道不存在**（旧行为「未配置就放行」是裸奔缺口，Phase 1 已堵）。
# 正常路径是登录账号后带 Bearer JWT（见 _require_admin）。
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


# ---------------------------------------------------------------------------
# ruamel.yaml（保留注释的 round-trip 加载/回写）
# ---------------------------------------------------------------------------

def _yaml_rt():
    """构造 round-trip YAML 实例。ruamel 缺失时给明确错误，不静默降级为 PyYAML
    （静默降级 = 注释被吞，属破坏性写盘，必须 fail loud）。"""
    try:
        from ruamel.yaml import YAML  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail="ruamel.yaml 未安装：为保证 model_mapping.yaml 的变更历史注释不丢失，"
                   "配置回写强制依赖 ruamel.yaml（PyYAML 会丢弃全部注释）。"
                   "请 pip install ruamel.yaml 后重启容器。",
        ) from exc
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # 避免长行被自动折行破坏注释对齐
    return y


def _load_commented() -> Any:
    """加载为 CommentedMap（保留注释与键序）。"""
    if not _mapping_path().exists():
        raise HTTPException(status_code=404, detail=f"配置文件不存在：{_mapping_path()}")
    return _yaml_rt().load(_mapping_path().read_text(encoding="utf-8"))


def _dump_commented(data: Any) -> str:
    """把 CommentedMap 序列化为字符串（保留注释）。"""
    import io

    buf = io.StringIO()
    _yaml_rt().dump(data, buf)
    return buf.getvalue()


def _to_plain(obj: Any) -> Any:
    """CommentedMap → 普通 dict/list（供 JSON 序列化与前端消费）。"""
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------

# 当前请求上下文（由 server.main._RequestContextMiddleware 每请求注入）。
# _require_admin 被 ~50 个端点**手动**调用（request=None），无法直接拿到 FastAPI
# 注入的 Request，故经由本 ContextVar 回退读取 Authorization: Bearer，
# 修复「登录态下访问 /admin/* 仍 401、进而被前端清 token 级联登出」的缺陷。
_current_request: "contextvars.ContextVar[Optional[Request]]" = contextvars.ContextVar(
    "rat_current_request", default=None
)


def _require_admin(
    x_admin_token: Optional[str] = Header(None),
    request: Optional[Request] = None,
) -> str:
    """资源端鉴权（Phase 1 · DESIGN_account_hierarchy §6.3「登录即租户」）。

    优先级：
      1) `Authorization: Bearer <jwt>` → 任何 active 账号，操作**自己的**租户命名空间
      2) `X-Admin-Token` → **仅在 .env 显式配置 ADMIN_TOKEN 时**存在的应急后门，
         自动落到系统主账号以保证有租户上下文

    ⚠️ 变更（堵缺口 R7）：旧实现在未配置 ADMIN_TOKEN 时返回 "dev_no_token" 直接放行，
    等于所有 admin 写操作裸奔。现已删除该分支——无凭据一律 401。
    """
    from server import tenancy

    # 手动调用场景下 request 为 None（~50 个端点签名未注入 Request），
    # 回退到中间件注入的 ContextVar 读取 Bearer，杜绝登录态下 admin 端点 401。
    req = request if request is not None else _current_request.get(None)
    auth_header = req.headers.get("authorization") if req is not None else None
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        secret = os.getenv("AUTH_SECRET", "").strip()
        if not secret:
            raise HTTPException(
                status_code=500,
                detail="服务端未配置 AUTH_SECRET，无法校验登录令牌；请在 .env 设置后重启容器。",
            )
        try:
            from jose import jwt as _jwt

            payload = _jwt.decode(token, secret, algorithms=["HS256"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="令牌无效或已过期，请重新登录") from exc
        aid = payload.get("sub")
        if not aid:
            raise HTTPException(status_code=401, detail="令牌缺少账号主体")
        tenancy.set_current_account(aid)
        return f"account:{aid}"

    # ---- X-Admin-Token 后门（未配置 = 该通道不存在，不再放行）----
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="服务端未配置 ADMIN_TOKEN，且请求未携带登录令牌。请先登录（/login）后重试。",
        )
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="缺失或无效的 X-Admin-Token")

    # 后门也需要租户上下文，否则下游 tenant_path 无法确定归属 → 落到系统主账号
    try:
        from server.database import get_sync_session
        from server.models import Account
        from sqlalchemy import select as _select

        with get_sync_session() as s:
            row = s.execute(
                _select(Account.id).where(Account.is_system_main.is_(True)).limit(1)
            ).first()
        if row:
            tenancy.set_current_account(row[0])
            return f"admin:{row[0]}"
    except Exception:  # noqa: BLE001
        pass  # 数据库不可用时：后门仅通过校验，后续 tenant_path 会 fail loud（不静默回落全局）
    return "admin"


# ---------------------------------------------------------------------------
# 校验（DESIGN_SETTINGS_CONSOLE §5.1）
# ---------------------------------------------------------------------------

def _validate_mapping(m: Any) -> List[str]:
    """返回错误列表（空列表 = 通过）。

    硬约束：
      1. 每个 role 必须有 base + model
      2. 每个 gate 必须有 base + model + reviews
      3. **gates.X.base != roles[reviews].base**（防自审包庇）
      4. reviews 指向的 role 必须存在
    """
    errors: List[str] = []
    roles = _to_plain(m.get("roles") or {})
    gates = _to_plain(m.get("gates") or {})

    if not roles:
        errors.append("roles 段为空：至少需要一个角色")

    for name, cfg in roles.items():
        cfg = cfg or {}
        if not cfg.get("base"):
            errors.append(f"roles.{name}.base 不能为空")
        if not cfg.get("model"):
            errors.append(f"roles.{name}.model 不能为空")

    for gname, gcfg in gates.items():
        gcfg = gcfg or {}
        if not gcfg.get("base"):
            errors.append(f"gates.{gname}.base 不能为空")
        if not gcfg.get("model"):
            errors.append(f"gates.{gname}.model 不能为空")
        rev = gcfg.get("reviews")
        if not rev:
            errors.append(f"gates.{gname}.reviews 不能为空")
            continue
        if rev not in roles:
            errors.append(f"gates.{gname}.reviews 指向不存在的角色：{rev}")
            continue
        # 硬约束 3：异基座
        if gcfg.get("base") and roles[rev].get("base") and gcfg["base"] == roles[rev]["base"]:
            errors.append(
                f"硬约束违反：gates.{gname}.base({gcfg['base']}) 与所审角色 "
                f"roles.{rev}.base({roles[rev]['base']}) 相同，会导致自审包庇"
            )

    return errors


# ---------------------------------------------------------------------------
# 原子写盘 + 备份 + 审计
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)          # 原子替换


def _write_audit(action: str, before: Any, after: Any, operator: str,
                 validation: str, errors: Optional[List[str]] = None,
                 subdir: str = "models",
                 src_path: Optional[Path] = None) -> str:
    """写审计记录 + 改动前快照。

    M8-2 起泛化：models（yaml）与 agents（md）共用，由 subdir / src_path 区分。
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    audit_dir = _audit_sub(subdir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    src = src_path or _mapping_path()
    ext = (src.suffix or "").lstrip(".") or "txt"

    # 改动前完整快照（.bak）
    try:
        shutil.copy2(src, audit_dir / f"{ts}-{action}.{ext}.bak")
    except Exception:
        pass

    # 文件相对 /app 的路径（审计记录里标明改了哪个文件）
    try:
        rel = str(src.relative_to(BASE))
    except Exception:
        rel = str(src)

    rec = {
        "ts": ts,
        "operator": operator,
        "action": action,
        "file": rel,
        "before": _to_plain(before),
        "after": _to_plain(after),
        "validation": validation,
        "errors": errors or [],
    }
    (audit_dir / f"{ts}-{action}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ts


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class MappingPut(BaseModel):
    """整体替换 roles / gates / eval 的 base+model（不改注释与其他段）。"""
    roles: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    gates: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    eval: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/admin/models")
async def get_models(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """读取当前模型映射（含 roles / gates / eval）。

    鉴权：与写操作一致（第 11 轮独立审议纠出——本端点原形参写作 `_: str = Header(None)`，
    既未绑定 header 名也未调用 _require_admin，导致即使配置了 ADMIN_TOKEN，读操作仍不鉴权）。
    """
    _require_admin(x_admin_token)
    m = _load_commented()
    errors = _validate_mapping(m)
    return {
        "mapping": _to_plain(m),
        "validation": {"ok": not errors, "errors": errors},
        "meta": {
            "file": "config/model_mapping.yaml",
            "token_required": bool(ADMIN_TOKEN),
            "hot_reload": "run_report 每次重新 load_model_mapping，改完下一个任务即生效",
        },
    }


@router.put("/admin/models")
async def put_models(
    payload: MappingPut,
    x_admin_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """整体更新 roles / gates / eval 的 base+model。

    只更新提交字段的值，**保留 yaml 中的全部注释与键序**。
    校验失败返回 400 且不落盘。
    """
    operator = _require_admin(x_admin_token)

    data = _load_commented()          # CommentedMap（带注释）
    before = _to_plain(data)

    # 逐字段更新（保留注释：只赋值，不重建容器）
    for role, cfg in (payload.roles or {}).items():
        if role not in data.get("roles", {}):
            raise HTTPException(status_code=404, detail=f"角色不存在：{role}（新增角色属 M8-5 范围）")
        for k, v in (cfg or {}).items():
            if k in ("base", "model"):
                data["roles"][role][k] = v

    for gate, cfg in (payload.gates or {}).items():
        if gate not in data.get("gates", {}):
            raise HTTPException(status_code=404, detail=f"闸不存在：{gate}（新增闸属 M8-5 范围）")
        for k, v in (cfg or {}).items():
            if k in ("base", "model"):
                data["gates"][gate][k] = v

    if payload.eval is not None:
        if "eval" not in data:
            data["eval"] = {}
        for k, v in payload.eval.items():
            if k in ("base", "model"):
                data["eval"][k] = v

    # 校验（写盘前）
    errors = _validate_mapping(data)
    if errors:
        _write_audit("rejected", before, _to_plain(data), operator, "fail", errors)
        raise HTTPException(status_code=400, detail={"message": "校验未通过，未落盘", "errors": errors})

    # 原子落盘
    _atomic_write(_mapping_path(), _dump_commented(data))

    # 回读验证（确认注释保留 + 可被 orchestrator 的 yaml.safe_load 解析）
    try:
        import yaml as pyyaml
        reparsed = pyyaml.safe_load(_mapping_path().read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"落盘后回读解析失败：{exc}") from exc

    after = _to_plain(reparsed)
    ts = _write_audit("update", before, after, operator, "pass")

    return {
        "ok": True,
        "audit_id": ts,
        "operator": operator,
        "mapping": after,
        "validation": {"ok": True, "errors": []},
        "note": "已落盘；orchestrator.run_report 每次重新加载，下一个任务即生效（无需重启）",
    }


@router.post("/admin/models/preview")
async def preview_models(
    payload: MappingPut,
    x_admin_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """解析预检：把提交的映射逐条解析成「实际会打到哪个端点 / 真实模型名」。

    为什么需要（DESIGN_model_mapping_uf.md 档 1）：
    `base` 只是「来源标签」，**完全不参与路由**；真正决定请求发往哪家的是 `model`
    能否命中一个「接口 id」（`custom:<provider>:<模型>`）。用户手填裸模型名时，
    引擎会**静默回落默认端点** → 可能把模型名发给完全不相干的厂商
    （实测对方回 `HTTP 503 model_not_found`）。本接口把解析结果显式暴露给 UI，
    消灭「异基座绿勾骗人」。

    实现复用引擎同款 `resolve_model_ref()`（单一事实源），**禁止在此重写解析规则**。
    响应体**不含任何密钥**。
    """
    _require_admin(x_admin_token)

    from orchestrator import (
        build_bare_index,
        load_model_interfaces_and_endpoints,
        resolve_model_ref,
    )

    eps_list, if_list, _gw, default_id = load_model_interfaces_and_endpoints()
    endpoints = {e["id"]: e for e in eps_list}
    interfaces = {i["id"]: i for i in if_list}
    bare = build_bare_index(interfaces)

    items: List[Dict[str, Any]] = []

    def _probe(path: str, model: Any) -> None:
        m = (model or "").strip() if isinstance(model, str) else ""
        if not m:
            return
        try:
            eid, real, _cfg, matched, disambiguated = resolve_model_ref(
                m, endpoints, interfaces, default_id, bare
            )
            items.append({
                "path": path,
                "model": m,
                "endpoint_id": eid,
                "real_model": real,
                "matched": matched,
                "disambiguated": disambiguated,
                "error": None,
            })
        except Exception as exc:                      # 诚实暴露，不吞
            items.append({
                "path": path,
                "model": m,
                "endpoint_id": None,
                "real_model": None,
                "matched": False,
                "disambiguated": False,
                "error": str(exc),
            })

    for role, cfg in (payload.roles or {}).items():
        _probe(f"roles.{role}", (cfg or {}).get("model"))
    for gate, cfg in (payload.gates or {}).items():
        _probe(f"gates.{gate}", (cfg or {}).get("model"))
    if payload.eval:
        _probe("eval", payload.eval.get("model"))

    return {
        "items": items,
        "default_endpoint_id": default_id,
        "endpoint_ids": sorted(endpoints),
        "interface_ids": sorted(interfaces),
        "note": (
            "base 只是来源标签、不参与路由；真正决定厂商的是 model 能否命中接口 id。"
            "matched=false 表示未命中接口、已回落默认端点（依赖端点顺序，属高危写法）。"
        ),
    }


@router.get("/admin/models/audit")
async def list_audit(
    limit: int = 20,
    x_admin_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """列出最近的模型映射改动审计记录（倒序）。"""
    _require_admin(x_admin_token)
    if not _models_audit_dir().exists():
        return {"items": [], "total": 0}
    files = sorted(_models_audit_dir().glob("*.json"), reverse=True)[:limit]
    items = []
    for f in files:
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return {"items": items, "total": len(items)}


@router.get("/admin/status")
async def admin_status() -> Dict[str, Any]:
    """配置控制台健康状态。"""
    try:
        m = _load_commented()
        errors = _validate_mapping(m)
        ok = not errors
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": ok,
        "errors": errors,
        "token_required": bool(ADMIN_TOKEN),
        "ruamel_available": True,
        "config_file": str(_mapping_path()),
        "modules": {
            "models": True,
            "agents": _agents_dir().exists(),
            "gates": _gates_dir().exists(),
            "templates": _templates_dir().exists(),
            "agents_library": _library_path().exists(),
        },
        "agent_names": _agent_names(),
        "agents_mounted": (_agents_dir() / "researcher.md").exists(),
        "gates_mounted": (_gates_dir() / "review.md").exists(),
        "templates_mounted": (_templates_dir() / "standard_research" / "manifest.yaml").exists(),
    }


# ---------------------------------------------------------------------------
# M8-2 · Agent prompt 编辑（agents/*.md）
#
# 热加载说明（实测，orchestrator.py:336-340）：
#   build_agent_system(role) → _load_md(f"agents/{role.lower()}.md") → 每次现读磁盘。
#   因此 UI 改完 prompt，**下一个任务立即生效**，无需重启容器。
#   与 model_mapping 同理，不引入 watchfiles（避免过度工程）。
# ---------------------------------------------------------------------------

LEGACY_AGENTS_DIR = BASE / "agents"


def _agents_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.tenant_path("agents")
    except ImportError:
        return LEGACY_AGENTS_DIR

AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_PROMPT_CHARS = 200_000       # 防误粘贴巨型内容（正常 prompt ~4KB）
AUDIT_PREVIEW_CHARS = 200        # 审计里只存摘要，完整快照靠 .bak


def _agent_names() -> List[str]:
    """白名单：直接派生自 agents/ 目录的实际文件（stem）。

    派生而非硬编码的理由：
      1. 天然防路径穿越 —— name 必须**等于某个真实 stem**，`../` 之类不可能命中；
      2. M8-5 新增角色后无需改这里，目录里多一个 md 就多一个可编辑角色。
    """
    if not _agents_dir().exists():
        return []
    return sorted(p.stem for p in _agents_dir().glob("*.md"))


def _resolve_agent(name: str) -> Path:
    """角色名 → 文件路径。非法名/不存在一律 404（不泄露目录结构细节）。"""
    if not name or not AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"非法的角色名：{name!r}")
    if name not in _agent_names():
        raise HTTPException(status_code=404, detail=f"角色不存在：{name}")
    return _agents_dir() / f"{name}.md"


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def _validate_prompt(content: str) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。errors 非空 → 拒绝落盘；warnings 仅提示。"""
    import hashlib

    errors: List[str] = []
    warnings: List[str] = []
    void = content or ""

    if not void.strip():
        errors.append("提示词内容不能为空（清空会导致该角色失去行为定义）")
    if len(void) > MAX_PROMPT_CHARS:
        errors.append(
            f"内容过长：{len(void)} 字符 > 上限 {MAX_PROMPT_CHARS}，疑似误粘贴"
        )
    if void.strip() and not _first_heading(void):
        warnings.append("缺少一级标题（# ...）：引擎仍可运行，但不利于维护")
    if void.count("```") % 2 == 1:
        warnings.append("代码块围栏 ``` 数量为奇数，疑似未闭合")

    # 审计摘要用
    _hash = hashlib.sha256(void.encode("utf-8")).hexdigest()[:12]
    return errors, warnings


def _prompt_digest(text: str) -> Dict[str, Any]:
    """审计里存内容摘要（完整快照由 .bak 承载，避免审计文件无限膨胀）。"""
    import hashlib

    return {
        "chars": len(text),
        "lines": len(text.splitlines()),
        "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "preview": (text[:AUDIT_PREVIEW_CHARS] + ("…" if len(text) > AUDIT_PREVIEW_CHARS else "")),
    }


class AgentPut(BaseModel):
    content: str


@router.get("/admin/agents")
async def list_agents(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """列出可编辑的 Agent 角色（派生自 agents/*.md）。"""
    _require_admin(x_admin_token)
    items = []
    for name in _agent_names():
        p = _agents_dir() / f"{name}.md"
        st = p.stat()
        text = p.read_text(encoding="utf-8")
        errors, warnings = _validate_prompt(text)
        items.append({
            "name": name,
            "title": _first_heading(text) or name,
            "size": st.st_size,
            "chars": len(text),
            "lines": len(text.splitlines()),
            "updated": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "headings": [l.strip() for l in text.splitlines() if l.strip().startswith("##")][:8],
            "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
        })
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "dir": "agents/",
            "hot_reload": "build_agent_system 每次现读 agents/*.md，改完下一个任务即生效",
            "max_chars": MAX_PROMPT_CHARS,
        },
    }


@router.get("/admin/agents/{name}")
async def get_agent(
    name: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """读取单个角色的完整 prompt。"""
    _require_admin(x_admin_token)
    path = _resolve_agent(name)
    text = path.read_text(encoding="utf-8")
    errors, warnings = _validate_prompt(text)
    return {
        "name": name,
        "file": f"agents/{name}.md",
        "content": text,
        "digest": _prompt_digest(text),
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
    }


@router.put("/admin/agents/{name}")
async def put_agent(
    name: str,
    payload: AgentPut,
    x_admin_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """保存角色 prompt（原子写盘 + 写前备份 + 审计）。"""
    operator = _require_admin(x_admin_token)
    path = _resolve_agent(name)
    before_text = path.read_text(encoding="utf-8")

    errors, warnings = _validate_prompt(payload.content)
    if errors:
        _write_audit(
            "rejected", _prompt_digest(before_text), _prompt_digest(payload.content),
            operator, "fail", errors, subdir="agents", src_path=path,
        )
        raise HTTPException(
            status_code=400, detail={"message": "校验未通过，未落盘", "errors": errors}
        )

    if payload.content == before_text:
        return {
            "ok": True,
            "changed": False,
            "note": "内容无变化，未写盘（避免产生无意义审计记录）",
            "warnings": warnings,
        }

    _atomic_write(path, payload.content)
    after_text = path.read_text(encoding="utf-8")   # 回读确认

    ts = _write_audit(
        "update", _prompt_digest(before_text), _prompt_digest(after_text),
        operator, "pass", warnings, subdir="agents", src_path=path,
    )

    return {
        "ok": True,
        "changed": True,
        "audit_id": ts,
        "operator": operator,
        "digest": _prompt_digest(after_text),
        "warnings": warnings,
        "note": "已落盘；build_agent_system 每次现读，下一个任务即生效（无需重启）",
    }


@router.get("/admin/agents/{name}/audit")
async def list_agent_audit(
    name: str, limit: int = 20, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出某角色 prompt 的改动审计（倒序）。"""
    _require_admin(x_admin_token)
    _resolve_agent(name)            # 非法名 → 404
    audit_dir = _audit_sub("agents")
    if not audit_dir.exists():
        return {"items": [], "total": 0}
    files = sorted(audit_dir.glob("*.json"), reverse=True)
    items = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 单个角色的审计（models 共用目录写法不同，这里按 file 过滤）
        if rec.get("file") == f"agents/{name}.md":
            items.append(rec)
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# M8-3 · Gate 审核规则编辑（gates/review.md）
#
# 热加载说明（实测，orchestrator.py:356-360）：
#   build_gate_system(gate_name, role) → _load_md("gates/review.md") → 每次现读磁盘。
#   关键：引擎对 GateA / GateB / GateC 均读取**同一个** review.md 全文件（再追加
#   "你现在执行 GateX，审核 Y 的产出"），因此本端点的资源是单一文件 review.md，
#   编辑即影响全部三道闸——不存在「按闸拆文件分别读取」的机制。
#
# 设计偏差（必须如实记录，详见 VERIFICATION §12）：
#   DESIGN_SETTINGS_CONSOLE.md §2.4 原写 /settings/gates/[gate] 分闸子路由 + 每闸一文件。
#   但引擎现实是全文件读取，且 gates/ 当前仅 review.md 一个文件。若强行按 per-gate 拆文件，
#   需同时改 orchestrator.build_gate_system 的分段加载逻辑——违背「改配置不碰引擎」立约。
#   故实现以单一资源为准：API 暴露 gates/review.md，UI 内以 ## 节做只读锚点导航。
# ---------------------------------------------------------------------------

LEGACY_GATES_DIR = BASE / "gates"


def _gates_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.tenant_path("gates")
    except ImportError:
        return LEGACY_GATES_DIR

GATE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _gate_names() -> List[str]:
    """白名单：派生自 gates/ 目录的实际文件（stem）。当前仅 review.md → ['review']。

    派生而非硬编码：天然防路径穿越；未来若拆分成 gate_a.md 等，目录多一个文件即多一个可编辑项。
    """
    if not _gates_dir().exists():
        return []
    return sorted(p.stem for p in _gates_dir().glob("*.md"))


def _resolve_gate(name: str) -> Path:
    """闸规则名 → 文件路径。非法名/不存在一律 404（不泄露目录结构细节）。"""
    if not name or not GATE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"非法的闸规则名：{name!r}")
    if name not in _gate_names():
        raise HTTPException(status_code=404, detail=f"闸规则不存在：{name}")
    return _gates_dir() / f"{name}.md"


@router.get("/admin/gates")
async def list_gates(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """列出可编辑的 Gate 审核规则（派生自 gates/*.md，当前为单文件 review）。"""
    _require_admin(x_admin_token)
    items = []
    for name in _gate_names():
        p = _gates_dir() / f"{name}.md"
        st = p.stat()
        text = p.read_text(encoding="utf-8")
        errors, warnings = _validate_prompt(text)
        items.append({
            "name": name,
            "title": _first_heading(text) or name,
            "size": st.st_size,
            "chars": len(text),
            "lines": len(text.splitlines()),
            "updated": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "headings": [l.strip() for l in text.splitlines() if l.strip().startswith("##")][:8],
            "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
        })
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "dir": "gates/",
            "hot_reload": "build_gate_system 每次现读 gates/review.md，改完下一个任务即生效（影响 GateA/B/C 全部三道闸）",
            "note": "引擎对每道闸均读取同一 review.md 全文件；DESIGN 原 per-gate 子路由已按引擎现实调整为单文件编辑",
            "max_chars": MAX_PROMPT_CHARS,
        },
    }


@router.get("/admin/gates/{name}")
async def get_gate(
    name: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """读取单个闸规则的完整内容。"""
    _require_admin(x_admin_token)
    path = _resolve_gate(name)
    text = path.read_text(encoding="utf-8")
    errors, warnings = _validate_prompt(text)
    return {
        "name": name,
        "file": f"gates/{name}.md",
        "content": text,
        "digest": _prompt_digest(text),
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
    }


@router.put("/admin/gates/{name}")
async def put_gate(
    name: str,
    payload: AgentPut,
    x_admin_token: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """保存闸规则（原子写盘 + 写前备份 + 审计）。"""
    operator = _require_admin(x_admin_token)
    path = _resolve_gate(name)
    before_text = path.read_text(encoding="utf-8")

    errors, warnings = _validate_prompt(payload.content)
    if errors:
        _write_audit(
            "rejected", _prompt_digest(before_text), _prompt_digest(payload.content),
            operator, "fail", errors, subdir="gates", src_path=path,
        )
        raise HTTPException(
            status_code=400, detail={"message": "校验未通过，未落盘", "errors": errors}
        )

    if payload.content == before_text:
        return {
            "ok": True,
            "changed": False,
            "note": "内容无变化，未写盘（避免产生无意义审计记录）",
            "warnings": warnings,
        }

    _atomic_write(path, payload.content)
    after_text = path.read_text(encoding="utf-8")   # 回读确认

    ts = _write_audit(
        "update", _prompt_digest(before_text), _prompt_digest(after_text),
        operator, "pass", warnings, subdir="gates", src_path=path,
    )

    return {
        "ok": True,
        "changed": True,
        "audit_id": ts,
        "operator": operator,
        "digest": _prompt_digest(after_text),
        "warnings": warnings,
        "note": "已落盘；build_gate_system 每次现读，下一个任务即生效（影响 GateA/B/C，无需重启）",
    }


@router.get("/admin/gates/{name}/audit")
async def list_gate_audit(
    name: str, limit: int = 20, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出某闸规则的改动审计（倒序）。"""
    _require_admin(x_admin_token)
    _resolve_gate(name)            # 非法名 → 404
    audit_dir = _audit_sub("gates")
    if not audit_dir.exists():
        return {"items": [], "total": 0}
    files = sorted(audit_dir.glob("*.json"), reverse=True)
    items = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 单个资源的审计（按 file 过滤）
        if rec.get("file") == f"gates/{name}.md":
            items.append(rec)
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items)}


# ===========================================================================
# M8-4：任务模板（templates/<name>/manifest.yaml）
# ---------------------------------------------------------------------------
# 设计定位（诚实边界，详见 VERIFICATION.md §13）：
#   当前引擎 build_graph 硬编码 3 Agent + 3 Gate 全链（orchestrator.py:961-990），
#   不读 templates/。本里程碑把「任务模板」从前端死字符串（web/src/stores/template.ts）
#   后端化为可 UI 增删改的资源，并被提交链路 create_task 真正读取（agents/gates
#   落库为任务元数据）。编排消费（按模板动态跑子集）留 M8-5 引擎改造。
#   · agents/gates 校验强制 == 已知 3+3 全集（防建引擎不认的子集 = 假配置）
#   · output_format 枚举仅 markdown（G6 渲染器 pdf/pptx/docx 剥离到 M9）
# ===========================================================================
import yaml as _yaml

# 公开 router：承载无需 admin token 的端点（提交页用的 /api/v1/templates）
public_router = APIRouter()

# ---------------------------------------------------------------------------
# 账号作用域读接口的鉴权（DESIGN_subaccount_model_isolation.md §6-补充）
#
# 背景（真机复现的跨账号配置泄漏）：下列 6 个只读接口原先挂在 `public_router` 上、
# **完全无鉴权**。FastAPI 里没有鉴权依赖 → `tenancy` 的 ContextVar 从未被设置 →
# 资源根在 `RAT_LEGACY_GLOBAL=1` 下回落到仓库全局目录（= 主账号的 config/）。
# 后果实测：**用子账号 wendy1 的浏览器调 GET /api/v1/models，返回的是主账号的模型清单**
# （auto-chat / LongCat-2.0 / new-api 网关动态模型 / 主账号的 custom provider）。
#
# 这不是"公开数据"，是跨账号配置泄漏。故：账号作用域的配置读接口一律要求登录，
# 并借 `get_current_account` 在同一依赖里完成 `set_current_account`。
# 前端对应的裸 `fetch` 必须改走 `authFetch` / `api`（否则 401 → 级联登出）。
# ---------------------------------------------------------------------------
from .auth import get_current_account  # noqa: E402  (置于此处：需先定义 public_router 之上的导入顺序无碍，但保持就近可读)
from .models import Account  # noqa: E402


async def _tenant_required(_acc: Account = Depends(get_current_account)) -> Account:
    """把「必须已登录」表达成一个具名依赖，便于 6 个端点统一引用与后续审计。"""
    return _acc


LEGACY_TEMPLATES_DIR = BASE / "templates"


def _templates_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.tenant_path("templates")
    except ImportError:
        return LEGACY_TEMPLATES_DIR

TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VALID_OUTPUT_FORMATS = ["markdown"]
LEGACY_TEMPLATE_AUDIT_DIR = BASE / ".audit" / "templates"


def _template_audit_dir() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.audit_path("templates")
    except ImportError:
        return LEGACY_TEMPLATE_AUDIT_DIR


# ---------------------------------------------------------------------------
# M9-1 · 智能体库（config/agents_library.yaml）——角色单一事实源
# ---------------------------------------------------------------------------
# 原 M8-4 在此硬编码 KNOWN_AGENTS / KNOWN_GATES / AGENT_GATE_PAIR 三个常量。
# 问题（独立审议第 16 轮 B1 纠出）：硬编码导致 UI 新增的自定义 Agent 在
# `_validate_template` 配对校验处 KeyError → 500，「真驱动」闭环在最关键证据点断裂。
# 现全部改为**派生自 agents_library.yaml**，自定义 Agent 新增后自动进入
# 模板可选集，经 M8-5 子集编排真实驱动流水线。
LEGACY_LIBRARY_PATH = LEGACY_CONFIG_DIR / "agents_library.yaml"


def _library_path() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.config_path("agents_library.yaml")
    except ImportError:
        return LEGACY_LIBRARY_PATH

LEGACY_LIBRARY_AUDIT_DIR = BASE / ".audit" / "agents-library"


def _library_audit_dir() -> Path:
    """按当前账号命名空间解析审计目录。"""
    return _audit_sub("agents-library")

VALID_SHAPES = ["researcher", "analyst", "writer"]
# shape → 产出字段 / 工具：由 shape 强制推导，不允许自由填（防 make_agent 产出键错配）
SHAPE_OUTPUT_KEY = {"researcher": "retrieval_records",
                    "analyst": "analysis_conclusions",
                    "writer": "draft_segments"}
SHAPE_TOOL = {"researcher": "web_search", "analyst": "data_proc", "writer": "doc_export"}


def _load_library_agents() -> List[Dict[str, Any]]:
    """读取库条目（每次调用现读磁盘 → 与引擎热加载同频，零重启）。"""
    if not _library_path().exists():
        return []
    data = _yaml.safe_load(_library_path().read_text(encoding="utf-8")) or {}
    return [a for a in (data.get("agents") or []) if isinstance(a, dict) and a.get("id")]


def _known_agents() -> List[str]:
    return [a["id"] for a in _load_library_agents()]


def _known_gates() -> List[str]:
    return [a["gate"] for a in _load_library_agents() if a.get("gate")]


def _agent_gate_pair() -> Dict[str, str]:
    return {a["id"]: a["gate"] for a in _load_library_agents() if a.get("gate")}


def _load_library_rt() -> Any:
    """round-trip 载入库文件（保留注释）。文件缺失时返回空 CommentedMap 骨架。"""
    y = _yaml_rt()
    if not _library_path().exists():
        from ruamel.yaml.comments import CommentedMap, CommentedSeq  # type: ignore
        data = CommentedMap()
        data["agents"] = CommentedSeq()
        return data
    return y.load(_library_path().read_text(encoding="utf-8"))


def _template_names() -> List[str]:
    """白名单：派生自 templates/*/manifest.yaml 的目录名。天然防路径穿越。"""
    if not _templates_dir().exists():
        return []
    return sorted(
        p.name for p in _templates_dir().iterdir()
        if p.is_dir() and (p / "manifest.yaml").exists()
    )


def _resolve_template(name: str) -> Path:
    if not name or not TEMPLATE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail=f"非法的模板名：{name!r}")
    if name not in _template_names():
        raise HTTPException(status_code=404, detail=f"模板不存在：{name}")
    return _templates_dir() / name / "manifest.yaml"


def _load_template(name: str) -> Dict[str, Any]:
    return _yaml.safe_load(_resolve_template(name).read_text(encoding="utf-8")) or {}


def _template_item(name: str) -> Dict[str, Any]:
    p = _templates_dir() / name / "manifest.yaml"
    st = p.stat()
    data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {
        "id": name,
        "name": data.get("name", name),
        "description": data.get("description", ""),
        "agents": data.get("agents", []),
        "gates": data.get("gates", []),
        "output_format": data.get("output_format", []),
        "ui_mode": data.get("ui_mode", "unified_shell"),
        "updated": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _validate_template(data: Dict[str, Any]) -> List[str]:
    """返回错误列表（非空则 400 不落盘）。"""
    errs: List[str] = []
    ags = data.get("agents") or []
    gts = data.get("gates") or []
    ofs = data.get("output_format") or []

    known_agents, known_gates = _known_agents(), _known_gates()
    if not isinstance(ags, list) or not ags:
        errs.append("agents 必须为非空列表")
    else:
        for a in ags:
            if a not in known_agents:
                errs.append(f"未知 agent：{a}（可选：{known_agents}）")
    if not isinstance(gts, list) or not gts:
        errs.append("gates 必须为非空列表")
    else:
        for g in gts:
            if g not in known_gates:
                errs.append(f"未知 gate：{g}（可选：{known_gates}）")
    if not isinstance(ofs, list) or not ofs:
        errs.append("output_format 必须为非空列表")
    else:
        for f in ofs:
            if f not in VALID_OUTPUT_FORMATS:
                errs.append(f"不支持的输出格式：{f}（M8-4 仅 {VALID_OUTPUT_FORMATS}，pdf/pptx/docx 待 M9）")

    # M8-5 子集编排校验：agents 为已知 3 角色的非空子集；
    # gates 必须 == agents 各自配对的闸（Researcher→GateA 等），不允许孤儿闸。
    if not errs:
        unknown = [a for a in ags if a not in known_agents]
        if unknown:
            errs.append(f"未知 agent：{unknown}（子集须取自 {known_agents}）")
        else:
            pair = _agent_gate_pair()
            expected_gates = {pair.get(a) for a in ags}
            if set(gts) != expected_gates:
                errs.append(
                    f"gates 必须与 agents 配对（{pair}），"
                    f"期望 {sorted(expected_gates)}，收到 {sorted(set(gts))}"
                )
    if not data.get("name"):
        errs.append("name 不可为空")
    return errs


class TemplatePut(BaseModel):
    name: str
    description: str = ""
    agents: List[str] = Field(default_factory=list)
    gates: List[str] = Field(default_factory=list)
    output_format: List[str] = Field(default_factory=list)
    ui_mode: str = "unified_shell"
    # M8-4 审议订正 M-4：显式模板 id（小写字母/数字/-/_）。中文名无法派生合法 id 时必须显式传。
    id: Optional[str] = None


@public_router.get("/templates")
async def public_list_templates(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """账号作用域的模板列表（提交页 TemplateSelect 用）。**需登录**——见 public_router 顶部注释。"""
    items = [_template_item(n) for n in _template_names()]
    return {"items": items, "total": len(items)}


@router.get("/admin/templates")
async def list_templates(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """列出可编辑的任务模板（派生自 templates/*/manifest.yaml）。"""
    _require_admin(x_admin_token)
    items = [_template_item(n) for n in _template_names()]
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "dir": "templates/",
            "known_agents": _known_agents(),
            "known_gates": _known_gates(),
            "valid_output_format": VALID_OUTPUT_FORMATS,
            "constraint": "agents/gates 为 agents_library.yaml 派生全集的子集（M8-5 子集编排 + M9-1 自定义 Agent 已落地，支持自定义角色进入子集，配对须满足 _agent_gate_pair）",
            "output_format_note": "G6 渲染器（pdf/pptx/docx）剥离到 M9，当前仅 markdown 真实渲染",
        },
    }


@router.get("/admin/templates/{name}")
async def get_template(
    name: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """读取单个模板的结构化内容。"""
    _require_admin(x_admin_token)
    _resolve_template(name)
    return _template_item(name)


@router.post("/admin/templates")
async def create_template(
    payload: TemplatePut, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """新增模板（建 templates/<id>/manifest.yaml）。"""
    operator = _require_admin(x_admin_token)
    # M8-4 审议订正 M-4：优先用显式 id；否则从 name 派生；非 ASCII 名必须显式给 id
    raw = (payload.id or "").strip() or re.sub(r"\s+", "-", payload.name.strip().lower())
    m = TEMPLATE_NAME_RE.match(raw)
    if not m:
        raise HTTPException(
            status_code=400,
            detail={"message": "模板 id 必须为小写字母/数字/连字符/下划线；中文名请显式填写模板 id"},
        )
    tid = m.group(0)
    if tid in _template_names():
        raise HTTPException(status_code=400, detail={"message": f"模板已存在：{tid}"})

    data = {
        "name": payload.name,
        "description": payload.description,
        "agents": payload.agents,
        "gates": payload.gates,
        "output_format": payload.output_format or ["markdown"],
        "ui_mode": payload.ui_mode,
    }
    errs = _validate_template(data)
    if errs:
        _write_audit(
            "rejected", None, data, operator, "fail", errs,
            subdir="templates", src_path=_templates_dir() / tid / "manifest.yaml",
        )
        raise HTTPException(status_code=400, detail={"message": "校验未通过，未落盘", "errors": errs})

    d = _templates_dir() / tid
    d.mkdir(parents=True, exist_ok=True)
    path = d / "manifest.yaml"
    _atomic_write(path, _yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    ts = _write_audit("create", None, data, operator, "pass", subdir="templates", src_path=path)
    return {"ok": True, "id": tid, "audit_id": ts, "operator": operator}


@router.put("/admin/templates/{name}")
async def update_template(
    name: str, payload: TemplatePut, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """保存模板（原子写盘 + 写前备份 + 审计）。"""
    operator = _require_admin(x_admin_token)
    path = _resolve_template(name)
    before = _load_template(name)
    data = {
        "name": payload.name,
        "description": payload.description,
        "agents": payload.agents,
        "gates": payload.gates,
        "output_format": payload.output_format or ["markdown"],
        "ui_mode": payload.ui_mode,
    }
    errs = _validate_template(data)
    if errs:
        _write_audit(
            "rejected", before, data, operator, "fail", errs,
            subdir="templates", src_path=path,
        )
        raise HTTPException(status_code=400, detail={"message": "校验未通过，未落盘", "errors": errs})

    if data == before:
        return {"ok": True, "changed": False, "note": "内容无变化，未写盘（避免无意义审计）"}

    _atomic_write(path, _yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    after = _load_template(name)
    ts = _write_audit("update", before, after, operator, "pass", subdir="templates", src_path=path)
    return {"ok": True, "changed": True, "audit_id": ts, "operator": operator}


@router.delete("/admin/templates/{name}")
async def delete_template(
    name: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """删除模板（保留至少 1 个）。"""
    operator = _require_admin(x_admin_token)
    path = _resolve_template(name)
    if len(_template_names()) <= 1:
        raise HTTPException(status_code=400, detail={"message": "至少保留 1 个模板，禁止删除最后一个"})
    before = _load_template(name)
    shutil.rmtree(_templates_dir() / name)
    ts = _write_audit("delete", before, None, operator, "pass", subdir="templates", src_path=path)
    return {"ok": True, "deleted": name, "audit_id": ts, "operator": operator}


@router.get("/admin/templates/{name}/audit")
async def list_template_audit(
    name: str, limit: int = 20, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出某模板的改动审计（倒序）。"""
    _require_admin(x_admin_token)
    _resolve_template(name)
    audit_dir = _template_audit_dir()
    if not audit_dir.exists():
        return {"items": [], "total": 0}
    files = sorted(audit_dir.glob("*.json"), reverse=True)
    items = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("file") == f"templates/{name}/manifest.yaml":
            items.append(rec)
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items)}


# ===========================================================================
# M9-1 · 真·智能体市场：智能体库 CRUD（config/agents_library.yaml）
# ---------------------------------------------------------------------------
# 与 M8-4 模板的最大差别：新增 Agent 不是「只落一条 UI 记录」，而是**复合写**：
#   agents/<id>.md（prompt 骨架） + gates/<gate>.md（审核规则）
#   + model_mapping.yaml（roles.<id> / gates.<gate>，异基座）
#   + agents_library.yaml（registry 条目）
# 四写任一失败 → 全回滚 + 400 + rejected 审计（DESIGN_M9-1 §4.3 / R5）。
# 引擎侧 orchestrator.load_agent_registry() 每次 run 热加载 → 下一个任务即生效。
# ===========================================================================


def _agent_lib_skeleton(aid: str, name: str, shape: str) -> str:
    """按 shape 生成 Agent prompt 骨架（用户可随后在 /settings/agents 编辑）。"""
    prod = SHAPE_OUTPUT_KEY[shape]
    tool = SHAPE_TOOL[shape]
    goal = {
        "researcher": "围绕 user_task 检索资料，产出结构化 retrieval_records（只从引擎来源集合筛选）",
        "analyst": "基于上游 retrieval_records 做结构化分析，产出 analysis_conclusions（source_ids 只能用真实 id）",
        "writer": "整合上游 analysis_conclusions 撰写终稿，产出 draft_segments + report_markdown",
    }[shape]
    return (
        f"# {name}（{aid}）\n\n"
        f"shape: {shape}　tool: {tool}　产出字段: {prod}\n\n"
        f"## 目标\n{goal}\n\n"
        f"## 工具\n{tool}（由引擎真实调用，非模型自称）\n\n"
        f"## 输出\n只输出一个 JSON 对象，必须包含数组字段 `{prod}`、tool_status；"
        f"rework 时含 change_note。\n\n"
        f"## 禁止\n- 禁止编造未出现在来源集合中的 url / id\n"
        f"- 禁止输出 JSON 以外的任何解释文字\n"
    )


def _gate_lib_skeleton(gate: str, aid: str, shape: str) -> str:
    prod = SHAPE_OUTPUT_KEY[shape]
    return (
        f"# {gate}（审核 {aid} 的 {prod}）\n\n"
        f"shape: {shape}\n\n"
        f"## 职责\n审核 {aid} 产出的 `{prod}`，只输出 gate_output JSON。\n\n"
        f"## 判定\n- advance：产出满足全部校验标准\n"
        f"- rework：列出具体 problem_points，让 {aid} 定向修正\n"
        f"- escalate：产出根本性问题，转人工\n\n"
        f"## 禁止\n- 禁止输出 JSON 以外的解释文字\n"
    )


def _library_item(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": a.get("id"),
        "name": a.get("name", a.get("id")),
        "avatar": a.get("avatar", ""),
        "tags": a.get("tags", []) or [],
        "description": a.get("description", ""),
        "shape": a.get("shape"),
        "tool": a.get("tool"),
        "output_key": a.get("output_key"),
        "gate": a.get("gate"),
        "visibility": a.get("visibility", "public"),
        "builtin": bool(a.get("builtin", False)),
    }


@public_router.get("/agents-library")
async def public_list_agents_library(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """公开列表（智能体市场页用，无 admin token）。"""
    items = [_library_item(a) for a in _load_library_agents()]
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "registry": "config/agents_library.yaml",
            "shapes": VALID_SHAPES,
            "hot_reload": "orchestrator.run_report 每次 load_agent_registry()，"
                          "改完下一个任务即生效（零重启）",
        },
    }


class AgentLibPost(BaseModel):
    id: str
    name: str
    shape: str
    gate: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    avatar: str = ""
    visibility: str = "private"
    # 模型绑定：默认异基座（role=custom/LongCat-2.0，gate=cloudflare/llama-3.3-70b）
    role_base: str = "custom"
    role_model: str = "LongCat-2.0"
    gate_base: str = "cloudflare"
    gate_model: str = "llama-3.3-70b"


@router.get("/admin/agents-library")
async def admin_list_agents_library(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    items = [_library_item(a) for a in _load_library_agents()]
    return {"items": items, "total": len(items),
            "shapes": VALID_SHAPES, "registry": str(_library_path())}


@router.post("/admin/agents-library")
async def create_agent_library(
    body: AgentLibPost, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """复合新增自定义 Agent（4 处写，全成功才落盘，任一失败全回滚）。"""
    operator = _require_admin(x_admin_token)
    aid, gate = body.id, body.gate

    # ---- 1) 校验前置（不写任何文件）----
    errs: List[str] = []
    if not AGENT_NAME_RE.match(aid):
        errs.append(f"非法的角色逻辑名：{aid!r}（只允许字母/数字/-/_）")
    if not AGENT_NAME_RE.match(gate):
        errs.append(f"非法的闸名：{gate!r}（只允许字母/数字/-/_）")
    if body.shape not in VALID_SHAPES:
        errs.append(f"不支持的 shape：{body.shape}（可选：{VALID_SHAPES}）")
    if body.role_base == body.gate_base:
        errs.append(
            f"硬约束违反：role_base({body.role_base}) 与 gate_base({body.gate_base}) 相同，"
            f"会导致自审包庇"
        )
    if not errs:
        existing = _load_library_agents()
        if any(a["id"] == aid for a in existing):
            errs.append(f"角色已存在：{aid}")
        if any(a.get("gate") == gate for a in existing):
            errs.append(f"闸名已被占用：{gate}")
        if (_agents_dir() / f"{aid.lower()}.md").exists():
            errs.append(f"prompt 文件已存在：agents/{aid.lower()}.md")
        if (_gates_dir() / f"{gate}.md").exists():
            errs.append(f"闸门 prompt 文件已存在：gates/{gate}.md")
    if errs:
        _write_audit("create_rejected", None, body.model_dump(), operator, "rejected",
                     errors=errs, subdir="agents-library", src_path=_library_path())
        raise HTTPException(status_code=400,
                            detail={"message": "校验未通过", "errors": errs})

    # ---- 2) 复合写（快照 + 回滚栈）----
    agent_md = _agents_dir() / f"{aid.lower()}.md"
    gate_md = _gates_dir() / f"{gate}.md"
    mapping_before = _mapping_path().read_text(encoding="utf-8") if _mapping_path().exists() else None
    library_before = _library_path().read_text(encoding="utf-8") if _library_path().exists() else None
    created: List[Path] = []
    try:
        # 2.1 agents/<id>.md
        _atomic_write(agent_md, _agent_lib_skeleton(aid, body.name, body.shape))
        created.append(agent_md)
        _write_audit("create", None, {"file": f"agents/{aid.lower()}.md"}, operator,
                     "pass", subdir="agents", src_path=agent_md)

        # 2.2 gates/<gate>.md
        _atomic_write(gate_md, _gate_lib_skeleton(gate, aid, body.shape))
        created.append(gate_md)
        _write_audit("create", None, {"file": f"gates/{gate}.md"}, operator,
                     "pass", subdir="gates", src_path=gate_md)

        # 2.3 model_mapping.yaml（ruamel round-trip 保留注释；先校验再落盘）
        mdata = _load_commented()
        mdata.setdefault("roles", {})[aid] = {"base": body.role_base, "model": body.role_model}
        mdata.setdefault("gates", {})[gate] = {
            "reviews": aid, "base": body.gate_base, "model": body.gate_model,
        }
        m_errs = _validate_mapping(mdata)
        if m_errs:
            raise ValueError("；".join(m_errs))
        _atomic_write(_mapping_path(), _dump_commented(mdata))
        _write_audit("create_agent", {"id": aid, "gate": gate},
                     {"roles": dict(mdata["roles"][aid]), "gates": dict(mdata["gates"][gate])},
                     operator, "pass", subdir="models", src_path=_mapping_path())

        # 2.4 agents_library.yaml（round-trip 追加）
        from ruamel.yaml.comments import CommentedMap  # type: ignore
        ldata = _load_library_rt()
        entry = CommentedMap()
        for k, v in (("id", aid), ("name", body.name), ("avatar", body.avatar),
                     ("tags", list(body.tags)), ("description", body.description),
                     ("shape", body.shape), ("tool", SHAPE_TOOL[body.shape]),
                     ("output_key", SHAPE_OUTPUT_KEY[body.shape]), ("gate", gate),
                     ("visibility", body.visibility), ("builtin", False)):
            entry[k] = v
        ldata.setdefault("agents", []).append(entry)
        _atomic_write(_library_path(), _dump_commented(ldata))
        _write_audit("create", None, _library_item(dict(entry)), operator, "pass",
                     subdir="agents-library", src_path=_library_path())
    except Exception as exc:
        # 全回滚：删已建文件 + 还原两个 yaml（顺序写 + 异常逆序清理，DESIGN §8 R5）
        for p in reversed(created):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        if mapping_before is not None:
            try:
                _atomic_write(_mapping_path(), mapping_before)
            except Exception:
                pass
        if library_before is not None:
            try:
                _atomic_write(_library_path(), library_before)
            except Exception:
                pass
        _write_audit("create_rollback", None, body.model_dump(), operator, "rejected",
                     errors=[str(exc)], subdir="agents-library", src_path=_library_path())
        raise HTTPException(
            status_code=400,
            detail={"message": f"新增失败，已全回滚：{exc}", "errors": [str(exc)]},
        )

    return {"ok": True, "item": _library_item(dict(entry)), "operator": operator,
            "written": [f"agents/{aid.lower()}.md", f"gates/{gate}.md",
                        "config/model_mapping.yaml", "config/agents_library.yaml"]}


@router.delete("/admin/agents-library/{aid}")
async def delete_agent_library(
    aid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """复合删除自定义 Agent（内置拒删；清理 md/gate/mapping/library + 审计）。"""
    operator = _require_admin(x_admin_token)
    existing = _load_library_agents()
    target = next((a for a in existing if a["id"] == aid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"角色不存在：{aid}")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail={"message": f"内置 Agent 不可删：{aid}"})

    gate = target.get("gate")
    # M9-1 补防护（设计文档未预见，实施时发现）：若仍有模板引用该 Agent/Gate，
    # 直接删除会让 manifest 悬空 —— create_task 时 build_graph 查不到 registry 条目 → 500。
    # 与其留一个「UI 显示正常、一提交就崩」的假状态，不如拒绝并点名模板（fail loud）。
    referencing = []
    for n in _template_names():
        try:
            t = _load_template(n)
        except Exception:
            continue
        if aid in (t.get("agents") or []) or gate in (t.get("gates") or []):
            referencing.append(n)
    if referencing:
        raise HTTPException(
            status_code=400,
            detail={"message": f"仍有 {len(referencing)} 个模板引用 {aid}/{gate}，"
                               f"请先修改或删除这些模板：{referencing}",
                    "errors": referencing},
        )

    agent_md = _agents_dir() / f"{aid.lower()}.md"
    gate_md = _gates_dir() / f"{gate}.md" if gate else None
    mapping_before = _mapping_path().read_text(encoding="utf-8") if _mapping_path().exists() else None
    library_before = _library_path().read_text(encoding="utf-8") if _library_path().exists() else None
    removed: List[Path] = []
    try:
        for p in (agent_md, gate_md):
            if p and p.exists():
                _write_audit("delete", {"file": p.name}, None, operator, "pass",
                             subdir="agents" if p is agent_md else "gates", src_path=p)
                p.unlink()
                removed.append(p)

        mdata = _load_commented()
        mdata.get("roles", {}).pop(aid, None)
        if gate:
            mdata.get("gates", {}).pop(gate, None)
        m_errs = _validate_mapping(mdata)
        if m_errs:
            raise ValueError("；".join(m_errs))
        _atomic_write(_mapping_path(), _dump_commented(mdata))
        _write_audit("delete_agent", {"id": aid, "gate": gate}, None, operator, "pass",
                     subdir="models", src_path=_mapping_path())

        ldata = _load_library_rt()
        ldata["agents"] = [a for a in (ldata.get("agents") or []) if a.get("id") != aid]
        _atomic_write(_library_path(), _dump_commented(ldata))
        _write_audit("delete", _library_item(target), None, operator, "pass",
                     subdir="agents-library", src_path=_library_path())
    except Exception as exc:
        # 回滚：两个 yaml 可还原；已删 md 无法自动重建 → 显式告知人工复核（不静默）
        if mapping_before is not None:
            try:
                _atomic_write(_mapping_path(), mapping_before)
            except Exception:
                pass
        if library_before is not None:
            try:
                _atomic_write(_library_path(), library_before)
            except Exception:
                pass
        _write_audit("delete_rollback", {"id": aid}, None, operator, "rejected",
                     errors=[f"{exc}；已删文件未自动重建，请人工复核"],
                     subdir="agents-library", src_path=_library_path())
        raise HTTPException(
            status_code=400,
            detail={"message": f"删除失败，配置已回滚：{exc}",
                    "errors": [f"已删文件未重建，请人工复核：{[str(p) for p in removed]}"]},
        )

    return {"ok": True, "deleted": aid, "gate": gate, "operator": operator,
            "removed": [str(p.relative_to(BASE)) for p in removed]}


# ===========================================================================
# M9-2 · 数据源插件市场（DataSource Plugins，真·控制器）
# 设计：DESIGN_M9-2.md。复用 M9-1 agents-library 的 ruamel round-trip / 原子写 / 审计范式。
# ===========================================================================

LEGACY_PLUGINS_PATH = LEGACY_CONFIG_DIR / "plugins.yaml"


def _plugins_path() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.config_path("plugins.yaml")
    except ImportError:
        return LEGACY_PLUGINS_PATH

LEGACY_PLUGINS_AUDIT_DIR = BASE / ".audit" / "plugins"


def _plugins_audit_dir() -> Path:
    """按当前账号命名空间解析审计目录。"""
    return _audit_sub("plugins")

LEGACY_SECRETS_PATH = BASE / ".secrets" / "plugins.env"


def _secrets_path() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.secrets_path("plugins.env")
    except ImportError:
        return LEGACY_SECRETS_PATH

VALID_AUTH_TYPES = ["none", "api_key", "oauth"]
# provider 白名单：基础类型 + 全部 keyless 真源（从引擎注册表派生，避免新增源时漏登记）
try:
    from tools.data_sources import KEYLESS_PROVIDERS as _KEYLESS
    _KEYLESS_IDS = sorted(_KEYLESS.keys())
except Exception:  # noqa: BLE001 — 引擎层不可用时退化为基础白名单，不阻塞 admin
    _KEYLESS_IDS = []
VALID_PROVIDERS = ["tavily", "mock", "coming_soon"] + _KEYLESS_IDS
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _load_plugins() -> List[Dict[str, Any]]:
    """读取插件条目（每次调用现读磁盘 → 与引擎热加载同频，零重启）。"""
    if not _plugins_path().exists():
        return []
    import yaml as _yaml

    data = _yaml.safe_load(_plugins_path().read_text(encoding="utf-8")) or {}
    return [p for p in (data.get("plugins") or []) if isinstance(p, dict) and p.get("id")]


def _load_plugins_rt() -> Any:
    """round-trip 载入插件文件（保留注释）。文件缺失时返回空 CommentedMap 骨架。"""
    y = _yaml_rt()
    if not _plugins_path().exists():
        from ruamel.yaml.comments import CommentedMap, CommentedSeq  # type: ignore
        data = CommentedMap()
        data["plugins"] = CommentedSeq()
        return data
    return y.load(_plugins_path().read_text(encoding="utf-8"))


def _plugin_item(p: Dict[str, Any]) -> Dict[str, Any]:
    # doc_url/key_guide_url/key_env 为 M10-P5 新增的「真实官网/获取密钥教程」元数据，
    # 仅供前端插件市场展示（不进引擎检索逻辑）。缺失即缺省空串。
    return {
        "id": p.get("id"),
        "name": p.get("name", ""),
        "icon": p.get("icon", ""),
        "category": p.get("category", ""),
        "description": p.get("description", ""),
        "auth_type": p.get("auth_type", "none"),
        "provider": p.get("provider", "coming_soon"),
        "enabled": bool(p.get("enabled", False)),
        "status": p.get("status", "disconnected"),
        "builtin": bool(p.get("builtin", False)),
        "doc_url": p.get("doc_url", "") or "",
        "key_guide_url": p.get("key_guide_url", "") or "",
        "key_env": p.get("key_env", "") or "",
    }


@public_router.get("/plugins")
async def public_list_plugins(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """公开列表（数据源市场页用，无 admin token）。"""
    from tools.data_sources import load_data_sources

    items = [_plugin_item(p) for p in load_data_sources()]
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "registry": "config/plugins.yaml",
            "hot_reload": "build_tools 每次重读 plugins.yaml，改完下一个任务即生效（零重启）",
            "secret_env": "DS_<ID大写>_API_KEY（写入 .secrets/plugins.env，不进 plugins.yaml）",
        },
    }


# ---------------------------------------------------------------------------
# M9-5 · 模型接口（config/models.yaml）——ChatEntry 模型接口下拉数据源
# ---------------------------------------------------------------------------
LEGACY_MODELS_PATH = LEGACY_CONFIG_DIR / "models.yaml"


def _models_path() -> Path:
    """按当前账号命名空间解析（各自一套资源）；缺上下文即 fail loud。"""
    try:
        from server import tenancy
        return tenancy.config_path("models.yaml")
    except ImportError:
        return LEGACY_MODELS_PATH


# 网关模型内存缓存：与每次现读磁盘的本地 interfaces 互补，避免高并发下频繁请求 new-api /v1/models
_GATEWAY_CACHE: Dict[str, Dict[str, Any]] = {}   # "<aid>|<url>" -> {"items":[...], "ts": float}
_GATEWAY_CACHE_LOCK = threading.Lock()
# 最近一次拉取元信息（供 public_list_models 在 meta 中诚实暴露网关状态，前端据此提示）。
# 按账号分区：不同账号的网关（可能同 url 不同 key）互不串；无上下文（公开端点/CLI）用 "public" 键。
_MODELS_META: Dict[str, Dict[str, Any]] = {}


def _cache_aid() -> str:
    """网关缓存/元信息分区键：当前账号 id；无上下文（公开端点/CLI）用 'public'。"""
    try:
        from server import tenancy
        aid = tenancy.current_account_id(allow_none=True)
    except ImportError:
        aid = None
    return aid or "public"


def _models_meta() -> Dict[str, Any]:
    key = _cache_aid()
    m = _MODELS_META.get(key)
    if m is None:
        m = {
            "gateway_ok": False,
            "gateway_error": None,
            "gateway_url": "",
            "fetched": 0,
            "cached": False,
            "disabled": True,
        }
        _MODELS_META[key] = m
    return m

logger = logging.getLogger(__name__)


# _expand_env_vars 已迁移至 orchestrator.load_model_interfaces_and_endpoints（单一事实源，
# DESIGN_OPENAI_ENDPOINTS.md）。本模块 _load_model_interfaces 改从此处加载，不再本地展开。


def _fetch_gateway_models(url: str, api_key: str, timeout: int) -> List[Dict[str, Any]]:
    """拉取 new-api /v1/models（OpenAI 兼容），返回扁平条目列表。

    响应格式：{"object":"list","data":[{"id":"xxx","object":"model",...}],"success":true}
    """
    # 归一化：new-api 模型列表根路径必须带 /v1，否则 /models 落到仪表盘 SPA 兜底。
    # 兼容「配置漏写 /v1」与「已带 /v1」两种写法（与 orchestrator.NewApiLLMClient._get_client 对齐）。
    base = url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    req = urllib.request.Request(f"{base}/models")
    req.add_header("Accept", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    payload = json.loads(body)
    items: List[Dict[str, Any]] = []
    for entry in (payload.get("data") or []):
        mid = entry.get("id") if isinstance(entry, dict) else None
        if not mid:
            continue
        items.append({
            "id": mid,
            "name": mid,
            "description": "new-api 可用模型（网关动态拉取）",
            "kind": "gateway",
            "source": "gateway",
        })
    return items


def _load_model_interfaces() -> List[Dict[str, Any]]:
    """读取可选模型接口列表（本地 interfaces + 网关动态拉取合并，DESIGN_OPENAI_ENDPOINTS.md）。

    行为：
      - 复用 orchestrator.load_model_interfaces_and_endpoints 读 endpoints/interfaces/gateway；
        本地 interfaces 每次现读磁盘（零重启），每个 item 带 endpoint_id。
      - 若 gateway.fetch_from_gateway=true 且 default endpoint 存在：从 default endpoint 拉取
        /v1/models 合并，网关模型 endpoint_id=default_endpoint_id（网关即 new-api 服务本身）。
      - 拉取失败/未启用：降级返回本地 interfaces，meta.gateway_ok=false 诚实标注。
    合并结果同时被 api.py create_task 的 valid_ids 复用，故用户可选任何真实模型。
    """
    # 重置本次 meta（本函数可能被 create_task 校验或 /models 列表调用）
    _models_meta()["gateway_ok"] = False
    _models_meta()["gateway_error"] = None
    _models_meta()["gateway_url"] = ""
    _models_meta()["fetched"] = 0
    _models_meta()["cached"] = False
    _models_meta()["disabled"] = True

    from orchestrator import load_model_interfaces_and_endpoints
    endpoints, interfaces, gw, default_endpoint_id = load_model_interfaces_and_endpoints()

    out: List[Dict[str, Any]] = []
    for m in interfaces:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append({
            "id": m["id"],
            "name": m.get("name", ""),
            "description": m.get("description", ""),
            "kind": m.get("kind", "auto"),
            "default": bool(m.get("default", False)),
            # 透传 source（builtin / custom），前端按此分组。旧版硬编码 local 会让自定义接口误归类。
            "source": m.get("source") or "local",
            "endpoint_id": m.get("endpoint"),
        })

    if not gw.get("fetch_from_gateway") or not default_endpoint_id:
        return out  # 逃生口：仅本地接口（旧行为）
    ep_ids = {e["id"] for e in endpoints}
    if default_endpoint_id not in ep_ids:
        return out
    _models_meta()["disabled"] = False

    ep = next(e for e in endpoints if e["id"] == default_endpoint_id)
    url = ep["base_url"]
    key = ep["api_key"]
    timeout = int(gw.get("timeout", 10))
    ttl = int(gw.get("cache_seconds", 60))
    _models_meta()["gateway_url"] = url

    # 内存缓存命中（TTL 内）→ 直接合并，不重复请求网关
    with _GATEWAY_CACHE_LOCK:
        cached = _GATEWAY_CACHE.get(f"{_cache_aid()}|{url}")
        if cached and (time.time() - cached["ts"]) < ttl:
            _models_meta()["cached"] = True
            _models_meta()["gateway_ok"] = True
            _models_meta()["fetched"] = len(cached["items"])
            for it in cached["items"]:
                if not any(o["id"] == it["id"] for o in out):
                    out.append(it)
            return out

    try:
        fetched = _fetch_gateway_models(url, key, timeout)
        for it in fetched:
            it["endpoint_id"] = default_endpoint_id
        with _GATEWAY_CACHE_LOCK:
            _GATEWAY_CACHE[f"{_cache_aid()}|{url}"] = {"items": fetched, "ts": time.time()}
        _models_meta()["gateway_ok"] = True
        _models_meta()["fetched"] = len(fetched)
        for it in fetched:
            if not any(o["id"] == it["id"] for o in out):
                out.append(it)
    except Exception as exc:  # 网关不可达/鉴权/超时 → 降级本地，诚实标注
        _models_meta()["gateway_ok"] = False
        _models_meta()["gateway_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("模型网关拉取失败，降级本地接口：%s", exc)
    return out


@public_router.get("/models")
async def public_list_models(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """当前账号可用的模型接口列表（ChatEntry 模型下拉用）。**需登录**。

    为什么必须鉴权：本接口读的是**当前账号**的 `config/models.yaml` + 自定义 provider。
    一旦无鉴权，就没有租户上下文 → 会读到全局/主账号的配置 → 子账号的模型下拉里出现
    主账号的 new-api 网关模型（跨账号配置泄漏，实测复现）。见 public_router 顶部注释。

    响应 meta 诚实暴露网关状态：gateway_ok / gateway_error / gateway_disabled，
    前端据此区分"网关模型已加载"与"降级本地接口"。
    """
    items = _load_model_interfaces()
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "registry": "config/models.yaml",
            "gateway_url": _models_meta()["gateway_url"] or None,
            "gateway_ok": _models_meta()["gateway_ok"],
            "gateway_error": _models_meta()["gateway_error"],
            "gateway_disabled": _models_meta()["disabled"],
            "fetched": _models_meta()["fetched"],
            "cached": _models_meta()["cached"],
            "hot_reload": "GET /models 每次读取本地 interfaces，网关模型 60s 内存缓存，改完下一个任务即生效（零重启）",
            "honest_boundary": "用户所选仅覆盖 Agent LLM 调用；Gate 维持 model_mapping.yaml 异基座隔离",
        },
    }


class PluginPost(BaseModel):
    id: str
    name: str
    category: str = ""
    description: str = ""
    auth_type: str = "none"
    provider: str = "coming_soon"
    icon: str = ""
    doc_url: str = ""
    key_guide_url: str = ""
    key_env: str = ""


@router.get("/admin/plugins")
async def admin_list_plugins(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    from tools.data_sources import load_data_sources

    items = [_plugin_item(p) for p in load_data_sources()]
    return {"items": items, "total": len(items),
            "valid_auth_types": VALID_AUTH_TYPES, "valid_providers": VALID_PROVIDERS,
            "registry": str(_plugins_path())}


@router.post("/admin/plugins")
async def create_plugin(
    body: PluginPost, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """复合新增自定义数据源插件（plugins.yaml 追加 + 审计；失败回滚）。"""
    operator = _require_admin(x_admin_token)
    pid = body.id

    errs: List[str] = []
    if not PLUGIN_ID_RE.match(pid):
        errs.append(f"非法的插件 id：{pid!r}（只允许字母/数字/-/_）")
    if body.auth_type not in VALID_AUTH_TYPES:
        errs.append(f"不支持的 auth_type：{body.auth_type}（可选：{VALID_AUTH_TYPES}）")
    if body.provider not in VALID_PROVIDERS:
        errs.append(f"不支持的 provider：{body.provider}（可选：{VALID_PROVIDERS}）")
    if not errs:
        existing = _load_plugins()
        if any(p["id"] == pid for p in existing):
            errs.append(f"插件已存在：{pid}")
    if errs:
        _write_audit("create_rejected", None, body.model_dump(), operator, "rejected",
                     errors=errs, subdir="plugins", src_path=_plugins_path())
        raise HTTPException(status_code=400,
                            detail={"message": "校验未通过", "errors": errs})

    before = _plugins_path().read_text(encoding="utf-8") if _plugins_path().exists() else None
    from ruamel.yaml.comments import CommentedMap  # type: ignore

    try:
        ldata = _load_plugins_rt()
        entry = CommentedMap()
        for k, v in (("id", pid), ("name", body.name), ("icon", body.icon),
                     ("category", body.category), ("description", body.description),
                     ("auth_type", body.auth_type), ("provider", body.provider),
                     ("enabled", False), ("status", "disconnected"), ("builtin", False)):
            entry[k] = v
        # 自定义源可选填官网 / 教程 / 密钥变量名（M10-P5 续）
        if body.doc_url:
            entry["doc_url"] = body.doc_url
        if body.key_guide_url:
            entry["key_guide_url"] = body.key_guide_url
        if body.key_env:
            entry["key_env"] = body.key_env
        ldata.setdefault("plugins", []).append(entry)
        _atomic_write(_plugins_path(), _dump_commented(ldata))
        _write_audit("create", None, _plugin_item(dict(entry)), operator, "pass",
                     subdir="plugins", src_path=_plugins_path())
    except Exception as exc:
        if before is not None:
            try:
                _atomic_write(_plugins_path(), before)
            except Exception:
                pass
        _write_audit("create_rollback", None, body.model_dump(), operator, "rejected",
                     errors=[str(exc)], subdir="plugins", src_path=_plugins_path())
        raise HTTPException(
            status_code=400,
            detail={"message": f"新增失败，已全回滚：{exc}", "errors": [str(exc)]},
        )

    return {"ok": True, "item": _plugin_item(dict(entry)), "operator": operator,
            "written": ["config/plugins.yaml"]}


@router.put("/admin/plugins/{pid}")
async def update_plugin(
    pid: str, body: Dict[str, Any], x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """更新插件（控制器入口）：enabled 启停 / status / description / category。

    coming_soon 插件拒绝 enabled=true（引擎永远跳过，防冒充可用）。
    """
    operator = _require_admin(x_admin_token)
    existing = _load_plugins()
    target = next((p for p in existing if p["id"] == pid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{pid}")
    if target.get("builtin") and body.get("provider") and body["provider"] != target.get("provider"):
        raise HTTPException(status_code=400, detail={"message": f"内置插件不可改 provider：{pid}"})

    want_enabled = body.get("enabled", target.get("enabled"))
    if want_enabled and target.get("provider") == "coming_soon":
        raise HTTPException(
            status_code=400,
            detail={"message": f"数据源尚未接入（coming_soon），无法启用：{pid}。请先接入该数据源。"},
        )

    before = _plugins_path().read_text(encoding="utf-8")
    try:
        ldata = _load_plugins_rt()
        for entry in (ldata.get("plugins") or []):
            if entry.get("id") == pid:
                for field in ("enabled", "status", "description", "category", "name", "icon"):
                    if field in body:
                        entry[field] = body[field]
                # enabled=true 且 api_key 无密钥 → status 保持 disconnected（不伪造 connected）
                if body.get("enabled") and target.get("auth_type") == "api_key":
                    from tools.data_sources import load_secrets
                    if not load_secrets().get(f"DS_{pid.upper()}_API_KEY"):
                        entry["status"] = "disconnected"
                break
        _atomic_write(_plugins_path(), _dump_commented(ldata))
        _write_audit("update", _plugin_item(target), _plugin_item(dict(entry)),
                     operator, "pass", subdir="plugins", src_path=_plugins_path())
    except Exception as exc:
        _atomic_write(_plugins_path(), before)
        _write_audit("update_rollback", _plugin_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="plugins", src_path=_plugins_path())
        raise HTTPException(status_code=400, detail={"message": f"更新失败，已回滚：{exc}"})

    return {"ok": True, "item": _plugin_item(next(p for p in _load_plugins() if p["id"] == pid)),
            "operator": operator}


@router.delete("/admin/plugins/{pid}")
async def delete_plugin(
    pid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """删除自定义插件（内置拒删；清理 plugins.yaml 条目 + 审计）。"""
    operator = _require_admin(x_admin_token)
    existing = _load_plugins()
    target = next((p for p in existing if p["id"] == pid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{pid}")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail={"message": f"内置插件不可删：{pid}"})

    before = _plugins_path().read_text(encoding="utf-8")
    try:
        ldata = _load_plugins_rt()
        ldata["plugins"] = [p for p in (ldata.get("plugins") or []) if p.get("id") != pid]
        _atomic_write(_plugins_path(), _dump_commented(ldata))
        _write_audit("delete", _plugin_item(target), None, operator, "pass",
                     subdir="plugins", src_path=_plugins_path())
    except Exception as exc:
        _atomic_write(_plugins_path(), before)
        _write_audit("delete_rollback", _plugin_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="plugins", src_path=_plugins_path())
        raise HTTPException(status_code=400, detail={"message": f"删除失败，已回滚：{exc}"})

    return {"ok": True, "deleted": pid, "operator": operator,
            "removed": ["config/plugins.yaml"]}


@router.post("/admin/plugins/{pid}/connect")
async def connect_plugin(
    pid: str, body: Dict[str, Any], x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """写入数据源凭证到 .secrets/plugins.env。
    api_key → DS_<ID大写>_API_KEY；oauth → DS_<ID大写>_COOKIE。
    不进 plugins.yaml，不回显 key/cookie。
    """
    operator = _require_admin(x_admin_token)
    existing = _load_plugins()
    target = next((p for p in existing if p["id"] == pid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{pid}")

    auth_type = target.get("auth_type")
    if auth_type == "api_key":
        value = (body.get("api_key") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail={"message": "api_key 不能为空"})
        env_key = f"DS_{pid.upper()}_API_KEY"
        audit_payload = {"id": pid, "key_set": True}
    elif auth_type == "oauth":
        value = (body.get("cookie") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail={"message": "cookie / 会话凭证不能为空"})
        env_key = f"DS_{pid.upper()}_COOKIE"
        audit_payload = {"id": pid, "cookie_set": True}
    else:
        raise HTTPException(status_code=400,
                            detail={"message": f"该插件无需凭证（auth_type={auth_type}）：{pid}"})

    try:
        _secrets_path().parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if _secrets_path().exists():
            for line in _secrets_path().read_text(encoding="utf-8").splitlines():
                k, _, _v = line.partition("=")
                if k.strip() != env_key:
                    lines.append(line)
        lines.append(f"{env_key}={value}")
        _atomic_write(_secrets_path(), "\n".join(lines) + "\n")
        _write_audit("connect", {"id": pid}, audit_payload, operator, "pass",
                     subdir="plugins", src_path=_secrets_path())
    except Exception as exc:
        _write_audit("connect_failed", {"id": pid}, None, operator, "rejected",
                     errors=[str(exc)], subdir="plugins", src_path=_secrets_path())
        raise HTTPException(status_code=500, detail={"message": f"凭证写入失败：{exc}"})

    # 同步 plugins.yaml 状态：
    # - api_key 且已 enabled → connected（引擎会取数）
    # - oauth → credential_saved + has_credential 标记（诚实：后端 Provider 未接，但凭证已落盘，卡片可持久显示"已保存"）
    if auth_type == "api_key" and target.get("enabled"):
        before = _plugins_path().read_text(encoding="utf-8")
        try:
            ldata = _load_plugins_rt()
            for entry in (ldata.get("plugins") or []):
                if entry.get("id") == pid:
                    entry["status"] = "connected"
                    break
            _atomic_write(_plugins_path(), _dump_commented(ldata))
        except Exception:
            _atomic_write(_plugins_path(), before)
    elif auth_type == "oauth":
        before = _plugins_path().read_text(encoding="utf-8")
        try:
            ldata = _load_plugins_rt()
            for entry in (ldata.get("plugins") or []):
                if entry.get("id") == pid:
                    entry["status"] = "credential_saved"
                    entry["has_credential"] = True
                    break
            _atomic_write(_plugins_path(), _dump_commented(ldata))
        except Exception:
            _atomic_write(_plugins_path(), before)

    return {"ok": True, "id": pid, "auth_type": auth_type,
            "status": "connected" if auth_type == "api_key" else "credential_saved",
            "operator": operator}


# ---------------------------------------------------------------------------
# M9-3 研报技能库 admin API（镜像 M9-2 plugins 范式；差异：无密钥管理）
# ---------------------------------------------------------------------------
from tools.skills import (
    load_skills as _sk_load,
    _recompute_status as _sk_status,
    SKILLS_PATH,
    SKILLS_DIR,
    SKILL_ID_RE,
    VALID_ROLES,
)


def _load_skills_rt() -> Any:
    """round-trip 载入技能文件（保留注释）。文件缺失时返回空骨架。"""
    y = _yaml_rt()
    if not SKILLS_PATH.exists():
        from ruamel.yaml.comments import CommentedMap, CommentedSeq  # type: ignore
        data = CommentedMap()
        data["skills"] = CommentedSeq()
        return data
    return y.load(SKILLS_PATH.read_text(encoding="utf-8"))


def _skill_item(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p.get("id"),
        "name": p.get("name", ""),
        "icon": p.get("icon", ""),
        "category": p.get("category", ""),
        "description": p.get("description", ""),
        "target_roles": p.get("target_roles") or [],
        "prompt_file": p.get("prompt_file", ""),
        "enabled": bool(p.get("enabled", False)),
        "installed": bool(p.get("installed", p.get("builtin", False))),
        "builtin": bool(p.get("builtin", False)),
        "status": _sk_status(p),
    }


@public_router.get("/skills")
async def public_list_skills(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """公开列表（技能市场页用，无 admin token）。不泄露 prompt 片段文本。"""
    items = [_skill_item(s) for s in _sk_load()]
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "registry": "config/skills.yaml",
            "hot_reload": "build_skill_context 每次重读 skills.yaml，改完下一个任务即生效（零重启）",
            "note": "技能是 prompt 框架软增强，启用后注入对应 Agent 的 system prompt（target_roles 作用域）",
        },
    }


class SkillPost(BaseModel):
    id: str
    name: str
    category: str = ""
    description: str = ""
    target_roles: List[str] = []
    icon: str = ""
    prompt: str = ""


@router.get("/admin/skills")
async def admin_list_skills(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    items = [_skill_item(s) for s in _sk_load()]
    return {"items": items, "total": len(items),
            "valid_roles": VALID_ROLES, "registry": str(SKILLS_PATH)}


@router.post("/admin/skills")
async def create_skill(
    body: SkillPost, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """复合新增自定义技能（skills.yaml 追加 + skills/<id>.md 写片段；失败回滚）。"""
    operator = _require_admin(x_admin_token)
    sid = body.id
    errs: List[str] = []
    if not SKILL_ID_RE.match(sid):
        errs.append(f"非法的技能 id：{sid!r}（只允许字母/数字/-/_）")
    bad_roles = [r for r in body.target_roles if r not in VALID_ROLES]
    if bad_roles:
        errs.append(f"不支持的 target_roles：{bad_roles}（可选：{VALID_ROLES}）")
    if not body.name:
        errs.append("技能名称不能为空")
    if not body.prompt.strip():
        errs.append("技能片段（prompt）不能为空")
    if not errs:
        if any(s["id"] == sid for s in _sk_load()):
            errs.append(f"技能已存在：{sid}")
    if errs:
        _write_audit("create_rejected", None, body.model_dump(), operator, "rejected",
                     errors=errs, subdir="skills", src_path=SKILLS_PATH)
        raise HTTPException(status_code=400, detail={"message": "校验未通过", "errors": errs})

    before = SKILLS_PATH.read_text(encoding="utf-8") if SKILLS_PATH.exists() else None
    md_path = SKILLS_DIR / f"{sid}.md"
    from ruamel.yaml.comments import CommentedMap  # type: ignore
    try:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        ldata = _load_skills_rt()
        entry = CommentedMap()
        for k, v in (("id", sid), ("name", body.name), ("icon", body.icon),
                     ("category", body.category), ("description", body.description),
                     ("target_roles", body.target_roles), ("prompt_file", f"skills/{sid}.md"),
                     ("enabled", True), ("installed", True), ("builtin", False),
                     ("status", "active")):
            entry[k] = v
        ldata.setdefault("skills", []).append(entry)
        _atomic_write(SKILLS_PATH, _dump_commented(ldata))
        md_path.write_text(body.prompt, encoding="utf-8")  # 片段由向导填写，非空（已校验）
        _write_audit("create", None, _skill_item(dict(entry)), operator, "pass",
                     subdir="skills", src_path=SKILLS_PATH)
    except Exception as exc:
        if before is not None:
            try:
                _atomic_write(SKILLS_PATH, before)
            except Exception:
                pass
        if md_path.exists():
            try:
                md_path.unlink()
            except Exception:
                pass
        _write_audit("create_rollback", None, body.model_dump(), operator, "rejected",
                     errors=[str(exc)], subdir="skills", src_path=SKILLS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"新增失败，已全回滚：{exc}"})

    return {"ok": True, "item": _skill_item(dict(entry)), "operator": operator,
            "written": ["config/skills.yaml", f"skills/{sid}.md"]}


@router.put("/admin/skills/{sid}")
async def update_skill(
    sid: str, body: Dict[str, Any], x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """更新技能（控制器入口）：enabled 启停 / description / category / name / icon / target_roles。

    启用守卫：enabled=True 且 prompt_file 对应 md 不存在/空 → 400 拒绝（防启用空技能）。
    builtin 技能允许改 enabled/target_roles/description，不允许改 prompt_file 指向。
    """
    operator = _require_admin(x_admin_token)
    existing = _sk_load()
    target = next((s for s in existing if s["id"] == sid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"技能不存在：{sid}")
    if target.get("builtin") and body.get("prompt_file") and body["prompt_file"] != target.get("prompt_file"):
        raise HTTPException(status_code=400, detail={"message": f"内置技能不可改 prompt_file：{sid}"})

    want_enabled = body.get("enabled", target.get("enabled"))
    if want_enabled:
        rel = body.get("prompt_file", target.get("prompt_file"))
        if not rel or not (BASE / rel).exists() or not (BASE / rel).read_text(encoding="utf-8").strip():
            raise HTTPException(
                status_code=400,
                detail={"message": f"技能片段缺失（{rel}），无法启用：{sid}。请先填写技能片段。"},
            )

    before = SKILLS_PATH.read_text(encoding="utf-8")
    try:
        ldata = _load_skills_rt()
        for entry in (ldata.get("skills") or []):
            if entry.get("id") == sid:
                for field in ("enabled", "description", "category", "name", "icon", "target_roles"):
                    if field in body:
                        entry[field] = body[field]
                # 启用守卫：md 缺失则保持 inactive（不伪造 active）
                if want_enabled and not ((BASE / entry.get("prompt_file", "")).exists()
                                         and (BASE / entry.get("prompt_file", "")).read_text(encoding="utf-8").strip()):
                    entry["enabled"] = False
                break
        _atomic_write(SKILLS_PATH, _dump_commented(ldata))
        _write_audit("update", _skill_item(target), _skill_item(dict(entry)),
                     operator, "pass", subdir="skills", src_path=SKILLS_PATH)
    except Exception as exc:
        _atomic_write(SKILLS_PATH, before)
        _write_audit("update_rollback", _skill_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="skills", src_path=SKILLS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"更新失败，已回滚：{exc}"})

    return {"ok": True, "item": _skill_item(next(s for s in _sk_load() if s["id"] == sid)),
            "operator": operator}


@router.delete("/admin/skills/{sid}")
async def delete_skill(
    sid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """删除自定义技能（内置拒删；复合删 = skills.yaml 移除 entry + 删 skills/<id>.md）。"""
    operator = _require_admin(x_admin_token)
    existing = _sk_load()
    target = next((s for s in existing if s["id"] == sid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"技能不存在：{sid}")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail={"message": f"内置技能不可删：{sid}（仅可停用）"})

    before = SKILLS_PATH.read_text(encoding="utf-8")
    md_path = SKILLS_DIR / f"{sid}.md"
    try:
        ldata = _load_skills_rt()
        ldata["skills"] = [s for s in (ldata.get("skills") or []) if s.get("id") != sid]
        _atomic_write(SKILLS_PATH, _dump_commented(ldata))
        if md_path.exists():
            md_path.unlink()
        _write_audit("delete", _skill_item(target), None, operator, "pass",
                     subdir="skills", src_path=SKILLS_PATH)
    except Exception as exc:
        _atomic_write(SKILLS_PATH, before)
        _write_audit("delete_rollback", _skill_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="skills", src_path=SKILLS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"删除失败，已回滚：{exc}"})

    return {"ok": True, "deleted": sid, "operator": operator,
            "removed": ["config/skills.yaml", f"skills/{sid}.md"]}


# ---------------------------------------------------------------------------
# M9-4 研报推送渠道 admin API（镜像 M9-3 skills 范式；差异：无片段文件、无密钥管理、增测试端点）
# ---------------------------------------------------------------------------
from tools.push import (
    load_channels as _ch_load,
    _recompute_status as _ch_status,
    CHANNELS_PATH,
    PROVIDER_REGISTRY,
    ComingSoonChannel,
)
from ruamel.yaml.comments import CommentedMap, CommentedSeq  # type: ignore

CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
VALID_TYPES = ["webhook", "mock", "coming_soon"]
VALID_FORMATS = ["dingtalk", "feishu", "wecom", "discord"]


def _load_channels_rt() -> Any:
    """round-trip 载入渠道文件（保留注释）。文件缺失时返回空骨架。"""
    y = _yaml_rt()
    if not CHANNELS_PATH.exists():
        data = CommentedMap()
        data["channels"] = CommentedSeq()
        return data
    return y.load(CHANNELS_PATH.read_text(encoding="utf-8"))


def _channel_item(c: Dict[str, Any], public: bool = False) -> Dict[str, Any]:
    item = {
        "id": c.get("id"),
        "name": c.get("name", ""),
        "icon": c.get("icon", ""),
        "category": c.get("category", ""),
        "description": c.get("description", ""),
        "channel_type": c.get("channel_type", "coming_soon"),
        "strategy": c.get("strategy") or [],
        "enabled": bool(c.get("enabled", False)),
        "installed": bool(c.get("installed", c.get("builtin", False))),
        "builtin": bool(c.get("builtin", False)),
        "status": _ch_status(c),
    }
    if not public:
        item["endpoint"] = c.get("endpoint", "")  # 管理视图才含端点（公开视图不泄露）
    return item


@public_router.get("/channels")
async def public_list_channels(_acc: Account = Depends(_tenant_required)) -> Dict[str, Any]:
    """公开列表（渠道市场页用，无 admin token）。不泄露 endpoint。"""
    items = [_channel_item(c, public=True) for c in _ch_load()]
    return {
        "items": items,
        "total": len(items),
        "meta": {
            "registry": "config/channels.yaml",
            "hot_reload": "push_report 每次重读 channels.yaml，改完下一个任务即生效（零重启）",
            "note": "启用+端点已填的渠道在研报完成(on_complete)/升级(on_gate_fail)时真实外发",
        },
    }


class ChannelPost(BaseModel):
    id: str
    name: str
    category: str = ""
    description: str = ""
    channel_type: str = "webhook"
    payload_format: str = "discord"
    strategy: List[str] = ["on_complete"]
    endpoint: str = ""
    icon: str = ""


# ---------------------------------------------------------------------------
# M11-2 · 反思建议 + 经验草稿（Asset Center 后端，A1 合并面板）
# ---------------------------------------------------------------------------
def _ensure_tools_path() -> None:
    """保证 tools 包可导入（server 目录下懒加载）。"""
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))


@router.get("/admin/reflections")
async def list_reflections(
    status: Optional[str] = None, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出反思建议（默认全部；?status=proposed|accepted|rejected 过滤）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.reflection import load_reflections

    items = load_reflections(status)
    return {"ok": True, "items": _to_plain(items), "count": len(items)}


def _set_plugin_enabled(sid: str, enabled: bool, operator: str) -> None:
    """白名单机械操作：改 plugins.yaml 某源 enabled（ruamel round-trip + 原子写 + 审计）。"""
    path = _cfg_dir() / "plugins.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"配置文件不存在：{path}")
    before = path.read_text(encoding="utf-8")
    data = _yaml_rt().load(before)
    sources = (data.get("sources") or []) if isinstance(data, dict) else []
    hit = next((s for s in sources if isinstance(s, dict) and s.get("id") == sid), None)
    if hit is None:
        raise HTTPException(status_code=400, detail={"message": f"数据源不存在：{sid}"})
    hit["enabled"] = bool(enabled)
    _atomic_write(path, _dump_commented(data))
    _write_audit(
        "reflection_set_enabled", _to_plain(_yaml_rt().load(before)),
        _to_plain(data), operator, "pass", subdir="reflections", src_path=path,
    )


def _apply_reflection_change(entry: Dict[str, Any], operator: str) -> None:
    """按 accept 白名单应用机械变更；op 不在白名单 → 400 fail loud。"""
    pc = entry.get("proposed_change") or {}
    op = pc.get("op")
    if op in (None, "none"):
        return
    if op == "set_enabled":
        tgt = entry.get("target") or {}
        key = tgt.get("key") or ""
        m = re.match(r"^sources\.([^.]+)\.enabled$", key or "")
        if not m:
            raise HTTPException(status_code=400, detail={"message": f"非法 target.key（仅允许 sources.<id>.enabled）：{key}"})
        _set_plugin_enabled(m.group(1), bool(pc.get("value", False)), operator)
        return
    raise HTTPException(status_code=400, detail={"message": f"op {op} 不在白名单（仅 set_enabled 可机械执行）"})


@router.post("/admin/reflections/{rid}/accept")
async def accept_reflection(rid: str, x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """采纳反思建议：机械 op 走白名单改 yaml；仅提示类仅置 status 留痕（不改任何配置）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.reflection import load_reflections, set_reflection_status

    entry = next((i for i in load_reflections() if i.get("id") == rid), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"反思不存在：{rid}")
    if entry.get("status") == "accepted":
        return {"ok": True, "item": _to_plain(entry), "operator": operator, "note": "已采纳"}
    _apply_reflection_change(entry, operator)
    updated = set_reflection_status(rid, "accepted")
    return {"ok": True, "item": _to_plain(updated), "operator": operator}


@router.post("/admin/reflections/{rid}/reject")
async def reject_reflection(rid: str, x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """驳回反思建议（仅置 rejected 留痕，不删除、不改配置）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.reflection import set_reflection_status

    updated = set_reflection_status(rid, "rejected")
    if updated is None:
        raise HTTPException(status_code=404, detail=f"反思不存在：{rid}")
    return {"ok": True, "item": _to_plain(updated), "operator": operator}


@router.get("/admin/lessons")
async def list_lessons(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """列出 ChatAgent 经验草稿（propose_lesson 写入）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.chat_learning import list_proposed

    return {"ok": True, "items": list_proposed(), "count": len(list_proposed())}


@router.post("/admin/lessons/accept")
async def accept_lesson_api(
    body: Dict[str, Any], x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """采纳经验草稿进 playbook（热加载，下轮对话生效）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.chat_learning import accept_lesson

    text = (body or {}).get("text", "")
    ok = accept_lesson(text)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": "经验采纳失败（配置不可写或文本为空）"})
    _write_audit("lesson_accept", None, {"text": text}, operator, "pass",
                 subdir="lessons", src_path=_cfg_dir() / "chat_playbook.yaml")
    return {"ok": True, "operator": operator}


@router.get("/admin/channels")
async def admin_list_channels(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    _require_admin(x_admin_token)
    items = [_channel_item(c, public=False) for c in _ch_load()]
    return {"items": items, "total": len(items),
            "valid_types": VALID_TYPES, "valid_formats": VALID_FORMATS,
            "registry": str(CHANNELS_PATH)}


@router.post("/admin/channels")
async def create_channel(
    body: ChannelPost, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """复合新增自定义渠道（仅 channels.yaml 追加，无片段文件；失败回滚）。"""
    operator = _require_admin(x_admin_token)
    cid = body.id
    errs: List[str] = []
    if not CHANNEL_ID_RE.match(cid):
        errs.append(f"非法的渠道 id：{cid!r}（只允许字母/数字/-/_）")
    if body.channel_type not in VALID_TYPES:
        errs.append(f"不支持的 channel_type：{body.channel_type}（可选：{VALID_TYPES}）")
    if body.channel_type == "webhook" and body.payload_format not in VALID_FORMATS:
        errs.append(f"不支持的 payload_format：{body.payload_format}（可选：{VALID_FORMATS}）")
    if not body.name:
        errs.append("渠道名称不能为空")
    if not errs and any(c["id"] == cid for c in _ch_load()):
        errs.append(f"渠道已存在：{cid}")
    if errs:
        _write_audit("create_rejected", None, body.model_dump(), operator, "rejected",
                     errors=errs, subdir="channels", src_path=CHANNELS_PATH)
        raise HTTPException(status_code=400, detail={"message": "校验未通过", "errors": errs})

    before = CHANNELS_PATH.read_text(encoding="utf-8") if CHANNELS_PATH.exists() else None
    try:
        ldata = _load_channels_rt()
        entry = CommentedMap()
        for k, v in (("id", cid), ("name", body.name), ("icon", body.icon),
                     ("category", body.category), ("description", body.description),
                     ("channel_type", body.channel_type), ("payload_format", body.payload_format),
                     ("strategy", body.strategy), ("endpoint", body.endpoint),
                     ("enabled", False), ("installed", True), ("builtin", False)):
            entry[k] = v
        entry["status"] = _ch_status(entry)  # 派生状态（webhook 空端点→disconnected）
        ldata.setdefault("channels", []).append(entry)
        _atomic_write(CHANNELS_PATH, _dump_commented(ldata))
        _write_audit("create", None, _channel_item(dict(entry)), operator, "pass",
                     subdir="channels", src_path=CHANNELS_PATH)
    except Exception as exc:
        if before is not None:
            try:
                _atomic_write(CHANNELS_PATH, before)
            except Exception:
                pass
        _write_audit("create_rollback", None, body.model_dump(), operator, "rejected",
                     errors=[str(exc)], subdir="channels", src_path=CHANNELS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"新增失败，已全回滚：{exc}"})

    return {"ok": True, "item": _channel_item(dict(entry)), "operator": operator,
            "written": ["config/channels.yaml"]}


@router.put("/admin/channels/{cid}")
async def update_channel(
    cid: str, body: Dict[str, Any], x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """更新渠道（控制器入口）：enabled 启停 / endpoint / strategy / description / category / name / icon / payload_format。

    启用守卫：enabled=True 且 channel_type=webhook 且 endpoint 空 → 400 拒绝（防启用空端点）。
    builtin 渠道不可改 channel_type。
    """
    operator = _require_admin(x_admin_token)
    existing = _ch_load()
    target = next((c for c in existing if c["id"] == cid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"渠道不存在：{cid}")
    if target.get("builtin") and body.get("channel_type") and body["channel_type"] != target.get("channel_type"):
        raise HTTPException(status_code=400, detail={"message": f"内置渠道不可改 channel_type：{cid}"})

    want_type = body.get("channel_type", target.get("channel_type"))
    want_enabled = body.get("enabled", target.get("enabled"))
    if want_enabled and want_type == "webhook":
        ep = body.get("endpoint", target.get("endpoint"))
        if not (ep or "").strip():
            raise HTTPException(
                status_code=400,
                detail={"message": f"webhook 渠道端点为空，无法启用：{cid}。请先填写 endpoint。"},
            )

    before = CHANNELS_PATH.read_text(encoding="utf-8")
    try:
        ldata = _load_channels_rt()
        for entry in (ldata.get("channels") or []):
            if entry.get("id") == cid:
                for field in ("enabled", "description", "category", "name", "icon",
                              "strategy", "endpoint", "payload_format"):
                    if field in body:
                        entry[field] = body[field]
                entry["status"] = _ch_status(entry)  # 重算派生状态
                break
        _atomic_write(CHANNELS_PATH, _dump_commented(ldata))
        _write_audit("update", _channel_item(target), _channel_item(dict(entry)),
                     operator, "pass", subdir="channels", src_path=CHANNELS_PATH)
    except Exception as exc:
        _atomic_write(CHANNELS_PATH, before)
        _write_audit("update_rollback", _channel_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="channels", src_path=CHANNELS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"更新失败，已回滚：{exc}"})

    return {"ok": True, "item": _channel_item(next(c for c in _ch_load() if c["id"] == cid)),
            "operator": operator}


@router.delete("/admin/channels/{cid}")
async def delete_channel(
    cid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """删除自定义渠道（内置拒删，仅可停用）；复合删 = channels.yaml 移除 entry。"""
    operator = _require_admin(x_admin_token)
    existing = _ch_load()
    target = next((c for c in existing if c["id"] == cid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"渠道不存在：{cid}")
    if target.get("builtin"):
        raise HTTPException(status_code=400, detail={"message": f"内置渠道不可删：{cid}（仅可停用）"})

    before = CHANNELS_PATH.read_text(encoding="utf-8")
    try:
        ldata = _load_channels_rt()
        ldata["channels"] = [c for c in (ldata.get("channels") or []) if c.get("id") != cid]
        _atomic_write(CHANNELS_PATH, _dump_commented(ldata))
        _write_audit("delete", _channel_item(target), None, operator, "pass",
                     subdir="channels", src_path=CHANNELS_PATH)
    except Exception as exc:
        _atomic_write(CHANNELS_PATH, before)
        _write_audit("delete_rollback", _channel_item(target), None, operator, "rejected",
                     errors=[str(exc)], subdir="channels", src_path=CHANNELS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"删除失败，已回滚：{exc}"})

    return {"ok": True, "deleted": cid, "operator": operator, "removed": ["config/channels.yaml"]}


@router.post("/admin/channels/{cid}/test")
async def test_channel(
    cid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """向该渠道发一条测试消息（真实 send，验证连通；复用 push 单发）。

    返回码：coming_soon → 400；webhook 且 endpoint 空 → 400；mock → 200；webhook 已填 → 200 真实 POST 结果。
    """
    operator = _require_admin(x_admin_token)
    spec = next((c for c in _ch_load() if c["id"] == cid), None)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"渠道不存在：{cid}")
    ctype = (spec.get("channel_type") or "coming_soon").lower()
    if ctype == "coming_soon":
        raise HTTPException(status_code=400, detail={"message": "coming_soon"})
    if ctype == "webhook" and not (spec.get("endpoint") or "").strip():
        raise HTTPException(status_code=400, detail={"message": "endpoint empty"})
    cls = PROVIDER_REGISTRY.get(ctype, ComingSoonChannel)
    try:
        res = cls(spec).send({"title": "测试推送", "summary": "M9-4 连通性验证", "status": "done"})
        _write_audit("test", _channel_item(spec), res, operator, "pass",
                     subdir="channels", src_path=CHANNELS_PATH)
        return {"ok": True, "id": cid, "result": res, "operator": operator}
    except ToolError as e:
        _write_audit("test", _channel_item(spec), {"error": str(e)}, operator, "rejected",
                     errors=[str(e)], subdir="channels", src_path=CHANNELS_PATH)
        raise HTTPException(status_code=400, detail={"message": f"测试发送失败：{e}"})


# ---------------------------------------------------------------------------
# M12-2 · Skill 自主获取 API（L1 闸门；验收 V1/V2/V4/V5/V6）
# ---------------------------------------------------------------------------
class SkillImportReq(BaseModel):
    url: str
    target_roles: Optional[List[str]] = None
    sid: Optional[str] = None
    name: Optional[str] = None
    auto_install_l0: bool = True


@router.post("/admin/skills/import")
async def admin_skill_import(
    payload: SkillImportReq, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """导入任意 URL 的 skill（DESIGN_M12 §4.3）。

    L0 → 直接安装；L1 → 落提案等审批；L2 → 拒绝。任何分支都给明确原因（V5）。
    """
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer

    try:
        res = skill_importer.import_skill(
            payload.url, operator=operator,
            target_roles=payload.target_roles,
            sid=payload.sid, name=payload.name,
            auto_install_l0=payload.auto_install_l0,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"message": f"导入异常：{e}"})
    return {"ok": True, **res}


@router.get("/admin/skills/proposals")
async def admin_list_skill_proposals(
    x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出待审批的 skill 提案（含等级 / 来源 / 适配原因）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer

    items = skill_importer.list_proposals()
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/admin/skills/proposals/{pid}/accept")
async def admin_accept_skill_proposal(
    pid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """一键采纳 skill 提案（落盘生效）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer

    try:
        res = skill_importer.accept_proposal(pid, operator=operator)
    except skill_importer.SkillImportError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    return {"ok": True, **res}


@router.delete("/admin/skills/proposals/{pid}")
async def admin_reject_skill_proposal(
    pid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """驳回 skill 提案（删除提案，零落盘）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer

    try:
        res = skill_importer.reject_proposal(pid, operator=operator)
    except skill_importer.SkillImportError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})
    return {"ok": True, **res}


@router.post("/admin/skills/{sid}/rollback")
async def admin_skill_rollback(
    sid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """一键回滚已安装 skill（V4）：从注册表移除 + 删 md，prompt 中消失。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer

    try:
        res = skill_importer.rollback(sid, operator=operator)
    except skill_importer.SkillImportError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    return {"ok": True, **res}


@router.get("/admin/skills")
async def admin_list_skills(
    x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出已安装的导入技能（config/skills.yaml 中 builtin=False 的项）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools.skills import load_skills

    items = [s for s in load_skills() if not s.get("builtin")]
    return {"ok": True, "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# M12-2 · 专家团管控 API（M12-3 落盘后的管理面；V3/V7）
# ---------------------------------------------------------------------------
class ExpertToggle(BaseModel):
    enabled: bool = True


class ExpertConvertReq(BaseModel):
    url: str
    id: Optional[str] = None
    name: Optional[str] = None
    shape: Optional[str] = None
    allow_team_downgrade: bool = False


@router.get("/admin/experts")
async def admin_list_experts(
    enabled_only: bool = False, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出专家团（去掉大段 body，避免响应过大）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import experts

    items = experts.list_experts(enabled_only=enabled_only)
    slim = [{k: v for k, v in e.items() if k not in ("body", "skills_text")} for e in items]
    return {
        "ok": True,
        "items": slim,
        "count": len(slim),
        "proposals": len(experts.list_expert_proposals()),
    }


@router.post("/admin/experts/{eid}/toggle")
async def admin_toggle_expert(
    eid: str, payload: ExpertToggle, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """启停专家（改完下个对话即生效）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import experts

    try:
        res = experts.set_enabled(eid, bool(payload.enabled), operator=operator)
    except experts.ExpertError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    return {"ok": True, **res}


@router.post("/admin/experts/convert")
async def admin_expert_convert(
    payload: ExpertConvertReq, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """从 URL 转化生成专家包草案（DESIGN_M12 §5.5）。

    首批采用**确定性启发式**（不调 LLM，诚实边界）：抓取 → 拆 frontmatter → 生成
    plugin.json + agents/<id>.md 草案 → 存提案等审批。**team 型**会标记需降级。
    """
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer, experts

    try:
        source = skill_importer.fetch_source(payload.url)
    except skill_importer.SkillImportError as e:
        raise HTTPException(status_code=400, detail={"message": f"抓取失败：{e}"})

    fm, body = skill_importer.parse_frontmatter(source["text"])
    eid = payload.id or skill_importer._slugify(
        fm.get("name") or payload.name or skill_importer._first_heading(body) or "imported-expert")
    name = fm.get("name") or payload.name or skill_importer._first_heading(body) or eid
    is_team = bool(re.search(r"\b(team|多角色|多 ?agent|multi-agent)\b", source["text"], re.I))
    shape = payload.shape or ("analyst" if is_team else "researcher")
    proposal = {
        "id": eid,
        "name": name,
        "source_url": payload.url,
        "shape": shape,
        "expert_type": "team" if is_team else "agent",
        "allow_team_downgrade": bool(payload.allow_team_downgrade),
        "agent_body": body.strip(),
        "provenance": {
            "source_url": payload.url,
            "source_type": source["source_type"],
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sha256": source["sha256"],
        },
    }
    pid = experts.save_expert_proposal(proposal)
    return {"ok": True, "status": "pending_approval", "id": pid,
            "expert_type": proposal["expert_type"], "proposal": proposal}


@router.get("/admin/experts/proposals")
async def admin_list_expert_proposals(
    x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """列出待审批的专家包转化提案。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import experts

    items = experts.list_expert_proposals()
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/admin/experts/proposals/{pid}/accept")
async def admin_accept_expert_proposal(
    pid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """审批通过专家包提案：写包 + 注册。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import experts

    try:
        res = experts.accept_expert_proposal(pid, operator=operator)
    except experts.ExpertError as e:
        raise HTTPException(status_code=400, detail={"message": str(e)})
    return {"ok": True, **res}


@router.delete("/admin/experts/proposals/{pid}")
async def admin_reject_expert_proposal(
    pid: str, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """驳回专家包提案。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import experts

    try:
        res = experts.reject_expert_proposal(pid, operator=operator)
    except experts.ExpertError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})
    return {"ok": True, **res}


# ---------------------------------------------------------------------------
# 统一审核中心（Review Center）：聚合三类待审核项 + 待推送变更集决策
# 设计：DESIGN_review_center.md（决策权在老板 UI；push 由宿主 AI 代理执行）
# ---------------------------------------------------------------------------
class ReviewRejectReq(BaseModel):
    reason: str = ""


class ReviewPushedReq(BaseModel):
    pushed_ref: str = ""


@router.get("/admin/review/queue")
async def admin_review_queue(x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """聚合三类待老板审核项：技能提案 / 专家提案 / 待推送变更集。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import skill_importer, experts, review_queue

    items: List[Dict[str, Any]] = []
    for p in skill_importer.list_proposals():
        items.append({
            "kind": "skill_proposal",
            "id": p.get("id"),
            "title": p.get("name"),
            "level": p.get("level"),
            "status": "pending",
            "decision": None,
            "detail": p,
        })
    for p in experts.list_expert_proposals():
        items.append({
            "kind": "expert_proposal",
            "id": p.get("id"),
            "title": p.get("name"),
            "level": p.get("level"),
            "status": "pending",
            "decision": None,
            "detail": p,
        })
    for it in review_queue.list_items():
        entry = dict(it)
        entry["kind"] = "pending_push"
        items.append(entry)

    counts = {
        "skill_proposal": sum(1 for x in items if x["kind"] == "skill_proposal"),
        "expert_proposal": sum(1 for x in items if x["kind"] == "expert_proposal"),
        "pending_push": sum(1 for x in items if x["kind"] == "pending_push"),
    }
    counts["total"] = len(items)
    return {"ok": True, "items": items, "counts": counts}


@router.post("/admin/review/queue/{item_id}/approve")
async def admin_review_approve(item_id: str, x_admin_token: Optional[str] = Header(None)) -> Dict[str, Any]:
    """待推送变更集：记录老板批准（仅写决策；真正 push 由宿主 AI 代理消费后执行）。"""
    operator = _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import review_queue

    try:
        entry = review_queue.approve(item_id, operator)
    except review_queue.ReviewQueueError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})
    return {"ok": True, **entry}


@router.post("/admin/review/queue/{item_id}/reject")
async def admin_review_reject(
    item_id: str, body: ReviewRejectReq, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """待推送变更集：打回（记录原因；不推送）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import review_queue

    try:
        entry = review_queue.reject(item_id, body.reason)
    except review_queue.ReviewQueueError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})
    return {"ok": True, **entry}


@router.post("/admin/review/queue/{item_id}/mark-pushed")
async def admin_review_mark_pushed(
    item_id: str, body: ReviewPushedReq, x_admin_token: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """宿主 AI 代理 push 完成后回写（闭环；UI 显示已推送 + remote ref）。"""
    _require_admin(x_admin_token)
    _ensure_tools_path()
    from tools import review_queue

    try:
        entry = review_queue.mark_pushed(item_id, body.pushed_ref)
    except review_queue.ReviewQueueError as e:
        raise HTTPException(status_code=404, detail={"message": str(e)})
    return {"ok": True, **entry}
