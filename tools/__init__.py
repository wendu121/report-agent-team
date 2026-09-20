# tools/ · 工具层（M4）
#
# 设计约束（DESIGN §3 / §11-2）：
#   - 首版直连，后续可替换为 MCP；抽象层隔离底层服务商。
#   - Researcher 只消费字段契约（id / url / title / snippet / credibility），
#     不硬编码任何服务商专有名词（DESIGN §11-2 强约束）。
#   - 工具失败须进 tool_status.ok=false，不静默崩溃（DESIGN §10 / memory/schema.md §2.4）。
#
# 关键变化（相对 M3）：M3 的 tool_status 由 LLM 自称产出（假工具调用）；
# M4 起工具由**引擎真实调用**，tool_status 反映真实执行结果。

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ToolError(Exception):
    """工具调用失败（配置缺失 / 网络 / 限流 / 参数非法）。引擎捕获后写 tool_status.ok=false。"""


@dataclass
class ToolBundle:
    """引擎持有的工具集合，注入 orchestrator 的 Agent 节点。"""
    web_search: object                 # WebSearchTool
    data_proc: object                  # DataProcTool
    doc_export: object                 # DocExportTool
    mcp: object = None                 # M11-3：MCPClient（外部工具桥；无配置为 None）
    # True 表示本次**显式**装载了 provider=mock 的源（离线/链路验证），产出为占位数据。
    # 注意：不再表示「没有真源」——那个含义已由 degraded 精确表达。
    using_mock_search: bool = False
    # 本次未能装载的源：[{id, name, reason, fix}]（reason: missing_key / provider_init_failed）。
    # 调用方必须据此如实告知「哪些源没参与检索」，绝不假装检索面完整（立约·禁假配置）。
    degraded: list = None


def build_tools(config_path: Optional[str] = None,
                plugin_filter: Optional[list] = None) -> ToolBundle:
    """按 config/plugins.yaml（数据源插件注册表）构建检索工具，多源聚合。

    M9-2：检索源由 config/plugins.yaml 的 enabled/连接状态驱动（真·控制器）。
    无 plugins.yaml 时回落 legacy config/tools.yaml 单 provider（向后兼容）。
    data_proc / doc_export 仍读 config/tools.yaml（不变）。

    plugin_filter：本次任务选定的数据源 id 列表（来自 ChatEntry「插件」多选）。
    None = 全部 enabled 源；非空 = 仅聚合列表内源（coming_soon/未知 id 已被 API 层拒绝）。

    密钥：api_key 源从 DS_<ID大写>_API_KEY 读（tavily 兼容旧 TAVILY_API_KEY）。
    **缺密钥的源一律不注册**，登记到 SearchTool.degraded（含 reason/fix），其结果不会
    出现；装载时逐源告警并写进 ToolBundle.degraded。仅「显式配置为 mock 的源」才会
    装载，其结果带 is_mock 标记且不进「来源」列表。
    """
    import os
    from pathlib import Path
    import yaml

    from .data_sources import load_data_sources, load_secrets, build_search_tool
    from .data_proc import DataProcTool
    from .doc_export import DocExportTool

    base = Path(__file__).resolve().parent.parent
    p = Path(config_path) if config_path else (base / "config" / "tools.yaml")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}

    ws_cfg = cfg.get("web_search", {}) or {}
    max_results = int(ws_cfg.get("max_results", 5))
    timeout = int(ws_cfg.get("timeout", 20))

    specs = load_data_sources()
    # ChatEntry「插件」多选：按本次任务所选 id 过滤（真·控制器效应）。
    if plugin_filter is not None:
        want = set(plugin_filter)
        specs = [s for s in specs if s.get("id") in want]
    secrets = load_secrets()
    web = build_search_tool(specs, secrets, max_results=max_results, timeout=timeout)

    if web.degraded:
        # 逐源如实报出：不要说「检索不可用」，要说清**哪个**源、**为什么**、**怎么修**。
        # 2026-09-19：此前这里判断的是全局 `using_mock_search`，只要还有任一 keyless 真源
        # 就永远是 False → 分支从未触发，缺密钥的源静默产出占位数据冒充检索结果。
        print(
            "\n" + "=" * 70
            + f"\n[WARN] 有 {len(web.degraded)} 个数据源本次**未参与检索**（不会贡献任何数据）："
        )
        for d in web.degraded:
            why = "未配置密钥" if d.get("reason") == "missing_key" else str(d.get("reason"))
            print(f"       - {d.get('id')}（{d.get('name')}）原因={why}"
                  f" → 修复：{d.get('fix')}")
        print("       若报告内容需要这些来源，请先在 UI 修复后再重跑。" + "\n" + "=" * 70 + "\n")

    if web.using_mock_search:
        print(
            "\n" + "=" * 70
            + "\n[WARN] 本次装载了**显式声明**的 mock（占位）数据源，产出为占位数据，"
            "仅用于链路验证，**不可作为真实研报依据**。\n"
            "       如需真实检索：在「设置 → 数据源」为对应插件填入密钥。\n"
            + "=" * 70 + "\n"
        )

    de_cfg = cfg.get("doc_export", {}) or {}
    export_dir = de_cfg.get("export_dir")

    # M11-3：MCP 消费桥（外部工具）；无配置/不可达时 mcp 为 None，降级跳过（不抛）。
    mcp = None
    try:
        from .mcp_client import MCPClient

        # 传 None 让 mcp_client 按当前账号命名空间惰性解析（各自一套 MCP 配置）
        mcp = MCPClient(config_path=None)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] MCPClient 初始化失败，外部工具不可用: {e}")

    return ToolBundle(
        web_search=web,
        data_proc=DataProcTool(enabled=bool((cfg.get("data_proc", {}) or {}).get("enabled", True))),
        doc_export=DocExportTool(
            enabled=bool(de_cfg.get("enabled", True)),
            export_dir=(base / export_dir) if export_dir else None,
        ),
        mcp=mcp,
        using_mock_search=web.using_mock_search,
        degraded=list(getattr(web, "degraded", []) or []),
    )
