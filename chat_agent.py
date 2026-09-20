"""ChatAgent（对话优先入口，阶段 2：自主调工具 + 自主学习 + 进化）

- 复用引擎已验证的 `_run_fc_loop` / `build_tools` / `MCPClient`，使 ChatAgent 能自主调
  web_search / data_proc / mcp:{server}:{tool}（直接回应 boss「他能自主调 MCP」诉求）。
- 自主学习：每轮先 `kb_retrieve` 召回历史结论；工具支撑的实质性回答 `kb_write` 为 chat_fact。
- 进化：playbook（已采纳经验）注入 system prompt；用户说「记住：…」存为草稿，管理员 accept 后生效
  （对齐 M11-2 D4 诚实边界：绝不静默自改 prompt/代码）。
- generate_report 仍走「意图 → api.py 触发引擎」旧路径（长阻塞任务不混入 fc-loop）。
"""

from __future__ import annotations

import json
import logging
import re
import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ModelNotConfiguredError(RuntimeError):
    """当前账号**没有任何可用的模型端点**（尚未自备 Provider）。

    为什么要单独一个异常类型：`step()` 末尾的通用 `except Exception` 会把所有失败
    吞成一句「抱歉，脑子打结了：…」并返回 HTTP 200。对"模型偶发抽风"这没问题，但对
    "这个账号根本没配置模型 API"是**误导**——用户会以为助手答不出来，而不是"我需要先去配"。

    设计依据：DESIGN_subaccount_model_isolation.md §3-D3。子账号不再继承主账号网关后，
    新注册的子账号必然先命中这个状态，所以必须把话说清楚。
    """


def _llm_has_endpoints(llm: Any) -> bool:
    """LLM 客户端是否至少有一个可路由端点（无端点 = 没配模型 API）。"""
    eps = getattr(llm, "_endpoints", None)
    if eps is None:
        return True  # 非 NewApiLLMClient（如 StubLLMClient/单测替身）→ 不做该判定
    return bool(eps)


