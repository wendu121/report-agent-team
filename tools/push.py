"""M9-4 研报推送渠道抽象层。

真·控制器：启用的渠道在 run_report 末态真实外发（webhook 真 POST / mock 真落盘）。
诚实边界（见 DESIGN_M9-4 §3.4 / §10）：
  - H1 coming_soon 永不发：build_push_targets 显式跳过，引擎永不调其 send。
  - H2 端点空诚实跳过：webhook 启用但 endpoint 空 → 跳过 + 记 skip 留痕（不冒充、不崩）。
  - H3 best-effort：单渠道失败仅记 delivery_status，不阻断研报产出。
  - H4 向后兼容：无 channels.yaml → load_channels 返回 [] → 不推送。
  - H5 无密钥引入：webhook 端点存 config（非 API key）；coming_soon 无密钥需求。
热加载：load_channels() 每次重读 config/channels.yaml（与 skills/data_sources/agents_library 同频）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # channels.yaml 无需 round-trip 注释保留；写操作由 server/admin.py 走 ruamel

from . import ToolError

# 项目根（report-agent-team/），与 orchestrator / skills.py 同源
BASE = Path(__file__).resolve().parent.parent
# 向后兼容常量；新代码用 _channels_path()（按账号惰性解析）
CONFIG_DIR = BASE / "config"
CHANNELS_PATH = CONFIG_DIR / "channels.yaml"
# 推送日志属**运行时产物** → 始终留在仓库全局，不进租户目录（避免拷贝模板时串日志）
PUSH_LOG = BASE / ".engine_state" / "push_log.jsonl"  # mock 通道落盘（已在 .gitignore）


def _channels_path() -> Path:
    """按当前账号命名空间解析推送渠道配置（各自一套渠道与凭据）。"""
    try:
        from server import tenancy
        return tenancy.config_path("channels.yaml")
    except ImportError:
        return CHANNELS_PATH


class PushChannel:
    """推送渠道接口（Protocol 风格，实现须有 send）。"""

    id: str
    name: str
    category: str

    def send(self, payload: dict) -> dict:
        """返回 {"ok": bool, "detail": str}。失败应抛 ToolError（由调用方捕获）。"""
        ...


class WebhookChannel:
    """webhook 类型：POST payload_format 对应的消息体到 endpoint。真·外发。"""

    def __init__(self, spec: dict):
        self.id = spec["id"]
        self.name = spec.get("name", spec["id"])
        self.category = spec.get("category", "webhook")
        self.fmt = (spec.get("payload_format") or "discord").lower()
        self.endpoint = (spec.get("endpoint") or "").strip()

    def _body(self, payload: dict) -> dict:
        title = payload.get("title", "研报通知")
        text = payload.get("summary", "")
        if self.fmt == "dingtalk":
            return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
        if self.fmt == "feishu":
            return {"msg_type": "text", "content": {"text": f"{title}\n\n{text}"}}
        if self.fmt == "wecom":
            return {"msgtype": "markdown", "markdown": {"content": f"# {title}\n{text}"}}
        # discord 默认
        return {"content": f"**{title}**\n{text}"}

    def send(self, payload: dict) -> dict:
        if not self.endpoint:
            raise ToolError(f"webhook 端点为空：{self.name}")
        try:
            import requests  # 软依赖：M9-2 web_search 已用，镜像已装；缺失由 best-effort 捕获
            r = requests.post(self.endpoint, json=self._body(payload), timeout=10)
            return {"ok": r.ok, "detail": f"HTTP {r.status_code}"}
        except Exception as e:  # 网络/依赖异常：抛 ToolError 交调用方 best-effort 记录
            raise ToolError(f"webhook 发送失败：{e}")


class MockChannel:
    """零配置本地通道：真落盘到 push_log.jsonl（不冒充外发）。用于验证链路。"""

    def __init__(self, spec: dict):
        self.id = spec["id"]
        self.name = spec.get("name", spec["id"])
        self.category = spec.get("category", "mock")

    def send(self, payload: dict) -> dict:
        PUSH_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"channel": self.id, "ts": time.time(), "payload": payload}
        with PUSH_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "detail": f"logged → {PUSH_LOG.name}"}


class ComingSoonChannel:
    """尚未接入：send 直接 raise，引擎永远跳过，绝不冒充可用（同 M9-2 ComingSoonProvider）。"""

    def __init__(self, spec: dict):
        self.id = spec["id"]
        self.name = spec.get("name", spec["id"])
        self.category = "coming_soon"

    def send(self, payload: dict) -> dict:
        raise ToolError(f"推送渠道尚未接入：{self.name}（{self.id}）")


PROVIDER_REGISTRY = {
    "webhook": WebhookChannel,
    "mock": MockChannel,
    "coming_soon": ComingSoonChannel,
}


def load_channels() -> List[Dict[str, Any]]:
    """读取渠道条目（每次调用现读磁盘 → 热加载，零重启）。

    文件缺失返回 []（向后兼容：无 channels.yaml → 不推送，现有行为不变）。
    """
    cp = _channels_path()
    if not cp.exists():
        return []
    data = yaml.safe_load(cp.read_text(encoding="utf-8")) or {}
    return [c for c in (data.get("channels") or []) if isinstance(c, dict) and c.get("id")]


def _recompute_status(spec: dict) -> str:
    """派生状态（与 build_push_targets 过滤同源）：
    - channel_type=coming_soon → coming_soon（永远）
    - channel_type=mock → connected（无需端点）
    - webhook：endpoint 非空 → connected，否则 disconnected
    """
    ctype = (spec.get("channel_type") or "coming_soon").lower()
    if ctype == "coming_soon":
        return "coming_soon"
    if ctype == "mock":
        return "connected"
    return "connected" if (spec.get("endpoint") or "").strip() else "disconnected"


def build_push_targets(channels: List[dict], event: str) -> List[Tuple[dict, PushChannel]]:
    """按规则聚合本事件应推送的渠道（真·控制器开关）：
    - enabled=False → 跳过
    - installed=False（且非 builtin）→ 跳过
    - event 不在 strategy → 跳过
    - channel_type=coming_soon → 跳过（诚实，绝不冒充）
    - webhook 且 endpoint 空 → 跳过（诚实，不冒充、不崩）
    返回 [(spec, channel_instance)]。
    """
    targets: List[Tuple[dict, PushChannel]] = []
    for c in channels:
        if not c.get("enabled"):
            continue
        if not (c.get("installed") or c.get("builtin")):
            continue
        if event not in (c.get("strategy") or []):
            continue
        ctype = (c.get("channel_type") or "coming_soon").lower()
        if ctype == "coming_soon":
            continue
        if ctype == "webhook" and not (c.get("endpoint") or "").strip():
            continue  # H2：启用但端点空，诚实跳过（留痕由 push_report 上层记）
        cls = PROVIDER_REGISTRY.get(ctype, ComingSoonChannel)
        targets.append((c, cls(c)))
    return targets


def push_report(final_state: dict, channels: Optional[List[dict]] = None) -> List[dict]:
    """研报末态 → 按 status 选事件 → 聚合启用渠道 → 逐个 send（best-effort）。

    event 映射：status==done → on_complete；status==escalated → on_gate_fail。
    每个渠道 send 包 try/except：失败仅记 delivery_status，不阻断研报产出。
    启用但 webhook 端点空的渠道记一条 skip 留痕（H2，不冒充）。
    返回 delivery_status 列表（供 run_report 写入 final_state 审计）。
    """
    if channels is None:
        channels = load_channels()
    status = final_state.get("routing_state", {}).get("status")
    event = "on_complete" if status == "done" else ("on_gate_fail" if status == "escalated" else None)
    if event is None:
        return []
    rs = final_state.get("routing_state", {})
    payload = {
        "title": (final_state.get("user_task") or {}).get("topic", "研报任务"),
        "summary": (final_state.get("report_markdown") or "")[:800],
        "status": status,
        "escalate_reason": rs.get("escalate_reason", ""),
    }
    out: List[dict] = []
    # 诚实留痕（H2）：启用但 webhook 端点空的渠道，记 skip 条目（不冒充、不崩）
    for c in channels:
        if c.get("enabled") and (c.get("channel_type") or "coming_soon") == "webhook" \
           and not (c.get("endpoint") or "").strip() and event in (c.get("strategy") or []):
            out.append({"channel": c["id"], "ok": False,
                        "detail": "skipped: enabled but endpoint empty (honest no-op)"})
    # 真实推送目标
    for spec, ch in build_push_targets(channels, event):
        try:
            res = ch.send(payload)
            out.append({"channel": spec["id"], "ok": bool(res.get("ok")), "detail": res.get("detail", "")})
        except ToolError as e:
            out.append({"channel": spec["id"], "ok": False, "detail": str(e)})
    return out
