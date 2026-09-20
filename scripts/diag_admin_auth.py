"""诊断：settings 控制台页面调用的 admin 端点，登录态(BBearer)是否真能被后端放行。
同时测「疑似非 admin 路径」(不带 /admin 前缀) 的存在性与鉴权要求，定位 fetch 缺 Bearer 的真实影响面。
"""
import asyncio
import json
import os
import urllib.error
import urllib.request

os.environ.setdefault("PYTHONPATH", "/app")

from server.auth import create_access_token, get_async_session
from server.models import Account
from sqlalchemy import select

BASE = "http://localhost:8000/api/v1"


async def get_first_active():
    async with get_async_session() as s:
        return (await s.execute(select(Account).where(Account.status == "active").limit(1))).scalars().first()


def hit(path, token=None, method="GET"):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa
        return f"EXC {type(e).__name__}"


async def main():
    acc = await get_first_active()
    if not acc:
        print("NO_ACTIVE_ACCOUNT")
        return
    token = create_access_token(acc)

    # 控制台页面真实调用的端点（含 /admin/* 与疑似非 admin 路径）
    endpoints = [
        "/admin/models", "/admin/agents", "/admin/providers", "/admin/mcp-servers",
        "/admin/gates", "/admin/templates", "/admin/agents-library", "/admin/plugins",
        "/admin/skills", "/admin/channels", "/admin/reflections", "/admin/lessons",
        "/admin/experts", "/admin/status",
        # 疑似非 admin（fetch 里写成 ${API}/channels 等）
        "/channels", "/plugins", "/skills", "/agents-library", "/models", "/templates",
    ]

    print(f"account={acc.username} role={acc.role}")
    print(f"{'PATH':32} | {'Bearer':>7} | {'NoAuth':>7}")
    print("-" * 56)
    for p in endpoints:
        s_auth = hit(p, token)
        s_no = hit(p, None)
        print(f"{p:32} | {str(s_auth):>7} | {str(s_no):>7}")


if __name__ == "__main__":
    asyncio.run(main())
