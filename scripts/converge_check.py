# scripts/converge_check.py · 漂移检测（Converge）
#
# 目标：每 round 结束后自动比对 Agent 产出 vs user_task spec，
#      量化差距，生成 gap_tasks 驱动 rework_reason。
#
# Constitution 对齐：原则 IV（持续对齐）+ 原则 II（提交时三道闸的运行时补充）。
#
# 约束：
#   - 只做结构化比对（scope 关键词、id 存在性、章节存在性），不调 LLM 做语义判断
#   - 幂等：同一 round 多次运行不产生重复 gap_tasks（按 id 去重）
#   - 与 Gate 共存：不替换 Gate 判定，是追加层（Gate 仍决定 rework/escalate）
#   - 追加式：tasks.md 只追加，不覆盖（由调用方决定落盘，本模块只生成结构化 gap_tasks）

from __future__ import annotations

import re
from typing import Any, Optional


# ----------------------------------------------------------------------------
# Gap Task Schema（对齐 CONVERGE_DESIGN.md §4）
# ----------------------------------------------------------------------------
def _make_gap(
    gate: str,
    gtype: str,
    description: str,
    spec_ref: str,
    suggested_action: str,
    round_num: int,
) -> dict[str, Any]:
    """构造一条 gap_task（id 由调用方注入去重逻辑）。"""
    return {
        "type": gtype,
        "gate": gate,
        "description": description,
        "spec_ref": spec_ref,
        "suggested_action": suggested_action,
        "detected_at": round_num,
    }


# ----------------------------------------------------------------------------
# GateA 检查（coverage 检查）
# ----------------------------------------------------------------------------
def converge_gateA(
    user_task: dict,
    retrieval_records: list,
    *,
    round_num: int = 1,
) -> list[dict]:
    """GateA：检查检索记录是否覆盖 user_task.scope 全部子问题。

    实现（CONVERGE_DESIGN.md §3 GateA）：
    对每个 scope 子问题，检查是否有检索记录的 title/snippet/url 含该子问题关键词（大小写忽略）。
    """
    gaps: list[dict] = []
    scope = user_task.get("scope") or []
    if not scope:
        return gaps

    for i, sub in enumerate(scope):
        covered = any(
            sub.lower() in (
                (r.get("title") or "") + (r.get("snippet") or "") + (r.get("url") or "")
            ).lower()
            for r in retrieval_records
        )
        if not covered:
            gaps.append({
                **_make_gap(
                    gate="GateA",
                    gtype="coverage",
                    description=f"子问题 '{sub}' 无检索记录覆盖",
                    spec_ref=f"user_task.scope[{i}]",
                    suggested_action=f"Researcher 补充检索'{sub}'",
                    round_num=round_num,
                ),
                "id": f"gap-A-{i+1}-{sub[:20].replace(' ', '-')}",
            })
    return gaps


# ----------------------------------------------------------------------------
# GateB 检查（reference 检查）
# ----------------------------------------------------------------------------
def converge_gateB(
    retrieval_records: list,
    analysis_conclusions: list,
    *,
    round_num: int = 1,
) -> list[dict]:
    """GateB：检查分析结论是否每条都有有效 source_ids。

    实现（CONVERGE_DESIGN.md §3 GateB）：
    - source_ids 为空 → broken_reference
    - source_ids 中的 id 不在 retrieval_records 集合中 → broken_reference
    """
    gaps: list[dict] = []
    rec_ids = {r.get("id") for r in retrieval_records if r.get("id")}

    for c in analysis_conclusions:
        cid = c.get("id", "unknown")
        sids = c.get("source_ids") or []

        if not sids:
            gaps.append({
                **_make_gap(
                    gate="GateB",
                    gtype="broken_reference",
                    description=f"结论 '{cid}' 无 source_ids",
                    spec_ref="analysis_conclusions[].source_ids (非空约束)",
                    suggested_action=f"Analyst 为结论 '{cid}' 补充引用来源 ID",
                    round_num=round_num,
                ),
                "id": f"gap-B-{cid}-no-source",
            })
        else:
            missing = [s for s in sids if s not in rec_ids]
            if missing:
                gaps.append({
                    **_make_gap(
                        gate="GateB",
                        gtype="broken_reference",
                        description=f"结论 '{cid}' 引用不存在的 source_ids: {missing}",
                        spec_ref="analysis_conclusions[].source_ids 必须存在于 retrieval_records",
                        suggested_action=f"Analyst 修正引用 ID: {missing} 不存在于检索记录",
                        round_num=round_num,
                    ),
                    "id": f"gap-B-{cid}-missing-{','.join(sorted(missing))}",
                })
    return gaps