CHAT_SYSTEM_PROMPT = """你是「研报助手」，一个能聊天、能调工具、能生成研报、能装 skill 的 AI 助手。

【你的能力】
- 闲聊 / 自我介绍 / 能力说明
- **跨会话长期记忆**：你拥有一层持久化知识库，跨会话保留你沉淀过的研究结论 / 事实 / 来源；回答相关问题时系统会先自动召回（以「你之前积累的相关知识」注入），供你参考。当前为关键词召回，复用关键术语召回最准。
- 回答事实、研究、数据、最新资讯类问题：**必须先用工具联网取证，再综合回答**，**严禁凭空编造数据或来源**。
- 实时天气 / 最新新闻：直接问「厦门天气」「今天热门新闻」即可——web_search 会自动路由到天气/新闻专用源，无需你手动选源。
- 抓取具体链接（抓上网）：用户给出具体 URL、尤其 GitHub 仓库/文档/博客并说「分析/抓取/读一下/看看这个链接/这个仓库讲什么」时，调 fetch_url(url=链接) 抓取正文再综合回答；**不要用 web_search 去盲搜该 URL**（搜索只返回不相关结果），也不要用 run_shell 的 curl（沙箱已拦截）。fetch_url 会去抓 GitHub 仓库的 README、网页正文等真实内容。
- 安装 skill：用户给出 GitHub/市场/网页链接、或说「安装/添加/导入 skill」时，调 install_skill 真·安装（L0 自动装、L1 交管理员审批、L2 拒绝并说明原因）。
- 受限计算/探查：需要跑小段代码或命令时，用 run_python / run_shell（固定沙箱，安全隔离）。
- 生成完整研报：仅当用户明确要「一份 / 详细 / 完整报告 / 研报 / 综述」时，按下方 JSON 协议触发。

【可调用的工具】（由系统按配置动态注入；事实/研究类问题**必须**调用，不要只用内部知识回答）
- web_search：联网检索真实资料（多源聚合，已含实时天气 open-meteo + 最新新闻 rss_news），返回带 url/title/snippet 的检索记录
- data_proc：对检索到的数值做计算/聚合
- mcp:<server>:<tool>：外部 MCP 工具（如 deepwiki 查仓库架构、浏览器自动化等）
- fetch_url：只读抓取**任意** http/https 链接正文（研究/学习用）；GitHub 仓库自动取 README，**也能直接抓仓库内的具体文件**——给它该文件的 blob/raw 直链（.md/.markdown/.txt/.rst 结尾）或任意普通网页均可。分析/读具体链接时调用，不要盲搜 URL
- install_skill：安装外部 skill（GitHub/市场链接），L0 自动装、L1 交审批、L2 拒绝
- run_python：受限沙箱执行 Python（计算/小实验/解析数据）
- run_shell：受限沙箱执行 shell 命令（列举/查看/轻量查询，危险命令被拦截）

【回答事实/研究类问题的强制动作】
1. 如果问题涉及当前/最新/真实资料（含天气/新闻），**先调 web_search 或合适的 mcp 工具取证**；
2. 如果用户提供的是**具体链接（URL）**——尤其是 GitHub 仓库、文档页、博客——**优先调 fetch_url 直接抓取正文**，不要把它当关键词丢给 web_search（那样只会搜出不相关结果），也不要用 run_shell 的 curl（被沙箱拦截）；抓到正文后综合回答，并标注链接来源。
3. 看到工具返回后，综合给出自然语言回答，并标注关键来源；
4. 不要只回一句「我不知道」或「我无法实时获取」——先尝试检索/抓取；
5. 工具返回为空或失败时，**如实说明，并点明是「哪些源」返回了空/失败**（系统回注给你的 failed_sources 已列出具体源与原因；fetch_url 失败会给出明确原因如「SSRF 防护拒绝/HTTP 404/超时」），不要用「我获取不到实时信息」笼统带过；确实一个源都没取到就明说「源返回空」。

【安装 skill 的动作】
- 用户给 GitHub/市场/网页 skill 链接，或说「安装/添加/导入 skill」→ 调 install_skill(url=链接)。
- 结果三种：installed（已安装，告知路径与生效范围）/ pending_approval（已提交管理员审批，告知提案 id）/ rejected（拒绝并说明原因）。如实把结果转述给用户，不要假装能直接生效或夸大。
- **非标准 skill 仓库**（仓库根没有 SKILL.md，例如「一堆 markdown 文档/agent 定义」的仓库）：**正确姿势是拿定具体文件
  的 blob/raw .md 直链后直接调 install_skill，不要先用 fetch_url 预读内容** —— install_skill 自己完成
  抓取→定级→适配→安装/落提案全流程；fetch_url 预读是多余一跳，还会在网络抖动时把你带偏。**不要**说「装不了 / 格式不支持」。
- 装仓库根报「没定位到 SKILL.md」时，**错误信息里会附上该仓库内的 .md 候选清单**（可能长达几十上百个，如 agency-agents
  这类多文件 agent 仓库）。直接从中挑用户想要的那个，把它的 URL 交给 install_skill；不要用漏掉的路径去瞎猜。
- **fetch/install 失败 ≠ 转头去 web_search**：搜索永远装不了 skill。失败时先看清具体原因（SSL 抖动/404/超时），
  对同一 URL **直接重试一次**（TLS 抖动很常见，再试一次往往就成），重试仍败就如实告知失败原因，
  **严禁假装「用搜索替代了安装」或含糊其辞**。
- **含代码块的第三方 agent 定义不会拒收**：这类文件只是进来当提示词文本用（引擎从不执行其中的代码），
  所以它的定级是 **L1 —— 落提案等管理员审批，审批后即可安装**，不是「格式不支持 / 装不了」。
  只有真正带执行意图的（声明 allowed-tools、shebang、subprocess/eval 调用、引用 scripts/ 目录）才会被 L2 拒绝。
  把 install_skill 返回的 pending_approval + 提案 id 如实告诉用户即可。

【诚实红线（不可违反）】
你**能够**联网：fetch_url 可抓任意 http/https 链接（含 GitHub 上的任意文件与 raw 直链），web_search 可联网检索，
mcp:<server>:<tool> 可调外部 MCP 工具。**严禁**对用户谎称「我无法访问 GitHub / 我无法下载 / 我没有联网能力 / 我的工具只有 XXX」——
那是彻头彻尾的不实陈述（刚用它抓完 README 却说抓不了，最伤信任）。
真实边界只有：私有/内网地址被 SSRF 防护拒绝、目标返回 404/超时、配额不足、缺少密钥、或工具确实报错。
这些都须**如实给出具体原因**（如「HTTP 404 / SSRF 拒绝 / 超时 15s / 网关不可达」），不得泛化成
「我做不到」「这是架构限制」「我底层不支持」。是能力不足就说能力不足，有别的原因就说那个真原因。

【何时触发研报生成（JSON 协议）】
仅当用户明确要一份完整研报时，输出严格 JSON（不要 markdown 代码块）：
{"intent":"generate_report","report":{"topic":"...","scope":["行业概况"],"constraints":[],"plugins":["tavily"]}}
topic 必填且 >= 4 字。其余对话场景一律用自然语言回答，不要输出该 JSON。

【你的长期记忆（跨会话知识库）】
- 你**确实拥有**跨会话持久记忆：每轮对话前，系统会按用户问题尝试从知识库召回你此前沉淀过的相关结论、事实与来源；**召回命中时**才注入本系统提示（标题为「你之前积累的相关知识」），未命中则不注入。
- 沉淀由系统自动完成：你对事实/研究类问题做出过工具取证的实质性回答后，系统会将其写入知识库（你无需手动存；用户说「记住：…」则是另一条经验草稿通道，需管理员采纳才生效）。
- 召回当前为**关键词匹配**（嵌入模型暂不可用，走中文 bigram + 英文词匹配）：复用关键术语的提问召回最准；换种说法、零词面重叠的提问可能召回不到——这是真实边界，**绝不要假装「记得」实际没召回到的内容**，也别在被问「你记得吗」时否认你有记忆。
- 当知识库命中与当前问题相关时，主动参考并在回答中体现（标注来源）；被问「你还记得 / 你之前说过」时，如实说明你确实能跨会话复用已沉淀的知识。

【检索数据的诚实边界（硬规则）】
- 工具结果里出现「unavailable_sources / failed_sources」或 UI 提示「某数据源未参与检索」时，你**必须如实说明**：明确指出哪些来源本次不可用、因此没有实时检索数据。
- **绝不允许**在未取得真实检索数据的情况下，编造带机构署名的数据（如「XX 咨询：市场规模 XXXX 亿」「某公司年报显示…」）来填充报告或答案。
- 若只能依靠你自身的先验知识作答，必须在答案中显著标注「以下内容来自模型自身知识，**未经本次检索验证**」，并说明建议核实的方向；宁可不给数字，也不给不可核验的数字。

【输出格式】
- 普通回答：直接自然语言（可含要点列表）。
- 触发研报：仅输出上面的 JSON。"""

