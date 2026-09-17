"""
账号体系（Phase 1 · DESIGN_account_hierarchy.md）

提供：注册 / 登录 / 自身信息 / 主账号管理子账号，以及给全站复用的鉴权依赖。

鉴权模型（§6.3「登录即租户」）：
- 资源端点：任何 active 账号凭自己的 JWT 操作**自己的** tenants/<id> 命名空间
- 账号管理端点：额外要求 role == main
- X-Admin-Token：**仅当 .env 显式配置 ADMIN_TOKEN 时**才生效（应急后门）；
  未配置时该通道不存在，堵死「共享静态口令 + 未配置即放行」的旧裸奔行为

诚实边界：注册/登录无限流与验证码（设计 §10 R6），公网暴露前必须补。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from server.database import get_async_session
from server.models import Account
from server import tenancy

router = APIRouter(prefix="/auth", tags=["auth"])

# ------------------------------------------------------------------ 配置

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()  # 空 = 该后门通道不存在


def _secret() -> str:
    """
    AUTH_SECRET：缺失必须 fail loud（不做随机兜底，否则容器重启即全登出且难排查）。
    """
    s = os.getenv("AUTH_SECRET", "").strip()
    if not s:
        raise HTTPException(
            status_code=500,
            detail="服务端未配置 AUTH_SECRET，无法签发/校验令牌；请在 .env 设置后重启容器。",
        )
    return s


def _ttl_hours() -> int:
    try:
        return max(1, int(os.getenv("AUTH_TOKEN_TTL_HOURS", "12")))
    except ValueError:
        return 12


ALGORITHM = "HS256"

# ------------------------------------------------------------------ 口令 / JWT


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(acc: Account) -> str:
    payload = {
        "sub": acc.id,
        "username": acc.username,
        "role": acc.role,
        "parent_id": acc.parent_id,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=_ttl_hours()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="令牌无效或已过期，请重新登录")


# ------------------------------------------------------------------ 依赖


async def get_current_account(
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None),
) -> Account:
    """
    解析当前账号并绑定租户上下文（ContextVar）。

    优先 Bearer JWT；其次 X-Admin-Token（仅在显式配置时有效）。
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif ADMIN_TOKEN and x_admin_token == ADMIN_TOKEN:
        # 应急后门：落到系统主账号（若存在）
        async with get_async_session() as session:
            r = await session.execute(
                select(Account).where(Account.is_system_main.is_(True)).limit(1)
            )
            acc = r.scalars().first()
        if not acc:
            raise HTTPException(status_code=403, detail="系统主账号尚未创建，无法使用该通道")
        tenancy.set_current_account(acc.id)
        return acc

    if not token:
        raise HTTPException(status_code=401, detail="未登录")

    payload = _decode(token)
    aid = payload.get("sub")
    if not aid:
        raise HTTPException(status_code=401, detail="令牌缺少主体")

    async with get_async_session() as session:
        acc = (await session.execute(select(Account).where(Account.id == aid))).scalars().first()

    if acc is None:
        raise HTTPException(status_code=401, detail="账号不存在（可能已被删除）")
    if acc.status != "active":
        raise HTTPException(status_code=403, detail=f"账号状态为 {acc.status}，不可使用")

    tenancy.set_current_account(acc.id)
    return acc


async def require_main(acc: Account = Depends(get_current_account)) -> Account:
    if acc.role != "main":
        raise HTTPException(status_code=403, detail="仅主账号可执行该操作")
    return acc


# ------------------------------------------------------------------ Schema


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    account_id: str


class AccountOut(BaseModel):
    id: str
    username: str
    role: str
    status: str
    parent_id: Optional[str] = None
    created_at: Optional[datetime] = None


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


def _out(acc: Account) -> AccountOut:
    return AccountOut(
        id=acc.id,
        username=acc.username,
        role=acc.role,
        status=acc.status,
        parent_id=acc.parent_id,
        created_at=acc.created_at,
    )


# ------------------------------------------------------------------ 端点


