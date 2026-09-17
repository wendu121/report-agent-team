# tools/reflection.py · M11-2 反思回写闭环（确定性规则引擎 v1）
#
# 职责：
# - generate_reflection(state)：读 tool_status / retrieval_records / routing_state，
#   产出**可验证**的结构化反思条目（每条都有真实 state 证据支撑，绝不编造因果）。
# - append_reflections / load_reflections / set_reflection_status：落库到
#   config/reflections.proposed.yaml（git-ignored，运行时生成）。
#
# 设计对齐：DESIGN_M11-2_reflection.md
# - D1 生成器：v1 确定性规则（非 LLM）→ 零幻觉、零额外 token、证据可审计。
# - D2 不落 kb.entries（防污染 Researcher 来源集）→ 只存本 yaml + 审计。
# - D3 accept 白名单：仅 `set_enabled` 可机械改 yaml；v1 全为"仅提示"（op=none），
#   机械 set_enabled 路径保留为未来规则使用（当前 state 缺可靠逐源失败遥测，见底部 NOTE）。
#
# 诚实边界：任何失败路径降级为 no-op / 返回 []，绝不抛错击穿任务终态。
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 向后兼容常量（旧代码引用）。新代码一律用 _reflections_cfg()（按账号惰性解析）。
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LEGACY_REFLECTIONS_PATH = CONFIG_DIR / "reflections.proposed.yaml"


def _reflections_cfg() -> Path:
    """按当前账号命名空间解析反思配置（各自一份）。"""
    try:
        from server import tenancy
        return tenancy.config_path("reflections.proposed.yaml")
    except ImportError:
        return LEGACY_REFLECTIONS_PATH

# accept 白名单：仅这些 op 允许机械改 yaml；其余一律"仅提示+人工"
MECHANICAL_OPS = {"set_enabled"}


# ---------------------------------------------------------------------------
# yaml 读写（复用 chat_learning 的 round-trip / 原子写，保持一致）
# ---------------------------------------------------------------------------
def _read_yaml(p: Path) -> dict:
    try:
        import yaml

        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_yaml_atomic(p: Path, data: dict) -> None:
    try:
        import ruamel.yaml

        ry = ruamel.yaml.YAML()
        ry.preserve_quotes = True
        tmp = p.with_suffix(p.suffix + ".tmp")
        ry.dump(data, tmp)
        os.replace(tmp, p)
        return
    except Exception:  # noqa: BLE001
        pass
    import yaml

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 生成器（确定性规则）
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(date_str: str, seq: int) -> str:
    return f"rfl-{date_str}-{seq:04d}"


def _cred_of(rec: dict) -> Optional[float]:
    c = rec.get("credibility")
    if isinstance(c, (int, float)):
        return float(c)
    return None


def generate_reflection(state: dict) -> List[Dict[str, Any]]:
    """从任务终态 state 产出反思条目。无证据则返回 []（绝不造无据建议）。

    读取的真实证据（实测 field:line）：
    - state["routing_state"]["escalate_reason"]（orchestrator.py:1620 写入）
    - state["retrieval_records"]（每条带 source/credibility，M9-2）
    - state["tool_status"]（engine 真实工具执行结果，orchestrator.py:84）
    - state["user_task"]["topic"]
    """
    state = state or {}
    rs = state.get("routing_state", {}) or {}
    recs = state.get("retrieval_records", []) or []
    topic = (state.get("user_task", {}) or {}).get("topic", "") or "(unknown)"

    out: List[Dict[str, Any]] = []

    # --- 规则 escalate_review：任务以 escalate 终结 ---
    esc = rs.get("escalate_reason")
    if esc:
        out.append({
            "kind": "escalate_review",
            "summary": f"任务以 escalate 终结：{esc}",
            "evidence": [
                f"escalate_reason: {esc}",
                f"last_gate: {rs.get('last_gate')}",
                f"last_agent: {rs.get('last_gate_role')}",
            ],
            "target": None,
            "proposed_change": {"op": "none"},
        })

    # --- 规则 plugin_hint：逐源可信度统计（命中但均低可信）---
    by_src: Dict[str, List[float]] = {}
    for r in recs:
        if not isinstance(r, dict):
            continue
        s = r.get("source")
        c = _cred_of(r)
        if s and c is not None:
            by_src.setdefault(s, []).append(c)
    for src, creds in by_src.items():
        if creds and all(c < 0.5 for c in creds):
            out.append({
                "kind": "plugin_hint",
                "summary": f"数据源 {src} 命中但可信度均偏低（<0.5），建议复核其可用性/接入质量",
                "evidence": [f"{src}: {len(creds)} 条, 可信度={[round(c, 2) for c in creds]}"],
                "target": {"file": "plugins.yaml", "key": f"sources.{src}.enabled"},
                "proposed_change": {"op": "none"},
            })

    # --- 规则 low_yield：检索素材偏少（未 escalate 时）---
    if not esc and len(recs) < 3:
        out.append({
            "kind": "low_yield",
            "summary": f"检索素材偏少（{len(recs)} 条），建议检查启用源连通性或扩源",
            "evidence": [f"retrieval_records 总数={len(recs)}"],
            "target": None,
            "proposed_change": {"op": "none"},
        })

    # --- 规则 rework_hotspot：某 Agent 被闸打回 ≥2 轮 ---
    rework = rs.get("rework")
    if isinstance(rework, int) and rework >= 2:
        out.append({
            "kind": "rework_hotspot",
            "summary": f"Agent {rs.get('last_gate_role')} 被闸打回 ≥{rework} 轮，建议至 Agent 配置台复核其 prompt",
            "evidence": [f"routing_state.rework={rework}", f"last_gate_role={rs.get('last_gate_role')}"],
            "target": None,
            "proposed_change": {"op": "none"},
        })

    # 统一填充 id / 时间戳 / status / task_topic
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    existing = load_reflections()
    seq = len(existing) + 1
    for e in out:
        e["id"] = _gen_id(date_str, seq)
        e["created_at"] = _now_iso()
        e["task_topic"] = topic
        e["status"] = "proposed"
        seq += 1
    return out


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
def _reflection_sig(e: dict) -> tuple:
    """内容签名：同 (kind, task_topic, summary) 视为同一条反思，避免重复堆积。"""
    return (e.get("kind"), e.get("task_topic"), e.get("summary"))