# ----------------------------------------------------------------------------
# GateC 检查（completeness 检查）
# ----------------------------------------------------------------------------
def converge_gateC(
    user_task: dict,
    draft_segments: list,
    report_markdown: str,
    retrieval_records: list,
    *,
    round_num: int = 1,
) -> list[dict]:
    """GateC：检查终稿是否覆盖 scope + 引用章节完整性。

    实现（CONVERGE_DESIGN.md §3 GateC）：
    1. 对每个 scope 子问题，检查 draft_segments.content 是否有覆盖
    2. 检查 report_markdown 是否含『引用/来源』章节
    3. 检查引用章节是否覆盖全部检索记录 id 与其 url
    """
    gaps: list[dict] = []
    scope = user_task.get("scope") or []
    rec_ids = {r.get("id") for r in retrieval_records if r.get("id")}

    # 1. Scope 覆盖检查
    for i, sub in enumerate(scope):
        covered = any(
            sub.lower() in (s.get("content") or "").lower()
            for s in draft_segments
        )
        if not covered:
            gaps.append({
                **_make_gap(
                    gate="GateC",
                    gtype="coverage",
                    description=f"终稿未覆盖子问题 '{sub}'",
                    spec_ref=f"user_task.scope[{i}]",
                    suggested_action=f"Writer 补充'{sub}'相关章节",
                    round_num=round_num,
                ),
                "id": f"gap-C-{i+1}-{sub[:20].replace(' ', '-')}",
            })

    # 2. 引用章节存在性检查
    has_heading = bool(re.search(r"^\s*##\s*(引用|参考|来源)", report_markdown, re.MULTILINE))
    if not has_heading:
        gaps.append({
            **_make_gap(
                gate="GateC",
                gtype="missing_section",
                description="终稿缺『引用 / 来源』章节",
                spec_ref="output_format_spec (引用章节要求)",
                suggested_action="Writer 补充引用章节",
                round_num=round_num,
            ),
            "id": f"gap-C-citation-missing-{round_num}",
        })
    else:
        # 3. 引用章节完整性：id 与 url 都必须出现在 report_markdown
        missing_ids: list[str] = []
        for r in retrieval_records:
            rid = r.get("id")
            if not rid:
                continue
            in_md = rid in report_markdown
            url = r.get("url") or ""
            url_in_md = bool(url and url in report_markdown)
            if not in_md or not url_in_md:
                missing_ids.append(rid)
        if missing_ids:
            gaps.append({
                **_make_gap(
                    gate="GateC",
                    gtype="incomplete_citation",
                    description=f"引用章节未覆盖全部检索记录: {missing_ids}",
                    spec_ref="output_format_spec (引用章节覆盖全部)",
                    suggested_action=f"Writer 补充引用: {missing_ids}",
                    round_num=round_num,
                ),
                "id": f"gap-C-citation-incomplete-{round_num}",
            })

    return gaps


# ----------------------------------------------------------------------------
# 统一入口
# ----------------------------------------------------------------------------
def check(state: dict, *, round_num: int = 1) -> list[dict]:
    """对当前 state 做全量 converge 检查，返回 gap_tasks 列表（已按 id 去重）。

    state 结构对齐 ReportState：
      - user_task: dict
      - retrieval_records: list
      - analysis_conclusions: list
      - draft_segments: list
      - report_markdown: str
      - routing_state: dict（含 round）
    """
    user_task = state.get("user_task", {}) or {}
    recs = state.get("retrieval_records", []) or []
    cons = state.get("analysis_conclusions", []) or []
    segs = state.get("draft_segments", []) or []
    md = state.get("report_markdown", "") or ""

    current_round = state.get("routing_state", {}).get("round", round_num)

    all_gaps: list[dict] = []
    all_gaps.extend(converge_gateA(user_task, recs, round_num=current_round))
    all_gaps.extend(converge_gateB(recs, cons, round_num=current_round))
    all_gaps.extend(converge_gateC(user_task, segs, md, recs, round_num=current_round))

    # 按 id 去重
    seen: set[str] = set()
    unique: list[dict] = []
    for g in all_gaps:
        gid = g.get("id")
        if gid and gid not in seen:
            seen.add(gid)
            unique.append(g)
    return unique


def merge_into_state(state: dict, gaps: list[dict]) -> dict:
    """将 gap_tasks 合并进 state（追加，不覆盖既有）；返回新 state dict。

    用于在 Gate 节点后或 Router 节点中调用。
    """
    new_state = dict(state)
    existing = list(new_state.get("gap_tasks", []) or [])
    # 合并：新增的加进去，已有的 id 不重复
    existing_ids = {g.get("id") for g in existing if g.get("id")}
    for g in gaps:
        if not g.get("id") or g["id"] not in existing_ids:
            existing.append(g)
    new_state["gap_tasks"] = existing
    return new_state


def gaps_to_rework_reason(gaps: list[dict], current_reason: Optional[str] = None) -> str:
    """把 gap_tasks 列表转成 human-readable rework_reason 文本。

    用于 Router 把 gap_tasks 追加到 rework_reason 透传给 Agent。
    """
    if not gaps:
        return current_reason or ""
    lines = []
    for g in gaps:
        lines.append(f"[{g.get('type','?')}] {g.get('description','')}")
    text = "\n  · ".join(lines)
    if current_reason:
        return f"{current_reason}\n[Converge 补充缺口]\n  · {text}"
    return f"[Converge 漂移检测]\n  · {text}"