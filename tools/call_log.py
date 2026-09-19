"""
调用流水（CallRecord）写入 · 老板裁定 3

用途（见 server/models.py CallRecord 注释）：
  ① 自己只看自己的调用记录
  ② Phase 2 主账号点开某个子账号「查阅」

设计要点：
- 引擎主循环 _run_fc_loop 是**同步函数**，且引擎子进程无 FastAPI 请求上下文，
  因此这里用 server.database.get_sync_session（独立短连接，提交即关）。
- **最佳努力**：任何异常都吞掉，绝不阻断报告生成主链路。
- 无账号上下文（account_id 为空）时直接 no-op（CLI / 未登录调试场景）。
- 写入只用 account_id 归属，不携带任何敏感正文（detail 仅存错误摘要）。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from server.database import get_sync_session
from server.models import CallRecord

# 错误摘要截断上限，避免把长堆栈塞进调用流水表
_DETAIL_LIMIT = 500


def record_call(
    account_id: Optional[str],
    kind: str,
    target: Optional[str] = None,
    ok: bool = True,
    latency_ms: Optional[int] = None,
    detail: Optional[str] = None,
    cost_hint: Optional[float] = None,
) -> None:
    """最佳努力写入一条调用流水。失败静默，永不抛出。"""
    if not account_id:
        return
    try:
        detail_trunc = (detail or "")[:_DETAIL_LIMIT] if detail else None
        with get_sync_session() as session:
            session.add(
                CallRecord(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    kind=kind,
                    target=target,
                    ok=ok,
                    latency_ms=latency_ms,
                    cost_hint=cost_hint,
                    detail=detail_trunc,
                    created_at=datetime.utcnow(),
                )
            )
        # get_sync_session 上下文管理器负责 commit / close
    except Exception:  # noqa: BLE001 - 审计写入失败绝不阻断主链路
        pass
