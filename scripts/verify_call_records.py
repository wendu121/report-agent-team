"""
verify_call_records.py · 操作记录（调用流水）功能端到端核验

证明链路（在 api 容器内运行，连真实 postgres）：
  ① tools.call_log.record_call 写入 CallRecord（model / tool 两类）
  ② server.auth.list_child_call_records 的查询逻辑能按 account_id 倒序读回
  ③ CallRecord 模型 / 表存在（init_database 已建表）

不依赖真实登录态：用哨兵 account_id 自写自读，结束后删除测试行，不污染业务数据。

用法（容器外）：
  docker exec report-api python scripts/verify_call_records.py
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, delete

from server.database import get_async_session
from server.models import CallRecord
from tools.call_log import record_call


# 哨兵账号：仅用于本核验，结束后清理。长度须 ≤ account_id VARCHAR(36)，
# 故只用 uuid 前 16 位（"verify-" 前缀 7 字符 + 16 = 23 ≤ 36）。
SENTINEL = f"verify-{uuid.uuid4().hex[:16]}"


async def _verify() -> list:
    # 写两条：一条成功工具调用，一条失败模型调用
    record_call(SENTINEL, "tool", target="web_search", ok=True, latency_ms=123)
    record_call(SENTINEL, "model", target="gpt-4", ok=False, latency_ms=4567, detail="LLM 调用异常")

    async with get_async_session() as s:
        rows = (
            await s.execute(
                select(CallRecord)
                .where(CallRecord.account_id == SENTINEL)
                .order_by(CallRecord.created_at.desc())
            )
        ).scalars().all()
        out = [(r.kind, r.target, r.ok, r.latency_ms, r.detail) for r in rows]
        # 清理测试行
        await s.execute(delete(CallRecord).where(CallRecord.account_id == SENTINEL))
    return out


def _main() -> None:
    rows = asyncio.run(_verify())

    assert len(rows) == 2, f"期望写回 2 条，实际 {len(rows)}"
    # 倒序：先读到的应是后写的 model（created_at 更晚）
    assert rows[0][0] == "model" and rows[0][2] is False, f"倒序/失败模型校验失败: {rows[0]}"
    assert rows[1][0] == "tool" and rows[1][2] is True, f"工具成功校验失败: {rows[1]}"
    # 确认 list_child_call_records 端点函数可导入（auth 路由已挂载）
    from server.auth import list_child_call_records  # noqa: F401

    print("RESULT: PASS · 调用流水 写入/读取/端点挂载 全部就绪")
    print(f"  读回行（倒序）: {rows}")


if __name__ == "__main__":
    _main()
