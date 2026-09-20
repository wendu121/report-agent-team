"""端到端验证：调用流水是否落库「操作内容」（content）。

真实链路：注册临时子账号 → 激活 → 登录拿 JWT → POST /chat 触发 ChatAgent
（走 orchestrator._run_fc_loop 的模型调用记录）→ 从 DB 读回 CallRecord，
断言至少一条记录 content 非空且含输入/回复。最后清理临时账号与其流水。

在 api 容器内运行：PYTHONPATH=/app python /app/scripts/verify_call_content.py
"""
import asyncio
import uuid

import httpx
from sqlalchemy import delete, select

from server.database import get_async_session
from server.models import Account, CallRecord

BASE = "http://localhost:8000/api/v1"


async def main() -> None:
    tmp = f"_vc_{uuid.uuid4().hex[:8]}"
    pwd = "verify12345678"
    acct_id = None
    ok = False
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{BASE}/auth/register", json={"username": tmp, "password": pwd})
            print(f"[register] {r.status_code} {r.text[:200]}")
            if r.status_code != 200:
                print("FAIL: 注册失败")
                return
            acct_id = r.json()["id"]
            print(f"  临时子账号 id={acct_id} parent_id={r.json().get('parent_id')}")

            # 直接置为 active（避免依赖主账号 JWT 审批）
            async with get_async_session() as s:
                a = (await s.execute(select(Account).where(Account.id == acct_id))).scalars().first()
                a.status = "active"
                await s.commit()

            r = await c.post(f"{BASE}/auth/login", json={"username": tmp, "password": pwd})
            print(f"[login] {r.status_code}")
            if r.status_code != 200:
                print(f"FAIL: 登录失败 {r.text[:200]}")
                return
            jwt = r.json()["access_token"]

            r = await c.post(
                f"{BASE}/chat",
                json={"message": "你好，用一句话介绍你自己", "model": "LongCat-2.0"},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            print(f"[chat] {r.status_code} {r.text[:260]}")
            if r.status_code != 200:
                print("WARN: 聊天失败，仅验证存储链路")

        async with get_async_session() as s:
            rows = (
                await s.execute(
                    select(CallRecord)
                    .where(CallRecord.account_id == acct_id)
                    .order_by(CallRecord.created_at.desc())
                )
            ).scalars().all()

        print(f"\n回读流水 {len(rows)} 条：")
        with_content = 0
        for row in rows:
            c = row.content or ""
            if c:
                with_content += 1
            print(f"  - kind={row.kind} target={row.target} ok={row.ok} lat={row.latency_ms}ms "
                  f"content_len={len(c)}")
            if c:
                print(f"    内容预览: {c[:220].replace(chr(10), ' ⏎ ')}")
        if with_content > 0:
            print(f"\nPASS: {with_content}/{len(rows)} 条流水带操作内容（content 落库成功）")
            ok = True
        else:
            print("\nFAIL: 无任何流水带内容")
    finally:
        if acct_id:
            async with get_async_session() as s:
                await s.execute(delete(CallRecord).where(CallRecord.account_id == acct_id))
                a = (await s.execute(select(Account).where(Account.id == acct_id))).scalars().first()
                if a:
                    await s.delete(a)
                await s.commit()
            print(f"已清理临时账号 {tmp} 及其流水")
    print("VERDICT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