_TOOL_HINT = "\n【本轮你可用的工具】"

# 注入预算闸：防止 kb_ctx + playbook + history 叠加超出模型上下文（new-api 400 input length too long）。
# 字符预算（中文 ~1.5 token/字，LongCat-2.0 上下文有限，留足余量给工具结果）。
_KB_CTX_BUDGET = 1200      # 知识召回注入总字符上限
_KB_HIT_TRUNC = 300        # 单条召回正文截断
_HISTORY_BUDGET = 2400     # 历史注入总字符上限
_HISTORY_MSG_TRUNC = 350   # 单条历史消息截断
_PLAYBOOK_BUDGET = 1500    # 已采纳经验 playbook 注入截断


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _safe_parse(raw: str) -> dict:
    """严格解析 generate_report 意图（复用既有契约：topic>=4 校验）。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"intent": "chat", "reply": text or "（空回复）"}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("chat_agent JSON 解析失败: %s | raw=%r", e, text[:300])
        return {"intent": "chat", "reply": text}
    if not isinstance(data, dict):
        return {"intent": "chat", "reply": text}
    intent = data.get("intent", "chat")
    reply = (data.get("reply") or "").strip()
    if intent not in ("chat", "generate_report"):
        intent = "chat"
    if not reply:
        reply = "好的。"
    out: dict[str, Any] = {"intent": intent, "reply": reply}
    if intent == "generate_report":
        report = data.get("report") or {}
        topic = (report.get("topic") or "").strip()
        if not topic or len(topic) < 4:
            return {
                "intent": "chat",
                "reply": "主题有点模糊，能再具体说说你想看哪方面的研报吗？比如行业、公司、时间范围？",
            }
        out["report"] = {
            "topic": topic,
            "scope": report.get("scope") or [],
            "constraints": report.get("constraints") or [],
            "plugins": report.get("plugins"),
        }
    return out


def _classify(raw: str) -> dict:
    """从 fc-loop 终态文本判定意图。仅当文本是含 intent=generate_report 的 JSON 才走研报路径，否则当普通回复。"""
    text = _strip_fence(raw)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and d.get("intent") == "generate_report":
                return _safe_parse(raw)
        except json.JSONDecodeError:
            pass
    return {"intent": "chat", "reply": text or raw}


def _tool_list_hint(schemas: list) -> str:
    names = [s.get("function", {}).get("name") for s in schemas if s.get("function", {}).get("name")]
    if not names:
        return "（当前无可用外部工具）"
    return " " + "、".join(names) + "。调用格式由 function-calling 协议决定，你直接决定何时调用即可。"


# ⚠️ 旧实现是「一次性记忆、永不失效」的全局单例：第一个账号读到的 tool_model
# 会被后续所有账号复用 → 跨账号串模型（DESIGN_account_hierarchy §2.3）。
# 改为**按账号维度**缓存：键 = 当前账号 id。
_CHAT_TOOL_MODEL: dict = {}


def _model_mapping_path() -> Path:
    """按当前账号命名空间解析 model_mapping.yaml（各自一套模型）。"""
    try:
        from server import tenancy
        return tenancy.config_path("model_mapping.yaml")
    except ImportError:
        return Path(__file__).resolve().parent / "config" / "model_mapping.yaml"


def _account_key() -> str:
    try:
        from server import tenancy
        return tenancy.current_account_id(allow_none=True) or "-"
    except Exception:
        return "-"


def _load_chat_tool_model() -> Optional[str]:
    """从 model_mapping.yaml 读取 chat.tool_model（按账号缓存）。"""
    key = _account_key()
    if key in _CHAT_TOOL_MODEL:
        return _CHAT_TOOL_MODEL[key] or None
    try:
        import yaml

        data = yaml.safe_load(_model_mapping_path().read_text(encoding="utf-8")) or {}
        chat_cfg = data.get("chat") or {}
        _CHAT_TOOL_MODEL[key] = chat_cfg.get("tool_model")
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 chat.tool_model 失败（降级）: %s", e)
        _CHAT_TOOL_MODEL[key] = ""
    return _CHAT_TOOL_MODEL[key] or None


def _resolve_chat_model(user_model: Optional[str]) -> str:
    """为 function-calling 工具环选择模型。

    universal alias（auto / auto-chat 等）可能路由到不支持 tools 或配额耗尽的渠道，
    导致 ChatAgent 不能自主调工具。故默认 fallback 到 model_mapping.yaml 的 chat.tool_model。
    用户显式指定具体模型时，尊重其选择。
    """
    if user_model and user_model not in ("auto", "auto-chat"):
        return user_model
    return _load_chat_tool_model() or user_model or "auto-chat"


def _build_sources(tool_entries: list) -> list:
    """从 tool_entries 抽取来源（含 web_search 真实检索记录 + mcp 外部工具结果）。

    供前端展示「来源」与后端学习（chat_learn_write）共用。纯派生，不影响引擎。

    - web_search：result 是检索记录列表，每条带 source/url/title/snippet -> 真实引用
    - mcp:*：外部工具，result 为结构化/文本 -> 以工具全名作为来源标识
    - data_proc：派生计算，不是来源，跳过（其使用已体现在 tool_calls）

    **占位数据不是来源**（2026-09-19 修真缺陷）：带 `is_mock` 的记录一律丢弃。
    此前缺密钥的源会静默产出 `[MOCK] …` 占位，前端把它渲染成「来源（8）」——
    用户看到 8 条「引用」，实际一条都不可核验，且真源结果被挤出展示位。
    """
    sources: list = []
    for t in tool_entries:
        if not isinstance(t, dict) or not t.get("ok"):
            continue
        res = t.get("result")
        name = t.get("tool") or "tool"
        if name == "web_search" and isinstance(res, list):
            for item in res:
                if isinstance(item, dict):
                    if item.get("is_mock"):
                        continue  # 占位数据：不得冒充引用
                    src = item.get("source") or item.get("url") or ""
                    if src:
                        sources.append({
                            "source": src,
                            "url": item.get("url"),
                            "title": item.get("title"),
                            "snippet": item.get("snippet"),
                            "tool": name,
                        })
        elif name.startswith("mcp:") and res is not None:
            text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
            sources.append({
                "source": name,
                "tool": name,
                "title": name,
                "content": text[:300],
            })
        elif name == "fetch_url" and isinstance(res, dict):
            # 只读抓取结果：final_url 作为来源，正文前 300 字作为内容摘要
            final = res.get("final_url") or res.get("url") or ""
            sources.append({
                "source": final or name,
                "url": final,
                "title": res.get("title") or final or "抓取链接",
                "content": (res.get("text") or "")[:300],
                "tool": name,
            })
        # data_proc 等：跳过（非来源）
    return sources


def _source_warnings(bundle, tool_entries: list) -> tuple[list[str], bool]:
    """诚实降级（2026-09-19 · DESIGN_source_honesty §3.3）：把「检索面不完整」讲清楚。

    返回 (warnings, sources_are_real)：

    - warnings：逐条人类可读告警。来源有两类：
      ① 本次**未装载**的源（`SearchTool.degraded`，如缺密钥的 tavily）——附修复指引；
      ② 曾收到但被丢弃的**占位记录**（`is_mock`）——明说它们不可作为事实依据。
      **仅当本轮真的发起过 web_search** 才报 ①：闲聊不检索时不弹「某源未配置」，
      否则每条无关对话都挂一条警告，用户会学会无视它。
    - sources_are_real 的**精确定义**（前端据此决定是否展示「来源（N）」）：
      本轮没发起检索 → True（无可质疑之处）；
      发起了检索 → 仅当「无降级源 且 无占位记录被丢弃」时为 True。
      宁可少展示，也不让用户把占位当引用。

    **绝不抛异常**：告警是附加可观测性，任何异常都不允许让 /chat 变 500。
    """
    warnings: list[str] = []
    try:
        entries = [t for t in (tool_entries or []) if isinstance(t, dict)]
        attempted_ws = any(t.get("tool") == "web_search" and t.get("ok") for t in entries)

        mock_dropped = 0
        for t in entries:
            if t.get("tool") != "web_search":
                continue
            for item in (t.get("result") or []):
                if isinstance(item, dict) and item.get("is_mock"):
                    mock_dropped += 1
        if mock_dropped:
            warnings.append(
                f"本次检索包含 {mock_dropped} 条占位数据，已从「来源」中剔除，不可作为事实依据。")

        degraded = list(getattr(getattr(bundle, "web_search", None), "degraded", None) or [])
        if attempted_ws:
            for d in degraded:
                if not isinstance(d, dict):
                    continue
                name = d.get("name") or d.get("id") or "未知数据源"
                why = "未配置密钥" if d.get("reason") == "missing_key" else str(d.get("reason"))
                fix = d.get("fix") or "请在「设置 → 数据源」中检查该插件配置"
                warnings.append(
                    f"数据源「{name}」{why}，本次未参与检索，回答未使用该来源。修复：{fix}")

        sources_are_real = (not attempted_ws) or ((not degraded) and mock_dropped == 0)
        return warnings, sources_are_real
    except Exception as e:  # noqa: BLE001 — 可观测性不得影响主链路
        logger.debug("source_warnings 构造降级: %s", e)
        return warnings, True


def _resolve_expert_context(user_text: str) -> tuple[str, Optional[dict]]:
    """解析本轮对话应启用的专家上下文（M12-4 · DESIGN_M12-4 §2.1）。

    自 M12-5 起委托 `tools.experts.resolve_expert`（单一解析入口，DRY，
    闭环 DESIGN_M12 §5.1）：解析逻辑只一份，专家 = 上层封装。

    **绝不抛异常**：专家是增强项，它的任何故障都不允许让 /chat 变 500（V8 闭环）。
    返回 (注入片段, 专家元信息)；未启用专家时为 ("", None)。
    """
    try:
        from tools import experts as ex
    except Exception as e:  # noqa: BLE001
        logger.debug("专家模块不可用，跳过专家接入：%s", e)
        return "", None
    return ex.resolve_expert(user_text)


class ChatAgent:
    """对话优先 Agent；阶段 2 = function-calling + 知识层（复用引擎基础设施）。"""

    def __init__(self, llm, bundle: Optional[Any] = None):
        self.llm = llm
        self._bundle = bundle  # 懒构建；按配置 mtime 热重载（见 _get_bundle）
        self._bundle_mtime = None  # 上次构建时的配置 mtime（None=未构建）
        # 【M12 修回归】ca8d010 的热重载把「显式注入的 bundle」也一并覆盖了：
        # _bundle_mtime 初始为 None，与 _config_mtime() 必不相等 → 首调即被 build_tools()
        # 替换。后果：① 测试注入的 FakeBundle 被换成真 bundle，单测打真网（日志出现
        # 429/SSL EOF/超时，属测试不纯）；② 破坏依赖注入契约。
        # 生产路径 ChatAgent(llm=...) 不注入 bundle，热重载语义不受影响。
        self._bundle_injected = bundle is not None

    def _get_bundle(self):
        # M11 修复：bundle 须随检索配置热重载。此前 ChatAgent 单例把 build_tools() 结果
        # 缓存整个进程生命周期，容器不重启时旧 bundle 缺 open_meteo 等新源 →
        # 「UI 已启用但引擎不取数」的假配置（违背「UI 是 controller」立约）。
        # 改为按 plugins.yaml / tools.yaml / mcp_servers.yaml / .secrets/plugins.env 的
        # mtime 失效缓存：改完下一个 /chat 即生效（零重启，对齐 agents/gates 热加载）。
        if self._bundle_injected:
            return self._bundle
        if self._bundle is None or self._bundle_mtime != self._config_mtime():
            from tools import build_tools

            self._bundle = build_tools()
            self._bundle_mtime = self._config_mtime()
        return self._bundle

    def _config_mtime(self) -> float:
        """检索相关配置的最新 mtime（用于 bundle 热重载失效判定）。

        必须按**当前账号**的命名空间取 mtime：否则 A 改了自己的配置，
        B 的 bundle 要么误失效要么不失效（跨账号串号/漏重载）。
        """
        try:
            from server import tenancy
            root = tenancy.account_root()
        except Exception:
            root = Path(__file__).resolve().parent
        paths = [
            root / "config" / "plugins.yaml",
            root / "config" / "tools.yaml",
            root / "config" / "mcp_servers.yaml",
            root / ".secrets" / "plugins.env",
        ]
        m = 0.0
        for p in paths:
            try:
                m = max(m, p.stat().st_mtime)
            except OSError:
                pass
        return m

    def step(self, history: list[dict], user_msg: str, model: str | None = None,
             cancel=None) -> dict:
        """跑一轮对话。

        cancel: 可选 `threading.Event`，由 API 层在检测到浏览器断开时置位；
                置位则本轮在工具循环的轮次边界中止，返回 `{"cancelled": True}`
                （调用方据此**整轮不落库**，见 DESIGN_chat_interrupt_edit.md §3.1）。
        """
        from orchestrator import _run_fc_loop, _build_tool_schemas, LoopCancelled
        from tools._async_util import run_async
        from tools.chat_learning import (
            load_playbook,
            chat_learn_write,
            propose_lesson,
        )

        user_text = (user_msg or "").strip()

        # 前置闸：本账号一个模型端点都没有 → 直接抛"未配置"，不要进入 fc-loop
        # （进去必然失败，且会被下面的通用 except 吞成一句敷衍回复 + HTTP 200，掩盖真实原因）。
        if not _llm_has_endpoints(self.llm):
            raise ModelNotConfiguredError(
                "当前账号尚未配置模型 API：请到「设置 → 自定义 API」添加你自己的 Provider"
                "（base_url + API Key），再选择模型。子账号不共享主账号的 new-api 网关。"
            )

        # 0) 「记住：…」→ 存为经验草稿（进化·写入侧，需管理员 accept 才生效）
        if user_text.startswith("记住：") or user_text.startswith("记住:"):
            body = user_text.split("：", 1)[-1].split(":", 1)[-1].strip()
            ok = propose_lesson(body)
            return {
                "intent": "chat",
                "reply": (
                    "已存入经验草稿，待管理员在「设置 → 经验」中采纳后生效。"
                    if ok
                    else "经验草稿保存失败（配置不可写）。"
                ),
                "tool_calls": [],
            }

        bundle = self._get_bundle()
        mcp = getattr(bundle, "mcp", None)

        # 1) 历史知识召回（自主学习·读）
        kb_ctx = ""
        try:
            from tools.kb_store import kb_retrieve

            hits = run_async(kb_retrieve(user_text, k=3))
            if hits:
                lines = []
                for i, h in enumerate(hits[:3], 1):
                    title = h.get("title") or f"条目{i}"
                    body = (h.get("content") or "")[:600]
                    lines.append(f"{i}. {title}：{body}")
                kb_ctx = (
                    "\n\n【你之前积累的相关知识（供参考，须自行核实时效性）】\n"
                    + "\n".join(lines)
                    + "\n"
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("chat KB 召回降级: %s", e)

        playbook = (load_playbook() or "")[:_PLAYBOOK_BUDGET]
        tool_schemas = _build_tool_schemas(bundle, mcp, role="ChatAgent")
        # M12-4：专家团接入（显式 @ / 隐式路由；任何故障降级为「不注入」，V8 闭环）
        expert_ctx, expert_meta = _resolve_expert_context(user_text)
        # M12-4：对话入口技能上下文（role=chat）。补 DESIGN_M12 §7 V1 的 /chat 侧实证 ——
        # 此前只验了研报流水线的 build_skill_context("researcher")，/chat 根本读不到技能。
        skill_ctx = ""
        try:
            from tools.skills import build_skill_context

            skill_ctx = build_skill_context("chat")
        except Exception as e:  # noqa: BLE001
            logger.warning("对话技能上下文注入降级（不注入，对话照常）：%s", e)
        system = (CHAT_SYSTEM_PROMPT + expert_ctx + skill_ctx
                  + _TOOL_HINT + _tool_list_hint(tool_schemas) + playbook + kb_ctx)
        # 注入当前日期：此前模型不知「今天」是几号（实测把 9 月答成 12 月），直接影响
        # 「今天/最新/近期」类查询的取数与作答基准。日期是实时基准，必须显式给。
        system += (
            f"\n\n【当前日期】{datetime.date.today().isoformat()}"
            "（涉及「今天/昨天/最近/最新」的问题以此为准；实时数据仍须调工具取证，不得凭记忆作答。）\n"
        )

        # 2) 历史 → user blob（带预算闸，超长历史截断防 400）
        msgs: list[str] = []
        budget = _HISTORY_BUDGET
        for h in (history or [])[-16:]:
            role = h.get("role")
            content = (h.get("content") or "").strip()[:_HISTORY_MSG_TRUNC]
            if role in ("user", "assistant") and content:
                seg = f"[{role}] {content}"
                if budget - len(seg) < 0:
                    break  # 预算耗尽，丢弃更早历史（保留最近若干轮）
                budget -= len(seg)
                msgs.append(seg)
        msgs.append(f"[user] {user_text or '你好'}")
        user_blob = "\n".join(msgs)

        # 对话入口模型：universal alias（auto-chat）可能不支持 tools 或配额耗尽，
        # 故为工具环 fallback 到 model_mapping.yaml 的 chat.tool_model（默认 LongCat-2.0）。
        # M12-4：专家可带模型偏好（注册表 model 字段）。用户显式指定时以用户为准，否则用
        # 专家偏好 —— 否则注册表里的 model 就是「能填、但运行时不消费」的假配置。
        chat_model = _resolve_chat_model(model or (expert_meta or {}).get("model") or None)
        try:
            raw, tool_entries, extra_search = _run_fc_loop(
                self.llm,
                chat_model,
                system,
                user_blob,
                tool_schemas,
                bundle,
                mcp,
                role="ChatAgent",
                shape="researcher",
                cancel=cancel,
            )
        except LoopCancelled:
            # 「用户主动停止」不是故障：必须在这里截住，绝不能落到下面的通用 except
            # 被伪装成「抱歉，脑子打结了」——那会让用户以为系统坏了（设计 §3.1）。
            logger.info("ChatAgent 轮次被用户中止（本轮不落库）")
            return {"intent": "chat", "reply": "", "tool_calls": [], "cancelled": True}
        except Exception as e:  # noqa: BLE001
            # 上游/网关故障 ≠ 内部 bug，必须分开说。
            # 旧实现对两者一律回「抱歉，脑子打结了：…」+ HTTP 200，把
            # 「你的 provider 返 503」「key 失效」「通道限流」这类**可执行**的真实原因
            # 伪装成系统抽风 —— 用户既不知道发生了什么，也没法自己修。
            # 2026-09-19 实测：子账号 provider 返 503，用户只看到一句「脑子打结」。
            from orchestrator import LLMError

            if isinstance(e, LLMError):
                logger.warning("ChatAgent 上游模型调用失败（如实上报，不伪装成内部故障）: %s", e)
                return {
                    "intent": "chat",
                    "reply": (
                        "⚠️ 模型服务调用失败（不是你的操作问题，本轮没有产生任何结论）。\n"
                        f"真实原因：{e}\n"
                        "可排查：该账号的 Provider/模型配置、上游余额与限流、网关联通性。"
                    ),
                    "tool_calls": [],
                    "upstream_error": str(e),
                }
            logger.exception("chat_agent fc-loop 失败")
            return {"intent": "chat", "reply": f"抱歉，脑子打结了：{e!s}", "tool_calls": []}

        parsed = _classify(raw)
        intent = parsed.get("intent", "chat")
        reply = parsed.get("reply") or _strip_fence(raw)

        # 3) 沉淀（自主学习·写）：任一工具成功且非研报意图即沉淀
        #    （门槛从 extra_search 改为「任一 tool ok」，使 MCP-only 回答也能被学习。
        #     R1 修复：此前仅 web_search 进 extra_search，MCP 结果既不进 sources 也不学习。）
        if intent == "chat" and any(t.get("ok") for t in tool_entries if isinstance(t, dict)) and reply:
            try:
                run_async(chat_learn_write(user_text, reply, tool_entries, extra_search))
            except Exception as e:  # noqa: BLE001
                logger.debug("chat 知识沉淀降级: %s", e)

        tool_calls = [t.get("tool") for t in tool_entries if isinstance(t, dict)]
        # sources 从 tool_entries 派生（web_search 真实引用 + mcp 外部工具结果），见 _build_sources。
        # 占位（is_mock）记录已被 _build_sources 剔除，不会冒充「来源」。
        sources = _build_sources(tool_entries)[:8]
        # 诚实降级告警（DESIGN_source_honesty §3.3）：缺密钥/未装载的源、被剔除的占位记录。
        source_warnings, sources_are_real = _source_warnings(self._get_bundle(), tool_entries)
        # 诊断日志：记录本轮 web_search 各**真实**源命中数，便于 `docker compose logs` 确认
        # 「页面不通」究竟是链路未触发（open_meteo/duckduckgo 未出现）还是模型未采纳。
        _ws_src: dict[str, int] = {}
        for s in sources:
            if s.get("tool") == "web_search" and s.get("source"):
                _ws_src[s["source"]] = _ws_src.get(s["source"], 0) + 1
        if _ws_src:
            logger.info("[chat] web_search 各源命中=%s tool_calls=%s", _ws_src, tool_calls)
        if source_warnings:
            # 降级必须是**显式**的：此前缺密钥的源静默产出占位，日志反倒显示「命中 8 条」，
            # 排查方向被日志本身带偏。这行是本次修复的核心可观测性证据。
            logger.warning("[chat] 检索降级 degraded=%s tool_calls=%s",
                           [d.get("id") for d in (getattr(self._get_bundle().web_search, "degraded", None) or [])],
                           tool_calls)
        out: dict[str, Any] = {
            "intent": intent,
            "reply": reply,
            "tool_calls": tool_calls,
            "sources": sources,
            # 诚实降级：前端据此显示醒目告警 / 决定是否展示「来源（N）」
            "source_warnings": source_warnings,
            "sources_are_real": sources_are_real,
            # M12-4：本轮启用的专家（未启用为 None）。可观测：前端/日志可据此判断
            # 到底有没有套上专家，而不是靠猜。
            "expert": expert_meta,
        }
        if intent == "generate_report" and "report" in parsed:
            out["report"] = parsed["report"]
        return out
