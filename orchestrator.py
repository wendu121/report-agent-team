# orchestrator.py · LangGraph 编排器（M3 交付物）
#
# 范围（DESIGN.md v2.3 §9 / M2 约束）：
#   - 真实 LangGraph 状态图：Router → Researcher → GateA → Analyst → GateB → Writer → GateC
#   - 含 rework 回退环（全部 rework 回 Router 中转，全局轮次统一管控）
#   - 含 JSON 容错（解析修复 → 重试一次 → schema 校验失败回退 rework/escalate）
#   - 异基座 Gate（model_mapping.yaml）、引用链机器校验、eval 降级
#   - 工具层（web_search/data_proc/doc_export）M4 才接真实实现，M3 为桩
#   - LLM 调用走可插拔适配器：StubLLMClient（离线演示）/ NewApiLLMClient（new-api 网关）
#
# 纪律：本文件只实现编排/路由/闸/容错，不硬编码任何角色业务规则；
#       角色 prompt 从 agents/*.md 加载，闸规则从 gates/review.md 加载。
# 日期：2026-09-04

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional, Dict, Any, List, Tuple

from langgraph.graph import StateGraph, START, END
from tools import ToolError  # 编排器捕获工具层异常（make_agent Writer 节点的 doc_export 可能抛）
from tools.skills import build_skill_context  # M9-3：技能片段注入对应 Agent 的 system prompt
from tools.push import push_report  # M9-4：研报完成/升级按启用渠道真实外发
from tools.kb_store import kb_retrieve, kb_write  # M11-1：持久知识层
from tools._async_util import run_async  # 共享：同步上下文安全跑协程（kb 节点用）
from scripts.converge_check import check as converge_check, merge_into_state as converge_merge, gaps_to_rework_reason as converge_reason  # M13-Converge：漂移检测


def _utcnow_iso() -> str:
    """审计事件时间戳（与 server/api.py 保持一致：UTC ISO 字符串）。

    选型 A（boss 2026-09-06 裁定）：以 server/api.py 的 Pydantic 模型为权威契约，
    orchestrator 侧补齐 gate_review_history / engine_events 缺失字段；不放宽 server 校验、
    不使用 construct()/extra='ignore' 兼容残缺数据，防止后续继续产出残缺状态。
    """
    return datetime.utcnow().isoformat()


# ----------------------------------------------------------------------------
# 角色注册表（M9-1）：原硬编码 4 字典（PROD_KEY/TOOL/GATE_NAME/GATE_REVIEWS）
# 已外置为 config/agents_library.yaml，由 load_agent_registry() 每次 run 热加载
# （mirror load_model_mapping，零重启）。以下 DEFAULT_REGISTRY 仅作「yaml 缺失」
# 时的兜底，保证向后兼容（agents=None → 全 3 内置）。
# ----------------------------------------------------------------------------
DEFAULT_REGISTRY = {
    "agents": {
        "Researcher": {"id": "Researcher", "shape": "researcher", "tool": "web_search",
                        "output_key": "retrieval_records", "gate": "GateA", "builtin": True},
        "Analyst": {"id": "Analyst", "shape": "analyst", "tool": "data_proc",
                    "output_key": "analysis_conclusions", "gate": "GateB", "builtin": True},
        "Writer": {"id": "Writer", "shape": "writer", "tool": "doc_export",
                   "output_key": "draft_segments", "gate": "GateC", "builtin": True},
    }
}

BASE = Path(__file__).parent


# ----------------------------------------------------------------------------
# 异常
# ----------------------------------------------------------------------------
class LLMError(Exception):
    """LLM 调用层异常（网络/限流/超时等）。"""


class JSONRepairError(Exception):
    """模型输出无法修复为合法 JSON。"""


# ----------------------------------------------------------------------------
# State 定义（对齐 memory/schema.md §1-§4）
# ----------------------------------------------------------------------------
class ReportState(TypedDict, total=False):
    user_task: dict
    retrieval_records: list            # Researcher 写
    analysis_conclusions: list         # Analyst 写
    draft_segments: list               # Writer 写
    report_markdown: str               # Writer 写
    tool_status: list                  # 工具调用结果（M4 起由引擎真实写入）
    prior_versions: dict               # 仅审计：每轮完整产出快照
    search_results: list               # M4：本轮引擎真实检索到的原始素材（溯源校验 + 审计）
    report_path: Optional[str]         # M4：doc_export 落盘路径（未导出则为 None）
    routing_state: dict                # 单一收敛路由状态
    gate_output: Optional[dict]        # 最新一道闸（或 Agent 解析失败）输出，Router 读取依据
    agent_result: Optional[str]        # 引擎瞬态信号："ok" | "parse_fail"
    kb_context: list                   # M11-1：KBRetrieve 从知识库召回的历史结论，注入 Researcher
    gap_tasks: list[dict]              # M13-Converge：漂移检测生成的差距任务（append-only）


# ----------------------------------------------------------------------------
# LLM 适配器
# ----------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """通用近似 token 计数（不绑定特定模型 tokenizer，中英文混合稳健）。

    用于输入预算闸：new-api 网关背后模型上下文窗口各异，超窗即 400
    `input length too long`。精确计数需各模型 tokenizer（本地无），故用启发式：
      - CJK（中日韩统一表意 + 全角符号）~1.6 token/字；
      - 拉丁词（连续 [A-Za-z0-9]）~0.25 token/词；
      - 其余字符 ~0.3 token/字符。
    误差对「是否超窗」判断足够，且下方 complete() 还有 input-too-long 自愈兜底。
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
    latin_chars = len(re.findall(r"[A-Za-z0-9]", text))
    other = len(text) - cjk - latin_chars
    return int(cjk * 1.6 + other * 0.3 + latin_words * 0.25) + 1


def _trunc_keep_tail(text: str, max_tokens: int) -> str:
    """按 token 近似从**头部**裁，保留尾部（最近轮对话 / 最近一次工具结果）。

    对话历史/工具结果累积在 user blob 前部，最新内容在尾部——丢旧保新。
    """
    if estimate_tokens(text) <= max_tokens:
        return text
    ratio = max(0.0, max_tokens) / max(1, estimate_tokens(text))
    keep = max(0, int(len(text) * ratio))
    return text[-keep:] + "\n…[已截断以适配上下文窗口]"


def fit_input_budget(system: str, user: str, max_input_tokens: int) -> tuple:
    """把 system+user 裁到 max_input_tokens 以内（防 400）。

    顺序：① 优先裁 user（含历史/工具结果，可丢旧保新）；② 仍超限再裁 system
    （保留头部指令）。返回 (system, user)。与具体模型/网关无关 → 通用 OpenAI 兼容。
    """
    sys_t = estimate_tokens(system)
    usr_t = estimate_tokens(user)
    if sys_t + usr_t <= max_input_tokens:
        return system, user
    usr_budget = max(0, max_input_tokens - sys_t)
    # 以 user 自身是否超预算为准裁 user（不依赖 system 是否非空）。
    # 旧实现用 `usr_budget < max_input_tokens`（等价于 sys_t>0）作门，system 为空时漏裁 → 400 无法自愈。
    if usr_t > usr_budget:
        user = _trunc_keep_tail(user, usr_budget)
    if sys_t > max_input_tokens:  # system 本身超限，裁 system（保头部）
        sys_budget = max(1, max_input_tokens)
        system = system[: max(0, int(len(system) * (sys_budget / max(1, sys_t))))] \
            + "\n…[system 已截断]"
    return system, user


# new-api 等网关对超窗的报错形态不一，统一识别「输入超上下文窗口」类错误以便自愈重试。
_INPUT_TOO_LONG_RE = re.compile(
    r"input length|context length|too (long|many) token|maximum context|"
    r"exceed.*context|context.*exceed|prompt is too long|token.*exceed",
    re.I,
)


def _is_input_too_long(exc: Exception) -> bool:
    return bool(_INPUT_TOO_LONG_RE.search(str(exc)))


class LLMClient:
    """所有 LLM 调用经此接口；具体实现可替换（new-api 网关 / 离线 stub）。"""

    def complete(self, model: str, system: str, user: str, **kw) -> str:
        raise NotImplementedError

    def complete_with_tools(self, model: str, system: str, user: str,
                            tools: list, **kw) -> dict:
        """M11-3 D1-B function-calling：返回 {content, tool_calls}。

        tool_calls 为 OpenAI 格式列表（每项 function.name/arguments）。
        基类默认不实现；真实生产走 NewApiLLMClient，离线测试走 StubLLMClient。
        """
        raise NotImplementedError


