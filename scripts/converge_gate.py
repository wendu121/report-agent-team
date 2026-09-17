"""converge_gate.py · Converge 漂移检测 Gate（edict-gate 集成）

定位：作为 edict-gate 的「第四闸」（在自审关之后、独立审议关之前执行），
      对 report-agent-team 类项目追加 Converge 漂移检测。

设计原则：
- 与 edict-gate gate.py 解耦：本脚本只做 converge 检查，不做门禁判定
- gate.py 调用本脚本后，读取其输出决定是追加到 REVIEW.md 还是封驳
- 对非 report-agent-team 项目可安全跳过（无 DESIGN.md 则静默退出）

输入约定：
  环境变量：
    CONVERGE_STATE_PATH  - state dict 的 JSON 文件路径（由 gate.py 或调用方提供）
    CONVERGE_ROUND       - 当前 round 编号（默认 1）
  若未提供 CONVERGE_STATE_PATH，则从项目内查找 .engine_state/state.json

输出约定：
  stdout: JSON 行，包含 {status, gaps_count, gaps, message}
  exit code:
    0 = 无 gap 或 gap 已关闭（PASS）
    1 = 有未关闭的 blocking gap（封驳）
    2 = 无法运行（非目标项目 / 状态缺失）

gap 严重度分类：
  - blocking：coverage / broken_reference 类型 → 必须修复
  - warning：missing_section / incomplete_citation 类型 → 建议修复，不强制
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径配置
BASE = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE / "scripts"
STATE_PATH = BASE / ".engine_state" / "state.json"
TASKS_PATH = BASE / "tasks.md"
REVIEW_PATH = BASE / "REVIEW.md"

# gap 严重度映射
BLOCKING_TYPES = {"coverage", "broken_reference"}
WARNING_TYPES = {"missing_section", "incomplete_citation"}


def load_state() -> Optional[Dict[str, Any]]:
    """加载当前 engine state。"""
    state_path = Path(os.environ.get("CONVERGE_STATE_PATH", str(STATE_PATH)))
    if not state_path.exists():
        return None
    try:
        with state_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_closed_gaps() -> set:
    """读取已关闭的 gap ids。"""
    if not TASKS_PATH.exists():
        return set()
    try:
        content = TASKS_PATH.read_text(encoding="utf-8")
        # 匹配 [x] 标记的行
        import re
        closed = set(re.findall(r"\[x\]\s+\*\*(gap-[\w-]+)\*\*", content))
        return closed
    except Exception:
        return set()


def run_converge_check(state: Dict[str, Any], round_num: int) -> List[Dict[str, Any]]:
    """运行 converge 检查，返回 gap 列表。"""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from converge_check import check as converge_check
        return converge_check(state, round_num=round_num)
    except ImportError:
        print(json.dumps({
            "status": "error",
            "message": "无法导入 converge_check 模块",
            "gaps_count": 0,
            "gaps": []
        }, ensure_ascii=False))
        sys.exit(2)
    finally:
        sys.path.pop(0)


def classify_gaps(gaps: List[Dict[str, Any]], closed: set) -> Dict[str, Any]:
    """对 gaps 分类：blocking vs warning，过滤已关闭的。"""
    blocking = []
    warnings = []
    for g in gaps:
        gid = g.get("id", "")
        if gid in closed:
            continue
        gtype = g.get("type", "")
        if gtype in BLOCKING_TYPES:
            blocking.append(g)
        elif gtype in WARNING_TYPES:
            warnings.append(g)
    return {"blocking": blocking, "warnings": warnings}


def append_to_tasks(gaps: List[Dict[str, Any]], round_num: int) -> None:
    """将新 gap 追加到 tasks.md。"""
    if not gaps:
        return
    timestamp = time.strftime("%Y-%m-%d")
    lines = [
        f"\n## [{timestamp}] Converge Gap - Round {round_num}\n",
        "",
    ]
    for g in gaps:
        gid = g.get("id", "unknown")
        gtype = g.get("type", "?")
        desc = g.get("description", "")
        spec_ref = g.get("spec_ref", "")
        action = g.get("suggested_action", "")
        lines.append(f"- [ ] **{gid}** ({gtype}): {desc}")
        if spec_ref:
            lines.append(f"  - spec_ref: {spec_ref}")
        if action:
            lines.append(f"  - suggested: {action}")
        lines.append("")

    content = "\n".join(lines)
    if TASKS_PATH.exists():
        with TASKS_PATH.open("a", encoding="utf-8") as f:
            f.write(content)
    else:
        with TASKS_PATH.open("w", encoding="utf-8") as f:
            f.write("# Tasks\n\n" + content)


def update_review_with_converge(blocking: List[Dict], warnings: List[Dict]) -> None:
    """在 REVIEW.md 末尾追加 Converge 检查结论。"""
    if not blocking and not warnings:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "",
        "---",
        f"\n## Converge 漂移检测（{timestamp}）",
        "",
        f"- Blocking gaps: {len(blocking)}",
        f"- Warning gaps: {len(warnings)}",
        "",
    ]
    if blocking:
        lines.append("### Blocking Gaps\n")
        for g in blocking:
            lines.append(f"- **{g.get('id')}** ({g.get('type')}): {g.get('description')}")
        lines.append("")
    if warnings:
        lines.append("### Warning Gaps\n")
        for g in warnings:
            lines.append(f"- **{g.get('id')}** ({g.get('type')}): {g.get('description')}")
        lines.append("")
    lines.append("> **注意**：Blocking gaps 必须在下次交付前修复，否则门禁封驳。")
    lines.append("")

    content = "\n".join(lines)
    if REVIEW_PATH.exists():
        with REVIEW_PATH.open("a", encoding="utf-8") as f:
            f.write(content)
    else:
        with REVIEW_PATH.open("w", encoding="utf-8") as f:
            f.write("# 独立审议 (REVIEW)\n\n" + content)


def main() -> int:
    """主入口。"""
    # 检查是否是目标项目
    if not (BASE / "DESIGN.md").exists():
        print(json.dumps({
            "status": "skipped",
            "message": "非 report-agent-team 项目，跳过 converge 检查",
            "gaps_count": 0,
            "gaps": []
        }, ensure_ascii=False))
        return 2

    # 加载 state
    state = load_state()
    if state is None:
        print(json.dumps({
            "status": "skipped",
            "message": "未找到 engine state，跳过 converge 检查",
            "gaps_count": 0,
            "gaps": []
        }, ensure_ascii=False))
        return 2

    # 运行检查
    round_num = int(os.environ.get("CONVERGE_ROUND", "1"))
    gaps = run_converge_check(state, round_num)

    # 加载已关闭的 gaps
    closed = load_closed_gaps()

    # 分类
    classified = classify_gaps(gaps, closed)
    blocking = classified["blocking"]
    warnings = classified["warnings"]

    # 追加到 tasks.md
    new_gaps = [g for g in gaps if g.get("id") not in closed]
    append_to_tasks(new_gaps, round_num)

    # 更新 REVIEW.md
    update_review_with_converge(blocking, warnings)

    # 输出结果
    result = {
        "status": "PASS" if not blocking else "FAIL",
        "gaps_count": len(gaps),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "closed_count": len([g for g in gaps if g.get("id") in closed]),
        "gaps": gaps,
        "message": "无 blocking gaps" if not blocking else f"发现 {len(blocking)} 个 blocking gap，需修复"
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
