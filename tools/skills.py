"""M9-3 研报技能库抽象层。

技能 = 可复用的 prompt 框架片段（soft enhancement），安装后可注入对应 Agent 的 system prompt。
SoT：config/skills.yaml（注册表）+ skills/<id>.md（片段文件）。
热加载：load_skills() 每次调用现读磁盘（与 build_tools / build_agent_registry 同频），零重启生效。
诚实边界（见 DESIGN_M9-3 §10）：
  - R1 软增强不谎称强制：引擎仅保证「接线真实」（片段拼入对应 role 的 system），
    不机器强制报告必含某结构；LLM 是否遵循取决于 prompt，不可控。
  - R3 空技能防御：片段缺失 → 跳过（不 raise、不崩溃、不冒充可用）。
  - R4 向后兼容：无 skills.yaml → load_skills 返回 [] → 无注入，现有行为不变。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # PyYAML（skills.yaml 无需 round-trip 注释保留；写操作由 server/admin.py 走 ruamel）

# 项目根（report-agent-team/），与 orchestrator._load_md(BASE / rel) 同源
BASE = Path(__file__).resolve().parent.parent


def _tenant_root() -> Path:
    """按当前账号命名空间解析资源根（各自一套技能）。"""
    try:
        from server import tenancy
        return tenancy.account_root()
    except ImportError:
        return BASE


def _config_dir() -> Path:
    return _tenant_root() / "config"


def _skills_path() -> Path:
    return _tenant_root() / "config" / "skills.yaml"


def _skills_dir() -> Path:
    return _tenant_root() / "skills"


# 向后兼容常量：指向仓库全局（旧代码/测试引用）。**新代码一律用上面的函数**。
CONFIG_DIR = BASE / "config"
SKILLS_PATH = CONFIG_DIR / "skills.yaml"
SKILLS_DIR = BASE / "skills"

# --------------------------------------------------------------------------
# 注入预算（2026-09-15 增设）
#
# 教训同源 _trim_web_results（术语第 14 条）：**任何回注模型的内容都必须有预算**。
# 从第三方仓库导入的 agent 定义动辄上万字符（实测 finance-financial-analyst.md 13.7KB），
# 装上 6 个就把 analyst 的 system prompt 推到 49,827 字符 —— 下游网关随即报
# `400 input length too long`（正是此前卡住真跑的那个错）。
# 因此这里**双闸限流**：单片段上限 + 总注入上限。
#   超预算**绝不静默**：截断在片段尾部留可见标记，被整条丢弃的技能	logger.warning 点名。
#   boss 立约：宁可留下失败痕迹，也不许假装没发生。
# 可用 SKILL_FRAGMENT_MAX / SKILL_CONTEXT_MAX 环境变量调整（0 表示不限）。
# --------------------------------------------------------------------------
MAX_FRAGMENT_CHARS = int(os.getenv("SKILL_FRAGMENT_MAX", "6000"))
MAX_CONTEXT_CHARS = int(os.getenv("SKILL_CONTEXT_MAX", "30000"))

_logger = logging.getLogger(__name__)

SKILL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# chat 角色自 M12-4 起由 build_skill_context("chat") 在用；手动向导须与 importer 的
# VALID_ROLES 保持一致，否则手动建 chat 技能会被 create_skill 400。
VALID_ROLES = ["chat", "researcher", "analyst", "writer"]


class SkillError(Exception):
    """技能层异常（复合写失败 / 校验未过等）。"""


def load_skills() -> List[Dict[str, Any]]:
    """读取技能条目（每次调用现读磁盘 → 热加载，零重启）。

    文件缺失返回 []（向后兼容：无 skills.yaml → 无注入，现有行为不变）。
    """
    p = _skills_path()
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [s for s in (data.get("skills") or []) if isinstance(s, dict) and s.get("id")]


def _recompute_status(spec: Dict[str, Any]) -> str:
    """enabled 且有 prompt_file 且文件存在 → active，否则 inactive。

    供 admin API 展示 / 校验使用（与 build_skill_context 过滤同源）。
    """
    if not spec.get("enabled"):
        return "inactive"
    rel = spec.get("prompt_file")
    if not rel:
        return "inactive"
    if not (_tenant_root() / rel).exists():
        return "inactive"
    return "active"


def _load_fragment(rel: str) -> str:
    """读 skills/<id>.md 片段（与 orchestrator._load_md 同源机制）。"""
    p = _tenant_root() / rel
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def build_skill_context(role: str, skills: Optional[List[dict]] = None) -> str:
    """返回应注入 role 的 system prompt 片段拼接串（可能为空）。

    过滤规则（顺序）：
      1. spec.enabled == True
      2. spec.installed == True（或 builtin）
      3. role ∈ spec.target_roles
      4. prompt_file 存在且非空（否则跳过该技能，防御不崩溃）
    每个命中技能：读片段，前后加分隔标记：
      '\\n【已启用技能：<name>】\\n' + fragment + '\\n【技能结束】\\n'
    多命中按 yaml 顺序拼接。

    skills=None 时内部调 load_skills()，保证调用点极简、热加载同频。
    """
    if skills is None:
        skills = load_skills()
    role_l = role.lower()  # 引擎 role 可能与 config/skills.yaml 的 target_roles 大小写不一致（注册表 key 可能大写），统一 lower 比较
    parts: List[str] = []
    total = 0
    for spec in skills:
        if not spec.get("enabled"):
            continue
        if not (spec.get("installed") or spec.get("builtin")):
            continue
        targets = [t.lower() for t in (spec.get("target_roles") or [])]
        if role_l not in targets:
            continue
        rel = spec.get("prompt_file")
        if not rel:
            continue
        frag = _load_fragment(rel)
        if not frag:
            continue  # 空技能防御：跳过不崩溃、不冒充
        name = spec.get("name") or spec.get("id") or "未命名技能"
        if MAX_FRAGMENT_CHARS and len(frag) > MAX_FRAGMENT_CHARS:
            frag = (frag[:MAX_FRAGMENT_CHARS]
                    + f"\n…[技能片段超预算被截断：原文 {len(frag)} 字符，"
                      f"已截到 {MAX_FRAGMENT_CHARS}（调 SKILL_FRAGMENT_MAX 可调）]")
        piece = f"\n【已启用技能：{name}】\n{frag}\n【技能结束】\n"
        if MAX_CONTEXT_CHARS and total + len(piece) > MAX_CONTEXT_CHARS:
            # 不静默丢弃：点名记录，让人能查为什么这个技能没生效
            _logger.warning(
                "技能 '%s' 因注入总预算（SKILL_CONTEXT_MAX=%s）已满被跳过 —— "
                "它**存在且已启用**，但没有进入 %s 的 system prompt。"
                "调高 SKILL_CONTEXT_MAX 或精简该角色的技能数量。",
                name, MAX_CONTEXT_CHARS, role_l)
            continue
        parts.append(piece)
        total += len(piece)
    return "\n".join(parts)