class StubLLMClient(LLMClient):
    """离线演示用：依据**产出形态 shape** 产出合法 JSON，可模拟 rework 场景验证回退环。

    M9-1：不再按角色名硬编码分支（否则自定义 Agent 只能拿到 "{}" 而解析失败，
    与引擎侧刚移除的 role 硬编码是同一反模式）。调用方以 kw 传入
    `kind`（agent/gate/eval）与 `shape`（researcher/analyst/writer），
    真实 LLMClient 忽略这些额外 kw，不影响生产路径。

    scenario="pass"  → 全链路 advance 直达终稿
    scenario="rework"→ 首次 GateA 返回 rework，触发一次 rework 后恢复
    """

    def __init__(self, scenario: str = "pass"):
        self.scenario = scenario
        self._gate_a_calls = 0
        self.fc = False  # M11-3：function-calling 模拟开关（默认关，保持旧测试行为）

    @staticmethod
    @staticmethod
    def _extract_injected_records(user: str) -> list[dict]:
        """从 user prompt 中回读引擎注入的真实检索结果（模拟"只从给定素材筛选"）。

        ⚠️ 必须**字符串感知**地配对中括号：检索素材的正文里常含 `[Free]`、
        `[12.5%]`、`[2025]` 这类方括号（如 accio_tariff 关税明细），
        朴素的 depth 计数会在**字符串内部**误增减，导致把一个合法 JSON 数组
        截断成非法片段 → 解析恒失败 → Researcher 误判"检索失败"。
        故扫描时须跳过 JSON 字符串字面量（含 \\" 转义）。
        """
        i = 0
        while True:
            i = user.find("[", i)
            if i == -1:
                return []
            depth = 0
            in_str = False
            esc = False
            for j in range(i, len(user)):
                ch = user[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            arr = json.loads(user[i:j + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(arr, list) and arr and all(
                                isinstance(x, dict) and "url" in x for x in arr):
                            return arr
                        break
            i += 1

    def complete(self, model: str, system: str, user: str, **kw) -> str:
        role = kw.get("role")
        shape = kw.get("shape")
        kind = kw.get("kind")
        if role == "Eval":
            return "0.8"
        if kind == "gate":
            if role == "GateA":
                self._gate_a_calls += 1
                if self.scenario == "rework" and self._gate_a_calls == 1:
                    return json.dumps({
                        "decision": "rework",
                        "reason": "检索记录未覆盖 user_task.scope 中的子问题。",
                        "eval_score": 0.4,
                        "problem_points": ["检索记录未逐条回应 scope 子问题（如缺'竞争格局'维度）"],
                    }, ensure_ascii=False)
            return self._gate_advance()
        if shape is None:  # 兼容未传 shape 的旧调用
            shape = {"Researcher": "researcher", "Analyst": "analyst", "Writer": "writer"}.get(role)
        if shape == "researcher":
            # M4：检索由引擎执行，stub 只能从注入结果中回显，不得自造来源，
            # 否则会被 Agent 节点 / GateA 的溯源硬校验判为编造引用。
            recs = self._extract_injected_records(user)
            if not recs:
                # 检索失败：如实返回空数组，交由 GateA 判 escalate（不编造）
                return json.dumps({"retrieval_records": [], "tool_status": [],
                                   "change_note": "本轮未取到任何资料，如实输出空数组"},
                                  ensure_ascii=False)
            return json.dumps({
                "retrieval_records": [
                    {"id": r.get("id"), "url": r.get("url"), "title": r.get("title"),
                     "snippet": r.get("snippet"), "credibility": r.get("credibility"),
                     "source": r.get("source")}
                    for r in recs
                ],
                "tool_status": [{"agent": role or shape, "tool": "web_search", "ok": True}],
                "change_note": "",
            }, ensure_ascii=False)
        if shape == "analyst":
            return json.dumps({
                "analysis_conclusions": [
                    {"id": "con-1", "claim": "供给端产能吃紧推高价格。",
                     "source_ids": ["rec-1", "rec-2"], "confidence": "high"},
                ],
                # 声明一次计算请求，用于验证 data_proc 真实执行（M4）
                "tool_requests": [{"expr": "mean([1, 2, 3, 4])"}, {"expr": "12 * (3 + 4) / 2"}],
                "tool_status": [{"agent": role or shape, "tool": "data_proc", "ok": True}],
                "change_note": "",
            }, ensure_ascii=False)
        if shape == "writer":
            return json.dumps({
                "draft_segments": [
                    {"id": "seg-1", "section": "市场概况", "content": "AI 芯片市场供需两旺。",
                     "conclusion_ids": ["con-1"]},
                ],
                "report_markdown": "# AI 芯片市场研报\n\n## 市场概况\nAI 芯片市场供需两旺。\n\n## 引用 / 来源\n- [rec-1](https://example.com/supply)\n- [rec-2](https://example.com/demand)\n",
                "tool_status": [{"agent": role or shape, "tool": "doc_export", "ok": True}],
                "change_note": "",
            }, ensure_ascii=False)
        return "{}"

    @staticmethod
    def _gate_advance() -> str:
        return json.dumps({
            "decision": "advance",
            "reason": "产出满足全部校验标准。",
            "eval_score": 0.85,
            "problem_points": ["无"],
        }, ensure_ascii=False)

    def complete_with_tools(self, model: str, system: str, user: str,
                            tools: list, **kw) -> dict:
        """M11-3 D1-B：function-calling 入口（stub 模拟）。

        - `tools` 为空 → 等同 `complete`（不触发工具循环，旧测试保持绿色）。
        - `self.fc` 为 False → 等同 `complete`（旧 stub 行为不变，直接出最终 JSON）。
        - `self.fc` 为 True 且 researcher/analyst：首轮发 1 个工具调用，
          次轮（user 已含【工具执行结果】）出最终 JSON。writer/gate/eval 直接出。
        """
        if not tools or not self.fc:
            return {"content": self.complete(model, system, user, **kw), "tool_calls": []}
        role = kw.get("role")
        shape = kw.get("shape") or {"Researcher": "researcher", "Analyst": "analyst",
                                    "Writer": "writer"}.get(role)
        if shape not in ("researcher", "analyst"):
            return {"content": self.complete(model, system, user, **kw), "tool_calls": []}
        # 次轮：工具结果已回注 → 直接出最终 JSON
        if "【工具执行结果】" in user:
            return {"content": self.complete(model, system, user, **kw), "tool_calls": []}
        # 首轮：挑第一个非 web_search 工具（analyst 优先 data_proc/mcp），否则 web_search
        fc_tool = next((t for t in tools
                        if t.get("function", {}).get("name") not in ("web_search",)), None)
        name = (fc_tool or {}).get("function", {}).get("name") or "web_search"
        if name == "web_search":
            args = {"queries": ["关于用户任务的检索词"]}
        elif name == "data_proc":
            args = {"requests": [{"expr": "mean([1, 2, 3, 4])"}, {"expr": "12 * (3 + 4) / 2"}]}
        else:  # mcp:{server}:{tool}
            args = {"arg1": "demo"}
        return {
            "content": "",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        }


class StubLLMClientFC(StubLLMClient):
    """M11-3 专用：开启 function-calling 模拟（首轮发 tool_call，次轮出 JSON）。"""

    def __init__(self, scenario: str = "pass"):
        super().__init__(scenario)
        self.fc = True


# 「重试无用」的错误特征：配额/额度耗尽、鉴权失败、模型不存在、无可用渠道。
# 这类错误重试纯属浪费时间，且配额型 429 每重试一次都在烧本就见底的额度
# （M5 实跑教训：google 免费配额 20 次耗尽后，闸仍重试 3 次，全部无效）。
_NON_RETRYABLE_PATTERNS = (
    "quota exceeded", "insufficient_quota", "exceeded your current quota",
    "配额", "额度",
    "invalid token", "invalid api key", "incorrect api key", "unauthorized",
    "model_not_found", "model not found", "no available channel", "没有可用渠道",
)


def _is_non_retryable(e: Exception) -> bool:
    """区分『配额耗尽型 429 / 鉴权 / 模型不存在』与『瞬断型 429 / 超时 / 掉线』。

    - 瞬断型（Rate limit、连接重置、超时）→ **应重试**，稍后即恢复；
    - 配额耗尽型（Quota exceeded）→ **不可重试**，重试无用且烧剩余额度，须换通道。
    """
    msg = f"{type(e).__name__}: {e}".lower()
    return any(p in msg for p in _NON_RETRYABLE_PATTERNS)


# ----------------------------------------------------------------------------
# LibreChat 借鉴：dropParams —— 按端点/接口剔除上游不支持的请求体参数
# ----------------------------------------------------------------------------
# 背景：new-api 网关背后挂着 26+ 上游通道，各家对 OpenAI 请求体的兼容度参差不齐。
# 挑剔的实现会为**一个多余的参数**把整个请求 400 掉（"unsupported parameter"、
# "Unknown parameter"、"Additional properties are not allowed"），本引擎因此整体失败。
# LibreChat 的解法是**静态**按端点声明 `dropParams`；我们在静态配置之上再加一层
# 「一次性自愈」——因为同一 interface 背后的上游随时会被网关切走，静态清单永远列不全。
#
# 注意：这里只处理**请求体参数**。`timeout` 是 OpenAI SDK 的客户端级选项，永远不在此列。
_UNSUPPORTED_PARAM_RE = re.compile(
    r"(unsupported|unknown|unexpected|invalid|unrecognized)[ _-]?param|"
    r"additional propert(y|ies)[^.]{0,60}not allowed|"
    r"unrecognized request argument|"
    r"不支持的参数",
    re.I,
)
# 自动自愈只敢剔除「不带语义」的参数。
# **绝不包括 tools / tool_choice**：那会把一次 function-calling 调用悄悄降级成普通对话，
# 模型随即凭空作答而没有工具依据 —— 这正是 boss 最恨的「编造数据」形态。
# 所以上游若明说不支持 tools，必须**响亮失败**，由人改配置/换通道，而不是静默降级。
_AUTO_HEALABLE = frozenset((
    "temperature", "top_p", "top_k", "presence_penalty", "frequency_penalty",
    "stop", "max_tokens", "max_completion_tokens", "seed", "logprobs", "top_logprobs",
    "n", "stream", "user", "response_format",
))
_QUOTED_TOKEN_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]{1,40})['\"]")


def _extract_bad_params(msg: str) -> List[str]:
    """从上游错误信息里摘出被拒的参数名；摘不出来就返回 [] —— 不猜、不瞎删。

    只认可已知参数名清单内的词，避免把报错文案里随便一个引号词当参数名删掉。
    """
    found: List[str] = []
    for tok in _QUOTED_TOKEN_RE.findall(msg or ""):
        t = tok.strip()
        if t in _AUTO_HEALABLE and t not in found:
            found.append(t)
    if found:
        return found
    m = re.search(r"(?:param|parameter|argument|propert)[^\w]{0,12}"
                  r"['\"]?([A-Za-z_][A-Za-z0-9_]{1,40})", msg or "", re.I)
    if m and m.group(1) in _AUTO_HEALABLE:
        return [m.group(1)]
    return []


class NewApiLLMClient(LLMClient):
    """通用 OpenAI 兼容 LLM 客户端（支持多端点，DESIGN_OPENAI_ENDPOINTS.md）。

    每个 interface（模型接口）指向一个 endpoint + model_id；complete() 调用时按
    所选 model（interface id 或 raw model_id）动态解析端点并用对应 OpenAI 客户端发起请求。
    密钥/故障转移/负载由网关处理（DESIGN §3）；本类只负责：
      - 端点解析（interface → endpoint + model_id）；
      - OpenAI 客户端缓存（按 endpoint_id）；
      - complete()：带指数退避重试（flaky 网关生产环境频繁 429/掉线，需自愈）。
    """

    def __init__(
        self,
        endpoints: Optional[List[Dict[str, Any]]] = None,
        interfaces: Optional[List[Dict[str, Any]]] = None,
        default_endpoint_id: Optional[str] = None,
        max_retries: int = 2,
        timeout: int = 120,
        max_input_tokens: int = 128000,
        reserved_output_tokens: int = 4096,
    ):
        self._endpoints = {e["id"]: e for e in (endpoints or [])}
        self._interfaces = {i["id"]: i for i in (interfaces or [])}
        self._default_endpoint_id = default_endpoint_id
        self._clients: Dict[str, Any] = {}   # endpoint_id -> OpenAI client cache
        self._max_retries = max_retries
        self._timeout = timeout
        # 输入预算闸：单次请求输入 token 上限（模型上下文窗口 - 预留输出）。
        # 默认 128k 为通用安全值；若网关背后模型窗口更小，下方 complete() 的
        # input-too-long 自愈会把预算逐级减半重试，无需先知窗口大小即通用可用。
        # 可通过 models.yaml defaults.max_input_tokens 或 LLM_MAX_INPUT_TOKENS 覆盖。
        self._max_input_tokens = int(os.getenv("LLM_MAX_INPUT_TOKENS", max_input_tokens))
        self._reserved_output_tokens = reserved_output_tokens

    def _resolve(self, model: str) -> Tuple[str, str, Dict[str, Any]]:
        """解析 model（interface id 或 raw model_id）→ (endpoint_id, real_model_id, endpoint_cfg)。

        规则：
          - 命中 interface 且 interface 指定 endpoint → 用该 endpoint，real_model = interface.model_id（缺省回退 model 本身）。
          - 未命中 interface（向后兼容：旧 model_mapping.yaml 直接写模型名）→ 走 default endpoint + raw model_id。
        """
        iface = self._interfaces.get(model)
        if iface and iface.get("endpoint"):
            eid = iface["endpoint"]
            real = iface.get("model_id") or model
            cfg = self._endpoints.get(eid)
            if cfg is not None:
                return eid, real, cfg
            # interface 指向的 endpoint 不存在 → 诚实报错（不静默回落，避免误调错端点）
            raise LLMError(f"模型接口 '{model}' 指向的端点 '{eid}' 未在 endpoints 中定义")
        eid = self._default_endpoint_id
        if eid and eid in self._endpoints:
            return eid, model, self._endpoints[eid]
        raise LLMError(f"无法解析模型 '{model}'：未找到对应接口或默认端点")

    def _get_client(self, endpoint_id: str):
        if endpoint_id not in self._clients:
            from openai import OpenAI  # 延迟导入，避免无网络环境强制依赖
            cfg = self._endpoints[endpoint_id]
            if not cfg.get("api_key"):
                raise LLMError(
                    f"端点 '{endpoint_id}' 缺少 api_key，请在 .env 注入对应密钥")
            # base_url 兜底：new-api 等 OpenAI 兼容网关的 API 根路径必须带 /v1，
            # 否则请求会拼成 host:3000/chat/completions 落到仪表盘 SPA 兜底 HTML
            # （实测表现为 ChatAgent 把 new-api 登录页当答案返回）。此处强制归一化，
            # 兼容「配置漏写 /v1」与「已带 /v1」两种写法，杜绝静默打到仪表盘。
            raw = str(cfg["base_url"] or "").rstrip("/")
            if not raw:
                raise LLMError(f"端点 '{endpoint_id}' 的 base_url 为空，请在 .env 注入 NEWAPI_BASE_URL")
            base_url = raw if raw.endswith("/v1") else raw + "/v1"
            # timeout：单次请求超时（秒）。flaky 网关下慢/假死上游会无限挂起，
            # OpenAI SDK 默认 600s，故显式收紧到 120s，挂死即快速失败→重试/escalate。
            self._clients[endpoint_id] = OpenAI(
                base_url=base_url,
                api_key=str(cfg["api_key"]),
                timeout=self._timeout,
            )
        return self._clients[endpoint_id]

    def _iface_cfg(self, model: str) -> Dict[str, Any]:
        """取 interface 配置（未命中返回 {}）。不改动 _resolve() 的既有返回值形态。"""
        return self._interfaces.get(model) or {}

    @staticmethod
    def _as_param_list(raw: Any) -> List[str]:
        """宽容解析：接受 list[str] 或 'a, b, c' 字符串；非法/空值返回 []，不抛。"""
        if not raw:
            return []
        if isinstance(raw, str):
            raw = [p for p in re.split(r"[,\s]+", raw) if p]
        if not isinstance(raw, (list, tuple, set)):
            return []
        return [str(p).strip().lower() for p in raw if str(p).strip()]

    def _drop_params(self, model: str, endpoint_id: str) -> set:
        """解析要剔除的**请求体参数**：interface > endpoint > 环境变量 LLM_DROP_PARAMS。

        借鉴 LibreChat 的 mcpServers/endpoints `dropParams` 设计：慢的/挑剔的通道单独放宽，
        而不是全站一刀切。默认不配 ⇒ 返回空集 ⇒ 行为与改动前完全一致。
        ⚠️ 判空必须用**真值**而非 `is not None`：loader 对未配置的端点/接口写入的是空列表
        []（而非 None），用 is-not-None 判断会把 [] 当成"已显式配置为空"，从而挡住
        向下 fallback，让 endpoint 级与环境变量级永远失效（自埋的配置黑洞）。
        """
        for src in (self._iface_cfg(model), self._endpoints.get(endpoint_id) or {}):
            raw = src.get("drop_params")
            if raw:  # 空 = 未配置 → 继续向下一级找
                return set(self._as_param_list(raw))
        return set(self._as_param_list(os.getenv("LLM_DROP_PARAMS", "")))

    def preflight(self) -> dict:
        """连通性预检（默认端点）。失败抛 LLMError（不改写，交由调用方决定）。"""
        if not self._default_endpoint_id:
            raise LLMError("无默认端点，无法做连通性预检")
        client = self._get_client(self._default_endpoint_id)
        try:
            resp = client.models.list()
            ids = [m.id for m in getattr(resp, "data", [])] if resp else []
            return {"ok": True, "base_url": self._endpoints[self._default_endpoint_id]["base_url"],
                    "models": ids}
        except Exception as e:  # 网络/鉴权/网关未起
            raise LLMError(f"网关预检失败({self._default_endpoint_id}): {e}") from e

    def complete(self, model: str, system: str, user: str, **kw) -> str:
        endpoint_id, real_model, _ = self._resolve(model)
        client = self._get_client(endpoint_id)
        drop = self._drop_params(model, endpoint_id)
        body = {"temperature": kw.get("temperature", 0.2)}
        last: Exception | None = None
        healed = False
        logger = logging.getLogger(__name__)
        factor = 1.0  # 输入预算因子：遇 input-too-long 逐级减半自愈（通用，无需先知窗口）
        halvings = 0
        # 超窗减半走**独立且有界**的预算（最多 3 次：1.0→0.5→0.25→0.125，且 1/8 真会被发出）。
        # 绝不放大通用重试次数——那会破坏「自愈有界、持续报错必须最终失败」的语义：
        # 曾把总次数无脑放宽到 >=4，导致 test_heal_bounded_to_once 由绿转红（fake 第 4 次返回成功，不再抛错）。
        _MAX_HALVINGS = 3
        attempt = 0
        while attempt <= self._max_retries + halvings:
            attempt += 1
            cur_max = max(1, int(self._max_input_tokens * factor))
            budget = max(1, cur_max - self._reserved_output_tokens)  # 防 reserved>上限致负预算
            sys_t, usr_t = fit_input_budget(system, user, budget)
            params = {k: v for k, v in body.items() if k.lower() not in drop}
            try:
                resp = client.chat.completions.create(
                    model=real_model,
                    messages=[
                        {"role": "system", "content": sys_t},
                        {"role": "user", "content": usr_t},
                    ],
                    timeout=self._timeout,
                    **params,
                )
                content, _ = _normalize_completion(resp)
                if not content:
                    raise LLMError("模型返回空内容")
                return content
            except Exception as e:  # 网络/限流/超时统一转 LLMError 并重试
                last = e
                msg = str(e)
                # 输入超上下文窗口：减半预算重裁重试（factor 最低 0.125，即 1/8），
                # 不依赖具体模型窗口大小即通用可用（new-api 背后 26+ 通道窗口各异）。
                if _is_input_too_long(e) and halvings < _MAX_HALVINGS:
                    factor *= 0.5
                    halvings += 1
                    logger.warning(
                        "LLM 输入超上下文窗口，已减半输入预算重试（factor=%.3f，第%d次减半，模型=%s 端点=%s）：%s",
                        factor, halvings, real_model, endpoint_id, msg[:160])
                    continue
                # 自愈须**先于** _is_non_retryable 判定：400 类报错若先走黑名单/穷举路径，
                # 这段就有可能成为永远执行不到的死代码（哪怕当前 pattern 未覆盖 400 也要显式保序）。
                if (not healed and _UNSUPPORTED_PARAM_RE.search(msg)
                        and (bad := [p for p in _extract_bad_params(msg)
                                     if p in _AUTO_HEALABLE])):
                    healed = True          # 每次调用最多自愈一次，杜绝被异常上游拖进无限重试
                    drop |= set(bad)
                    logger.warning(
                        "LLM 调用被上游拒绝（不支持的参数 %s），已自动剔除并重试一次；"
                        "若该通道长期如此，请在 interface/endpoint 配置里显式加 drop_params。"
                        "模型=%s 端点=%s", ",".join(bad), real_model, endpoint_id)
                    continue
                # 配额耗尽 / 鉴权 / 模型不存在：重试无用（且配额型会烧额度）→ 立即放弃
                if _is_non_retryable(e):
                    raise LLMError(f"LLM 调用失败(不可重试，须换通道或修配置): {e}") from e
                if attempt <= self._max_retries:  # attempt 已在循环顶部自增，等价于原 attempt < max_retries
                    time.sleep(2 ** (attempt - 1))  # 退避
                    continue
        raise LLMError(f"LLM 调用失败(已重试 {self._max_retries} 次): {last}") from last

    def complete_with_tools(self, model: str, system: str, user: str,
                            tools: list, **kw) -> dict:
        """M11-3 D1-B：带 tools 的 chat 调用，返回 {content, tool_calls}。

        仅把 OpenAI SDK 的 message.tool_calls 原样转出（dict 列表），由编排器 dispatch。
        """
        endpoint_id, real_model, _ = self._resolve(model)
        client = self._get_client(endpoint_id)
        drop = self._drop_params(model, endpoint_id)
        body = {"temperature": kw.get("temperature", 0.2),
                "tools": tools, "tool_choice": "auto"}
        last: Exception | None = None
        healed = False
        logger = logging.getLogger(__name__)
        factor = 1.0  # 输入预算因子：遇 input-too-long 逐级减半自愈
        halvings = 0
        # 同 complete()：减半走独立有界预算，绝不放大通用重试次数。
        _MAX_HALVINGS = 3
        attempt = 0
        while attempt <= self._max_retries + halvings:
            attempt += 1
            cur_max = max(1, int(self._max_input_tokens * factor))
            budget = max(1, cur_max - self._reserved_output_tokens)  # 防 reserved>上限致负预算
            sys_t, usr_t = fit_input_budget(system, user, budget)
            params = {k: v for k, v in body.items() if k.lower() not in drop}
            try:
                resp = client.chat.completions.create(
                    model=real_model,
                    messages=[
                        {"role": "system", "content": sys_t},
                        {"role": "user", "content": usr_t},
                    ],
                    timeout=self._timeout,
                    **params,
                )
                content, calls = _normalize_completion(resp)
                return {"content": content, "tool_calls": calls}
            except Exception as e:
                last = e
                msg = str(e)
                # 输入超上下文窗口：减半预算重试（tools/tool_choice 绝不裁——那是把有工具
                # 依据的调用降成凭空作答，等同制造幻觉；只裁 system/user 文本）。
                if _is_input_too_long(e) and halvings < _MAX_HALVINGS:
                    factor *= 0.5
                    halvings += 1
                    logger.warning(
                        "LLM 工具调用输入超上下文窗口，已减半输入预算重试（factor=%.3f，第%d次减半，模型=%s 端点=%s）：%s",
                        factor, halvings, real_model, endpoint_id, msg[:160])
                    continue
                # 同 complete()：自愈先于黑名单判定；且**绝不自动剔除 tools/tool_choice**
                # （那是把有工具依据的调用悄悄降成凭空作答，等同制造幻觉）。
                if (not healed and _UNSUPPORTED_PARAM_RE.search(msg)
                        and (bad := [p for p in _extract_bad_params(msg)
                                     if p in _AUTO_HEALABLE])):
                    healed = True
                    drop |= set(bad)
                    logger.warning(
                        "LLM 工具调用被上游拒绝（不支持的参数 %s），已自动剔除并重试一次。"
                        "模型=%s 端点=%s", ",".join(bad), real_model, endpoint_id)
                    continue
                if _is_non_retryable(e):
                    raise LLMError(f"LLM 工具调用失败(不可重试): {e}") from e
                if attempt <= self._max_retries:  # attempt 已在顶部自增，等价于原 attempt < max_retries
                    time.sleep(2 ** (attempt - 1))
                    continue
        raise LLMError(f"LLM 工具调用失败(已重试 {self._max_retries} 次): {last}") from last


# ----------------------------------------------------------------------------
# JSON 容错（memory/schema.md §0-5：M3 引擎层责任）
# ----------------------------------------------------------------------------
def robust_json_load(text: str):
    """尝试解析 JSON；失败则做有限修复（去围栏/抽取首个 {...} 或 [...]）。"""
    if not isinstance(text, str):
        raise JSONRepairError("输入非字符串")
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 去 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 逐个候选起点做括号配对，取首个可解析结构。
    # 注意：须按"出现位置最早"选取候选（先扫到 '[' 就先试数组），
    # 否则 '前缀 [{"id":"rec-1"}] 后缀' 会被截成内层对象而丢失数组语义。
    for i, ch in enumerate(s):
        if ch not in "{[":
            continue
        close = "}" if ch == "{" else "]"
        depth = 0
        for j in range(i, len(s)):
            if s[j] == ch:
                depth += 1
            elif s[j] == close:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[i:j + 1])
                    except json.JSONDecodeError:
                        break
    raise JSONRepairError("无法从模型输出中提取合法 JSON")


# ----------------------------------------------------------------------------
# LLM 响应归一化（防御 flaky 网关返回非 OpenAI 结构）
# ----------------------------------------------------------------------------
def _tool_call_to_dict(tc, i: int) -> dict:
    """把 OpenAI message.tool_calls[i]（pydantic）转为 {id,type,function{name,arguments}}。"""
    fn = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", f"call_{i}"),
        "type": "function",
        "function": {
            "name": getattr(fn, "name", "") if fn else "",
            "arguments": getattr(fn, "arguments", "{}") if fn else "{}",
        },
    }