def append_reflections(entries: List[Dict[str, Any]]) -> int:
    """追加写入。幂等：同 id 跳过；同内容签名（kind+topic+summary）也跳过，
    防止同一任务反复运行把「经验与反思」堆成几百条重复噪声。失败降级返回 0。"""
    if not entries:
        return 0
    try:
        data = _read_yaml(_reflections_cfg()) if _reflections_cfg().exists() else {}
        reflections = data.get("reflections") or []
        seen = {r.get("id") for r in reflections if isinstance(r, dict)}
        seen_sig = {_reflection_sig(r) for r in reflections if isinstance(r, dict)}
        n = 0
        for e in entries:
            rid = e.get("id")
            sig = _reflection_sig(e)
            if (rid and rid in seen) or sig in seen_sig:
                continue
            reflections.append(e)
            seen.add(rid)
            seen_sig.add(sig)
            n += 1
        data["reflections"] = reflections
        _write_yaml_atomic(_reflections_cfg(), data)
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("反思条目写入失败（降级跳过）: %s", e)
        return 0


def dedupe_reflections() -> int:
    """一次性清理：对已有 reflections.proposed.yaml 按内容签名去重（保留首条）。
    用于治理历史上已堆积的重复条目。返回删除条数；文件不存在返回 0。"""
    if not _reflections_cfg().exists():
        return 0
    try:
        data = _read_yaml(_reflections_cfg())
        reflections = data.get("reflections") or []
        seen_sig: set = set()
        kept: List[Dict[str, Any]] = []
        removed = 0
        for r in reflections:
            if not isinstance(r, dict):
                continue
            sig = _reflection_sig(r)
            if sig in seen_sig:
                removed += 1
                continue
            seen_sig.add(sig)
            kept.append(r)
        if removed:
            data["reflections"] = kept
            _write_yaml_atomic(_reflections_cfg(), data)
        return removed
    except Exception as e:  # noqa: BLE001
        logger.warning("反思去重失败（降级跳过）: %s", e)
        return 0


def load_reflections(status: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        data = _read_yaml(_reflections_cfg()) if _reflections_cfg().exists() else {}
        items = data.get("reflections") or []
        if status:
            items = [i for i in items if isinstance(i, dict) and i.get("status") == status]
        return [i for i in items if isinstance(i, dict)]
    except Exception:  # noqa: BLE001
        return []


def set_reflection_status(rid: str, status: str) -> Optional[Dict[str, Any]]:
    """按 id 置 status（proposed→accepted/rejected 留痕）。找不到返回 None。"""
    try:
        if not _reflections_cfg().exists():
            return None
        data = _read_yaml(_reflections_cfg())
        reflections = data.get("reflections") or []
        hit = None
        for r in reflections:
            if isinstance(r, dict) and r.get("id") == rid:
                r["status"] = status
                hit = r
                break
        if hit is None:
            return None
        _write_yaml_atomic(_reflections_cfg(), data)
        return hit
    except Exception as e:  # noqa: BLE001
        logger.warning("反思状态更新失败（降级）: %s", e)
        return None


# ---------------------------------------------------------------------------
# NOTE（诚实边界 / 设计偏离记录）
# ---------------------------------------------------------------------------
# DESIGN_M11-2_reflection.md §4 原计划 v1 含 `plugin_disable`（机械 op=set_enabled，
# 关闭全失败的源）。但当前 M4/M11-3 的 tool_status 是"逐工具"（web_search/data_proc/
# doc_export）而非"逐源"，逐源失败不进 retrieval_records 也不进 tool_status 的逐源字段。
# 为避免"无据禁用源"的假闭环，v1 仅产出可验证的"仅提示"类（plugin_hint/low_yield/
# escalate_review/rework_hotspot）+ 人工 accept 留痕；机械 set_enabled 路径在 accept 端点
# 已保留白名单闸门，待引擎补"逐源失败遥测"（retrieval_records 带 ok/error）后即可启用，
# 届时 plugin_disable 规则自然接入（列入 TECH_DEBT，不阻塞本里程碑）。
