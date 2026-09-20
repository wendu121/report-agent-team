"""统一审核中心 · 待推送变更集清单存储（per 租户）。

与 skill_importer 共享 `.audit` 目录与租户隔离逻辑；本模块只负责
pending_push 类审核项（已过三道闸、待老板在 UI 点「批准推送」后由宿主 AI 代理执行 git push）。

schema 见 DESIGN_review_center.md §5。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.skill_importer import _tenant_root


class ReviewQueueError(Exception):
    """清单项不存在或状态非法。"""


def _queue_path() -> Path:
    return _tenant_root() / ".audit" / "review_queue.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> Dict[str, Any]:
    p = _queue_path()
    if not p.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "items": []}
    if not isinstance(data, dict) or "items" not in data:
        return {"version": 1, "items": []}
    return data


def _save(data: Dict[str, Any]) -> None:
    p = _queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def list_items() -> List[Dict[str, Any]]:
    return _load().get("items", [])


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    for it in list_items():
        if it.get("id") == item_id:
            return it
    return None


# 决策字段：upsert / 重新写入时不得覆盖老板已做的决策
_DECISION_FIELDS = ("decision", "approved_by", "approved_at", "pushed_at", "pushed_ref", "reject_reason")


def upsert_pending_push(item: Dict[str, Any]) -> Dict[str, Any]:
    """插入或更新一条 pending_push 条目（按 id）。

    若已存在同 id 条目，保留其决策字段（decision/approved_*/pushed_*/reject_reason），
    其余字段用新值覆盖——避免覆盖老板已做的批准/打回。
    """
    if not item.get("id"):
        raise ReviewQueueError("pending_push 条目缺少 id")
    data = _load()
    items = data.setdefault("items", [])
    existing = next((it for it in items if it.get("id") == item["id"]), None)
    if existing is None:
        item = dict(item)
        item["kind"] = "pending_push"
        item["decision"] = "pending"
        item["created_at"] = item.get("created_at") or _now_iso()
        # 清掉可能误带的决策字段（新提案默认 pending）
        for f in _DECISION_FIELDS:
            item.pop(f, None)
        items.append(item)
        _save(data)
        return item
    # 更新非决策字段
    for k, v in item.items():
        if k in _DECISION_FIELDS:
            continue
        existing[k] = v
    _save(data)
    return existing


def approve(item_id: str, approver: str) -> Dict[str, Any]:
    """记录老板批准（仅写决策；真正 push 由宿主 AI 代理消费后执行）。"""
    data = _load()
    it = next((x for x in data.get("items", []) if x.get("id") == item_id), None)
    if it is None:
        raise ReviewQueueError(f"清单项不存在：{item_id}")
    it["decision"] = "approved"
    it["approved_by"] = approver
    it["approved_at"] = _now_iso()
    _save(data)
    return it


def reject(item_id: str, reason: str) -> Dict[str, Any]:
    """打回待推送变更集（记录原因；不推送，可回流代理重做）。"""
    data = _load()
    it = next((x for x in data.get("items", []) if x.get("id") == item_id), None)
    if it is None:
        raise ReviewQueueError(f"清单项不存在：{item_id}")
    it["decision"] = "rejected"
    it["reject_reason"] = reason
    _save(data)
    return it


def mark_pushed(item_id: str, pushed_ref: str) -> Dict[str, Any]:
    """宿主 AI 代理 push 完成后回写（闭环，UI 显示已推送）。"""
    data = _load()
    it = next((x for x in data.get("items", []) if x.get("id") == item_id), None)
    if it is None:
        raise ReviewQueueError(f"清单项不存在：{item_id}")
    it["decision"] = "pushered"
    it["pushed_ref"] = pushed_ref
    it["pushed_at"] = _now_iso()
    _save(data)
    return it