def _completion_from_dict(data: dict) -> tuple:
    """从已解析的 OpenAI 结构 dict 抽取 (content, tool_calls)。"""
    choices = data.get("choices") or []
    if not choices:
        # 部分网关把 content 放顶层
        if "content" in data:
            return data.get("content") or "", []
        return "", []
    msg = choices[0].get("message", {}) or {}
    content = msg.get("content") or ""
    tcs = msg.get("tool_calls") or []
    calls = []
    for i, tc in enumerate(tcs):
        fn = tc.get("function") or {}
        calls.append({
            "id": tc.get("id", f"call_{i}"),
            "type": "function",
            "function": {"name": fn.get("name", ""), "arguments": fn.get("arguments", "{}")},
        })
    return content, calls


def _normalize_completion(resp):
    """把 LLM 原始响应归一化为 (content:str, tool_calls:list[dict])。

    防御 new-api 等网关在 tools 请求下返回**非 OpenAI 结构**的体（原始 str / dict / 缺 choices）：
    此前 `resp.choices` 在 str 上抛 AttributeError，导致 ChatAgent 整轮失败
    （报“脑子打结了：'str' object has no attribute 'choices'”）。此处健壮处理：
      - 标准 ChatCompletion 对象（含 .choices）→ 原路径；
      - dict（已解析的 OpenAI 结构）→ 抽取；
      - str → 尝试 json.loads 还原为 dict；仍失败则当作模型/网关直答文本返回（**不再崩**）。
    """
    # 1) 标准 pydantic ChatCompletion 对象
    if hasattr(resp, "choices"):
        msg = resp.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        return msg.content or "", [_tool_call_to_dict(tc, i) for i, tc in enumerate(tcs)]
    # 2) 已是 dict
    if isinstance(resp, dict):
        return _completion_from_dict(resp)
    # 3) 原始字符串（网关可能对 tools 请求回了纯文本/错误串）
    if isinstance(resp, str):
        s = resp.strip()
        logger.warning("LLM 返回非标准(str)响应，前 300 字: %r", s[:300])
        try:
            data = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            # 当模型/网关直答文本处理（不再崩，交由上层呈现或闸判）
            return s, []
        return _completion_from_dict(data)
    raise LLMError(f"LLM 返回无法解析的响应类型: {type(resp).__name__}")


# ----------------------------------------------------------------------------
# Prompt 加载（角色/闸 prompt 由 md 文件加载，非硬编码）
# ----------------------------------------------------------------------------
def _load_md(rel: str) -> str:
    """按当前账号命名空间加载 prompt 片段（相对租户根）。"""
    try:
        from server import tenancy
        return tenancy.tenant_path(rel).read_text(encoding="utf-8")
    except ImportError:
        # 无 server 包（纯 CLI/单测）时回落仓库根，保持旧行为
        return (BASE / rel).read_text(encoding="utf-8")


def build_agent_system(role: str, reg: Optional[dict] = None) -> str:
    if reg is None:
        reg = load_agent_registry()
    body = _load_md(f"agents/{role.lower()}.md")
    return (
        body
        + "\n\n【输出格式硬约束】你只能输出一个 JSON 对象，必须包含生产数组字段（"
        + reg["agents"][role]["output_key"]
        + "）、tool_status、rework 时含 change_note。禁止输出 JSON 以外的任何解释文字。"
    )


def build_agent_user(user_task: dict, rework_reason: Optional[str]) -> str:
    u = json.dumps(user_task, ensure_ascii=False)
    if rework_reason:
        return f"user_task:\n{u}\n\n【rework 定向修正】上一道闸指出：{rework_reason}\n请全量替换产出并输出 change_note 说明改动。"
    return f"user_task:\n{u}\n请产出结构化 JSON。"


def build_gate_system(gate_name: str, role: str) -> str:
    """M9-1：per-gate prompt 文件，缺失回落共享 gates/review.md。

    内置 3 闸（GateA/B/C）无独立文件，沿用 gates/review.md（现状不变）；
    自定义闸（如 GateD）由 admin 复合写生成 gates/<gate_name>.md，此处优先加载，
    避免污染 review.md（DESIGN_M9-1 §3.4 / R1）。
    """
    from server import tenancy  # noqa: E402  （延迟导入，避免 server 包缺失时引擎 CLI 不可用）

    per_gate = tenancy.tenant_path("gates", f"{gate_name}.md")
    body = per_gate.read_text(encoding="utf-8") if per_gate.exists() else _load_md("gates/review.md")
    return (
        body
        + f"\n\n你现在执行 {gate_name}，审核 {role} 的产出。只输出 gate_output JSON，禁止多余文字。"
    )


# TD-005：Gate 审核上下文补充 retrieval_records（设计文档 TD-005_GATE_CONTEXT_DESIGN.md）
# 根因：Agent 生产侧注入了「唯一可引用的来源集合」（见 analyst prompt 构建处），
# 而 build_gate_user 只给 gate_name + 待审产出 + user_task，审核方看不到记录，
# 只能臆断 source_ids「不存在」→ 必然 rework。此处补齐审核侧上下文。
# 决议（boss 2026-09-06 评审通过）：D1=S2 仅 GateB / D2=F2 id+title+credibility
#                                  / D3=id 全量 + 明细截断 / D4=不含 GateC / D5=不改 prompt
GATE_CONTEXT_MAX_REC_DETAIL = 200   # 明细条数上限；id 集合始终全量（存在性校验唯一依据）


def _fmt_retrieval_records(state: dict) -> Optional[str]:
    """把检索记录格式化为 Gate 审核上下文（仅存在性/可信度所需字段，控 token）。

    返回 None 表示无记录可附（调用方跳过追加，避免空段污染 prompt）。
    """
    recs = state.get("retrieval_records", []) or []
    if not recs:
        return None
    ids = [r.get("id") for r in recs if r.get("id")]
    detail = [
        {"id": r.get("id"), "title": r.get("title"), "credibility": r.get("credibility")}
        for r in recs[:GATE_CONTEXT_MAX_REC_DETAIL]
    ]
    return (
        f"检索记录 retrieval_records（共 {len(recs)} 条｜被审产出的 source_ids 只能取自下列 id 集合）:\n"
        f"全部 id 集合: {ids}\n"
        f"记录明细（最多 {GATE_CONTEXT_MAX_REC_DETAIL} 条）:\n"
        f"{json.dumps(detail, ensure_ascii=False)}"
    )


def build_gate_user(gate_name: str, state: ReportState, reg: Optional[dict] = None,
                    role: Optional[str] = None) -> str:
    if reg is None:
        reg = load_agent_registry()
    # M10-P4：优先用显式被审 role（多 Agent 共用一闸时反查会串角色）
    role = role or reg["reviews"][gate_name]
    prod_key = reg["agents"][role]["output_key"]
    shape = reg["gate_shape"][gate_name]
    parts = [
        f"Gate: {gate_name}",
        f"待审 {role} 产出（结构化段 {prod_key}）:\n"
        f"{json.dumps(state.get(prod_key, []), ensure_ascii=False)}",
    ]
    # M5 修复：GateC 此前只评估 draft_segments（结构化段，本无引用章节），
    # 看不到 doc_export 已确定性重建的 report_markdown，导致误判"缺引用章节"。
    # 现把最终研报（含引用/来源章节）一并喂给 GateC，使其评估真实交付物。
    if shape == "writer":
        parts.append(
            "最终研报 report_markdown（含『引用 / 来源』章节，由 doc_export 确定性重建）:\n"
            f"{state.get('report_markdown', '')}"
        )
    # TD-005：GateB 审 Analyst 引用链（source_ids ↔ rec_ids），必须提供检索记录上下文
    # 供存在性校验与来源可信度评估；GateA 产出即 retrieval_records 本身（无需重复），
    # GateC 已有 report_markdown + 代码硬校验兜底（D1=S2 / D4=否）。
    if shape == "analyst":
        ctx = _fmt_retrieval_records(state)
        if ctx:
            parts.append(ctx)
    parts.append(f"user_task:\n{json.dumps(state.get('user_task', {}), ensure_ascii=False)}")
    parts.append("请按闸门规则输出 gate_output JSON。")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------
# 模型映射加载（config/model_mapping.yaml）
# ----------------------------------------------------------------------------
def load_model_mapping(path: Optional[str] = None) -> dict:
    import yaml
    if path:
        p = Path(path)
    else:
        from server import tenancy
        p = tenancy.config_path("model_mapping.yaml")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# OpenAI 兼容端点 + 模型接口加载（config/models.yaml，DESIGN_OPENAI_ENDPOINTS.md）
# ----------------------------------------------------------------------------
def _expand_env_vars(value):
    """展开 ${ENV} 与 ${ENV:-default} 占位符（不依赖第三方库）。

    设计：config/models.yaml 的端点 url/api_key 用占位符，从 .env 注入，
    实现"密钥不落盘进 yaml"。与 admin.py 的 _expand_env_vars 同源（单一定义在此）。
    """
    if not isinstance(value, str) or "$" not in value:
        return value
    pat = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
    def _repl(m):
        name = m.group(1)
        default = m.group(2)
        return os.getenv(name, default if default is not None else "")
    return pat.sub(_repl, value)


