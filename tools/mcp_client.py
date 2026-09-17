# tools/mcp_client.py · MCP 消费桥（M11-3, D1-B）
"""把外部 MCP server 的工具接入引擎，供 Researcher/Analyst 经 function-calling 自主调用。

- 配置 SoT：`config/mcp_servers.yaml`（id/name/transport[sse|http]/url/headers/enabled/tools_whitelist）。
- 密钥：header 类鉴权放 `config/mcp_servers.yaml` 的 `headers`（不进 git；或 .secrets，依部署约定）。
- transport：SSE（`mcp.client.sse`）/ Streamable HTTP（`mcp.client.streamable_http`）。
  stdio 型须打进 api 镜像（跨容器起进程坑多），M11-3 暂不内置支持，文档标注。
- 诚实边界：server 不可达 → `list_tools` 返回 `[]`、`call_tool` 返回 `ok=False`，**绝不伪造结果**。
- 依赖 `mcp` SDK 延迟导入：未装 SDK 时模块仍可 import，仅实际连接时报错（降级）。
"""
from __future__ import annotations

import os
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _default_cfg() -> Path:
    """按当前账号命名空间解析 MCP 配置（各自一套 server 与凭据）。"""
    try:
        from server import tenancy
        return tenancy.config_path("mcp_servers.yaml")
    except ImportError:
        return Path(__file__).resolve().parent.parent / "config" / "mcp_servers.yaml"


# 向后兼容：旧代码可能直接 import DEFAULT_CFG。真正的默认解析走 _default_cfg()（按账号惰性解析）
DEFAULT_CFG = Path(__file__).resolve().parent.parent / "config" / "mcp_servers.yaml"


from ._async_util import run_async as _run_async


def _load_config(config_path: Optional[str]) -> list:
    p = Path(config_path) if config_path else _default_cfg()
    if not p.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data.get("servers", []) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP 配置读取失败，按无 server 处理: %s", e)
        return []