@router.post("/register", response_model=AccountOut)
async def register(body: RegisterIn):
    """开放注册。系统尚无账号 → 引导为主账号并直接激活；否则 pending 待主审批。"""
    async with get_async_session() as session:
        exists = (
            await session.execute(select(Account).where(Account.username == body.username))
        ).scalars().first()
        if exists:
            raise HTTPException(status_code=409, detail="用户名已被占用")

        total = (await session.execute(select(func.count()).select_from(Account))).scalar() or 0
        first = total == 0

        acc = Account(
            id=str(uuid.uuid4()),
            username=body.username,
            password_hash=hash_password(body.password),
            role="main" if first else "sub",
            parent_id=None,
            status="active" if first else "pending",
            is_system_main=bool(first),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(acc)
        await session.commit()
        await session.refresh(acc)

    # 资源目录：主账号从 legacy 全局布局迁移；子账号待 approve 时按主账号模板创建
    tenancy.ensure_account_layout(acc.id)
    if first:
        _bootstrap_from_legacy(acc.id)

    return _out(acc)


def _bootstrap_from_legacy(account_id: str) -> None:
    """
    把仓库根的全局资源（config/ agents/ gates/ templates/ skills/ experts/）
    复制进主账号命名空间。**复制而非移动**，原地保留以便人工核对后再清理。
    委托 tenancy.ensure_account_layout(seed_from_base=True) 统一实现。
    """
    # 先建齐所有资源目录（含 .secrets/.audit），再从 BASE 拷贝配置类内容（不拷密钥）
    tenancy.ensure_account_layout(account_id, seed_from_base=True)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn):
    async with get_async_session() as session:
        acc = (
            await session.execute(select(Account).where(Account.username == body.username))
        ).scalars().first()

    if acc is None or not verify_password(body.password, acc.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if acc.status == "pending":
        raise HTTPException(status_code=403, detail="账号待主账号审批，暂不可登录")
    if acc.status in ("disabled", "rejected"):
        raise HTTPException(status_code=403, detail=f"账号状态为 {acc.status}，不可登录")

    tenancy.set_current_account(acc.id)
    return TokenOut(
        access_token=create_access_token(acc),
        role=acc.role,
        username=acc.username,
        account_id=acc.id,
    )


@router.get("/me", response_model=AccountOut)
async def me(acc: Account = Depends(get_current_account)):
    return _out(acc)


@router.post("/change-password")
async def change_password(
    body: ResetPasswordIn,
    acc: Account = Depends(get_current_account),
):
    async with get_async_session() as session:
        a = (await session.execute(select(Account).where(Account.id == acc.id))).scalars().first()
        a.password_hash = hash_password(body.new_password)
        a.updated_at = datetime.utcnow()
        await session.commit()
    return {"ok": True}


@router.get("/accounts", response_model=list[AccountOut])
async def list_sub_accounts(main: Account = Depends(require_main)):
    """子账号列表。**不含主账号自身**（对称实现「子不见主」）。"""
    async with get_async_session() as session:
        rows = (
            await session.execute(
                select(Account).where(Account.parent_id == main.id).order_by(Account.created_at)
            )
        ).scalars().all()
    return [_out(a) for a in rows]


async def _approve_impl(main: Account, child_id: str, new_status: str, approve: bool):
    async with get_async_session() as session:
        child = (
            await session.execute(
                select(Account).where(Account.id == child_id, Account.parent_id == main.id)
            )
        ).scalars().first()
        if child is None:
            # 越权/不存在：一律 404，避免枚举探测
            raise HTTPException(status_code=404, detail="子账号不存在")
        child.status = new_status
        child.updated_at = datetime.utcnow()
        if approve:
            child.approved_at = datetime.utcnow()
            child.approved_by = main.id
        await session.commit()
        await session.refresh(child)
        child_id_out = child.id

    # 首次激活：按主账号模板初始化自己的资源
    if approve and new_status == "active":
        tenancy.ensure_account_layout(child_id_out, seed_from=main.id)
    return {"ok": True, "id": child_id_out, "status": new_status}


@router.post("/accounts/{child_id}/approve")
async def approve_account(child_id: str, main: Account = Depends(require_main)):
    return await _approve_impl(main, child_id, "active", approve=True)


@router.post("/accounts/{child_id}/reject")
async def reject_account(child_id: str, main: Account = Depends(require_main)):
    return await _approve_impl(main, child_id, "rejected", approve=False)


@router.post("/accounts/{child_id}/disable")
async def disable_account(child_id: str, main: Account = Depends(require_main)):
    return await _approve_impl(main, child_id, "disabled", approve=False)


@router.post("/accounts/{child_id}/enable")
async def enable_account(child_id: str, main: Account = Depends(require_main)):
    return await _approve_impl(main, child_id, "active", approve=False)


@router.post("/accounts/{child_id}/reset-password")
async def reset_password(
    child_id: str,
    body: ResetPasswordIn,
    main: Account = Depends(require_main),
):
    async with get_async_session() as session:
        child = (
            await session.execute(
                select(Account).where(Account.id == child_id, Account.parent_id == main.id)
            )
        ).scalars().first()
        if child is None:
            raise HTTPException(status_code=404, detail="子账号不存在")
        child.password_hash = hash_password(body.new_password)
        child.updated_at = datetime.utcnow()
        await session.commit()
    return {"ok": True}


@router.delete("/accounts/{child_id}")
async def delete_account(child_id: str, main: Account = Depends(require_main)):
    """删除子账号。**资源目录归档而非级联删除**（避免误删数据）。"""
    async with get_async_session() as session:
        child = (
            await session.execute(
                select(Account).where(Account.id == child_id, Account.parent_id == main.id)
            )
        ).scalars().first()
        if child is None:
            raise HTTPException(status_code=404, detail="子账号不存在")
        await session.delete(child)
        await session.commit()
    archived = tenancy.archive_account_layout(child_id)
    return {"ok": True, "archived": str(archived) if archived else None}
