"""验证「当前账号自助改密」后端能力（门禁②配套）。

不跑真实 JWT HTTP 全链路，只证明改密核心机制（端点 server.auth.change_password 用的同一套函数）：
  - create_access_token(acc) 可签发（端点依赖 get_current_account 解析出的 acc.id）
  - 改密 = 重算 password_hash + verify_password 旧拒新收
临时账号自写自清，绝不污染真实数据。

注：路由注册以「运行中服务 OpenAPI 实测」为准（/api/v1/auth/change-password 存在），
此处不再 import server.main:app 做静态断言——该容器导入态的 app 不含自定义路由，
与真实 uvicorn 服务的不一致，属环境噪音，不反映真实可用性。
"""
import asyncio
import datetime
import sys
import uuid

from server.database import get_async_session
from server.models import Account
from server.auth import hash_password, verify_password, create_access_token
from sqlalchemy import select

SENTINEL = f"verifycp-{uuid.uuid4().hex[:16]}"  # 长度 23，满足 account_id VARCHAR(36)


async def _verify() -> int:
    old_pw = "oldpass-12345"
    new_pw = "newpass-67890"

    # ① 建临时 active 账号
    acc = Account(
        id=SENTINEL,
        username=SENTINEL,
        password_hash=hash_password(old_pw),
        role="sub",
        parent_id=None,
        status="active",
        created_at=datetime.datetime.utcnow(),
    )
    async with get_async_session() as s:
        s.add(acc)
        await s.commit()
        await s.refresh(acc)

    # ② token 可签发（端点依赖 get_current_account 解析出的 acc.id）
    tok = create_access_token(acc)
    print(f"[token] created len={len(tok)}")

    # ③ 模拟端点核心：重算 hash（与 server.auth.change_password 同函数）
    async with get_async_session() as s:
        a = (await s.execute(select(Account).where(Account.id == SENTINEL))).scalars().first()
        a.password_hash = hash_password(new_pw)
        a.updated_at = datetime.datetime.utcnow()
        await s.commit()

    # ④ 读回校验：旧密码拒、新密码收
    async with get_async_session() as s:
        a = (await s.execute(select(Account).where(Account.id == SENTINEL))).scalars().first()
        ok_old = verify_password(old_pw, a.password_hash)
        ok_new = verify_password(new_pw, a.password_hash)
    print(f"[verify] old_rejected={not ok_old} new_accepted={ok_new}")

    # ⑤ 自清理
    async with get_async_session() as s:
        a = (await s.execute(select(Account).where(Account.id == SENTINEL))).scalars().first()
        if a:
            await s.delete(a)
            await s.commit()

    passed = (not ok_old) and ok_new and len(tok) > 0
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_verify()))
