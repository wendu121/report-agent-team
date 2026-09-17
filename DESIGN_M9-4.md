# DESIGN_M9-4 · 研报推送渠道（Delivery Channels）

> 元信息：起草 2026-09-09（M9-4，推进 M9 扩展层第 4 里程碑）
> 状态：待评审（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议），全 PASS 才动代码
> 关联：[DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.4（渠道 spec）、[DESIGN_M9-3.md](./DESIGN_M9-3.md)（技能库范式，本设计镜像其结构与诚实边界）、[DESIGN_M9-2.md](./DESIGN_M9-2.md)（provider 范式：ComingSoon 诚实占位）

---

## 0. 范围裁定（boss 铁律：先设计后编码 / UI 是真 controller / 禁假配置 / 禁过度工程）

### 0.1 领域替换（沿用 §2.2 平台架构、换领域内容）
Accio 图2「消息渠道」= 钉钉/微信/飞书/企微/Telegram/Discord 群机器人。研报系统**直接适用**该架构（研报完成推送到 IM），**无需把电商内容搬进来**（与 M9-2 不同，M9-2 必须把 Shopify 等电商插件换成 Tavily 等研报源）。故 M9-4 渠道目录 = `钉钉/飞书/企业微信/Discord/Telegram/微信`，与平台设计 §3.4 完全一致。

### 0.2 真·控制器（非装饰卡片）
启用/禁用某渠道、填写/清空 webhook 端点，**真实改变引擎在研报完成时的外发行为**：
- 启用 + 端点已填 → 研报完成时**真向该 webhook POST**（E6 实证）；
- 禁用 → 该渠道**永不外发**（E2 实证）；
- 启用但端点为空 → **诚实跳过 + 留痕告警**（不冒充已发送、不崩）；
- `coming_soon` 渠道 → 引擎**永远跳过**，绝不冒充可用（同 M9-2 ComingSoonProvider 纪律）。

### 0.3 不做清单（防范围蔓延 / 过度工程）
- ❌ 微信个人号 OAuth / 公众号资质接入（需官方资质，标 `coming_soon`，不实现）
- ❌ Telegram bot token 密钥管理体系（标 `coming_soon`，留接口待 M9 后补）
- ❌ 独立「手动推送流水线」（复用 `POST /admin/channels/:id/test` 单发即满足「手动推送」诉求，不另开管道）
- ❌ 公网下载链接拼接（需 server base URL 配置，非 M9-4 范围；payload 自含标题+摘要+状态，全文由用户在应用内查看）
- ❌ 新增 `.secrets/channels.env` 密钥体系（webhook 端点即 URL，存 config/channels.yaml；coming_soon 渠道暂无密钥需求；未来若引 Telegram token 再评估 env 化）

---

## 1. 现状实证（基于真实代码，非印象）

- M9-1 智能体市场 ✅（commit 7a7762a）、M9-2 数据源插件 ✅（4e582d5）、M9-3 研报技能库 ✅（ba889bb），均未 push。
- **推送现状**：引擎产研报后仅经 WS 广播 `task_done`（`server/api.py:354` → `websocket.emit_task_done`），**无外部 IM/Webhook 推送**。TD-002 缺口（中间事件未广播）与 M9-4 无关。
- **注入点已锁定**：`orchestrator.run_report()` 在 `:1135` 返回 `graph.invoke(init)` 前的 final state，`status` 已由节点置为 `done`(`:917`) 或 `escalated`(`:973`)。在此处调用 `push_report(final)` 为**单一、清晰、可测**的钩子，与 M9-3 在 `make_agent`(`:646`) 注入技能同构（都在 orchestrator 层、单次注入）。
- **消费链**：`server/engine_runner.py:54` 调 `run_report` → 写 `outputs/<task_id>.json`；WS 广播在 `api.py`。push 在 `run_report` 内完成，对 engine_runner / api 零改动（仅 final_state 多一个 `delivery_status` 字段，向后兼容）。
- **可复用范式**：`tools/skills.py`（`build_skill_context` 热加载 + lower 归一）、`tools/data_sources.py`（`ComingSoonProvider` + `PROVIDER_REGISTRY` + `load_*` 热加载 + `build_*` 聚合）、`server/admin.py:1632-1843`（skills 复合写 ruamel + 审计 + 回滚 + builtin 拒删 + 启用守卫）、`config/skills.yaml`（SoT 形状）、`web/src/views/Skills.vue`（市场/已安装页）。

---

## 2. Goals / Non-Goals

### Goals
1. 研报完成（`status=done`）/ 升级（`status=escalated`）时，按启用渠道**真实外发**通知（真·控制器）。
2. 渠道目录 UI 化（市场/已安装 + 启停 + 端点配置 + 测试发送），后端 admin 复合写回 `config/channels.yaml`。
3. 诚实边界贯穿：webhook 端点在引擎层真发；`coming_soon` 永不发；端点空诚实跳过。
4. 热加载：改 channels.yaml 下一个任务即生效（与 M9-1/2/3 同频）。
5. 复用 M9 范式，改动面最小，三道闸全闭环。

### Non-Goals
- 微信/Telegram 真实接入（coming_soon）。
- 公网链接 / 附件投递。
- 独立手动推送管道（以 test 端点替代）。
- 渠道密钥 env 化（本期不需要）。

---

## 3. 架构

### 3.1 SoT：`config/channels.yaml`（镜像 skills.yaml 形状，字段统一）
```yaml
# 研报推送渠道 SoT（M9-4）。status 由引擎/admin 按 enabled + endpoint 实况重算。
# 真·控制器：enabled + endpoint 已填 → 研报完成真向外发；coming_soon 永不发。
channels:
  - id: dingtalk
    name: 钉钉
    icon: /channels/dingtalk.svg
    category: 即时通讯
    description: 研报完成后推送到钉钉群机器人（markdown）
    channel_type: webhook            # webhook | mock | coming_soon
    payload_format: dingtalk         # dingtalk|feishu|wecom|discord（webhook 专用）
    strategy: [on_complete]          # on_complete | on_gate_fail（引擎驱动触发点）
    endpoint: ""                     # webhook URL（webhook 类型必填；coming_soon 留空）
    enabled: false                   # 默认关：需用户填 endpoint 后启用（诚实：未配置不假发）
    installed: true
    builtin: true
    status: disconnected             # connected | disconnected | coming_soon（派生）
  - id: feishu
    name: 飞书
    channel_type: webhook
    payload_format: feishu
    strategy: [on_complete]
    endpoint: ""
    enabled: false
    installed: true
    builtin: true
    status: disconnected
  - id: wecom
    name: 企业微信
    channel_type: webhook
    payload_format: wecom
    strategy: [on_complete]
    endpoint: ""
    enabled: false
    installed: true
    builtin: true
    status: disconnected
  - id: discord
    name: Discord
    channel_type: webhook
    payload_format: discord
    strategy: [on_complete]
    endpoint: ""
    enabled: false
    installed: true
    builtin: true
    status: disconnected
  - id: mock                          # 本地测试通道：零配置、真落盘，用于验证控制器效应
    name: 本地测试通道
    category: 测试
    description: 研报完成后写入本地投递日志（不外发），用于验证推送链路
    channel_type: mock
    strategy: [on_complete, on_gate_fail]
    endpoint: ""
    enabled: true                    # 默认开：开箱即证明 pipeline 工作
    installed: true
    builtin: true
    status: connected
  - id: telegram
    name: Telegram
    channel_type: coming_soon
    strategy: [on_complete]
    endpoint: ""
    enabled: false
    installed: true
    builtin: true
    status: coming_soon
  - id: wechat
    name: 微信
    channel_type: coming_soon
    strategy: [on_complete]
    endpoint: ""
    enabled: false
    installed: true
    builtin: true
    status: coming_soon
```

### 3.2 抽象层：`tools/push.py`（新建，镜像 data_sources.py + skills.py）
```python
"""M9-4 研报推送渠道抽象层。
真·控制器：启用的渠道在 run_report 末态真实外发（webhook 真 POST / mock 真落盘）。
诚实边界：coming_soon 永远跳过；webhook 端点空 → 跳过+告警（不冒充、不崩）。
热加载：load_channels() 每次重读 config/channels.yaml（与 skills/data_sources 同频）。
"""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import yaml
from . import ToolError

BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"
CHANNELS_PATH = CONFIG_DIR / "channels.yaml"
PUSH_LOG = BASE / ".engine_state" / "push_log.jsonl"   # mock 通道落盘（gitignore）


class PushChannel(Protocol):
    id: str
    name: str
    category: str
    def send(self, payload: dict) -> dict:  # 返回 {ok:bool, detail:str}


class WebhookChannel:
    """webhook 类型：POST payload_format 对应的消息体到 endpoint。真·外发。"""
    def __init__(self, spec: dict):
        self.id = spec["id"]; self.name = spec.get("name", spec["id"])
        self.category = spec.get("category", "webhook")
        self.fmt = spec.get("payload_format", "discord")
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
            import requests
            r = requests.post(self.endpoint, json=self._body(payload), timeout=10)
            return {"ok": r.ok, "detail": f"HTTP {r.status_code}"}
        except Exception as e:
            raise ToolError(f"webhook 发送失败：{e}")


class MockChannel:
    """零配置本地通道：真落盘到 push_log.jsonl（不冒充外发）。用于验证链路。"""
    def __init__(self, spec: dict):
        self.id = spec["id"]; self.name = spec.get("name", spec["id"])
        self.category = spec.get("category", "mock")
    def send(self, payload: dict) -> dict:
        PUSH_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"channel": self.id, "ts": time.time(), "payload": payload}
        with PUSH_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "detail": f"logged → {PUSH_LOG.name}"}


class ComingSoonChannel:
    """尚未接入：send 直接 raise，引擎永远跳过，绝不冒充可用（同 M9-2）。"""
    def __init__(self, spec: dict):
        self.id = spec["id"]; self.name = spec.get("name", spec["id"])
        self.category = "coming_soon"
    def send(self, payload: dict) -> dict:
        raise ToolError(f"推送渠道尚未接入：{self.name}（{self.id}）")


PROVIDER_REGISTRY = {
    "webhook": WebhookChannel,
    "mock": MockChannel,
    "coming_soon": ComingSoonChannel,
}


def load_channels() -> List[Dict[str, Any]]:
    """读 channels.yaml（缺省返回 []，向后兼容：无 channels.yaml → 不推送）。"""
    if not CHANNELS_PATH.exists():
        return []
    data = yaml.safe_load(CHANNELS_PATH.read_text(encoding="utf-8")) or {}
    return [c for c in (data.get("channels") or []) if isinstance(c, dict) and c.get("id")]


def _recompute_status(spec: dict) -> str:
    if spec.get("channel_type") == "coming_soon":
        return "coming_soon"
    if spec.get("channel_type") == "mock":
        return "connected"
    # webhook：有 endpoint → connected，否则 disconnected
    return "connected" if (spec.get("endpoint") or "").strip() else "disconnected"


def build_push_targets(channels: List[dict], event: str) -> List[tuple]:
    """按规则聚合本事件应推送的渠道（真·控制器开关）：
    - enabled=False → 跳过
    - installed=False → 跳过
    - event 不在 strategy → 跳过
    - channel_type=coming_soon → 跳过（诚实，绝不冒充）
    - webhook 且 endpoint 空 → 跳过（返回告警，不崩）
    返回 [(spec, channel_instance)]。
    """
    targets = []
    for c in channels:
        if not c.get("enabled") or not (c.get("installed") or c.get("builtin")):
            continue
        if event not in (c.get("strategy") or []):
            continue
        ctype = (c.get("channel_type") or "coming_soon").lower()
        if ctype == "coming_soon":
            continue
        if ctype == "webhook" and not (c.get("endpoint") or "").strip():
            # 诚实跳过：启用但端点空，留痕由调用方记录，不 raise
            continue
        cls = PROVIDER_REGISTRY.get(ctype, ComingSoonChannel)
        targets.append((c, cls(c)))
    return targets


def push_report(final_state: dict, channels: Optional[List[dict]] = None) -> List[dict]:
    """研报末态 → 按 status 选事件 → 聚合启用渠道 → 逐个 send（best-effort）。

    event 映射：status==done → on_complete；status==escalated → on_gate_fail。
    每个渠道 send 包 try/except：失败仅记 delivery_status，不阻断研报产出。
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
    out = []
    # 诚实留痕（独立审议 NOTE-2）：启用但 webhook 端点空的渠道，记一条 skip 条目，
    # 不冒充已发、不崩，让用户可在 delivery_status 看到误配置。
    for c in channels:
        if c.get("enabled") and (c.get("channel_type") or "coming_soon") == "webhook" \
           and not (c.get("endpoint") or "").strip() and event in (c.get("strategy") or []):
            out.append({"channel": c["id"], "ok": False,
                        "detail": "skipped: enabled but endpoint empty (honest no-op)"})
    for spec, ch in build_push_targets(channels, event):
        try:
            res = ch.send(payload)
            out.append({"channel": spec["id"], "ok": bool(res.get("ok")), "detail": res.get("detail", "")})
        except ToolError as e:
            out.append({"channel": spec["id"], "ok": False, "detail": str(e)})
    return out
```

### 3.3 orchestrator 注入（最小改动，单点）
`orchestrator.py`：
- `:25` 同 skills 引入：`from tools.push import push_report  # M9-4：研报完成/升级按启用渠道真实外发`
- `run_report` 末尾（`:1135` `return graph.invoke(init)` 改为）：
```python
    final = graph.invoke(init)
    # M9-4 研报推送渠道：研报完成(on_complete)/升级(on_gate_fail) 按启用渠道真实外发。
    try:
        delivery = push_report(final)           # 热加载 channels.yaml（load_channels）
        if delivery:
            final.setdefault("delivery_status", []).extend(delivery)
    except Exception as e:                       # best-effort：推送失败不阻断研报产出
        final.setdefault("delivery_status", []).append(
            {"channel": "_push", "ok": False, "detail": f"push_report: {e}"})
    return final
```
- **向后兼容**：无 channels.yaml → `load_channels()` 返回 [] → `push_report` 返回 [] → final 不变；有 channels.yaml 但全 disabled/空端点 → 仅 mock(默认开) 落盘，webhook 跳过。现有 21 passing 回归测试不受影响（3 个 TD-009 失败与推送无关）。

### 3.4 诚实边界（不可逾越）
- H1 软增强不谎称强制：`coming_soon` 渠道在 UI 标「即将推出」且引擎**永不调用其 send**（build_push_targets 跳过）。
- H2 端点空防御：webhook 启用但 endpoint 空 → `build_push_targets` 跳过（不 raise、不冒充已发），UI 给「未配置端点」提示。
- H3 best-effort：单渠道失败仅记 `delivery_status`，不抛异常阻断研报；`delivery_status` 进入 final_state 审计。
- H4 向后兼容：无 channels.yaml → 无推送，现有行为不变。
- H5 无密钥引入：webhook 端点存 config（非密钥 env），coming_soon 无密钥需求；不新建 `.secrets/channels.env`。

---

## 4. 后端 admin API（镜像 admin.py:1632-1843 skills 范式）

### 4.1 常量/辅助（文件尾追加，复用 `_atomic_write` / `_write_audit` / `_dump_commented`）
```python
from tools.push import CHANNELS_PATH, load_channels, _recompute_status
def _load_channels_rt():  # ruamel round-trip（保注释，同 _load_skills_rt:1639）
    ...
def _channel_item(c: dict) -> dict:  # 公开视图：隐藏 endpoint，重算 status
    return {"id": c["id"], "name": ..., "category": ..., "channel_type": ...,
            "strategy": ..., "enabled": ..., "installed": ..., "builtin": ...,
            "status": _recompute_status(c)}   # 不返回 endpoint
```

### 4.2 端点
| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/channels`（`@public_router`） | 公开列出渠道（无 endpoint 泄露） | 无 |
| GET | `/admin/channels`（`@router`） | 管理视图（含 endpoint，供编辑） | admin token |
| POST | `/admin/channels` | 复合新增自定义渠道（yaml 追加 + 校验） | 启用守卫 |
| PUT | `/admin/channels/{cid}` | 改 enabled/endpoint/strategy/description | builtin 不可改 channel_type；启用守卫（enabled=True 且 webhook 且 endpoint 空 → 400） |
| DELETE | `/admin/channels/{cid}` | 内置拒删（仅可停用），自定义复合删 yaml entry | builtin → 400 |
| POST | `/admin/channels/{cid}/test` | 向该渠道发**一条测试消息**（真实 send，验证连通；复用 push 单发） | coming_soon → 400 |

- **复合写**：POST/PUT 走 ruamel round-trip（`_dump_commented`）+ `_atomic_write` + `_write_audit(subdir="channels", src_path=CHANNELS_PATH)`；失败 `_atomic_write` 回滚 yaml。
- **启用守卫**：`enabled=True` 且 `channel_type=webhook` 且 `endpoint` 空 → 400 拒绝（防启用空端点，对应 H2）。
- **builtin 拒删**：`builtin=True` 的 DELETE → 400，仅允许 PUT `enabled=False` 停用。
- **测试端点**：`POST /test` 调 `ch.send({"title":"测试推送","summary":"M9-4 连通性验证","status":"done"})`，返回 `{ok, detail}`。返回码（独立审议 NOTE-3 明确）：`coming_soon` 渠道 → 400 `{message:"coming_soon"}`（诚实，不冒充）；`webhook` 且 `endpoint` 空 → 400 `{message:"endpoint empty"}`（无法测未配置）；`mock` → 200 `{ok:True, detail:"logged..."}`；`webhook` 且 `endpoint` 已填 → 200 真实 POST 结果。
- **CORS**：沿用 M8 已放行的 PUT/DELETE + `X-Admin-Token`（无需新改）。

### 4.3 新增渠道默认 enabled 决策（对齐 M9-3 NOTE-S1 教训）
自定义渠道（`builtin=False`）POST 默认 `enabled=False`（须用户填 endpoint 后主动启用，诚实：未配置不假发）。`mock` 内置默认 `enabled=True`（零配置即验证链路）。

---

## 5. 前端 `web/src/views/Channels.vue`（新建，镜像 Skills.vue / Plugins.vue）

- **路由**：`web/src/router/index.ts` 加 `{ path: '/channels', name: 'Channels', component: Channels, meta:{title:'推送渠道'} }`（M9-3 `/skills` 同法）。
- **侧边栏**：`web/src/layouts/DefaultLayout.vue` 在「技能库」后加「推送渠道」菜单项（图标 `<Promotion />` 或 `<ChatDotRound />`，全局已注册免 import）。
- **页面**：Tabs「渠道配置 / 授权管理」（对应 §3.4 平台文档）；卡片网格（图标 + 名称 + 描述 + 状态标签 + 启停开关 + 端点输入 + 测试发送按钮 + 删除(仅自定义)）。
- **状态标签**：`connected` 绿 / `disconnected` 灰 / `coming_soon` 黄（复用 §6 色板）；coming_soon 卡片禁用操作、显「即将推出」。
- **数据**：`GET /channels`（公开列表）+ `GET/PUT/DELETE /admin/channels` + `POST /admin/channels/:id/test`。
- **类型**：`web/src/types/ui.ts` 加 `ChannelItem` 接口（id/name/icon/category/channel_type/strategy/enabled/installed/builtin/status，注 endpoint 不进公开类型）。

---

## 6. 数据流

```
用户提交研报 → engine_runner.run_engine → orchestrator.run_report
  → graph.invoke → status=done/escalated
  → [M9-4 钩子] push_report(final)
       → load_channels() 热加载
       → event = on_complete( done ) | on_gate_fail( escalated )
       → build_push_targets(channels, event)   # enabled+installed+strategy+非 coming_soon+端点非空
       → 逐个 ch.send(payload)  → delivery_status（best-effort）
  → final 写 outputs/<task_id>.json（含 delivery_status）
  → WS task_done 广播（既有，未改）
```
UI 侧：`GET /channels` 展示状态；`PUT /admin/channels/:id` 改 enabled/endpoint → 下一个任务 `push_report` 即生效（热加载）。

---

## 7. 校验（三道闸 ①）
- `python -m py_compile orchestrator.py tools/push.py server/admin.py` → COMPILE_OK。
- 前端 `npm run build`（或 vite 类型检查）通过（M9 既有流程）。

---

## 8. 热加载（与 M9-1/2/3 同频）
- `push_report` 内 `load_channels()` **每次 run_report 重读** `config/channels.yaml`（与 `build_skill_context`/`load_data_sources`/`load_agent_registry` 一致）→ 改完下一个任务即生效，**无需 watchfiles / 重启**。
- `config/` 已在 M8 bind mount（`./config:/app/config`），UI 改动落盘即引擎可读，不丢（NOTE-1 教训已规避，M9-4 无新增 bind mount，因无 `channels/*.md` 片段目录）。

---

## 9. 验证计划 E1–E7（实施阶段实证，对齐 M9-3 §9）

| # | 验证项 | 方法 | 预期 |
|---|---|---|---|
| E1 | 无 channels.yaml 向后兼容 | 临时移走 channels.yaml 跑 push_report | 返回 []，final 不变，现有测试不受影响 |
| E2 | 启停真改外发 | 禁用 mock → push_report 不落盘；启用 → 落盘 | 控制器效应真 |
| E3 | webhook 真发 | 起本地 http 监听，配 dingtalk endpoint=本地，跑 run_report(done) | 收到 POST（markdown body） |
| E4 | coming_soon 永不发 | 启用 telegram → push_report | delivery_status 无 telegram 条目，不 raise |
| E5 | 端点空诚实跳过 | 启用 dingtalk 但 endpoint="" → push_report | 跳过该渠道（detail 记未配置），不崩 |
| E6 | 真实节点流注入 | monkeypatch StubLLMClient，跑 run_report，断言 final.delivery_status 含 mock 条目（done 事件） | E2E 全链路真触发 |
| E7 | 测试端点 | `POST /admin/channels/mock/test` | 返回 {ok:True, detail:logged...}；coming_soon 测试 → 400 |

> E3/E6 用本地 http.server 接收 webhook POST，断言 body 形状（dingtalk markdown / discord content），证明**非装饰**。

---

## 10. 诚实边界汇总（交付承诺）
- 4 个 webhook 渠道（钉钉/飞书/企业微信/Discord）= **真·可发**（仅需 URL）。
- mock = 真·落盘验证通道（默认开，开箱证明 pipeline）。
- Telegram/微信 = `coming_soon`，UI 标「即将推出」、引擎永不发、不冒充。
- webhook 端点存 config（非密钥 env），无新密钥体系。
- 推送失败 best-effort，仅留痕，不阻断研报。
- 全链路无新 bind mount 需求（channels 无片段文件目录）。

---

## 11. 不做清单（重申，防越界）
- 微信个人号 / 公众号资质接入（coming_soon）
- Telegram bot token 密钥管理（coming_soon，留接口）
- 独立手动推送管道（以 `/test` 端点满足）
- 公网下载链接拼接（payload 自含，全文应用内查看）
- `.secrets/channels.env` 密钥体系（本期无需求）

---

## 12. 提交纪律（edict-gate）
- 三道闸全 PASS 才 commit：`py_compile` + `VERIFICATION_M9-4.md`（设计 §17 + 实施 §18，E1-E7 实证）+ `REVIEW_M9-4.md`（独立子代理落款，主代理不自签）。
- commit：`git commit --only report-agent-team`（先 `git add report-agent-team` 含 untracked），**不 push**（无 boss 显式指令）。
- 交付文件清单：`tools/push.py` / `config/channels.yaml` / `orchestrator.py`(注入) / `server/admin.py`(channels CRUD+test) / `web/src/views/Channels.vue` + 路由 + 侧边栏 + `ui.ts` 类型 / `DESIGN_PLATFORM_FUNCTIONS.md §3.4 标注 ✅` / `DESIGN_M9-4.md` + `VERIFICATION_M9-4.md` + `REVIEW_M9-4.md`。
- 同步 `DESIGN_PLATFORM_FUNCTIONS.md` §3.4 加「✅ M9-4 已交付」交叉引用；路线图表 M9-4 标 ✅。
