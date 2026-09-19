"""一次性数据修复：把 parent_id 为 NULL 的孤立子账号重新挂到系统主账号。

根因：旧 register() 把子账号 parent_id 写死 None，导致 list_sub_accounts 用
parent_id == main.id 过滤时查不到，子账号既不在审批列表、approve/reject 也 404。

本脚本只读系统主账号（is_system_main），把所有 role=sub 且 parent_id IS NULL 的账号
（通常是 status=pending 的开放注册子账号）的 parent_id 设为系统主账号 id。
仅改 parent_id，不动密码/状态，可重复执行、幂等。
"""
import asyncio
from sqlalchemy import select, update

from server.database import get_async_session
from server.models import Account


async def main() -> None:
    async with get_async_session() as session:
        sys_main = (
            await session.execute(
                select(Account).where(Account.is_system_main.is_(True)).limit(1)
            )
        ).scalars().first()
        if sys_main is None:
            sys_main = (
                await session.execute(
                    select(Account).where(Account.role == "main").limit(1)
                )
            ).scalars().first()
        if sys_main is None:
            print("NO_MAIN: 找不到任何主账号，无法修复")
            return
        print(f"SYSTEM_MAIN id={sys_main.id} username={sys_main.username}")

        orphans = (
            await session.execute(
                select(Account).where(
                    Account.role == "sub", Account.parent_id.is_(None)
                )
            )
        ).scalars().all()
        if not orphans:
            print("NO_ORPHANS: 没有 parent_id 为空的子账号，无需修复")
            return

        ids = [o.id for o in orphans]
        await session.execute(
            update(Account)
            .where(Account.id.in_(ids))
            .values(parent_id=sys_main.id, updated_at=__import__("datetime").datetime.utcnow())
        )
        await session.commit()
        for o in orphans:
            print(f"REPARENTED id={o.id} username={o.username} status={o.status} -> parent={sys_main.id}")


if __name__ == "__main__":
    asyncio.run(main())
