"""M12-3 专家团（Expert Corps）。

定位（DESIGN_M12 §5.1）：**专家 = 上层封装，不是第二套 Agent 体系**。
执行时展开为 `<persona> + <方法点/工作流程> + <包内技能>`，交给现有 `make_agent`（shape 驱动）。
复用 M9-1 的 shape 机制，零重复实现（N3：不重建 Agent 体系）。

包格式：**1:1 采用 WorkBuddy 专家包规范 v2.0**（DESIGN_M12 §5.2），不自创字段：

    experts/<kebab-name>/
    ├── plugin.json                       # 元数据 + 展示字段
    ├── agents/<agentName>.md             # 角色/核心能力/工作流程/输出规范/注意事项
    ├── skills/<skill-name>/              # 可选，随包自带技能
    └── avatars/                          # 可选

`config/experts.yaml` **只存注册表映射**（id / 目录 / enabled / shape / model / 溯源），
包体原样保留 WorkBuddy 原生结构 —— 于是 WorkBuddy 生态的专家包可原样导入、零转换。

诚实边界（DESIGN_M12 §5.6）：
  - `expertType: team` 的**真·多角色并发协作**本引擎没有等价物（固定 LangGraph 流水线）。
    默认**拒绝**；仅当注册表条目显式 `allow_team_downgrade: true` 时才降级为
    「单专家 + 主理人提示」，并在 system prompt 里标注「语义有损」。
  - Agent MD frontmatter 的 `tools` 字段**不由包声明**（WorkBuddy 铁律：工具权限系统统一分配）。
  - `maxTurns` / `bin/` 等本引擎无对应概念 → 忽略，仅记 warning，不报错。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 向后兼容常量（旧代码/测试引用）：指向仓库全局。**新代码一律用下面的函数**。
BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"
EXPERT_DIR = BASE / "experts"
REGISTRY_PATH = CONFIG_DIR / "experts.yaml"
AUDIT_DIR = BASE / ".audit"
EXPERT_LOG = AUDIT_DIR / "experts.jsonl"


def _root() -> Path:
    """按当前账号命名空间解析资源根（各自一套专家包与注册表）。"""
    try:
        from server import tenancy
        return tenancy.account_root()
    except ImportError:
        return BASE


def _registry_path() -> Path:
    return _root() / "config" / "experts.yaml"


def _expert_dir() -> Path:
    return _root() / "experts"


def _audit_dir() -> Path:
    return _root() / ".audit"


def _expert_log() -> Path:
    return _root() / ".audit" / "experts.jsonl"

VALID_SHAPES = ("researcher", "analyst", "writer")

# categoryId → 建议 shape（导入时的**启发式默认值**，审批时可改；非强约束）
CATEGORY_SHAPE = {
    "01-ProductDesign": "analyst",
    "02-Engineering": "researcher",
    "03-DataAnalytics": "analyst",
    "04-ContentWriting": "writer",
    "05-DesignCreative": "writer",
    "06-Marketing": "writer",
    "07-Sales": "writer",
    "08-HumanResources": "analyst",
    "09-FinanceAccounting": "analyst",
    "10-LegalCompliance": "analyst",
    "11-EducationTraining": "researcher",
    "12-IndustryConsultant": "analyst",
}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)


class ExpertError(Exception):
    """专家层异常：任何失败都给明确原因，不静默吞错。"""


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------
def load_registry() -> List[Dict[str, Any]]:
    """读 config/experts.yaml；缺失返回 []（向后兼容：无专家团 → 行为不变）。"""
    rp = _registry_path()
    if not rp.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(rp.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        raise ExpertError(f"专家注册表读取失败：{e}")
    return [e for e in (data.get("experts") or []) if isinstance(e, dict) and e.get("id")]


def _yaml_rt():
    try:
        from ruamel.yaml import YAML
    except ImportError as e:  # pragma: no cover
        raise ExpertError(f"ruamel.yaml 未安装，拒绝降级写配置（缺失须 fail loud）: {e}")
    y = YAML()
    y.preserve_quotes = True
    rp = _registry_path()
    data = y.load(rp.read_text(encoding="utf-8")) if rp.exists() else None
    return y, data


def _save_registry(y, data) -> None:
    import io

    buf = io.StringIO()
    y.dump(data, buf)
    rp = _registry_path()
    rp.parent.mkdir(parents=True, exist_ok=True)
    tmp = rp.with_suffix(".yaml.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(rp)


def _audit(action: str, payload: Dict[str, Any]) -> None:
    _audit_dir().mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, **payload}
    with _expert_log().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 包体解析
# --------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


def read_expert(entry: Dict[str, Any]) -> Dict[str, Any]:
    """读一个专家包：plugin.json + agents/<agentName>.md + 包内 skills。

    返回结构化 dict；包体缺失/损坏 → 抛 ExpertError（诚实，不静默降级为「没有专家」）。
    """
    eid = entry.get("id")
    rel = entry.get("dir") or f"experts/{eid}"
    root = _root() / rel
    pj_path = root / "plugin.json"
    if not pj_path.exists():
        raise ExpertError(f"专家包缺少 plugin.json：{rel}")
    try:
        plugin = json.loads(pj_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ExpertError(f"plugin.json 解析失败（{rel}）：{e}")

    etype = (plugin.get("expertType") or "agent").lower()
    if etype == "team" and not entry.get("allow_team_downgrade"):
        raise ExpertError(
            f"专家包 {eid!r} 是 expertType=team：真·多角色并发协作本引擎无等价物，"
            f"默认拒绝。若接受「降级为单专家 + 主理人提示」（语义有损），"
            f"请在注册表该条目上设 allow_team_downgrade: true。"
        )

    agent_name = plugin.get("agentName") or eid
    md_path = root / "agents" / f"{agent_name}.md"
    body = ""
    fm: Dict[str, str] = {}
    if md_path.exists():
        raw = md_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
    if not body.strip():
        raise ExpertError(f"专家包 {eid!r} 的 agents/{agent_name}.md 缺失或为空")

    if "tools" in fm:
        _audit("frontmatter_tools_ignored", {
            "id": eid, "note": "工具权限由系统统一分配，包内 tools 声明被忽略"})

    skills_text = _load_pack_skills(root, fm.get("skills") or plugin.get("skills") or [])

    return {
        "id": eid,
        "dir": rel,
        "enabled": bool(entry.get("enabled", True)),
        "shape": (entry.get("shape") or "").lower() or "researcher",
        "model": entry.get("model") or "",
        "plugin": plugin,
        "frontmatter": fm,
        "body": body.strip(),
        "skills_text": skills_text,
        "expert_type": etype,
        "degraded_team": etype == "team",
        "display_name": _zh(plugin.get("displayName")) or plugin.get("name") or eid,
        "profession": _zh(plugin.get("profession")) or "",
        "tags": plugin.get("tags") or [],
        "category_id": plugin.get("categoryId") or "",
    }


def _zh(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("zh") or v.get("en") or ""
    return v if isinstance(v, str) else ""


def _load_pack_skills(root: Path, names: List[str]) -> str:
    """读包内 skills/<name>/SKILL.md（或 <name>.md），拼成注入片段。"""
    parts: List[str] = []
    for n in names:
        if not isinstance(n, str):
            continue
        n = n.strip().strip("/")
        for cand in (root / "skills" / n / "SKILL.md", root / "skills" / f"{n}.md"):
            if cand.exists():
                parts.append(f"\n【专家自带技能：{n}】\n{cand.read_text(encoding='utf-8').strip()}\n【技能结束】\n")
                break
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 运行时：拼 system prompt
# --------------------------------------------------------------------------
def build_expert_system(expert: Dict[str, Any]) -> str:
    """专家上下文 → 注入 make_agent 的 system prompt 片段。"""
    head = f"\n【专家：{expert['display_name']}】"
    if expert.get("profession"):
        head += f"（{expert['profession']}）"
    if expert.get("degraded_team"):
        head += "\n⚠️ 本包原为 expertType=team，本引擎无多角色并发协作等价物，"
        head += "已**降级**为单专家 + 主理人提示，多角色分工语义有损。"
    head += "\n以下为专家包的角色定义、核心能力、工作流程与输出规范，务必遵循：\n"
    return head + expert["body"] + expert.get("skills_text", "") + "\n【专家结束】\n"


def list_experts(enabled_only: bool = True) -> List[Dict[str, Any]]:
    """列出可用专家；单个包损坏**只跳过该包**并告警，不影响其它（防御纵深）。"""
    out: List[Dict[str, Any]] = []
    for entry in load_registry():
        if enabled_only and not entry.get("enabled", True):
            continue
        try:
            out.append(read_expert(entry))
        except ExpertError as e:
            _audit("expert_load_failed", {"id": entry.get("id"), "error": str(e)})
            continue
    return out


def get_expert(eid: str) -> Optional[Dict[str, Any]]:
    for entry in load_registry():
        if entry.get("id") == eid:
            return read_expert(entry)
    return None


# --------------------------------------------------------------------------
# 调度：显式 @ / 隐式路由
# --------------------------------------------------------------------------
def resolve_mention(text: str, experts: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """显式调度：`@<专家名>` → expert id。匹配 id 或中文 displayName。"""
    if not text or "@" not in text:
        return None
    experts = experts if experts is not None else list_experts()
    m = re.search(r"@([A-Za-z0-9_\-\u4e00-\u9fa5]{1,32})", text)
    if not m:
        return None
    token = m.group(1).lower()
    for e in experts:
        if token == e["id"].lower() or token == e["display_name"]:
            return e["id"]
    return None


def route(text: str, experts: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """隐式路由（V7）：关键词打分，达不到高置信**必须回退**通用 ChatAgent。

    判据保守：命中数 < 2 → 不选（宁可不用专家，也不误路由）。
    """
    experts = experts if experts is not None else list_experts()
    if not experts or not text:
        return None
    t = text.lower()
    scored: List[Tuple[int, str]] = []
    for e in experts:
        keys = [e["id"], e["display_name"], e["profession"], e["category_id"]]
        keys += [str(x) for x in (e.get("tags") or [])]
        keys += re.findall(r"[\u4e00-\u9fa5]{2,4}", e["body"][:400])
        score = 0
        for k in keys:
            k = (k or "").strip().lower()
            if len(k) >= 2 and k in t:
                score += 1
        if score:
            scored.append((score, e["id"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    best, best_id = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0
    if best < 2:
        return None
    if best == runner:
        return None  # 并列第一 = 无法确定，回退（不猜）
    return best_id


# --------------------------------------------------------------------------
# 注册 / 注销
# --------------------------------------------------------------------------
def register_expert(eid: str, dir_rel: str, *, shape: Optional[str] = None,
                    model: str = "", source_url: str = "",
                    allow_team_downgrade: bool = False,
                    operator: str = "system") -> Dict[str, Any]:
    """把已落盘的专家包写入注册表（先备份后写，可回滚）。"""
    if shape and shape not in VALID_SHAPES:
        raise ExpertError(f"非法 shape：{shape!r}（可选：{list(VALID_SHAPES)}）")
    # M12-4（REVIEW_M12.md 残留 MINOR-1）：注册表 id 也必须过 _norm_id。
    # 本函数不以 eid 拼路径（路径取自已规范化的 dir_rel），故无越界写风险；
    # 但 id 存原始字符串会让「注册表里出现 ../../evil 之类的脏 id」，
    # 与 _expert_proposal_path / _write_package 的规范化不一致，属一致性缺陷。
    eid = _norm_id(eid)
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    backup = None
    rp = _registry_path()
    if rp.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bd = _audit_dir() / "expert_backups" / stamp
        bd.mkdir(parents=True, exist_ok=True)
        (bd / "experts.yaml").write_text(rp.read_text(encoding="utf-8"), encoding="utf-8")
        backup = str(bd.relative_to(_root()))

    y, data = _yaml_rt()
    if data is None:
        data = CommentedMap()
    if data.get("experts") is None:
        data["experts"] = CommentedSeq()

    entry = None
    for e in data["experts"]:
        if isinstance(e, dict) and e.get("id") == eid:
            entry = e
            break
    if entry is None:
        entry = CommentedMap()
        data["experts"].append(entry)

    resolved_shape = shape or CATEGORY_SHAPE.get(_category_of(dir_rel), "researcher")
    entry["id"] = eid
    entry["dir"] = dir_rel
    entry["enabled"] = True
    entry["shape"] = resolved_shape
    entry["model"] = model
    entry["source_url"] = source_url
    entry["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if allow_team_downgrade:
        entry["allow_team_downgrade"] = True

    _save_registry(y, data)
    _audit("expert_registered", {"id": eid, "shape": resolved_shape,
                                 "backup": backup, "operator": operator})
    return {"status": "registered", "id": eid, "shape": resolved_shape, "backup": backup}


def _category_of(dir_rel: str) -> str:
    try:
        pj = json.loads((_root() / dir_rel / "plugin.json").read_text(encoding="utf-8"))
        return pj.get("categoryId") or ""
    except Exception:  # noqa: BLE001
        return ""


def unregister_expert(eid: str, operator: str = "system") -> Dict[str, Any]:
    y, data = _yaml_rt()
    if data is None or not data.get("experts"):
        raise ExpertError(f"专家 {eid!r} 不在注册表中")
    for i, e in enumerate(list(data["experts"])):
        if isinstance(e, dict) and e.get("id") == eid:
            data["experts"].pop(i)
            _save_registry(y, data)
            _audit("expert_unregistered", {"id": eid, "operator": operator})
            return {"status": "unregistered", "id": eid}
    raise ExpertError(f"专家 {eid!r} 不在注册表中")


def set_enabled(eid: str, enabled: bool, operator: str = "system") -> Dict[str, Any]:
    y, data = _yaml_rt()
    if data is None or not data.get("experts"):
        raise ExpertError(f"专家 {eid!r} 不在注册表中")
    for e in data["experts"]:
        if isinstance(e, dict) and e.get("id") == eid:
            e["enabled"] = bool(enabled)
            _save_registry(y, data)
            _audit("expert_toggled", {"id": eid, "enabled": bool(enabled), "operator": operator})
            return {"status": "ok", "id": eid, "enabled": bool(enabled)}
    raise ExpertError(f"专家 {eid!r} 不在注册表中")


# --------------------------------------------------------------------------
# 转化提案（M12-3 V3）：从 URL/正文转化专家包草案 → 审批 → 落盘
# --------------------------------------------------------------------------
PROPOSAL_DIR = AUDIT_DIR / "expert_proposals"  # 向后兼容常量；实际读写走 _proposal_dir()


def _proposal_dir() -> Path:
    return _root() / ".audit" / "expert_proposals"


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", (s or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "imported-expert"


def _norm_id(raw: Optional[str]) -> str:
    """规范化用户可控 id 为安全文件名（防路径遍历，DESIGN §6.2）。"""
    return _slugify(raw or "") or "imported-expert"


def _expert_proposal_path(pid: str) -> Path:
    return _proposal_dir() / f"{_norm_id(pid)}.json"  # 防路径遍历（DESIGN §6.2）


def save_expert_proposal(proposal: Dict[str, Any]) -> str:
    """存专家包转化草案（等审批）。幂等：同名 id 覆盖。"""
    _proposal_dir().mkdir(parents=True, exist_ok=True)
    pid = _norm_id(proposal.get("id") or "imported-expert")
    _expert_proposal_path(pid).write_text(
        json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    _audit("expert_proposal_created", {"id": pid, "source_url": proposal.get("source_url")})
    return pid


def list_expert_proposals() -> List[Dict[str, Any]]:
    pd = _proposal_dir()
    if not pd.exists():
        return []
    out = []
    for p in sorted(pd.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def get_expert_proposal(pid: str) -> Optional[Dict[str, Any]]:
    p = _expert_proposal_path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write_package(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """把提案写成 WorkBuddy 原生专家包（plugin.json + agents/<id>.md）。

    首批采用**确定性启发式**（不调 LLM，诚实边界见 DESIGN_M12 §5.5）：
    包体结构完整、本引擎可直接吃；字段尽量贴近 WorkBuddy 规范，但
    标签/quickPrompts 用占位默认值（本引擎 read_expert 不消费这些，仅保留兼容性）。
    """
    eid = _norm_id(proposal["id"])  # 防路径遍历：id 先规范化再拼包目录
    etype = (proposal.get("expert_type") or "agent").lower()
    name = proposal.get("name") or eid
    prof = proposal.get("profession_zh") or proposal.get("profession") or name
    root = _expert_dir() / eid
    # 防御纵深：_norm_id 已剥离 / . ..，root 必在 EXPERT_DIR 内
    if ".." in eid or "/" in eid or "\\" in eid:
        raise ExpertError(f"非法专家 id（含路径片段）：{proposal.get('id')!r}")
    (root / "agents").mkdir(parents=True, exist_ok=True)

    plugin = {
        "name": eid,
        "version": "1.0.0",
        "description": name,
        "expertType": etype,
        "agentName": eid,
        "agents": [f"agents/{eid}.md"],
        "skills": [],
        "displayName": {"en": name, "zh": name},
        "profession": {"en": prof, "zh": prof},
        "displayDescription": {
            "en": (proposal.get("description") or name)[:50],
            "zh": (proposal.get("description_zh") or name)[:50],
        },
        "categoryId": proposal.get("category_id") or "12-IndustryConsultant",
        "tags": [name, eid, proposal.get("category_id") or "expert"],
        "quickPrompts": [
            {"label": "开始", "prompt": f"请作为{eid}开始工作"},
            {"label": "分析", "prompt": "请深入分析这个主题"},
            {"label": "总结", "prompt": "请给出结论性总结"},
        ],
        "avatar": "",
        "plugin": eid,
    }
    (root / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2), encoding="utf-8")

    md = (
        f"---\n"
        f"name: {eid}\n"
        f"description: {name}\n"
        f"displayName: {{en: {name}, zh: {name}}}\n"
        f"profession: {{en: {prof}, zh: {prof}}}\n"
        f"maxTurns: 50\n"
        f"skills: []\n"
        f"---\n\n"
        f"# 角色描述\n{name}（{prof}）。\n\n"
        f"# 核心能力\n{proposal.get('core_abilities') or '依据下方工作流程执行领域任务。'}\n\n"
        f"# 工作流程\n{proposal.get('agent_body') or ''}\n\n"
        f"# 输出规范\n给出结构化、可核验的结论，标注来源与不确定性。\n\n"
        f"# 注意事项\n仅基于可靠来源作答；不确定时明确说明，不编造。\n"
    )
    (root / "agents" / f"{eid}.md").write_text(md, encoding="utf-8")
    # 相对**租户根**而非写死的 BASE：多租户改造后资源根由 tenancy 解析，
    # TENANTS_ROOT 指向别处时 relative_to(BASE) 会 ValueError（与 skill_importer._backup 同源）。
    # 兜底：不在租户根下就退回绝对路径，绝不让「算相对路径」把审批流程搞崩。
    try:
        pkg_dir = str(root.relative_to(_root()))
    except ValueError:
        pkg_dir = str(root)
    return {"package_dir": pkg_dir, "plugin": plugin}


def accept_expert_proposal(pid: str, operator: str = "system") -> Dict[str, Any]:
    """审批通过：写包 → 注册 → 删提案。"""
    prop = get_expert_proposal(pid)
    if prop is None:
        raise ExpertError(f"专家提案不存在：{pid!r}")
    pkg = _write_package(prop)
    reg = register_expert(
        prop["id"], pkg["package_dir"], shape=prop.get("shape"),
        model=prop.get("model", ""), source_url=prop.get("source_url", ""),
        allow_team_downgrade=bool(prop.get("allow_team_downgrade")), operator=operator,
    )
    try:
        _expert_proposal_path(pid).unlink()
    except OSError:
        pass
    _audit("expert_proposal_accepted", {"id": pid, "operator": operator})
    return {"status": "installed", "id": prop["id"], "package": pkg, "registry": reg}


def reject_expert_proposal(pid: str, operator: str = "system") -> Dict[str, Any]:
    prop = get_expert_proposal(pid)
    if prop is None:
        raise ExpertError(f"专家提案不存在：{pid!r}")
    _expert_proposal_path(pid).unlink()
    _audit("expert_proposal_rejected", {"id": pid, "operator": operator})
    return {"status": "rejected", "id": pid}
