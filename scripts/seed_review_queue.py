#!/usr/bin/env python3
"""宿主侧：把当前未推送 commit 写入审核中心待推送清单（供老板在 UI 审核）。

由 AI 代理在仓库根运行：python scripts/seed_review_queue.py [--account <id>]
直接读写 tenants/<account>/.audit/review_queue.json（schema 同 tools/review_queue）。

设计边界（DESIGN_review_center.md §2/§9）：容器内无宿主 .git / 无凭证，不能 push。
本脚本在宿主侧运行，把"待老板审核的变更集"写进清单；真正 push 由宿主 AI 代理
消费 approved 项后执行。脚本不依赖 web 上下文（_tenant_root 不可用），直接算路径。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ACCOUNT = "9910ebbc-112f-4ef2-ad7b-ed2582999382"

# 决策字段：重新 seed 时不得覆盖老板已做的决策
_DECISION_FIELDS = ("decision", "approved_by", "approved_at", "pushed_at", "pushed_ref", "reject_reason")
ITEM_ID = "local-unpushed"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_log(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "log", *args], capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return ""
    return out.stdout if out.returncode == 0 else ""


def git_unpushed() -> list[dict]:
    """取未推送 commit。优先 origin/master..HEAD；退化到 origin..HEAD；再退化到最近 8 条。"""
    out = _git_log(["origin/master..HEAD", "--oneline"])
    if not out.strip():
        out = _git_log(["origin..HEAD", "--oneline"])
    if not out.strip():
        out = _git_log(["--oneline", "-8"])
    commits: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        h, _, sub = line.partition(" ")
        commits.append({"hash": h, "subject": sub})
    return commits


def find_gate_docs() -> list[str]:
    docs: list[str] = []
    for p in Path(".").glob("*.md"):
        n = p.name
        if n.startswith("VERIFICATION") or n.startswith("REVIEW"):
            docs.append(n)
    return sorted(docs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    ap.add_argument("--base-ref", default="origin/master")
    args = ap.parse_args()

    queue_file = Path("tenants") / args.account / ".audit" / "review_queue.json"
    commits = git_unpushed()
    docs = find_gate_docs()

    if not commits:
        print("无未推送 commit，跳过写入。")
        return

    # git log 默认新→旧，末尾是最早的 commit（本次变更集起点）
    title = commits[-1]["subject"]
    entry = {
        "id": ITEM_ID,
        "kind": "pending_push",
        "title": title,
        "commits": [c["hash"] for c in commits],
        "base_ref": args.base_ref,
        "gate": {
            "verification": next((d for d in docs if d.startswith("VERIFICATION")), None),
            "review": next((d for d in docs if d.startswith("REVIEW")), None),
            "status": "PASS_WITH_NOTES",
        },
        "decision": "pending",
        "approved_by": None,
        "approved_at": None,
        "pushed_at": None,
        "pushed_ref": None,
        "reject_reason": None,
        "created_at": now_iso(),
        "commit_subjects": [c["subject"] for c in commits],
        "gate_docs": docs,
    }

    data = {"version": 1, "items": []}
    if queue_file.exists():
        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {"version": 1, "items": []}
    data.setdefault("items", [])
    existing = next((it for it in data["items"] if it.get("id") == ITEM_ID), None)
    if existing is None:
        data["items"].append(entry)
    else:
        for k, v in entry.items():
            if k in _DECISION_FIELDS:
                continue
            existing[k] = v

    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {queue_file}")
    print(f"  条目 id={ITEM_ID}, commits={len(commits)}, gate_docs={docs}")


if __name__ == "__main__":
    main()
