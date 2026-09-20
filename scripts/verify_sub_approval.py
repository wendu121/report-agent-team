"""验证子账号审批链路修复（对应 register() 现在会把 parent_id 挂到系统主账号）。

1. 列出当前主账号下的子账号（与 账号管理 页 GET /auth/accounts 同查询：parent_id==main.id）
2. 临时注册一个子账号，断言其 parent_id == 系统主账号 id（证明代码修复生效、未来注册不再孤立）
3. 重新列出，断言该临时子账号出现在列表中（证明会出现在 账号管理 审批列表）
4. 清理：删除临时子账号（幂等，可重复跑）
"""
import asyncio
import uuid

from sqlalchemy import select

from server.auth import RegisterIn, register
from server.database import get_async_session
from server.models import Account


async def _system_main():
    async with get_async_session() as s:
        m = (await s.execute(select(Account).where(Account.is_system_main.is_(True)).limit(1))).scalars().first()
        if m is None:
            m = (await s.execute(select(Account).where(Account.role == "main").limit(1))).scalars().first()
    return m


async def main() -> None:
    main = await _system_main()
    if main is None:
        print("FAIL: 无主账号")
        return
    print(f"SYSTEM_MAIN id={main.id} username={main.username}")

    async with get_async_session() as s:
        before = (await s.execute(
            select(Account).where(Account.parent_id == main.id)
        )).scalars().all()
    print(f"当前主账号下子账号数（账号管理可见）: {len(before)}")
    for a in before:
        print(f"  - id={a.id} username={a.username} status={a.status}")

    # 临时注册一个子账号
    tmp = f"_verify_{uuid.uuid4().hex[:8]}"
    out = await register(RegisterIn(username=tmp, password="verify12345678"))
    print(f"注册临时子账号 {tmp} -> parent_id={out.parent_id} status={out.status}")
    if out.parent_id != main.id:
        print(f"FAIL: 临时子账号 parent_id={out.parent_id} 不等于主账号 {main.id}")
    else:
        print("PASS: 新注册子账号已自动归属主账号（代码修复生效）")

    async with get_async_session() as s:
        after = (await s.execute(
            select(Account).where(Account.parent_id == main.id)
        )).scalars().all()
    found = any(a.username == tmp for a in after)
    print(f"重新列出主账号下子账号数: {len(after)}  临时子账号是否可见: {found}")
    if not found:
        print("FAIL: 临时子账号未出现在主账号列表")
    else:
        print("PASS: 新子账号会出现在 账号管理 审批列表")

    # 清理
    async with get_async_session() as s:
        orphan = (await s.execute(select(Account).where(Account.username == tmp))).scalars().first()
        if orphan:
            await s.delete(orphan)
            await s.commit()
            print(f"已清理临时子账号 {tmp}")


if __name__ == "__main__":
    asyncio.run(main())
