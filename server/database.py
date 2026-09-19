"""
数据库会话管理
M6-4 产出：PostgreSQL 连接与会话管理
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator, Optional
import os

from .models import Base

# ============================================================================
# 数据库配置
# ============================================================================

# 同步引擎（用于迁移/初始化）
SYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/report_agent_team"
)
sync_engine = create_engine(SYNC_DATABASE_URL, echo=False)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

# 异步引擎（用于运行时）
ASYNC_DATABASE_URL = os.getenv(
    "ASYNC_DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/report_agent_team"
)
async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ============================================================================
# 同步会话管理（用于迁移/初始化）
# ============================================================================


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """获取同步数据库会话（上下文管理器）"""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ============================================================================
# 异步会话管理（用于运行时）
# ============================================================================


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库会话（上下文管理器）"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_async_session_dependency() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：获取异步会话"""
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================================
# 数据库初始化
# ============================================================================


def init_database():
    """初始化数据库（创建所有表，幂等：持久卷下重复启动忽略 already exists）"""
    from sqlalchemy.exc import ProgrammingError
    from sqlalchemy import text
    try:
        Base.metadata.create_all(bind=sync_engine, checkfirst=True)
    except ProgrammingError as e:
        # DDL 隐式提交：部分表/索引已存在时，结构一致则安全跳过（避免重启卷致容器退出）
        if "already exists" in str(e):
            print("⚠️ 部分表/索引已存在，幂等跳过（init_database）")
        else:
            raise
    # 轻量迁移：为已存在的旧表补新增列（create_all 不会给已有表加列）
    try:
        with sync_engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE call_records ADD COLUMN IF NOT EXISTS content TEXT"
            ))
    except Exception as e:  # noqa: BLE001 - 迁移失败不阻断启动（旧表结构可能本就不支持）
        print(f"⚠️ call_records.content 迁移跳过：{e}")
    print("✅ 数据库表初始化完成")


def drop_database():
    """删除所有表（危险操作，仅用于测试）"""
    Base.metadata.drop_all(bind=sync_engine)
    print("⚠️ 数据库表已删除")


# ============================================================================
# 数据库连接测试
# ============================================================================


async def test_database_connection() -> bool:
    """测试数据库连接"""
    try:
        async with async_engine.connect() as conn:
            await conn.execute("SELECT 1")
        print("✅ 数据库连接正常")
        return True
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return False


# ============================================================================
# Redis 客户端（可选）
# ============================================================================

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    print("⚠️ redis 未安装，跳过 Redis 支持")


class RedisClient:
    """Redis 客户端（用于实时状态缓存）"""

    def __init__(self, url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")):
        if not HAS_REDIS:
            raise RuntimeError("redis 未安装")
        self.url = url
        self.client: Optional[aioredis.Redis] = None

    async def connect(self):
        """连接 Redis"""
        self.client = await aioredis.from_url(self.url, encoding="utf-8", decode_responses=True)
        print("✅ Redis 连接成功")

    async def close(self):
        """关闭 Redis 连接"""
        if self.client:
            await self.client.close()
            print("🔌 Redis 连接关闭")

    async def set_task_status(self, task_id: str, status: str, ttl: int = 3600):
        """设置任务状态（缓存）"""
        if self.client:
            await self.client.setex(f"task:{task_id}:status", ttl, status)

    async def get_task_status(self, task_id: str) -> Optional[str]:
        """获取任务状态（缓存）"""
        if self.client:
            return await self.client.get(f"task:{task_id}:status")
        return None

    async def delete_task_status(self, task_id: str):
        """删除任务状态（缓存）"""
        if self.client:
            await self.client.delete(f"task:{task_id}:status")


# 全局 Redis 客户端
redis_client: Optional[RedisClient] = None


async def init_redis(url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")):
    """初始化 Redis 客户端"""
    global redis_client
    if HAS_REDIS:
        redis_client = RedisClient(url)
        await redis_client.connect()


async def close_redis():
    """关闭 Redis 客户端"""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None