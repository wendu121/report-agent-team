"""
FastAPI 服务层入口
M6-2 产出：FastAPI 应用主文件
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .api import router as api_router
from .websocket import router as ws_router
from .admin import router as admin_router, public_router as admin_public_router
# 账号体系（Phase 1 · DESIGN_account_hierarchy.md）：/api/v1/auth/*
from .auth import router as auth_router


def _configure_logging() -> None:
    """给 root logger 挂 handler，否则业务模块 logger.info 全被 lastResort 丢弃。

    现象：uvicorn 默认只配置 uvicorn.* 三个 logger，root 无 handler。
    Python 兜底 lastResort 级别为 WARNING，于是 [search_many] 源 X 命中 N 条
    这类 INFO 诊断全部静默，只有失败（warning）能冒出来 —— 表现为
    「日志里只有失败、看不到任何命中」，极易误判成数据源全挂。
    级别可用环境变量 LOG_LEVEL 调整（默认 INFO）。
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    print("🚀 Report Agent Team 服务层启动")
    from .database import init_database
    init_database()
    yield
    # 关闭时清理
    print("🛑 Report Agent Team 服务层关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="Report Agent Team API",
    description="多 Agent 研究报告协作系统 - HTTP/WebSocket 服务层",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 配置（开发环境允许所有来源）
# M8-1：配置控制台需要 PUT/DELETE + 自定义 X-Admin-Token 头，原配置只放行 GET/POST
# 且 header 白名单无 X-Admin-Token，会导致 admin 写操作被浏览器预检拦截。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为 UI 域名
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Admin-Token"],
)


# 请求上下文中间件：把当前 Request 注入 ContextVar，供 server.admin._require_admin
# 在手动调用（request=None）时回退读取 Authorization: Bearer，
# 修复「登录态下 /admin/* 仍 401 → 前端清 token 级联登出」缺陷。
#
# ⚠️ 必须是**纯 ASGI 中间件**，绝不能用 BaseHTTPMiddleware（2026-09-19 实测踩坑）。
# BaseHTTPMiddleware 会在 app 外面再套一层 receive/send（内存流），`http.disconnect`
# 被它自己消费掉，于是端点里 `await request.is_disconnected()` **永远返回 False** ——
# 表现为「用户点停止 → 服务端完全不知道，照跑到最后并把整轮写进库」，
# 也就是把「停止生成」做成了假按钮。
# 对照实验见 scripts/probe_disconnect_middleware.py：同样 RST 断开，
# 无中间件 → 立刻打出 DISCONNECT_DETECTED；套 BaseHTTPMiddleware → 永远打不出。
class _RequestContextMiddleware:
    """纯 ASGI：把 Request 注入 ContextVar，并把 receive 原样透传给下游。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from .admin import _current_request

        # 仅用于读 header（admin.py:178），不消费 body，故 receive 透传不影响端点逻辑
        request = Request(scope, receive)
        ctx_token = _current_request.set(request)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_request.reset(ctx_token)


app.add_middleware(_RequestContextMiddleware)

# 注册路由
app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router, prefix="/api/v1")
# M8-1：配置控制台（/api/v1/admin/*）
app.include_router(admin_router, prefix="/api/v1")
# M8-4：公开模板端点（提交页 TemplateSelect 用，无需 admin token）
app.include_router(admin_public_router, prefix="/api/v1")
# 账号体系：注册 / 登录 / 账号管理（登录/注册公开，其余依赖 Bearer JWT）
app.include_router(auth_router, prefix="/api/v1")


@app.get("/")
async def root():
    """根路径健康检查"""
    return {
        "service": "Report Agent Team API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "rest": "/api/v1/tasks",
            "websocket": "/api/v1/tasks/{task_id}/stream"
        }
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # 开发模式自动重载
        log_level="info"
    )