def _load_custom_providers() -> list[dict]:
    """读取用户自定义 API provider（config/custom_providers.yaml + .secrets/custom_providers.json）。

    字段：
      - id          : provider id（全局唯一，frontend 选中时作为 endpoint 标识）
      - name        : 显示名
      - base_url    : OpenAI 兼容根 URL
      - api_key     : 从 .secrets 读取，绝不落进 yaml
      - model_ids   : 该 provider 上提供的模型 id 列表（可空，启动后由 GET /v1/models 拉取）
      - enabled     : 是否启用（disabled 的仍出现在列表，但 ChatAgent 会过滤）

    文件缺失 = 空列表（向后兼容）。"""
    import json as _json
    import yaml
    from server import tenancy  # 按账号命名空间解析（各自一套 provider 与密钥）
    cfg = tenancy.config_path("custom_providers.yaml")
    sec = tenancy.secrets_path("custom_providers.json")
    out: list[dict] = []
    if cfg.exists():
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            items = data.get("providers") or []
        except Exception:
            items = []
        for p in items:
            if not isinstance(p, dict) or not p.get("id"):
                continue
            out.append({
                "id": p["id"],
                "name": p.get("name") or p["id"],
                "base_url": _expand_env_vars(p.get("base_url", "")),
                "api_key": "",  # 由 .secrets 注入
                "model_ids": list(p.get("model_ids") or []),
                "enabled": bool(p.get("enabled", True)),
                "source": "custom",
            })
    # 注入密钥（id → api_key）
    keys: dict = {}
    if sec.exists():
        try:
            keys = _json.loads(sec.read_text(encoding="utf-8")) or {}
        except Exception:
            keys = {}
    for p in out:
        p["api_key"] = (keys.get(p["id"]) or "").strip()
    return out


# 自定义 provider 自动发现缓存（与 admin._GATEWAY_CACHE 对称；避免每次 GET /models 都打网络）。
_CUSTOM_MODELS_CACHE: Dict[str, Dict[str, Any]] = {}
_CUSTOM_MODELS_CACHE_TTL = 120
logger = logging.getLogger(__name__)