class MCPClient:
    """MCP server 注册表消费端。无状态：每次调用按需连接（stateless 工具足够，生命周期最简）。"""

    def __init__(self, config_path: Optional[str] = None, servers: Optional[list] = None):
        self._servers = servers if servers is not None else _load_config(config_path)
        # id -> server cfg（仅 enabled）
        self._by_id = {s["id"]: s for s in self._servers if s.get("enabled", True)}

    # ---- 公共 API（同步，供 dispatch_tool 直接调用）----
    # list_tools 结果 TTL 缓存（含「不可达=空」负缓存）：ChatAgent 每次 /chat 都重建 tool
    # schemas 并逐个连 MCP server，不可达 server（如 DeepWiki 出口不通）每次都干等连接超时，
    # 是 /chat 固定延迟的来源之一。TTL 内零连接开销，到期自动重试（默认 5 分钟）。
    _TOOLS_TTL = float(os.getenv("MCP_LIST_TOOLS_TTL", "300") or 300)
    # 单次 MCP 操作硬超时：不可达 server 若靠 SDK 默认连接超时，实测可干等 ~36s
    # （13:46:37 step start → 13:47:13 deepwiki 列举失败），直接吃掉 ChatAgent step 300s 预算的一大块。
    # 策略（同 LibreChat 对外部依赖的处理）：短超时 + 降级，绝不干等；失败进 TTL 负缓存，后续零开销。
    _LIST_TIMEOUT = float(os.getenv("MCP_LIST_TOOLS_TIMEOUT", "15") or 15)
    _CALL_TIMEOUT = float(os.getenv("MCP_CALL_TIMEOUT", "30") or 30)

    def list_tools(self) -> list:
        """返回 [{server, name, description, input_schema}]；不可达 server 跳过。"""
        now = time.time()
        cached = getattr(self, "_tools_cache", None)
        if cached is not None and now - getattr(self, "_tools_cache_ts", 0.0) < self._TOOLS_TTL:
            return cached
        out = []
        for sid, cfg in self._by_id.items():
            try:
                out.extend(_run_async(self._list_one(sid, cfg)))
            except (Exception, asyncio.CancelledError) as e:  # noqa: BLE001 — 不可达/超时一律降级 []，绝不打穿 /chat
                logger.warning("MCP server %s 列举工具失败（降级跳过，TTL 内缓存空结果）: %s", sid, e)
        self._tools_cache = out
        self._tools_cache_ts = now
        return out

    def call_tool(self, server_id: str, name: str, args: dict) -> dict:
        """调用工具；失败返回 {ok:False}（诚实边界）。"""
        cfg = self._by_id.get(server_id)
        if cfg is None:
            return {"ok": False, "error": f"MCP server '{server_id}' 未启用或不存在"}
        try:
            result = _run_async(self._call_one(cfg, name, args or {}))
            return {"ok": True, "result": result, "source": f"mcp:{server_id}:{name}"}
        except (Exception, asyncio.CancelledError) as e:  # noqa: BLE001 — 失败一律降级 ok=False，绝不伪造/打穿
            logger.warning("MCP 调用 %s/%s 失败（降级）: %s", server_id, name, e)
            return {"ok": False, "error": str(e), "source": f"mcp:{server_id}:{name}"}

    # ---- async 实现 ----
    async def _connect(self, cfg: dict):
        """按 transport 建立 (read, write, session)；不支持的 transport 抛错。"""
        transport = (cfg.get("transport") or "sse").lower()
        url = cfg.get("url")
        headers = cfg.get("headers") or {}
        if transport in ("sse",):
            from mcp.client.sse import sse_client
            from mcp import ClientSession

            ctx = sse_client(url, headers=headers or None)
            read, write = await ctx.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            return ctx, session
        if transport in ("http", "streamable", "streamable_http"):
            from mcp.client.streamable_http import streamablehttp_client
            from mcp import ClientSession

            ctx = streamablehttp_client(url, headers=headers or None)
            read, write, _ = await ctx.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            return ctx, session
        raise ValueError(f"不支持的 MCP transport: {transport}（M11-3 仅支持 sse/http）")

    async def _list_one(self, sid: str, cfg: dict) -> list:
        """带硬超时的列举：不可达 server 短超时降级，不干等 SDK 默认超时。

        借鉴 LibreChat（`mcpServers.<id>.timeout`，注释明写默认 60000）：
        **超时按 server 可覆盖**，慢的 stdio 型 server 可单独放宽；未配则回落全局 env 默认值。
        """
        tmo = self._server_timeout(cfg, self._LIST_TIMEOUT)
        try:
            return await asyncio.wait_for(
                self._list_one_inner(sid, cfg), timeout=tmo)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"MCP server '{sid}' 列举工具超时（>{tmo}s）")

    @staticmethod
    def _server_timeout(cfg: dict, default: float) -> float:
        """按 server 读超时覆盖值（LibreChat 风格）；未配或非法值一律回落 default。"""
        try:
            v = float((cfg or {}).get("timeout") or 0)
        except (TypeError, ValueError):
            return default
        return v if v > 0 else default

    async def _list_one_inner(self, sid: str, cfg: dict) -> list:
        ctx = session = None
        try:
            ctx, session = await self._connect(cfg)
            resp = await session.list_tools()
            wl = set(cfg.get("tools_whitelist") or [])
            out = []
            for t in resp.tools:
                if wl and t.name not in wl:
                    continue
                out.append({
                    "server": sid,
                    "name": t.name,
                    "description": getattr(t, "description", "") or "",
                    "input_schema": (getattr(t, "inputSchema", None) or {}),
                })
            return out
        finally:
            await self._safe_close(session, ctx)

    async def _call_one(self, cfg: dict, name: str, args: dict) -> str:
        """带硬超时的工具调用：慢/假死 MCP server 不得拖垮 ChatAgent step。

        同样支持按 server 覆盖（见 `_server_timeout`）。
        """
        tmo = self._server_timeout(cfg, self._CALL_TIMEOUT)
        try:
            return await asyncio.wait_for(
                self._call_one_inner(cfg, name, args), timeout=tmo)
        except asyncio.TimeoutError:
            raise TimeoutError(f"MCP 调用超时（>{tmo}s）")

    async def _call_one_inner(self, cfg: dict, name: str, args: dict) -> str:
        ctx = session = None
        try:
            ctx, session = await self._connect(cfg)
            resp = await session.call_tool(name, args)
            # 结果可能是文本块列表；拼成字符串回注 LLM
            parts = []
            for block in getattr(resp, "content", []) or []:
                txt = getattr(block, "text", None)
                if txt is not None:
                    parts.append(txt)
                else:
                    parts.append(json.dumps(getattr(block, "data", None), ensure_ascii=False))
            return "\n".join(parts)
        finally:
            await self._safe_close(session, ctx)

    @staticmethod
    async def _safe_close(session, ctx):
        # session/ctx 可能因 _connect 中途抛错而为 None，须判空，避免清理阶段再抛（UnboundLocalError/AttributeError）
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