def _discover_custom_models(base_url: str, api_key: str, timeout: int = 10) -> List[str]:
    """按自定义 provider 的 base_url 拉 /v1/models 自动发现模型 id 列表。

    与 admin._fetch_gateway_models 对称：失败（网络/鉴权/超时/代理）一律降级为空列表，
    不阻断整体——诚实标注而非伪造模型（DESIGN_chat_model_picker.md round 2 的"自动发现"承诺）。
    绕过系统代理（容器内/本机内部地址不应走 127.0.0.1:10808），避免 TLS 证书不匹配误报。
    """
    if not base_url:
        return []
    # 缓存键**必须带账号维度**：否则两个账号共用同一 base_url 时，
    # A 自动发现的模型列表会被 B 读到（跨账号串号）。见 DESIGN_account_hierarchy §2.3
    try:
        from server import tenancy
        _aid = tenancy.current_account_id(allow_none=True) or "-"
    except Exception:
        _aid = "-"
    key = f"{_aid}|{base_url.rstrip('/')}|{bool(api_key)}"
    cached = _CUSTOM_MODELS_CACHE.get(key)
    if cached and (time.time() - cached["ts"]) < _CUSTOM_MODELS_CACHE_TTL:
        return list(cached["ids"])

    ids: List[str] = []
    url = base_url.rstrip("/") + "/models"
    import urllib.request as _req
    req = _req.Request(url, headers={"Accept": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        opener = _req.build_opener(_req.ProxyHandler({}))  # 禁用代理
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        data = json.loads(body)
        items = (data.get("data") or []) if isinstance(data, dict) else []
        ids = [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("自定义 provider 模型自动发现失败 (%s): %s", base_url, exc)
        ids = []

    _CUSTOM_MODELS_CACHE[key] = {"ids": ids, "ts": time.time()}
    return ids


def load_model_interfaces_and_endpoints(path: Optional[str] = None) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """读取 config/models.yaml 的端点与接口定义。

    返回：(endpoints, interfaces, gateway_cfg, default_endpoint_id)

    向后兼容（旧配置无 endpoints 段）：自动构造一个 default endpoint，
    url/api_key 取自 gateway 段（已 env 展开）或 NEWAPI_* 环境变量，
    行为退化为"单一 new-api 端点"，与改造前一致。

    endpoint 项字段：{id, name, base_url, api_key}
    interface 项字段：{id, name, description, kind, default, endpoint, model_id}

    自定义 provider（config/custom_providers.yaml + .secrets/custom_providers.json）会被合并进
    endpoints，并按 model_ids 自动生成对应 interface（每个 model 一个 interface），
    供 NewApiLLMClient._resolve 命中后路由到独立 endpoint。
    """
    import yaml
    if path:
        p = Path(path)
    else:
        from server import tenancy
        p = tenancy.config_path("models.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {} or {}
    data = data or {}

    raw_endpoints = data.get("endpoints") or []
    endpoints: List[Dict[str, Any]] = []
    for e in raw_endpoints:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        endpoints.append({
            "id": e["id"],
            "name": e.get("name", e["id"]),
            "base_url": _expand_env_vars(e.get("base_url", "")),
            "api_key": _expand_env_vars(e.get("api_key", "") or ""),
            "source": "builtin",  # 与 custom 区分；前端按 source 分组
            # LibreChat 借鉴 dropParams：挑剔/旧的 OpenAI 兼容实现会因一个多余参数 400
            # 掉整个请求。该字段**必须透传**，否则 config/models.yaml 里写了也不生效
            # （loader 白名单会把未知键静默剥掉，形成"假配置"）。
            "drop_params": e.get("drop_params") or [],
        })

    raw_interfaces = data.get("interfaces") or []
    interfaces: List[Dict[str, Any]] = []
    for m in raw_interfaces:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        interfaces.append({
            "id": m["id"],
            "name": m.get("name", ""),
            "description": m.get("description", ""),
            "kind": m.get("kind", "auto"),
            "default": bool(m.get("default", False)),
            "endpoint": m.get("endpoint"),
            "model_id": m.get("model_id"),
            # 同上：**必须透传**。interface 级优先于 endpoint 级（见 _drop_params）。
            "drop_params": m.get("drop_params") or [],
            "source": "builtin",
        })

    # 合并用户自定义 API provider（DESIGN_chat_model_picker.md round 2）
    for cp in _load_custom_providers():
        if not cp.get("enabled", True):
            continue  # disabled 不出现，避免脏数据混入端点表
        eid = f"custom:{cp['id']}"
        endpoints.append({
            "id": eid,
            "name": cp.get("name") or cp["id"],
            "base_url": cp.get("base_url", ""),
            "api_key": cp.get("api_key", ""),
            "source": "custom",
            # 自定义 provider 各家兼容度最参差，这里的 drop_params 往往是刚需
            "drop_params": cp.get("drop_params") or [],
        })
        # 模型列表：显式 model_ids 优先；为空则按 base_url 拉 /v1/models 自动发现
        # （兑现 DESIGN 的"留空=自动发现"承诺，杜绝"加了 provider 却无任何可选模型"的假壳）。
        raw_mids = [m for m in (cp.get("model_ids") or []) if m]
        discovered = False
        if not raw_mids:
            discovered = True
            raw_mids = _discover_custom_models(cp.get("base_url", ""), cp.get("api_key", ""))
        # 已存在的 model_id（网关/内置）集合，自动发现时跳过重复，避免下拉出现两份同名模型
        existing_model_ids = {i.get("model_id") for i in interfaces}
        for mid in raw_mids:
            interface_id = f"custom:{cp['id']}:{mid}"
            if any(i["id"] == interface_id for i in interfaces):
                continue  # 同 provider 内去重
            if discovered and mid in existing_model_ids:
                continue  # 自动发现时跳过与网关/内置重复的模型
            interfaces.append({
                "id": interface_id,
                "name": mid,
                "description": (f"自动发现 · " if discovered else "") + f"自定义 · {cp.get('name') or cp['id']}",
                "kind": "concrete",
                "default": False,
                "endpoint": eid,
                "model_id": mid,
                "source": "custom",
            })

    gw = data.get("gateway") or {}

    # 向后兼容：无 endpoints 段 → 构造 default endpoint
    default_endpoint_id: Optional[str] = None
    for e in endpoints:
        if e.get("id") and e.get("base_url"):
            # 第一个有 base_url 的端点作为默认（或显式 default: true）
            if e.get("id") == "new-api" or default_endpoint_id is None:
                default_endpoint_id = e["id"]
                if e.get("id") == "new-api":
                    break
    if not endpoints:
        gw_url = _expand_env_vars(gw.get("url", "")) or os.getenv(
            "NEWAPI_BASE_URL", "http://localhost:3000/v1")
        gw_key = _expand_env_vars(gw.get("api_key", "")) or os.getenv("NEWAPI_API_KEY", "sk-no-key")
        endpoints.append({
            "id": "new-api",
            "name": "new-api 网关",
            "base_url": gw_url,
            "api_key": gw_key,
            "source": "builtin",
        })
        default_endpoint_id = "new-api"

    return endpoints, interfaces, gw, default_endpoint_id


def load_agent_registry(path: Optional[str] = None) -> dict:
    """加载智能体库（config/agents_library.yaml），返回含派生反查表的注册表。

    M9-1：取代原硬编码 PROD_KEY/TOOL/GATE_NAME/GATE_REVIEWS 四字典，热加载
    （每次 run_report 调用，mirror load_model_mapping），改完下一个任务即生效。
    文件缺失时回落 DEFAULT_REGISTRY，保证向后兼容。
    """
    import yaml
    if path:
        p = Path(path)
    else:
        from server import tenancy
        p = tenancy.config_path("agents_library.yaml")
    if not p.exists():
        data = {"agents": list(DEFAULT_REGISTRY["agents"].values())}
    else:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    agents = {a["id"]: a for a in data.get("agents", [])}
    gate_of = {a["id"]: a["gate"] for a in agents.values()}
    shape_of = {a["id"]: a["shape"] for a in agents.values()}
    gate_shape = {a["gate"]: a["shape"] for a in agents.values()}
    # M10-P4：闸 → 被审角色是 **1:N** 关系（Analyst / EcomAnalyst / SourcingAdvisor 共用 GateB，
    # MarketScout 与 Researcher 共用 GateA）。原 `reviews = {a["gate"]: a["id"]}` 是 1:1 反查，
    # 多角色共闸时后写者静默覆盖前者（实测 GateB 被 SourcingAdvisor 覆盖，导致审错角色）。
    # 正解是按「闸」聚合为角色列表；顺序 = yaml 中定义顺序（内置在前，自定义在后）。
    gate_roles: dict[str, list[str]] = {}
    for a in agents.values():
        gate_roles.setdefault(a["gate"], []).append(a["id"])
    # ⚠️ 弃用兼容：`reviews` 保留仅为兼容历史状态读取（缺失 last_gate_role 时的回落值）。
    # 它是**有损**的 1:1 投影（只留该闸最后一个角色），新代码一律用 gate_roles / last_gate_role。
    reviews = {g: ids[-1] for g, ids in gate_roles.items()}
    return {
        "agents": agents, "gate_of": gate_of, "reviews": reviews,
        "gate_roles": gate_roles,
        "shape_of": shape_of, "gate_shape": gate_shape,
    }


# ----------------------------------------------------------------------------
# 引用链机器校验（gates/review.md §5：代码层硬校验，优先于 LLM 主观判断）
# ----------------------------------------------------------------------------
def machine_check(gate_name: str, state: ReportState, reg: Optional[dict] = None):
    """返回 (decision_or_None, reason_or_None)。decision 为 'rework'/'escalate' 时强制覆盖 LLM。

    M9-1：判定键由「闸名」改为「产出形态 shape」（原 if gate_name=="GateA/B/C" 三段逻辑
    只依赖 retrieval/analysis/draft 三种形态，与闸名无关）。→ 自定义闸（GateD/GateE…）
    自动复用同一套一等公民质量校验，而非降级为 LLM-only 软校验。
    """
    if reg is None:
        reg = load_agent_registry()
    shape = reg["gate_shape"][gate_name]
    if shape == "researcher":
        recs = state.get("retrieval_records", [])
        if not recs:
            return ("escalate", "检索完全无有效素材（retrieval_records 为空）")
        for r in recs:
            if not all(k in r for k in ("id", "url", "title", "snippet", "credibility")):
                return ("rework", f"检索记录缺必需字段: {r}")
        # M4 溯源硬校验（纵深防御）：url 必须来自引擎本轮真实检索结果集。
        # Agent 节点已拦一道，此处再拦一次，防止未来变更绕过。
        fetched = state.get("search_results")
        if fetched:
            fetched_urls = {r.get("url") for r in fetched}
            bad = [r.get("url") for r in recs if r.get("url") not in fetched_urls]
            if bad:
                return ("rework", f"存在非检索结果来源的 url（疑似编造）: {bad[:3]}")
        return (None, None)
    if shape == "analyst":
        recs = state.get("retrieval_records", [])
        rec_ids = {r.get("id") for r in recs}
        cons = state.get("analysis_conclusions", [])
        if not cons:
            return ("rework", "analysis_conclusions 为空")
        for c in cons:
            sids = c.get("source_ids") or []
            if not sids:
                return ("rework", f"结论 {c.get('id')} 缺 source_ids")
            for sid in sids:
                if sid not in rec_ids:
                    return ("rework",
                            f"结论 {c.get('id')} 引用 {sid} 不存在于检索记录；"
                            f"当前可用记录 id 集合为 {sorted(rec_ids)}，"
                            f"请只从中选取")
        return (None, None)
    if shape == "writer":
        cons = state.get("analysis_conclusions", [])
        con_ids = {c.get("id") for c in cons}
        segs = state.get("draft_segments", [])
        if not segs:
            return ("rework", "draft_segments 为空")
        for s in segs:
            cids = s.get("conclusion_ids") or []
            if not cids:
                return ("rework", f"段落 {s.get('id')} 缺 conclusion_ids")
            for cid in cids:
                if cid not in con_ids:
                    return ("rework", f"段落 {s.get('id')} 引用 {cid} 不存在于分析结论")
        # 引用/来源章节完整性：由 doc_export 确定性重建（gates/review.md §5 代码所有项）。
        # 校验 report_markdown 含引用章节且覆盖全部检索记录 id —— 满足即代码硬通过，
        # 不被 LLM 主观 rework 覆盖（与 GateA/B 机器校验同优先级）。
        md = state.get("report_markdown", "") or ""
        rec_ids = {r.get("id") for r in state.get("retrieval_records", [])}
        if not rec_ids:
            return ("advance", "无检索记录，引用章节无可校验内容")
        has_heading = bool(re.search(r"^\s*##\s*(引用|参考|来源)", md, re.MULTILINE))
        missing = [rid for rid in rec_ids if rid and rid not in md]
        if not has_heading:
            return ("rework", "report_markdown 缺『引用 / 来源』章节（doc_export 应已确定性重建）")
        if missing:
            return ("rework", f"引用章节未覆盖全部检索记录 id，缺: {missing}")
        return ("advance", "引用章节由 doc_export 确定性重建且覆盖全部检索记录，代码硬校验通过")
    return (None, None)


# ----------------------------------------------------------------------------
# eval 调用（辅助，非替代业务规则；失败降级 eval_score=None）
# ----------------------------------------------------------------------------
def call_eval(llm: LLMClient, models: dict, state: ReportState, gate_name: str,
              reg: Optional[dict] = None, role: Optional[str] = None) -> Optional[float]:
    if reg is None:
        reg = load_agent_registry()
    eval_model = models["eval"]["model"]
    # M10-P4：优先用显式被审 role（多 Agent 共用一闸时反查会串角色）
    role = role or reg["reviews"][gate_name]
    user = (
        f"请对以下 {gate_name} 审核对象的整体质量打分(0-1)：\n"
        f"{json.dumps(state.get(reg['agents'][role]['output_key'], []), ensure_ascii=False)}"
    )
    raw = llm.complete(eval_model, "你是评分器，只返回一个 0-1 的小数。", user, role="Eval")
    m = re.search(r"0?\.?\d+", raw)
    return float(m.group(0)) if m else None


# ----------------------------------------------------------------------------
# Agent 节点（通用）
# ----------------------------------------------------------------------------
def _agent_fail(state: ReportState, rs: dict, role: str, reason: str,
                on_event: Optional[Callable] = None) -> dict:
    """Agent 产出不可用（LLM 失败 / JSON 解析失败 / 缺字段）→ 发 rework 信号回 Router。"""
    rs = dict(rs)
    rs["rework_target_agent"] = role
    rs["rework_reason"] = reason
    # 审计留痕（memory/schema.md §4 engine_events）：
    # 产出不可用属引擎层事件，不混入 gate_review_history（后者仅记录闸门评审），
    # 否则重试成功后 rework_reason 被清空，"崩坏过几次"将彻底丢失。
    events = list(rs.get("engine_events", []))
    events.append({
        "event": "agent_output_unusable",
        "agent": role,
        "reason": reason,
        "round": rs["round"],
        "timestamp": _utcnow_iso(),
    })
    rs["engine_events"] = events
    # TD-002 实时事件：Agent 产出不可用也推到前端时间线，用户才能看到"错在那一步"。
    if on_event:
        try:
            on_event({
                "event_type": "agent_output_unusable",
                "event": "agent_output_unusable",
                "agent": role,
                "reason": reason,
                "round": rs["round"],
            })
        except Exception as _ev:
            print(f"⚠️ agent_output_unusable 事件推送失败: {_ev}")
    go = {"decision": "rework", "reason": reason, "eval_score": None, "problem_points": [reason]}
    return {"routing_state": rs, "gate_output": go, "agent_result": "parse_fail"}


def build_search_queries(user_task: dict) -> list[str]:
    """由 user_task 构造检索式：主题 + 每个 scope 子问题各一条。

    逐条 scope 检索使 GateA 最在意的"覆盖度"成为**结构化可保证**的属性，
    而非依赖 LLM 自觉（M4 设计要点）。
    """
    topic = (user_task.get("topic") or "").strip()
    scope = user_task.get("scope") or []
    qs = [topic] if topic else []
    qs.extend([f"{topic} {s}".strip() for s in scope])
    return [q for q in qs if q]


def plan_search_queries(user_task: dict, existing: Optional[list], rework_reason: Optional[str]) -> list[str]:
    """规划本轮检索式（成本感知）。

    - 首轮：主题 + 每个 scope 子问题；
    - 返工：只在返工原因上做**一次定向补充检索**。
      理由：用同样的 query 重搜会得到同样的结果，既浪费 API 调用，
      也永远修不好 GateA 的"覆盖度不足"。既有结果会被合并复用。
    """
    if not existing:
        return build_search_queries(user_task)
    if rework_reason:
        topic = (user_task.get("topic") or "").strip()
        return [f"{topic} {rework_reason}".strip()[:120]]
    return []


def merge_search_results(existing: Optional[list], new: Optional[list]) -> list[dict]:
    """合并检索结果：按 url 去重，并重编号为 rec-1..rec-n。

    返工后 Researcher 会全量替换 retrieval_records，引用链随之重建，
    故重编号安全；去重则避免多次检索把同一来源重复计入。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for r in list(existing or []) + list(new or []):
        url = (r or {}).get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(r)
    return [{**r, "id": f"rec-{i}"} for i, r in enumerate(out, 1)]


# ----------------------------------------------------------------------------
# M11-3 D1-B：function-calling 工具分发
# ----------------------------------------------------------------------------
def dispatch_tool(name: str, args: dict, bundle, mcp, role: str) -> dict:
    """把 LLM 发出的 tool_call 路由到具体工具实现。

    - web_search          → bundle.web_search.search_many（沿用 M4 多源聚合）
    - data_proc           → bundle.data_proc.run_requests（沿用 M4 AST 求值）
    - mcp:{server}:{tool} → mcp.call_tool（外部 MCP server）
    返回 {ok, result|error, source}；任何异常 → {ok:False}（诚实边界，不击穿图）。
    """
    try:
        if name == "web_search":
            # 【M12 修真 bug】此前写成 `_, entries = search_many(...)` —— 真实检索记录被丢弃，
            # 只把 status（{agent,tool,source,ok,error}）当 result 返回给模型。
            # 后果：function-calling 路径（ChatAgent + Researcher/Analyst）里模型调 web_search
            # 永远只看到「某某源 ok:true」的空壳，拿不到任何 title/url/snippet，
            # 于是回答「检索返回空结果」——日志里源其实命中了。
            # 同时 _build_sources 依赖 result 为检索记录列表，被此 bug 一并废掉（引用全空）。
            queries = (args or {}).get("queries", [])
            if isinstance(queries, str):  # 模型常把数组写成裸字符串，按字符迭代会炸
                queries = [queries]
            # 聊天体（ChatAgent）启用按意图路由源：天气/新闻不再盲搜全部 18 源被学术垃圾淹没；
            # 引擎研究检索（Researcher/Analyst）不过滤，保持全源聚合零回归。
            results, entries = bundle.web_search.search_many(
                queries, agent=role, route_by_intent=(role == "ChatAgent"))
            return {"ok": True, "result": results, "status": entries,
                    "source": "web_search"}
        if name == "data_proc":
            results, entries = bundle.data_proc.run_requests(
                (args or {}).get("requests", []), agent=role)
            return {"ok": True, "result": {"calc": results, "entries": entries},
                    "source": "data_proc"}
        if name.startswith("mcp:"):
            if mcp is None:
                return {"ok": False, "error": "MCP 客户端未初始化", "source": name}
            server, _, tool = name[4:].partition(":")
            return mcp.call_tool(server, tool, args or {})
        # —— 聊天体专用工具（仅 ChatAgent 暴露 schema，引擎研究检索不可见）——
        if name == "install_skill":
            # M12 扩展层 skill_importer 接入对话入口：用户给 GitHub/市场链接即可真安装。
            # L0 自动装、L1 交管理员审批、L2 拒绝并附原因（绝不静默自改 prompt/代码）。
            from tools import skill_importer

            url = (args or {}).get("url", "")
            if not url:
                return {"ok": False, "error": "缺少 url 参数", "source": name}
            try:
                res = skill_importer.import_skill(
                    url,
                    operator=role or "chat",
                    auto_install_l0=bool((args or {}).get("auto_install", True)),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("install_skill(%s) 失败: %s", url, e)
                return {"ok": False, "error": f"安装失败: {e}", "source": name}
            return {"ok": True, "result": res, "source": name}
        if name in ("run_python", "run_shell"):
            # T44 受限沙箱：对话体可在固定工作目录跑小段代码/命令（超时+危险命令拦截+输出截断），
            # 不污染引擎目录，不提供提权通道。
            from tools import chat_sandbox

            try:
                if name == "run_python":
                    res = chat_sandbox.run_python((args or {}).get("code", ""))
                else:
                    res = chat_sandbox.run_shell((args or {}).get("command", ""))
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 沙箱异常: %s", name, e)
                return {"ok": False, "error": f"沙箱执行失败: {e}", "source": name}
            error = res.get("error") if not res.get("ok") else None
            return {"ok": bool(res.get("ok")), "result": res,
                    "source": name, "error": error}
        if name == "fetch_url":
            # M12-2 只读研究抓取：用户给具体链接（尤其 GitHub 仓库/文档/博客）要求「分析/抓取/
            # 读一下/看看」时调用。fetch 任意可信 http/https 正文，不落盘不执行；host 越界（私有/
            # 环回/链路本地）会被 SSRF 防护拒绝。run_shell 的 curl 已被沙箱拦截，抓取一律走这里。
            from tools import web_fetch

            url = (args or {}).get("url", "")
            if not url:
                return {"ok": False, "error": "缺少 url 参数", "source": name}
            try:
                res = web_fetch.fetch_url(
                    url,
                    max_chars=int((args or {}).get("max_chars", 120_000) or 120_000),
                )
            except Exception as e:  # noqa: BLE001 — fetch_url 自身意外崩溃（非 WebFetchError 路径）
                logger.warning("fetch_url(%s) 意外异常: %s", url, e)
                return {"ok": False, "error": f"抓取失败: {e}", "source": name}
            # 成败都留痕：成功证明「调到了且取到」；ok=False 给出原因。
            # 此前 fetch_url 返回 ok=False 时静默，排查成黑洞——12:42 那次 300s 超时就是因为看不到任何痕迹。
            if res.get("ok"):
                logger.info("fetch_url(%s) -> ok type=%s len=%d",
                            url, res.get("source_type"), len(res.get("text", "")))
            else:
                logger.warning("fetch_url(%s) 返回失败: %s", url, res.get("error"))
            return {"ok": bool(res.get("ok")), "result": res, "source": name,
                    "error": res.get("error") if not res.get("ok") else None}
        return {"ok": False, "error": f"未知工具: {name}", "source": name}
    except Exception as e:  # noqa: BLE001
        logger.warning("dispatch_tool(%s) 失败: %s", name, e)
        return {"ok": False, "error": str(e), "source": name}


def _build_tool_schemas(bundle, mcp, role: str) -> list:
    """为某 role 构建 OpenAI function 工具 schema 列表（web_search + data_proc + mcp）。"""
    schemas = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网检索真实资料（多源聚合），返回带 url/title/snippet/credibility 的检索记录。",
            "parameters": {
                "type": "object",
                "properties": {"queries": {"type": "array", "items": {"type": "string"},
                                             "description": "检索词列表"}},
                "required": ["queries"],
            },
        },
    }]
    if role == "Analyst" or (bundle and getattr(bundle, "data_proc", None) is not None):
        schemas.append({
            "type": "function",
            "function": {
                "name": "data_proc",
                "description": "对检索到的数值做计算/聚合（表达式求值），返回计算结果。",
                "parameters": {
                    "type": "object",
                    "properties": {"requests": {"type": "array", "items": {"type": "object"},
                                                 "description": "计算请求列表，每项含 expr"}},
                    "required": ["requests"],
                },
            },
        })
    if mcp is not None:
        try:
            mcp_tools = mcp.list_tools()
        except Exception as e:  # noqa: BLE001 — MCP 不可达/SDK 清理错误一律降级，绝不打穿 /chat
            logger.warning("MCP 工具列举失败，外部工具降级跳过: %s", e)
            mcp_tools = []
        for t in mcp_tools:
            try:
                sname = t.get("name") or t.get("server")
                if not sname:
                    continue
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp:{t.get('server', '')}:{t.get('name', '')}",
                        "description": t.get("description") or f"MCP 工具 {sname}",
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                    },
                })
            except Exception as ex:  # noqa: BLE001 — 畸形工具 schema 跳过，绝不打穿 /chat
                logger.warning("跳过畸形 MCP 工具 schema: %s", ex)
    if role == "ChatAgent":
        # 聊天体专用工具（引擎研究检索不暴露，避免污染/误触发）：
        # - fetch_url：只读研究抓取任意 http/https 链接正文（仓库/文档/博客），支撑「抓上网」学习
        # - install_skill：真·安装外部 skill（GitHub/市场/网页），对齐 boss「接入 M12 skill_importer」诉求
        # - run_python / run_shell：受限沙箱，对话体可跑小段代码/命令做计算或轻量探查
        schemas.append({
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": "只读抓取任意 http/https 链接的正文（研究/学习用，不落盘不执行）。当用户给出具体链接、尤其是 GitHub 仓库/文档/博客并说「分析/抓取/读一下/看看这个链接/这个仓库讲什么」时调用，返回提炼后的正文文本；不要用 web_search 去盲搜该 URL（搜索只会返回不相关结果）。file:// 等本地协议与私有/环回地址会被拒绝。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要抓取的链接，如 https://github.com/user/repo 或 https://example.com/doc"},
                        "max_chars": {"type": "integer", "description": "返回正文最大字符数（默认 120000，上限 300000）"},
                    },
                    "required": ["url"],
                },
            },
        })
        schemas.append({
            "type": "function",
            "function": {
                "name": "install_skill",
                "description": "安装一个外部 skill（GitHub 仓库 / WorkBuddy 市场 / 网页）。当用户给出 skill 链接、或明确要求『安装/添加/导入/下载 skill』时调用。L0 自动安装；L1 提交管理员审批；L2 拒绝并说明原因。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "skill 的仓库/市场/网页链接，如 https://github.com/user/repo"},
                        "auto_install": {"type": "boolean", "description": "是否自动安装 L0 级 skill（默认 true；false 则一律提交管理员审批）"},
                    },
                    "required": ["url"],
                },
            },
        })
        schemas.append({
            "type": "function",
            "function": {
                "name": "run_python",
                "description": "在受限沙箱中执行 Python 代码（仅标准库 + 已装依赖，工作目录固定，超时即终止，输出截断）。用于数据计算/小实验/解析数据。禁止访问系统敏感路径。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "要执行的 Python 代码"},
                    },
                    "required": ["code"],
                },
            },
        })
        schemas.append({
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "在受限沙箱中执行 shell 命令（工作目录固定、超时即终止、输出截断、危险命令被拦截）。用于文件列举/查看/轻量系统查询。禁止 rm -rf / dd / sudo / mkfs 等破坏性命令。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 shell 命令"},
                    },
                    "required": ["command"],
                },
            },
        })
    return schemas


# function-calling 最大轮数（防 runaway：达上限强制取最后一次 content）
MCP_MAX_ROUNDS = 4

# 回注模型的检索结果预算（M11 性能/质量修复）：此前把 search_many 全部 entries
# （16 源 × 每源 5 条 ≈ 80 条、每条 snippet 500 字符）整段 json 塞回模型 —— token 爆炸
# 拖慢响应，且排后的关键源（如天气）被几十条学术结果淹没，模型读不到 → 误报「没查到」。
# 裁剪仅作用于回注 prompt；extra_search 仍保留完整结果供 sources 展示与学习门槛判定。
_WEB_PER_SOURCE = 3
_WEB_MAX_TOTAL = 20
_WEB_SNIPPET = 220


def _fmt_failures(status: Optional[list], limit: int = 12) -> list[str]:
    """把 search_many 的 status 压成「源: 原因」短串，供回注模型做诚实降级说明。"""
    out: list[str] = []
    for s in status or []:
        if not isinstance(s, dict) or s.get("ok"):
            continue
        sid = s.get("source") or "unknown"
        err = (s.get("error") or "未知原因").strip().replace("\n", " ")
        if len(err) > 120:
            err = err[:120] + "…"
        out.append(f"{sid}: {err}")
        if len(out) >= limit:
            break
    return out


def _trim_web_results(entries: list, per_source: int = _WEB_PER_SOURCE,
                      total: int = _WEB_MAX_TOTAL, snippet: int = _WEB_SNIPPET) -> list:
    """按源限额 + 总量限额裁剪检索结果，保序（前置源——如置顶的天气——优先保留）。"""
    out: list = []
    per: dict = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        sid = e.get("source") or ""
        if per.get(sid, 0) >= per_source:
            continue
        per[sid] = per.get(sid, 0) + 1
        d = dict(e)
        s = d.get("snippet") or ""
        if len(s) > snippet:
            d["snippet"] = s[:snippet] + "…"
        out.append(d)
        if len(out) >= total:
            break
    return out


def _run_fc_loop(llm, model: str, system: str, user: str, tool_schemas: list,
                 bundle, mcp, role: str, shape: str) -> tuple:
    """D1-B 工具循环：调用 LLM（带 tools），解析 tool_calls，dispatch，回注，直到无 tool_calls。

    返回 (raw_content, tool_entries, extra_search)。LLM 异常向上抛（由调用方转 _agent_fail）。
    工具执行失败仅追加 {ok:False} 记录，不阻断。
    """
    conv_user = user
    tool_entries: list = []
    extra_search: list = []
    raw = ""
    for _ in range(MCP_MAX_ROUNDS):
        resp = llm.complete_with_tools(model, system, conv_user, tools=tool_schemas,
                                       role=role, shape=shape, kind="agent")
        raw = resp.get("content") or ""
        calls = resp.get("tool_calls") or []
        if not calls:
            break
        summaries = []
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            r = dispatch_tool(name, args, bundle, mcp, role)
            tool_entries.append({
                "agent": role, "tool": name,
                "ok": r.get("ok", False),
                "error": r.get("error"),
                # 结果随条目带回（ok 工具才带），供 ChatAgent 构造 sources / 学习门槛
                # （不影响引擎：仅为附加字段，引擎只读 agent/tool/ok/error）。
                "result": r.get("result") if r.get("ok") else None,
            })
            if r.get("ok"):
                if name == "web_search":
                    entries = r.get("result") or []
                    extra_search.extend(entries)
                    # 裁剪后回注：防 token 爆炸 + 防关键源被淹没（见 _trim_web_results 注释）
                    summary = {"tool": name, "results": _trim_web_results(entries)}
                    # 诚实降级：一条都没取到时把失败明细回给模型，
                    # 否则模型只会笼统答「返回空结果」，掩盖「境外被拦/源异常」这类真实原因。
                    if not entries:
                        fails = _fmt_failures(r.get("status"))
                        if fails:
                            summary["failed_sources"] = fails
                    summaries.append(summary)
                elif name == "data_proc":
                    summaries.append({"tool": name, "calc": r.get("result", {}).get("calc")})
                else:
                    summaries.append({"tool": name, "result": r.get("result")})
            else:
                summaries.append({"tool": name, "error": r.get("error")})
        conv_user = conv_user + "\n【工具执行结果】\n" + json.dumps(summaries, ensure_ascii=False)
    return raw, tool_entries, extra_search


def make_agent(role: str, llm: LLMClient, models: dict, tools=None, reg: Optional[dict] = None,
               model_override: Optional[str] = None, on_event: Optional[Callable] = None):
    """通用 Agent 节点工厂（M9-1：按 registry 的 shape 驱动，不再按角色名硬编码）。

    同一 shape 的自定义 Agent（如 Coder shape=researcher）自动获得：
    web_search 真实检索 + 溯源硬校验 + 允许空产出交闸判 escalate，
    无需为它写任何新分支。

    model_override：任务级模型接口覆盖（仅作用于 Agent 调用；
    Gate 始终走 model_mapping.yaml 以维持异基座隔离，见 make_gate）。
    """
    if reg is None:
        reg = load_agent_registry()
    prod_key = reg["agents"][role]["output_key"]
    shape = reg["shape_of"][role]
    # Researcher 允许产出空数组：按 memory/schema.md §2.1 + gates/review.md §2，
    # "检索完全无有效素材"须交由 GateA 判 escalate，不能在 Agent 节点短路掉。
    allow_empty = (shape == "researcher")

    def node(state: ReportState) -> dict:
        rs = dict(state.get("routing_state", {}))
        rs["_last_agent"] = role
        rework_reason = rs.get("rework_reason")
        system = build_agent_system(role, reg)
        # M9-3 研报技能库：按 enabled + target_roles 作用域将启用技能片段拼入 system prompt。
        # 真·控制器效应：启停某技能 → 此处拼入片段增减 → 报告结构随之变化。
        # 热加载：build_skill_context 内 load_skills() 每次 run_report 重读 skills.yaml（零重启）。
        skill_ctx = build_skill_context(role)
        if skill_ctx:
            system = system + "\n\n" + skill_ctx
        # 任务级模型接口覆盖（通用接口）；未指定则沿用 role→model 映射。
        model = model_override or models["roles"][role]["model"]

        # 0) 工具前置调用（M4 关键：工具由引擎真实调用，
        #    tool_status 反映真实执行结果，不再采信 LLM 自称的 tool_status）
        tool_entries: list[dict] = []
        extra_context = ""
        patch: dict = {}
        search_results: Optional[list] = None

        if shape == "researcher" and tools is not None:
            queries = plan_search_queries(state.get("user_task", {}),
                                          state.get("search_results"), rework_reason)
            try:
                fetched_new, entries = tools.web_search.search_many(queries, agent=role)
            except Exception as e:  # 工具层异常不得击穿图
                fetched_new, entries = [], [{
                    "agent": role, "tool": "web_search", "ok": False,
                    "error": f"检索工具异常: {e}",
                }]
            tool_entries.extend(entries)
            search_results = merge_search_results(state.get("search_results"), fetched_new)
            patch["search_results"] = search_results
            if search_results:
                extra_context = (
                    "\n【已由引擎检索到的真实资料】以下为**唯一可信来源集合**。\n"
                    "你只能从中筛选、结构化并为每条标注 credibility；\n"
                    "**严禁新增任何未出现在该集合中的 url**，否则判定为编造引用。\n"
                    "每条检索记录须保留其 source 字段（标明数据来源插件 id，如 tavily），"
                    "不要丢弃或改写。\n"
                    + json.dumps(search_results, ensure_ascii=False)
                )
            else:
                extra_context = (
                    "\n【检索失败】本轮未取到任何资料。请如实输出空数组，不得编造来源。"
                )
                fails = _fmt_failures(entries)
                if fails:
                    extra_context += (
                        "\n失败明细（如需向用户说明，请据此说清是检索通道不可用，"
                        "不要笼统说「返回空结果」）：\n- " + "\n- ".join(fails)
                    )
            # M11-1：持久知识库历史结论注入（仅 researcher 在首轮复用，避免重复检索同类主题）。
            kb_ctx = state.get("kb_context") or []
            if kb_ctx:
                kb_text = "\n\n【历史知识库相关结论（往期任务沉淀，供你复用/对照，须自行核实）】\n"
                for i, item in enumerate(kb_ctx[:5], 1):
                    title = item.get("title") or f"条目{i}"
                    body = (item.get("content") or "")[:800]
                    cred = (item.get("metadata") or {}).get("credibility")
                    kb_text += f"{i}. {title}"
                    if cred:
                        kb_text += f"（可信度 {cred}）"
                    kb_text += f":\n{body}\n"
                extra_context = extra_context + kb_text

        # 0.5) 下游 Agent 上游状态注入（M5 修复：此前 Analyst/Writer 未收到上游
        #      真实 state，导致照 prompt 模板示例瞎编引用 ID，被 Gate 机器校验抓住）。
        #      Researcher 的检索结果已在上方 0) 注入 search_results，此处补 Analyst/Writer。
        if shape == "analyst":
            recs = state.get("retrieval_records", []) or []
            if recs:
                extra_context = (
                    "\n【上游调研素材 retrieval_records｜你唯一可引用的来源集合】\n"
                    "每条结论的 source_ids **只能**从下列真实 id 中选取，"
                    "严禁编造或臆测 id；不在集合内的 id 一律判为编造引用。\n"
                    + json.dumps(
                        [{"id": r.get("id"), "title": r.get("title"),
                          "credibility": r.get("credibility")} for r in recs],
                        ensure_ascii=False)
                )
        elif shape == "writer":
            cons = state.get("analysis_conclusions", []) or []
            if cons:
                extra_context = (
                    "\n【上游分析结论 analysis_conclusions｜你撰写终稿的唯一依据】\n"
                    "终稿须覆盖下列全部结论，并完整保留其 source_ids 对应的引用"
                    "（引用章节由引擎从 retrieval_records 确定性重建，你只需在正文中呼应用结论）。\n"
                    + json.dumps(
                        [{"id": c.get("id"), "claim": c.get("claim"),
                          "source_ids": c.get("source_ids"),
                          "confidence": c.get("confidence")} for c in cons],
                        ensure_ascii=False)
                )

        user = build_agent_user(state.get("user_task", {}), rework_reason) + extra_context

        # M11-3 D1-B：Researcher/Analyst 走 function-calling 循环（工具由 LLM 自主决定调用）。
        # 其余 shape（writer）及无工具配置时，沿用原 complete 管道（向后兼容）。
        mcp = getattr(tools, "mcp", None) if tools else None
        tool_schemas = (_build_tool_schemas(tools, mcp, role)
                        if (tools is not None and shape in ("researcher", "analyst")) else [])

        # 1) LLM 调用（失败 → rework 信号）
        if tool_schemas:
            try:
                raw, fc_entries, fc_search = _run_fc_loop(
                    llm, model, system, user, tool_schemas, tools, mcp, role, shape)
            except LLMError as e:
                return _agent_fail(state, rs, role, f"LLM 工具调用失败: {e}", on_event=on_event)
            tool_entries.extend(fc_entries)
            if fc_search:
                search_results = merge_search_results(search_results, fc_search)
                patch["search_results"] = search_results
        else:
            try:
                raw = llm.complete(model, system, user, role=role, shape=shape, kind="agent")
            except LLMError as e:
                return _agent_fail(state, rs, role, f"LLM 调用失败: {e}", on_event=on_event)

        # 2) JSON 容错：解析 → 失败重试一次 → 仍失败 rework
        try:
            data = robust_json_load(raw)
        except JSONRepairError:
            try:
                raw2 = llm.complete(
                    model, system,
                    user + "\n【严重】上一轮输出非合法 JSON，必须只输出 JSON 数组，禁止任何解释文字。",
                    role=role, shape=shape, kind="agent",
                )
                data = robust_json_load(raw2)
            except JSONRepairError:
                return _agent_fail(state, rs, role, "JSON 解析失败，请严格输出 JSON 数组", on_event=on_event)

        # 2.5) Analyst：执行 LLM 声明的 tool_requests（AST 白名单安全求值），
        #      结果回注后二次生成，保证数值不是"模型口算"
        if shape == "analyst" and tools is not None and isinstance(data.get("tool_requests"), list):
            results, entries = tools.data_proc.run_requests(data["tool_requests"], agent=role)
            tool_entries.extend(entries)
            if results:
                user2 = (
                    user + "\n【工具执行结果】\n" + json.dumps(results, ensure_ascii=False)
                    + "\n请基于上述计算结果输出最终 JSON（不再包含 tool_requests）。"
                )
                try:
                    data = robust_json_load(
                        llm.complete(model, system, user2, role=role, shape=shape, kind="agent"))
                except (LLMError, JSONRepairError):
                    pass  # 二次生成失败则沿用首次产出（不静默丢，走 Gate 校验）

        # 2.7) Researcher 降级兜底（M11-3 真实 e2e 实测）：fc-loop 拿到真实检索结果后，
        #      模型最终 JSON 偶发缺 retrieval_records 键 / 产出空数组（结构不稳定，
        #      stub 测不出来）。检索数据本就是引擎所有（M4「引擎预取」语义），
        #      模型职责只是筛选/标注——转述失败时由引擎按字段契约确定性合成，
        #      不阻断流水线；数据全部来自真实结果集（非编造），留降级事件可审计。
        if (shape == "researcher" and search_results
                and not (isinstance(data.get("retrieval_records"), list) and data.get("retrieval_records"))):
            data["retrieval_records"] = [
                {"id": r.get("id"), "url": r.get("url"), "title": r.get("title"),
                 "snippet": r.get("snippet"), "credibility": r.get("credibility"),
                 "source": r.get("source")}
                for r in search_results
            ]
            evs = list(rs.get("engine_events", []))
            evs.append({"event": "researcher_records_synthesized", "agent": role,
                        "reason": "模型最终 JSON 缺失/无效/空 retrieval_records，由引擎从真实检索结果确定性合成",
                        "count": len(data["retrieval_records"]),
                        "round": rs["round"], "timestamp": _utcnow_iso()})
            rs["engine_events"] = evs
            if on_event:
                try:
                    on_event({"event_type": "researcher_records_synthesized",
                              "event": "researcher_records_synthesized",
                              "agent": role, "count": len(data["retrieval_records"]),
                              "round": rs["round"]})
                except Exception:
                    pass

        # 3) schema 校验
        arr = data.get(prod_key)
        if not isinstance(arr, list):
            return _agent_fail(state, rs, role, f"产出缺少数组字段 {prod_key}", on_event=on_event)
        if len(arr) == 0 and not allow_empty:
            return _agent_fail(state, rs, role, f"产出缺少非空 {prod_key}", on_event=on_event)

        # 3.5) Researcher 来源溯源硬校验：url 必须来自本轮真实检索结果集。
        #      这是 DESIGN §10「大模型编造引用」风险的根治手段——
        #      检索由引擎执行，模型无法凭空造出不在结果集内的 url。
        if shape == "researcher" and search_results is not None and search_results:
            fetched_urls = {r.get("url") for r in search_results}
            bad = [r.get("url") for r in arr if r.get("url") not in fetched_urls]
            if bad:
                return _agent_fail(state, rs, role,
                                   f"引用了非检索结果来源的 url（疑似编造）: {bad[:3]}",
                                   on_event=on_event)

        # 3.6) 来源标记回填（M9-2）：保证每条 retrieval_records 带 source（来自本轮检索结果集）。
        #      即便 LLM 漏写 source，也按 url 从 search_results 回填，使 source 成为引擎级可信字段。
        if shape == "researcher" and search_results:
            src_by_url = {r.get("url"): r.get("source") for r in search_results}
            for rec in arr:
                if not rec.get("source") and rec.get("url") in src_by_url:
                    rec["source"] = src_by_url[rec["url"]]

        # 4) Writer：用 doc_export 确定性补齐「引用 / 来源」章节并可选落盘。
        #    GateC 的"引用完整"是硬校验项，交给 LLM 写有漏写风险。
        if shape == "writer" and tools is not None:
            md = data.get("report_markdown") or ""
            md, regenerated, entry = tools.doc_export.ensure_citation_section(
                md, state.get("retrieval_records", []))
            tool_entries.append(entry)
            patch["report_markdown"] = md
            if regenerated:
                # 可审计：终稿引用章节被代码重建过（LLM 原稿缺引用或含编造 url）
                evs = list(rs.get("engine_events", []))
                evs.append({"event": "writer_citation_regenerated", "agent": role,
                            "reason": "终稿引用章节缺失或含非检索来源的 url，已由 doc_export 确定性重建",
                            "round": rs["round"], "timestamp": _utcnow_iso()})
                rs["engine_events"] = evs
            try:
                path = tools.doc_export.export(md, (state.get("user_task", {}) or {}).get("topic", "report"))
            except ToolError as e:
                tool_entries.append({"agent": role, "tool": "doc_export", "ok": False, "error": str(e)})
                path = None
            if path:
                patch["report_path"] = path

        # 5) 全量替换 + 快照 prior_versions（memory/schema.md §0-3）
        pv = {k: list(v) for k, v in state.get("prior_versions", {}).items()}
        pv.setdefault(role, [])
        pv[role].append(list(state.get(prod_key, [])))  # 替换前快照旧全量数组

        new_state: dict = dict(state)
        new_state[prod_key] = arr
        new_state["prior_versions"] = pv
        new_state.update(patch)
        # tool_status 只记录引擎真实调用；LLM 自带的 tool_status 一律忽略（防自称通过）
        ts = list(state.get("tool_status", []))
        ts.extend(tool_entries)
        new_state["tool_status"] = ts
        rs["rework_target_agent"] = None
        rs["rework_reason"] = None
        new_state["routing_state"] = rs
        new_state["agent_result"] = "ok"
        new_state["gate_output"] = None  # 清除上一轮残留信号
        # TD-002 实时事件：Agent 产出完成 → 经 on_event 推给前端时间线
        # （on_event 在引擎子进程中写入 JSONL，由服务进程 tail 后广播到 WS）
        if on_event:
            try:
                for _e in tool_entries:
                    if not _e.get("ok"):
                        on_event({
                            "event_type": "tool_error", "round": rs["round"],
                            "agent": role, "tool": _e.get("tool", ""),
                            "error": _e.get("error", ""),
                        })
                on_event({
                    "event_type": "agent_complete", "round": rs["round"],
                    "agent": role, "tool_status": tool_entries,
                })
            except Exception as _ev:
                print(f"⚠️ agent 事件推送失败: {_ev}")
        return new_state

    return node


# ----------------------------------------------------------------------------
# Gate 节点（通用）
# ----------------------------------------------------------------------------
def make_gate(gate_name: str, llm: LLMClient, models: dict, is_terminal: bool = False,
              reg: Optional[dict] = None, gate_model_override: Optional[str] = None,
              role: Optional[str] = None, on_event: Optional[Callable] = None):
    """审核闸节点工厂。

    gate_model_override：任务级审核模型覆盖（方案 A：ChatEntry 独立「审核模型」下拉）。
      - None = 沿用 model_mapping.yaml 的异基座默认（审阅独立性默认生效）；
      - 显式值 = 覆盖 Gate 异基座默认，仅作用于本 Gate 节点。
    不论是否覆盖，Agent 模型与 Gate 模型的覆盖互相独立（互不牵连）。

    role：本闸**实际审核的角色**（M10-P4）。多个 Agent 共用同一闸时（如 Analyst 与
      EcomAnalyst 都用 GateB），`reg["reviews"]` 的 1:1 反查会被后者覆盖 → 审错对象。
      由 build_graph 直接传入被审 role 消除该歧义；不传则回落反查（向后兼容）。
    """
    if reg is None:
        reg = load_agent_registry()
    role = role or reg["reviews"][gate_name]

    def node(state: ReportState) -> dict:
        rs = dict(state.get("routing_state", {}))
        code_dec, code_reason = machine_check(gate_name, state, reg)
        # 异基座默认优先；仅当用户显式指定审核模型时才覆盖（方案 A）。
        gate_model = gate_model_override or models["gates"][gate_name]["model"]
        system = build_gate_system(gate_name, role)
        user = build_gate_user(gate_name, state, reg, role=role)

        # 1) Gate LLM 调用 + JSON 容错（flaky 网关韧性：最多 3 次尝试）
        #    瞬断网关常返回截断/非 JSON，多试一次大多能恢复。
        parsed = None
        for _attempt in range(3):
            try:
                if _attempt == 0:
                    raw = llm.complete(gate_model, system, user, role=gate_name,
                                       shape=reg["gate_shape"][gate_name], kind="gate")
                else:
                    raw = llm.complete(
                        gate_model, system,
                        user + "\n【严重】上一轮输出非合法 JSON，必须只输出 gate_output JSON，禁止任何解释文字。",
                        role=gate_name, shape=reg["gate_shape"][gate_name], kind="gate")
                parsed = robust_json_load(raw)
                break
            except (LLMError, JSONRepairError):
                parsed = None

        # 2) 解析失败兜底（M5 修订：区分代码校验结论）
        #    设计上 LLM 闸是「建议性」质量判断，代码硬校验才是权威（gates/review.md §5）。
        #    故：代码校验已通过（code_dec=None）而闸 LLM 不可用 → 降级放行（可审计），
        #        不再误杀整任务；仅当代码校验本身判 rework/escalate 时才照代码结论。
        if parsed is None:
            if code_dec == "rework":
                decision, reason, problem_points, eval_score = "rework", code_reason, [code_reason], None
            elif code_dec == "escalate":
                decision, reason, problem_points, eval_score = "escalate", code_reason, [code_reason], None
            else:
                # 代码硬校验通过，但审核 LLM 多次无法解析 → 降级放行 + 审计事件
                evs = list(rs.get("engine_events", []))
                evs.append({"event": "gate_llm_unavailable_degraded_advance",
                            "gate": gate_name, "round": rs["round"],
                            "reason": "审核 LLM 多次返回不可解析内容；代码硬校验已通过，降级放行（非报告缺陷）",
                            "timestamp": _utcnow_iso()})
                rs["engine_events"] = evs
                decision = "advance"
                reason = "审核 LLM 不可用（降级放行）：代码硬校验已通过，未见业务致命问题"
                problem_points, eval_score = ["审核 LLM 不可用，已降级放行"], None
        else:
            llm_dec = parsed.get("decision")
            reason = parsed.get("reason", code_reason or "")
            problem_points = parsed.get("problem_points") or [reason or "无"]
            eval_score = parsed.get("eval_score")
            if code_dec == "escalate":
                # 代码硬校验判定业务致命（如检索全空）→ 直接 escalate，
                # **不允许 LLM 的 advance 覆盖**（优先级：代码 escalate > 代码 rework > 代码 advance > LLM 判定）
                decision, reason, problem_points = "escalate", code_reason, [code_reason]
            elif code_dec == "rework" and llm_dec != "escalate":
                # 代码硬校验优先于 LLM 主观判断
                decision, reason, problem_points = "rework", code_reason, [code_reason]
            elif code_dec == "advance":
                # 代码硬校验已确认关键项（如 GateC 引用章节完整性）达标 → 强制放行，
                # 不被 LLM 主观 rework 覆盖（引用章节是 doc_export 确定性重建的代码所有项）
                decision, reason, problem_points = "advance", code_reason, []
            else:
                decision = llm_dec if llm_dec in ("advance", "rework", "escalate") else "rework"

        # 3) eval 降级（DESIGN §10 / gates/review.md §6）
        if eval_score is None:
            try:
                eval_score = call_eval(llm, models, state, gate_name, reg, role=role)
            except Exception:
                eval_score = None

        go = {
            "decision": decision,
            "reason": reason,
            "eval_score": eval_score,
            "problem_points": problem_points if problem_points else ["无"],
            "gate": gate_name,
            "round": rs["round"],
            "timestamp": _utcnow_iso(),
        }
        # DESIGN §2 状态图：末闸判定 advance 后直达 END（不回 Router），
        # 故"done"终态在此落定；Router 的 advance 分支仅需推进中间环节。
        # M8-5 子集编排：末闸 = 子集最后一道闸（is_terminal），非固定 GateC。
        if is_terminal and decision == "advance":
            rs["status"] = "done"
        rs["last_gate"] = gate_name
        # M10-P4：同时记录本闸**实际被审的角色**。多 Agent 共用同一闸时（如 Analyst 与
        # EcomAnalyst 都用 GateB），`reg["reviews"]` 的 1:1 反查会串角色，故 Router 改为
        # 优先读此字段精确定位推进目标（缺失时回落反查，兼容历史状态）。
        rs["last_gate_role"] = role
        rs["gate_review_history"] = list(rs.get("gate_review_history", [])) + [go]
        if decision == "rework":
            rs["rework_target_agent"] = role
            rs["rework_reason"] = "; ".join(problem_points)
        else:
            rs["rework_target_agent"] = None
            rs["rework_reason"] = None
        # TD-002 实时事件：闸决策完成 → 推给前端（agent_complete 由 Agent 节点推）
        if on_event:
            try:
                on_event({
                    "event_type": "gate_complete", "round": rs["round"],
                    "gate": gate_name, "decision": decision, "reason": reason,
                    "eval_score": eval_score if eval_score is not None else 0.0,
                    "problem_points": problem_points if problem_points else ["无"],
                })
            except Exception as _ev:
                print(f"⚠️ gate 事件推送失败: {_ev}")
        # M13-Converge：漂移检测（与 Gate 共存，不替换 Gate 判定）
        # 每轮闸决策后比对产出 vs spec，生成 gap_tasks 追加到 state；
        # gap_tasks 由 Router 合并到 rework_reason，驱动定向修正。
        try:
            from scripts.converge_check import check as _converge_check, merge_into_state as _converge_merge
            _gaps = _converge_check(state, round_num=rs["round"])
            if _gaps:
                _patched = _converge_merge(state, _gaps)
                # 把 gaps 记入 gate_output 供 Router 读取
                go = {**go, "converge_gaps": _gaps}
                return {**_patched, "routing_state": rs, "gate_output": go}
        except Exception as _cv:  # noqa: BLE001
            print(f"⚠️ converge 检查异常（降级放行）: {_cv}")
        return {"routing_state": rs, "gate_output": go}

    return node


# ----------------------------------------------------------------------------
# Router 节点（单一收敛：全局轮次 + 超限判断；DESIGN §2 职责边界澄清）
# ----------------------------------------------------------------------------
def make_router(on_event: Optional[Callable] = None):
    def router(state: ReportState) -> dict:
        rs = dict(state.get("routing_state", {}))
        go = state.get("gate_output")

        if go is None:  # 防御性兜底：常态流程 START 直达首个 Agent 不经此分支，round 已由 INIT 置 1；
            # 仅当某变体把 START 指向 router 时此处再确认首轮 = 1（与 INIT 幂等）。
            rs["round"] = 1
            rs["status"] = "running"
            rs["rework_target_agent"] = None
            rs["rework_reason"] = None
            return {"routing_state": rs}

        d = go.get("decision")
        if d == "escalate":
            return {"routing_state": rs}  # escalate 节点收尾
        if d == "advance":
            # GateC 的 advance 由 Gate 节点直接置 done 并直达 END，此处不会收到；
            # GateA/GateB 的 advance 仅推进到下一 Agent，status 保持 running。
            return {"routing_state": rs}
        if d == "rework":
            if rs["round"] + 1 > rs["max_rounds"]:
                rs["status"] = "escalated"
                rs["escalate_reason"] = (
                    f"rework 将使 round={rs['round'] + 1} 超过 max_rounds={rs['max_rounds']}"
                )
            else:
                rs["round"] += 1
                rs["status"] = "running"
                # M13-Converge：把 gap_tasks 合并进 rework_reason，驱动定向修正
                _converge_gaps = go.get("converge_gaps") or []
                if _converge_gaps:
                    try:
                        from scripts.converge_check import gaps_to_rework_reason as _gtr
                        _conv_reason = _gtr(_converge_gaps, rs.get("rework_reason"))
                        rs["rework_reason"] = _conv_reason
                    except Exception as _cv:  # noqa: BLE001
                        print(f"⚠️ converge reason 合并异常（降级）: {_cv}")
                # TD-002 实时事件：返工触发 → 进入新一轮（round_update + rework_trigger）
                if on_event:
                    try:
                        on_event({
                            "event_type": "round_update", "round": rs["round"],
                            "max_rounds": rs["max_rounds"],
                        })
                        on_event({
                            "event_type": "rework_trigger", "round": rs["round"],
                            "rework_target_agent": rs.get("rework_target_agent") or "",
                            "rework_reason": rs.get("rework_reason", ""),
                            "last_gate": rs.get("last_gate") or "",
                            "converge_gap_count": len(_converge_gaps) if _converge_gaps else 0,
                        })
                    except Exception as _ev:
                        print(f"⚠️ rework 事件推送失败: {_ev}")
            return {"routing_state": rs}
        return {"routing_state": rs}

    return router


# ----------------------------------------------------------------------------
# Escalate 节点（不空结束：半成品 + 告警 + 评审留痕；DESIGN §2）
# ----------------------------------------------------------------------------
def node_escalate(state: ReportState) -> dict:
    rs = dict(state.get("routing_state", {}))
    rs["status"] = "escalated"
    if "escalate_reason" not in rs:
        rs["escalate_reason"] = "闸判定业务严重问题，转人工复核。"
    return {"routing_state": rs}


# ----------------------------------------------------------------------------
# 路由条件函数
# ----------------------------------------------------------------------------
def _fallback_role(reg: dict, gate_name: Optional[str]) -> Optional[str]:
    """闸 → 被审角色的**兜底**推断（仅当 state 缺 last_gate_role 时使用）。

    M10-P4：一个闸可被多个角色共用（1:N），故此处无法总是唯一确定角色：
      - 该闸仅 1 个角色 → 唯一解，直接返回；
      - 该闸多个角色 → **有歧义**，打印告警并返回 None（由调用方按 `role not in chain`
        兜底为 END / router，绝不猜一个可能错误的角色去推进）。
    历史实现用 `reviews`（1:1 反查）会在此静默返回「最后一个角色」从而推错对象，
    故本函数取代之，把歧义显式化。
    """
    if not gate_name:
        return None
    roles = (reg.get("gate_roles") or {}).get(gate_name) or []
    if len(roles) == 1:
        return roles[0]
    if len(roles) > 1:
        print(
            f"⚠️ 路由兜底遇歧义：{gate_name} 由 {len(roles)} 个角色共用 {roles}，"
            f"且 state 缺 last_gate_role，无法唯一确定推进目标 → 保守返回 None。"
        )
        return None
    return None


def _make_route_router(chain: list, reg: Optional[dict] = None):
    """路由节点：START 入口 / rework 回退 / advance 推进。chain=有序角色列表（M8-5 子集编排）。"""
    if reg is None:
        reg = load_agent_registry()

    def route(state: ReportState):
        rs = state.get("routing_state", {})
        go = state.get("gate_output")
        if go is None:
            return chain[0].lower()
        d = go.get("decision")
        if d == "escalate":
            return "escalate"
        if d == "advance":
            # 防御性分支：正常路径下末闸的 advance 已在 Gate 节点直达 END，
            # 若因配置变体走到此处，仍保持与状态图一致的终态/推进语义。
            # M10-P4：优先用闸节点记录的确切被审角色。回落仅用于兼容「历史状态 /
            # 直接构造的 state」这类无 last_gate_role 的场景；该闸仅一个角色时才可靠，
            # 多角色共闸时回落值有歧义 → 走 _fallback_role() 显式判定（歧义则告警）。
            role = rs.get("last_gate_role") or _fallback_role(reg, rs.get("last_gate"))
            if role is None or role not in chain:
                return END
            idx = chain.index(role)
            return chain[idx + 1].lower() if idx + 1 < len(chain) else END
        if d == "rework":
            if rs.get("status") == "escalated":
                return "escalate"
            tgt = rs.get("rework_target_agent")
            return tgt.lower() if tgt in chain else "escalate"  # 子集外 rework → 诚实升级
        return "escalate"
    return route


def _make_route_after_agent(chain: list, reg: Optional[dict] = None):
    """Agent 完成后 → 其后接闸；异常 → router。"""
    if reg is None:
        reg = load_agent_registry()

    def route(state: ReportState):
        if state.get("agent_result") == "ok":
            role = state.get("routing_state", {}).get("_last_agent")
            gate = reg["gate_of"].get(role)
            if gate:
                return "gate_" + role.lower()
        return "router"
    return route


def _make_route_after_gate(chain: list, reg: Optional[dict] = None):
    """闸完成后 → advance 进下一 Agent / END；rework → router；escalate → escalate。"""
    if reg is None:
        reg = load_agent_registry()

    def route(state: ReportState):
        go = state.get("gate_output", {})
        d = go.get("decision")
        if d == "escalate":
            return "escalate"
        if d == "rework":
            return "router"
        last = state.get("routing_state", {}).get("last_gate")
        # M10-P4：同 _make_route_router —— 优先确切被审角色，回落走 _fallback_role。
        role = state.get("routing_state", {}).get("last_gate_role") or _fallback_role(reg, last)
        if role is None or role not in chain:
            return "router"
        idx = chain.index(role)
        return chain[idx + 1].lower() if idx + 1 < len(chain) else END
    return route


# ----------------------------------------------------------------------------
# 图构建
# ----------------------------------------------------------------------------
def default_chain(reg: Optional[dict] = None) -> list:
    """缺省角色链 = registry 中 builtin 条目（保持原有序），全空则回落内置 3 项。

    自定义 Agent 不会自动进入缺省链——须由模板/任务显式选中（M8-5 子集编排），
    否则新增一个 Agent 就会静默改变所有既有任务的行为。
    """
    if reg is None:
        reg = load_agent_registry()
    chain = [aid for aid, meta in reg["agents"].items() if meta.get("builtin")]
    return chain or list(DEFAULT_REGISTRY["agents"].keys())


def _run_async(coro):
    """在同步节点里安全地跑协程（实现见 tools/_async_util.run_async，单一份避免重复）。

    引擎正式路径：run_report 跑在独立子进程（server/engine_client.py 用 subprocess 起
    engine_runner.py），该进程内无运行中的 event loop → 走 asyncio.run（主路径）。
    兜底（若从 async 上下文直接调用）：改用独立线程 asyncio.run，规避嵌套 loop 崩溃。
    """
    return run_async(coro)


def node_kb_retrieve(state: ReportState) -> dict:
    """M11-1：任务开始前，从知识库语义召回历史相关结论/素材，注入 Researcher。

    任何失败都降级为空（不阻断任务）；DB/嵌入不可达时返回 {}。
    """
    try:
        task = state.get("user_task", {}) or {}
        q = " ".join([str(task.get("topic", "")), " ".join(task.get("scope") or [])]).strip()
        if not q:
            return {}
        results = _run_async(kb_retrieve(q, k=5))
        if results:
            return {"kb_context": results}
        return {}
    except Exception as e:  # noqa: BLE001
        logger.warning("KBRetrieve 节点降级: %s", e)
        return {}


def node_kb_write(state: ReportState) -> dict:
    """M11-1：任务结束后，把最终报告结论 + 高可信检索素材写入知识库。

    失败降级为 no-op（返回 {}），不阻断研报产出。
    """
    try:
        task = state.get("user_task", {}) or {}
        topic = task.get("topic", "")
        entries = []
        report_md = state.get("report_markdown", "")
        if report_md:
            entries.append({
                "kind": "report_conclusion",
                "topic": topic,
                "title": topic,
                "content": report_md[:8000],
                "source": "report",
                "metadata": {"topic": topic},
            })
        for r in (state.get("retrieval_records", []) or []):
            cred = r.get("credibility")
            if cred is not None and cred >= 0.5:
                entries.append({
                    "kind": "retrieval_record",
                    "topic": topic,
                    "title": r.get("title"),
                    "content": (r.get("snippet") or r.get("content") or "")[:2000],
                    "source": r.get("source"),
                    "metadata": {"url": r.get("url"), "credibility": cred},
                })
        if entries:
            _run_async(kb_write(entries))
    except Exception as e:  # noqa: BLE001
        logger.warning("KBWrite 节点降级: %s", e)
    return {}


def node_reflect(state: ReportState) -> dict:
    """M11-2：任务终态（escalate 或 kb_write 之后）生成反思建议，append 写
    config/reflections.proposed.yaml。任何失败降级为 no-op（不阻断任务终态）。

    设计：DESIGN_M11-2_reflection.md §7。反思仅"建议"，永不自动改系统配置；
    机械 apply 须管理员在 /admin/reflections/{id}/accept 显式确认（D3 白名单）。
    """
    try:
        from tools.reflection import generate_reflection, append_reflections

        entries = generate_reflection(state)
        if entries:
            append_reflections(entries)
    except Exception as e:  # noqa: BLE001
        logger.warning("Reflect 节点降级: %s", e)
    return {}


def build_graph(llm: LLMClient, models: dict, tools=None, agents=None, reg: Optional[dict] = None,
               model_override: Optional[str] = None, gate_model_override: Optional[str] = None,
               on_event: Optional[Callable] = None):
    """构建研报状态图。agents=有序角色列表（缺省=registry 内置全量，向后兼容 M8-4 前行为）。

    M8-5 子集编排：图按 agents 动态建节点+边，agent 节点=role.lower()，
    gate 节点=gate_<role.lower()>（内部），gate 由 reg["gate_of"][role] 推导（M9-1 起走 registry）。

    model_override：任务级模型接口覆盖，仅作用于 Agent 节点。
    gate_model_override：任务级审核模型覆盖（方案 A），仅作用于 Gate 节点；
       None = 沿用 model_mapping.yaml 异基座默认。
    on_event：TD-002 实时事件回调（Agent/Gate/Router 节点完成即回调，供前端时间线流式展示）。
    """
    if reg is None:
        reg = load_agent_registry()
    chain = list(agents) if agents else default_chain(reg)
    g = StateGraph(ReportState)
    g.add_node("router", make_router(on_event))
    for role in chain:
        g.add_node(role.lower(),
                   make_agent(role, llm, models, tools, reg=reg,
                              model_override=model_override, on_event=on_event))
        g.add_node("gate_" + role.lower(),
                   make_gate(reg["gate_of"][role], llm, models,
                             is_terminal=(role == chain[-1]), reg=reg,
                             gate_model_override=gate_model_override, role=role,
                             on_event=on_event))
    g.add_node("escalate", node_escalate)
    g.add_node("kb_retrieve", node_kb_retrieve)
    g.add_node("kb_write", node_kb_write)
    g.add_node("reflect", node_reflect)  # M11-2：反思回写节点（落库后生成建议，降级 no-op）；缺则 g.compile() 崩

    g.add_edge(START, "kb_retrieve")
    g.add_edge("kb_retrieve", "router")
    g.add_conditional_edges("router", _make_route_router(chain, reg), {
        **{r.lower(): r.lower() for r in chain},
        "escalate": "escalate", END: "kb_write",  # M11-1：终态先落库再 END
    })
    for role in chain:
        g.add_conditional_edges(role.lower(), _make_route_after_agent(chain, reg), {
            "gate_" + role.lower(): "gate_" + role.lower(), "router": "router",
        })
        g.add_conditional_edges("gate_" + role.lower(), _make_route_after_gate(chain, reg), {
            **{r.lower(): r.lower() for r in chain if chain.index(r) > chain.index(role)},
            "router": "router", "escalate": "escalate", END: "kb_write",  # M11-1：末闸终态先落库
        })
    g.add_edge("escalate", "reflect")
    g.add_edge("kb_write", "reflect")
    g.add_edge("reflect", END)  # M11-2：落库/终态后生成反思建议（降级 no-op）
    return g.compile()


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def run_report(user_task: dict, llm: LLMClient, max_rounds: int = 2,
               model_mapping_path: Optional[str] = None, tools=None,
               tools_config_path: Optional[str] = None,
               agents=None,
               plugins: Optional[list] = None,
               model: Optional[str] = None,
               gate_model: Optional[str] = None,
               on_event: Optional[Callable] = None) -> ReportState:
    """运行一次研报任务。

    tools=None 时按 config/tools.yaml 构建（缺省 provider=tavily，
    无 TAVILY_API_KEY 则按 fallback_to_mock 降级并告警）。

    plugins：本次任务使用的数据源 id 列表（None = 全部 enabled 源）。
    model：任务级模型接口覆盖（仅作用于 Agent 调用；Gate 维持异基座隔离）。
           None / "auto" = 沿用 model_mapping.yaml。
    gate_model：任务级审核模型覆盖（方案 A，ChatEntry 独立「审核模型」下拉）。
           None / "auto" = 沿用 model_mapping.yaml 异基座默认；显式值仅覆盖 Gate 节点。
    """
    models = load_model_mapping(model_mapping_path)
    # M9-1：registry 每次 run 热加载（mirror model_mapping），改完下一个任务即生效。
    reg = load_agent_registry()
    # 模型接口归一：「auto」/None = 不覆盖（沿用 model_mapping.yaml）。
    model_override = None if model in (None, "auto") else model
    # 审核模型归一（方案 A）：与 Agent 覆盖互相独立。
    gate_model_override = None if gate_model in (None, "auto") else gate_model
    if tools is None:
        from tools import build_tools
        # M9-5 / ChatEntry 控制项：按本次任务所选插件过滤数据源（真·控制器）。
        tools = build_tools(tools_config_path, plugin_filter=plugins)
    graph = build_graph(llm, models, tools, agents=agents, reg=reg,
                        model_override=model_override,
                        gate_model_override=gate_model_override, on_event=on_event)
    # TD-002：首轮进度事件（前端进度条从第 1 轮开始推进）
    if on_event:
        try:
            on_event({"event_type": "round_update", "round": 1, "max_rounds": max_rounds})
        except Exception as _ev:
            print(f"⚠️ round 事件推送失败: {_ev}")
    init: ReportState = {
        "user_task": user_task,
        "retrieval_records": [],
        "analysis_conclusions": [],
        "draft_segments": [],
        "report_markdown": "",
        "tool_status": [],
        "prior_versions": {role: [] for role in (list(agents) if agents else default_chain(reg))},
        "search_results": [],
        "report_path": None,
        "routing_state": {
            # TD-009 修复：round 为 1-based 尝试计数。START 直达首个 Agent（add_edge(START, chain[0])），
            # 不经过 router 的 go is None 兜底，故首轮必须由 INIT 计为 1，否则 rework 计数与 max_rounds 边界整体 off-by-one。
            "round": 1, "max_rounds": max_rounds, "last_gate": None, "status": "running",
            "rework_target_agent": None, "rework_reason": None, "gate_review_history": [],
            "engine_events": [],  # 引擎层事件（Agent 产出不可用等），仅审计
        },
        "gate_output": None,
        "agent_result": None,
    }
    final = graph.invoke(init)
    # M9-4 研报推送渠道：研报完成(on_complete) / 升级(on_gate_fail) 按启用渠道真实外发。
    try:
        delivery = push_report(final)           # 热加载 channels.yaml（load_channels）
        if delivery:
            final.setdefault("delivery_status", []).extend(delivery)
    except Exception as e:                        # best-effort：推送失败不阻断研报产出
        final.setdefault("delivery_status", []).append(
            {"channel": "_push", "ok": False, "detail": f"push_report: {e}"})
    return final


def _summarize(final: ReportState) -> str:
    rs = final.get("routing_state", {})
    lines = [
        f"status={rs.get('status')}  round={rs.get('round')}/{rs.get('max_rounds')}  last_gate={rs.get('last_gate')}",
        f"escalate_reason={rs.get('escalate_reason')}",
        f"retrieval_records={len(final.get('retrieval_records', []))}  "
        f"analysis_conclusions={len(final.get('analysis_conclusions', []))}  "
        f"draft_segments={len(final.get('draft_segments', []))}",
        "gate_review_history:",
    ]
    for i, h in enumerate(rs.get("gate_review_history", []), 1):
        lines.append(f"  {i}. {h.get('decision')} | eval={h.get('eval_score')} | {h.get('reason')}")
    evs = rs.get("engine_events", [])
    if evs:
        lines.append(f"engine_events: {len(evs)} 条")
        for e in evs:
            lines.append(f"  - [{e.get('agent')}] {e.get('reason')}")
    ts = final.get("tool_status", [])
    ok_n = sum(1 for t in ts if t.get("ok"))
    lines.append(f"tool_status: {ok_n}/{len(ts)} 成功")
    for t in ts:
        if not t.get("ok"):
            lines.append(f"  - FAIL {t.get('agent')}/{t.get('tool')}: {t.get('error')}")
    if final.get("report_path"):
        lines.append(f"report_path: {final['report_path']}")
    if rs.get("status") == "done":
        lines.append("—— 研报已完成（report_markdown 见 state）——")
    return "\n".join(lines)


if __name__ == "__main__":
    import os
    from tools import build_tools

    mode = os.getenv("MODE", "stub").lower()
    if mode == "real":
        from run_real import main as _run_real
        _run_real()
        raise SystemExit(0)

    scenario = os.getenv("STUB_SCENARIO", "pass")
    llm = StubLLMClient(scenario=scenario)
    task = {
        "topic": "AI 芯片市场研报",
        "scope": ["供给", "需求", "竞争格局"],
        "output_format_spec": "markdown",
        "constraints": ["中文输出"],
    }
    # 真实检索需环境变量 TAVILY_API_KEY；缺失时按 config/tools.yaml 的
    # fallback_to_mock 降级为占位数据并打印醒目告警（不静默冒充真实检索）。
    bundle = build_tools()
    print(f"[tools] web_search={'MOCK(占位)' if bundle.using_mock_search else 'TAVILY(真实)'}"
          f"  data_proc=on  doc_export=on")
    final = run_report(task, llm, max_rounds=2, tools=bundle)
    print(_summarize(final))
