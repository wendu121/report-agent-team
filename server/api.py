"""
REST 接口实现
M6-2 产出：REST API 路由
实现 API_SPEC.md §2 定义的所有 REST 接口
"""

from fastapi import APIRouter, HTTPException, status, Response, Depends
from pydantic import BaseModel, Field, ValidationError
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime
from enum import Enum
from pathlib import Path
import os
import re

BASE_DIR = Path(__file__).parent.parent

import uuid
import json
import asyncio
import logging
import time
import traceback

# 多租户：所有本路由下的端点都先经 get_current_account 绑定租户 ContextVar；
# 无有效 JWT / X-Admin-Token 一律 401，绝不「没人说话就当全局」（假隔离）。
from server.auth import get_current_account

router = APIRouter(dependencies=[Depends(get_current_account)])
logger = logging.getLogger(__name__)

# ============================================================================
# 引擎进程管理器（M6-3 集成）
# ============================================================================

from .engine_client import EngineProcessManager, EngineStateStore

# 状态目录
STATE_DIR = Path(__file__).parent.parent / ".engine_state"

# 全局引擎进程管理器
engine_manager = EngineProcessManager(STATE_DIR)

# 状态存储
state_store = EngineStateStore(STATE_DIR)

# TD-002 修复：服务进程持有 WS 连接（manager），需把引擎子进程写出的事件广播出去。
# 引擎跑在独立子进程，子进程内的 websocket.manager 是空连接表（跨进程共享不了），
# 故事件经 JSONL 文件转交：子进程写 → 服务进程并发 tail → manager.broadcast。
from .websocket import manager as _ws_manager

# 输出渲染（DESIGN_OUTPUT_RENDERING.md v1.0）：doc_render 在渲染函数内部惰性 import
# 文档库（python-docx / python-pptx / reportlab），故此处顶层导入不会让容器因缺库而启动失败。
from urllib.parse import quote
from .audit_export import AuditExportGenerator
from tools.doc_render import (  # noqa: E402
    RendererUnavailable, parse_markdown, render_docx, render_pdf, render_pptx,
)


async def _stream_engine_events(task_id: str, events_file: Path):
    """并发 tail 引擎事件 JSONL，逐条广播到 WS（TD-002 实时时间线）。

    - 每 0.3s 扫描一次新行；按行号增量读取（不依赖字节偏移，避免半行被截）。
    - 任务完成后被 cancel，cancel 时再 flush 一次收尾（catch wait() 后落盘的最终事件）。
    """
    sent = 0

    async def _flush() -> int:
        try:
            if events_file.exists():
                with open(events_file, "r", encoding="utf-8") as _f:
                    lines = _f.read().splitlines()
                for _line in lines[sent:]:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _ev = json.loads(_line)
                    except json.JSONDecodeError:
                        continue
                    await _ws_manager.broadcast(task_id, _ev)
                return len(lines)
        except Exception as _e:
            print(f"⚠️ 引擎事件广播异常: {_e}")
        return sent

    try:
        while True:
            sent = await _flush()
            await asyncio.sleep(0.3)
    except asyncio.CancelledError:
        await _flush()  # 收尾 flush，确保 wait() 后落盘的事件全部广播
        raise


# ============================================================================
# 数据模型定义（对齐 API_SPEC.md）
# ============================================================================


class TaskStatus(str, Enum):
    """任务状态枚举"""
    RUNNING = "running"
    DONE = "done"
    ESCALATED = "escalated"
    ABORTED = "aborted"


class UserTask(BaseModel):
    """用户任务定义（对齐 DESIGN.md §7）"""
    topic: str = Field(..., description="研究主题")
    scope: List[str] = Field(..., description="研究范围维度列表")
    output_format_spec: str = Field(..., description="输出格式规范")
    constraints: List[str] = Field(default_factory=list, description="约束条件列表")


class EngineEvent(BaseModel):
    """引擎层事件（对齐 schema.md §4.1）"""
    event: Literal[
        "agent_output_unusable",
        "writer_citation_regenerated",
        "gate_llm_unavailable_degraded_advance",
        "researcher_records_synthesized"
    ]
    # M10-P4：agent/gate 名不再闭集（自定义 Agent/闸由 registry 驱动），见 RoutingState 注释。
    agent: Optional[str] = None
    gate: Optional[str] = None
    reason: str
    round: int
    timestamp: str


class GateReview(BaseModel):
    """Gate 审计记录"""
    decision: Literal["advance", "rework", "escalate"]
    reason: str
    eval_score: float
    problem_points: List[str]
    gate: str
    round: int
    timestamp: str


class RoutingState(BaseModel):
    """路由状态（对齐 DESIGN.md §7）"""
    round: int
    max_rounds: int
    # M10-P4：闸名/角色名由 config/agents_library.yaml 驱动，用户可新增自定义闸（GateD…）
    # 与自定义 Agent（如 EcomAnalyst）。原先的 Literal 闭集会把自定义闸/角色判为非法值，
    # 使 `RoutingState(**routing)` 抛 ValidationError、整个任务投影崩掉（已实测复现）。
    # 故改为 str + 非空约束：合法集合由引擎层 registry 保证，不在传输层做闭集硬编码。
    last_gate: Optional[str] = None
    status: TaskStatus
    rework_target_agent: Optional[str] = None
    rework_reason: Optional[str] = None
    gate_review_history: List[GateReview] = Field(default_factory=list)
    engine_events: List[EngineEvent] = Field(default_factory=list)


class RetrievalRecord(BaseModel):
    """检索记录"""
    id: str
    url: str
    title: str
    snippet: str
    credibility: Literal["high", "medium", "low"]
    # M10-P3/M9-2：多源聚合后每条记录带 source（= 数据源插件 id，如 taobao_suggest）。
    # 原先模型未声明该字段 → 序列化时 Pydantic 报 SerializationUnexpectedValue 告警
    # （数据仍会透传，但污染日志、且前端 TS 类型拿不到该字段）。此处补声明对齐。
    source: Optional[str] = None


class AnalysisConclusion(BaseModel):
    """分析结论"""
    id: str
    claim: str
    source_ids: List[str]
    # M10-P4 对齐（实测）：引擎产出的是**字符串枚举** high|medium|low
    # （见 agents/analyst.md 的输出契约，LLM 按此产出并原样透传），
    # 而本模型原声明为 float、API_SPEC.md §501 的示例也写作 0.85，二者与实现不符。
    # 因 prompt 才是运行时真实契约且无代码消费该字段做数值计算，此处改从实现。
    # ⚠️ API_SPEC.md 该示例待同步（已记入 VERIFICATION_M10-P4.md 遗留项）。
    confidence: Optional[str] = None


class DraftSegment(BaseModel):
    """草稿片段"""
    id: str
    section: str
    content: str
    conclusion_ids: List[str]


class ToolStatus(BaseModel):
    """工具状态"""
    # M10-P4：自定义 Agent（如 MarketScout）会出现在此，故不做闭集校验。
    agent: str
    tool: str
    # M9-2：多源聚合后每条工具状态带 source（= 数据源插件 id）。
    source: Optional[str] = None
    ok: bool
    # 成功时引擎不写 error 键（而非写 null），故给默认值避免 Required 告警。
    error: Optional[str] = None


class TaskResponse(BaseModel):
    """任务响应（GET /tasks/{id}）"""
    task_id: str
    status: TaskStatus
    created_at: str
    updated_at: str
    user_task: UserTask
    routing_state: RoutingState
    retrieval_records: List[RetrievalRecord] = Field(default_factory=list)
    analysis_conclusions: List[AnalysisConclusion] = Field(default_factory=list)
    draft_segments: List[DraftSegment] = Field(default_factory=list)
    tool_status: List[ToolStatus] = Field(default_factory=list)
    prior_versions: dict = Field(default_factory=dict)
    report_markdown: Optional[str] = None
    escalate_reason: Optional[str] = None
    template_id: Optional[str] = None
    agents: List[str] = Field(default_factory=list)
    gates: List[str] = Field(default_factory=list)
    # M9-5 / ChatEntry 控制项：本次任务的数据源 + 模型接口（回显）。
    plugins: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    # 方案 A：审核模型(Gate)覆盖回显（None/"auto"=异基座默认）。
    gate_model: Optional[str] = None
    # 多租户：建任务时服务端注入 owner_id（客户端传一律忽略）；越权访问按 owner 过滤/404。
    owner_id: Optional[str] = None


class TaskHistoryItem(BaseModel):
    """任务历史列表项（侧边栏）"""
    task_id: str
    topic: str
    status: TaskStatus
    created_at: str
    updated_at: str
    owner_id: Optional[str] = None


class CreateTaskRequest(BaseModel):
    """创建任务请求（POST /tasks）"""
    user_task: UserTask
    template_id: Optional[str] = None
    # M9-1：显式指定 Agent 子集（智能体市场「对话」入口传 ?agents=A,B）。
    # 非空时覆盖模板的 agents/gates，gates 由 registry 配对派生（防孤儿闸）。
    agents: Optional[List[str]] = None
    # M9-5 / ChatEntry 控制项：本次任务使用的数据源 id 列表 + 模型接口。
    # plugins 非空 = 仅使用所选数据源；model 非空 = Agent 调用覆盖（"auto"/None=沿用 model_mapping）。
    plugins: Optional[List[str]] = None
    model: Optional[str] = None
    # 方案 A：独立「审核模型(Gate)」下拉覆盖。None/"auto" = 沿用 model_mapping.yaml 异基座默认；
    # 显式值 = 覆盖 Gate 节点（仅 Gate，与 Agent 模型覆盖互相独立）。
    gate_model: Optional[str] = None


class CreateTaskResponse(BaseModel):
    """创建任务响应"""
    task_id: str
    status: TaskStatus
    created_at: str
    user_task: UserTask
    template_id: Optional[str] = None
    agents: List[str] = Field(default_factory=list)
    gates: List[str] = Field(default_factory=list)
    plugins: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    # 方案 A：审核模型(Gate)覆盖回显。
    gate_model: Optional[str] = None


class ReviewAction(str, Enum):
    """复核操作类型"""
    CONFIRM = "confirm"
    RETRY = "retry"
    ABORT = "abort"


class ReviewRequest(BaseModel):
    """复核请求（POST /tasks/{id}/review）"""
    action: ReviewAction
    reviewer_comment: str = ""
    # M10-P4：重试目标可为自定义 Agent（如 EcomAnalyst），故不做闭集校验。
    rework_target_agent: Optional[str] = None


class ReviewResponse(BaseModel):
    """复核响应"""
    task_id: str
    status: TaskStatus
    message: str


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    message: str
    details: dict = Field(default_factory=dict)
    timestamp: str


# ============================================================================
# 内存存储（临时实现，M6-4 会替换为 PostgreSQL/Redis）
# ============================================================================

_tasks: dict[str, TaskResponse] = {}  # task_id -> TaskResponse


def _validate_records(model, raw, field_name: str = "") -> list:
    """把引擎产出的 raw list[dict] 校验为模型实例（M10-P4，含单条隔离）。

    为什么需要：直接把 dict 列表塞进已校验的 model 字段会**绕过校验**，
    响应序列化阶段 Pydantic 遂对每个字段报 `SerializationUnexpectedValue`
    告警，既污染日志，也让「引擎产出 shape 与 API 契约漂移」这种真问题
    被降级成 warning 静默滑过。

    处置策略（**单条隔离，而非整批连坐**）：
      - shape 一致 → 转模型实例，序列化零告警；
      - 单条 shape 漂移 → **丢弃该条并显式告警**（打印含 field/原因），其余照常返回。
        不抛异常：一条畸形记录不应让整份报告被丢弃（那样用户从「带瑕疵报告」
        退化为「无报告」，比修复前更差——此取舍经独立审议指出后修正）。
    空/None 输入返回 []，保持原有默认语义。
    """
    if not raw:
        return []
    out = []
    dropped = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            dropped.append(f"#{idx} 非 dict（{type(item).__name__}）")
            continue
        try:
            out.append(model(**item))
        except ValidationError as e:
            dropped.append(f"#{idx} {str(e).splitlines()[0]}")
    if dropped:
        # fail loud：不静默吞掉，明确打出被隔离的记录与原因，便于定位契约漂移。
        print(
            f"⚠️ 产出记录 shape 漂移，已隔离 {len(dropped)}/{len(raw)} 条"
            f"（字段 {field_name or model.__name__}）:\n    " + "\n    ".join(dropped)
        )
    return out


# ============================================================================
# 路由实现
# ============================================================================


@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateTaskResponse
)
async def create_task(request: CreateTaskRequest):
    """
    创建任务（POST /tasks）

    业务规则：
    - 验证 user_task 合法性
    - 生成 UUID-v4 task_id
    - 初始化任务状态为 running
    - TODO: M6-3 启动引擎进程
    """
    # 验证 user_task
    if not request.user_task.topic:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="INVALID_USER_TASK",
                message="topic 不可为空",
                timestamp=datetime.utcnow().isoformat()
            ).dict()
        )

    if not request.user_task.scope:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="INVALID_USER_TASK",
                message="scope 不可为空",
                timestamp=datetime.utcnow().isoformat()
            ).dict()
        )

    # M8-4：模板被提交链路真正读取（agents/gates 落库为任务元数据）；
    # M8-5：编排消费=引擎按 agents 子集动态构建流水线（零回归，缺省全 3+3）
    # 审议订正 M-3：省略 template_id 时回落到首个可用模板，而非硬编码 standard_research
    try:
        from .admin import _load_template, _template_names
        _names = _template_names()
        template_id = request.template_id or (_names[0] if _names else None)
        if not template_id or template_id not in _names:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="INVALID_TEMPLATE",
                    message=f"模板不存在：{template_id or '（无可用模板）'}",
                    timestamp=datetime.utcnow().isoformat(),
                ).dict(),
            )
        _tpl = _load_template(template_id)
        tpl_agents = _tpl.get("agents", [])
        tpl_gates = _tpl.get("gates", [])
    except ImportError:
        tpl_agents, tpl_gates = [], []

    # M9-1：显式子集覆盖模板（市场页「用这些智能体跑」）。
    # 校验：必须是已知 Agent 的非空子集；gates 由 registry 配对派生，不允许自填（防孤儿闸）。
    if request.agents:
        try:
            from .admin import _agent_gate_pair, _known_agents
            pair, known = _agent_gate_pair(), _known_agents()
        except ImportError:
            pair, known = {}, []
        unknown = [a for a in request.agents if a not in known]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorResponse(
                    error="INVALID_AGENTS",
                    message=f"未知 agent：{unknown}（可选：{known}）",
                    timestamp=datetime.utcnow().isoformat(),
                ).dict(),
            )
        tpl_agents = list(request.agents)
        tpl_gates = [pair[a] for a in request.agents if a in pair]

    # M9-5 / ChatEntry 控制项：插件 + 模型接口校验（诚实边界，不冒充可用）。
    req_plugins = list(request.plugins) if request.plugins else []
    req_model = request.model
    req_gate_model = request.gate_model  # 方案 A：独立审核模型
    if req_plugins or req_model or req_gate_model:
        try:
            from tools.data_sources import load_data_sources
        except ImportError:
            load_data_sources = None
        if req_plugins:
            if load_data_sources is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=ErrorResponse(
                        error="ENGINE_UNAVAILABLE",
                        message="引擎模块不可用，无法校验插件",
                        timestamp=datetime.utcnow().isoformat(),
                    ).dict(),
                )
            src_map = {p["id"]: p for p in load_data_sources()}
            bad = []
            for pid in req_plugins:
                spec = src_map.get(pid)
                if spec is None:
                    bad.append(f"未知插件：{pid}")
                elif spec.get("status") == "coming_soon" or spec.get("provider") == "coming_soon":
                    bad.append(f"插件尚未接入（coming_soon）：{pid}")
            if bad:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="INVALID_PLUGINS",
                        message="；".join(bad),
                        timestamp=datetime.utcnow().isoformat(),
                    ).dict(),
                )
        if req_model and req_model != "auto":
            # 模型接口须 ∈ config/models.yaml 的接口 id（杜绝传入不存在的模型名冒充可用）。
            try:
                from .admin import _load_model_interfaces
                valid_ids = {m["id"] for m in _load_model_interfaces()}
            except ImportError:
                valid_ids = set()
            except Exception:
                valid_ids = set()
            if req_model not in valid_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="INVALID_MODEL",
                        message=f"未知模型接口：{req_model}（可选：{sorted(valid_ids) or 'auto'}）",
                        timestamp=datetime.utcnow().isoformat(),
                    ).dict(),
                )
        # 方案 A：审核模型(Gate)校验（与 Agent 模型平行；同源 valid_ids 全集）。
        if req_gate_model and req_gate_model != "auto":
            try:
                from .admin import _load_model_interfaces
                valid_ids_g = {m["id"] for m in _load_model_interfaces()}
            except Exception:
                valid_ids_g = set()
            if req_gate_model not in valid_ids_g:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorResponse(
                        error="INVALID_GATE_MODEL",
                        message=f"未知审核模型：{req_gate_model}（可选：{sorted(valid_ids_g) or 'auto'}）",
                        timestamp=datetime.utcnow().isoformat(),
                    ).dict(),
                )

    # 生成 task_id
    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # 创建初始任务状态
    task = TaskResponse(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        user_task=request.user_task,
        routing_state=RoutingState(
            round=1,
            max_rounds=2,
            last_gate=None,
            status=TaskStatus.RUNNING,
            rework_target_agent=None,
            rework_reason=None
        ),
        template_id=template_id,
        agents=tpl_agents,
        gates=tpl_gates,
        plugins=req_plugins,
        model=req_model,
        gate_model=req_gate_model,  # 方案 A：审核模型覆盖
        owner_id=_current_aid(),     # 多租户：注入归属（客户端忽略），用于隔离/越权 404
    )

    # 存储任务（临时内存存储）
    _tasks[task_id] = task

    # M6-3: 启动引擎进程（后台任务）
    async def start_engine_background():
        """后台启动引擎进程"""
        poller = None
        try:
            # 多租户：状态目录 + 子进程租户根都按当前账号隔离
            aid = _current_aid()
            sdir = _task_state_dir()
            await engine_manager.launch_engine(
                task_id=task_id,
                user_task=request.user_task.dict(),
                max_rounds=task.routing_state.max_rounds,
                agents=task.agents or None,
                plugins=task.plugins or None,
                model=task.model,
                gate_model=task.gate_model,  # 方案 A：审核模型透传
                account_id=aid,
            )

            # TD-002：并发流式广播中间件事件。引擎子进程把事件写进 JSONL，
            # 此处启动 tail 协程，与 engine_process.wait() 并行，把事件实时推到 WS。
            events_file = sdir / f"{task_id}_events.jsonl"
            poller = asyncio.create_task(_stream_engine_events(task_id, events_file))

            # 等待引擎完成
            engine_process = await engine_manager.get_engine_process(task_id)
            if engine_process:
                output = await engine_process.wait()

                # 更新任务状态
                if "error" in output:
                    task.status = TaskStatus.ESCALATED
                    task.routing_state.status = TaskStatus.ESCALATED
                    task.escalate_reason = output["error"]
                else:
                    # 从引擎输出更新任务状态
                    routing = output.get("routing_state", {})
                    task.status = TaskStatus(routing.get("status", "running"))
                    task.routing_state = RoutingState(**routing)
                    # 升级原因：引擎把它放在 routing_state.escalate_reason（或 rework_reason 兜底），
                    # 必须透传到 TaskResponse 顶层，复核页/时间线终态弹窗才能展示"错在那一步"。
                    if task.status == TaskStatus.ESCALATED:
                        task.escalate_reason = (
                            routing.get("escalate_reason")
                            or routing.get("rework_reason")
                            or "引擎执行失败"
                        )
                    # ⚠️ 必须经模型校验后再赋值：直接把引擎的 raw dict 塞进已校验字段会绕过
                    # 校验，响应序列化时 Pydantic 对「dict vs 模型」报 SerializationUnexpectedValue
                    # 告警（实测每字段各一条，污染日志；且字段一旦漂移会静默漏传）。
                    # 用 TypeAdapter 批量校验，字段不匹配时 fail loud 而非静默告警。
                    task.retrieval_records = _validate_records(RetrievalRecord, output.get("retrieval_records"), "retrieval_records")
                    task.analysis_conclusions = _validate_records(AnalysisConclusion, output.get("analysis_conclusions"), "analysis_conclusions")
                    task.draft_segments = _validate_records(DraftSegment, output.get("draft_segments"), "draft_segments")
                    task.tool_status = _validate_records(ToolStatus, output.get("tool_status"), "tool_status")
                    task.prior_versions = output.get("prior_versions", {})
                    task.report_markdown = output.get("report_markdown")

                task.updated_at = datetime.utcnow().isoformat()

                # 推送 WebSocket 事件
                from .websocket import (
                    emit_task_done, emit_task_escalated
                )

                if task.status == TaskStatus.DONE:
                    await emit_task_done(task_id, task.routing_state.round, task.report_markdown)
                elif task.status == TaskStatus.ESCALATED:
                    await emit_task_escalated(
                        task_id,
                        task.routing_state.round,
                        task.escalate_reason or "引擎执行失败",
                        task.routing_state.last_gate or "GateC"
                    )

        except Exception as e:
            print(f"❌ 引擎进程异常: {e}")
            task.status = TaskStatus.ESCALATED
            task.routing_state.status = TaskStatus.ESCALATED
            task.escalate_reason = f"引擎进程异常: {str(e)}"
            task.updated_at = datetime.utcnow().isoformat()
        finally:
            # TD-002：无论正常结束还是异常路径，都必须停掉事件 tail 协程，避免协程泄漏
            if poller is not None:
                poller.cancel()
                try:
                    await poller
                except asyncio.CancelledError:
                    pass

    # 启动后台任务
    asyncio.create_task(start_engine_background())

    print(f"📝 任务创建成功: {task_id}")

    return CreateTaskResponse(
        task_id=task_id,
        status=task.status,
        created_at=task.created_at,
        user_task=request.user_task,
        template_id=task.template_id,
        agents=task.agents,
        gates=task.gates,
        plugins=task.plugins,
        model=task.model,
    )


@router.get(
    "/tasks",
    response_model=List[TaskHistoryItem]
)
async def list_tasks():
    """
    任务历史列表（侧边栏）

    扫描 .engine_state 下的 output/input 文件，返回最近 50 条任务摘要。
    已结束任务以 output.json 为准；运行中无 output 的任务以 input.json 兜底。
    内存中的任务合并覆盖，保证 API 重启后仍能看到历史。
    """
    aid = _current_aid()
    items: dict[str, dict] = {}
    sdir = _task_state_dir()
    if sdir.exists():
        # 1) 已结束/已升级任务：*_output.json
        for p in sorted(sdir.glob("*_output.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            tid = p.stem.replace("_output", "")
            if tid in items:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                user_task = data.get("user_task") or {}
                routing = data.get("routing_state") or {}
                mtime = datetime.utcfromtimestamp(p.stat().st_mtime).isoformat()
                items[tid] = {
                    "task_id": tid,
                    "topic": (user_task.get("topic") or "无主题")[:60],
                    "status": routing.get("status", "escalated"),
                    "created_at": mtime,
                    "updated_at": mtime,
                }
            except Exception:
                continue
        # 2) 运行中任务：*_input.json（尚无 output）
        for p in sorted(sdir.glob("*_input.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            tid = p.stem.replace("_input", "")
            if tid in items:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                user_task = data.get("user_task") or {}
                mtime = datetime.utcfromtimestamp(p.stat().st_mtime).isoformat()
                items[tid] = {
                    "task_id": tid,
                    "topic": (user_task.get("topic") or "无主题")[:60],
                    "status": "running",
                    "created_at": mtime,
                    "updated_at": mtime,
                }
            except Exception:
                continue
    # 3) 合并内存中任务（可能比文件更新）——按 owner 过滤，绝不把别人的任务漏进来
    for tid, task in _tasks.items():
        if aid and task.owner_id and task.owner_id != aid:
            continue
        if tid in items:
            items[tid]["status"] = task.status.value
            items[tid]["updated_at"] = task.updated_at
        else:
            items[tid] = {
                "task_id": tid,
                "topic": (task.user_task.topic or "无主题")[:60],
                "status": task.status.value,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            }
    sorted_items = sorted(items.values(), key=lambda x: x["updated_at"], reverse=True)[:50]
    return sorted_items


def _load_task_from_file(task_id: str) -> Optional[TaskResponse]:
    """从 {tenant}/.engine_state/{id}_output.json 恢复任务（API 重启后历史任务仍可查看）。

    状态目录已按账号物理隔离，故只可能恢复出自己的任务；owner_id 从同目录
    {id}_input.json 的 account_id 回填，便于内存态与文件态一致。
    """
    sdir = _task_state_dir()
    output_file = sdir / f"{task_id}_output.json"
    if not output_file.exists():
        return None
    input_file = sdir / f"{task_id}_input.json"
    owner_id = None
    if input_file.exists():
        try:
            owner_id = json.loads(input_file.read_text(encoding="utf-8")).get("account_id")
        except Exception:
            owner_id = None
    try:
        data = json.loads(output_file.read_text(encoding="utf-8"))
        routing = data.get("routing_state", {})
        status_val = routing.get("status", "escalated")
        mtime = datetime.utcfromtimestamp(output_file.stat().st_mtime).isoformat()
        user_task_raw = data.get("user_task", {})
        return TaskResponse(
            task_id=task_id,
            status=TaskStatus(status_val),
            created_at=mtime,
            updated_at=mtime,
            user_task=UserTask(**user_task_raw),
            routing_state=RoutingState(**routing),
            retrieval_records=_validate_records(RetrievalRecord, data.get("retrieval_records"), "retrieval_records"),
            analysis_conclusions=_validate_records(AnalysisConclusion, data.get("analysis_conclusions"), "analysis_conclusions"),
            draft_segments=_validate_records(DraftSegment, data.get("draft_segments"), "draft_segments"),
            tool_status=_validate_records(ToolStatus, data.get("tool_status"), "tool_status"),
            prior_versions=data.get("prior_versions", {}),
            report_markdown=data.get("report_markdown"),
            escalate_reason=routing.get("escalate_reason") or routing.get("rework_reason") or None,
            owner_id=owner_id,
        )
    except Exception as e:
        print(f"⚠️ 从文件恢复任务 {task_id} 失败: {e}")
        return None


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse
)
async def get_task(task_id: str):
    """
    获取任务状态（GET /tasks/{id}）

    业务规则：
    - 返回完整状态投影（对齐 API_SPEC.md §4）
    - 路由状态、产出层、审计历史
    - API 重启后仍可从 .engine_state 文件恢复历史任务
    """
    return _owned_task(task_id)


@router.post(
    "/tasks/{task_id}/review",
    response_model=ReviewResponse
)
async def review_task(task_id: str, request: ReviewRequest):
    """
    人工复核操作（POST /tasks/{id}/review）

    业务规则：
    - 仅在 status=escalated 时可用
    - action=confirm: status=done
    - action=retry: status=running, 指定 rework_target_agent
    - action=abort: status=aborted
    """
    # 越权：经 _owned_task 强制按 owner 隔离，一律 404
    task = _owned_task(task_id)

    # 检查任务状态
    if task.status != TaskStatus.ESCALATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                error="TASK_NOT_ESCALATED",
                message="任务不在升级状态",
                timestamp=datetime.utcnow().isoformat()
            ).dict()
        )

    # 验证 action
    if request.action == ReviewAction.RETRY and not request.rework_target_agent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="INVALID_REVIEW_ACTION",
                message="action=retry 时必须指定 rework_target_agent",
                timestamp=datetime.utcnow().isoformat()
            ).dict()
        )

    # 执行复核操作
    now = datetime.utcnow().isoformat()
    task.updated_at = now

    if request.action == ReviewAction.CONFIRM:
        task.status = TaskStatus.DONE
        task.routing_state.status = TaskStatus.DONE
        message = "复核已确认，任务完成"

    elif request.action == ReviewAction.RETRY:
        task.status = TaskStatus.RUNNING
        task.routing_state.status = TaskStatus.RUNNING
        task.routing_state.rework_target_agent = request.rework_target_agent
        task.routing_state.rework_reason = request.reviewer_comment or "人工复核后重试"
        message = "任务已重新启动"

    elif request.action == ReviewAction.ABORT:
        task.status = TaskStatus.ABORTED
        task.routing_state.status = TaskStatus.ABORTED
        message = "任务已终止"

    print(f"🔍 人工复核: {task_id} - {request.action.value}")

    return ReviewResponse(
        task_id=task_id,
        status=task.status,
        message=message
    )


@router.get(
    "/tasks/{task_id}/audit-export",
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "审计包 ZIP 文件"
        }
    }
)
async def export_audit(task_id: str):
    """
    审计包下载（GET /tasks/{id}/audit-export）

    业务规则：
    - 仅在 status=done|escalated|aborted 时可用
    - 返回 ZIP 包（report.md + gate_review_history.json + prior_versions.json + user_task.json + engine_events.json）
    - TODO: M6-5 实现完整 ZIP 生成逻辑
    """
    # 越权：经 _owned_task 强制按 owner 隔离，一律 404
    task = _owned_task(task_id)

    # 检查任务状态
    if task.status not in [TaskStatus.DONE, TaskStatus.ESCALATED, TaskStatus.ABORTED]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                error="TASK_NOT_COMPLETED",
                message="任务未完成",
                timestamp=datetime.utcnow().isoformat()
            ).dict()
        )

    # M6-5 真实 ZIP：生成器按 DB 模型写，端点手里是 TaskResponse →
    # 经 AuditExportGenerator.from_api_task() 做形状适配（见 server/audit_export.py）。
    generator = AuditExportGenerator.from_api_task(task)
    zip_data = generator.generate_zip()

    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(generator.get_filename())},
    )


# ---------------------------------------------------------------------------
# 研报下载（DESIGN_OUTPUT_RENDERING.md v1.0 · OR-3）
# ---------------------------------------------------------------------------
# 设计要点：
#   - SoT 仍是 report_markdown；docx/pptx/pdf 是**下载时才渲染**的派生视图，引擎零改动。
#   - 渲染库缺失必须 503 响亮失败，绝不返回空文件冒充成功（诚实边界）。
_REPORT_FORMATS = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}
_REPORT_RENDERERS = {"docx": render_docx, "pptx": render_pptx, "pdf": render_pdf}


def _md_title(md: str, fallback: str) -> str:
    """取 md 首个 # 标题作文档标题，取不到则用主题兜底。"""
    m = re.search(r"^#\s+(.+)$", md or "", re.MULTILINE)
    return (m.group(1).strip() if m else "") or fallback


def _content_disposition(filename: str) -> str:
    """RFC 5987：ASCII 兜底名 + UTF-8 编码名（中文主题不乱码）。"""
    ascii_name = re.sub(r"[^\w.-]+", "_", filename, flags=re.ASCII) or "report"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


@router.get(
    "/tasks/{task_id}/report",
    responses={200: {"description": "研报文件（md / docx / pptx / pdf）"}},
)
async def download_report(task_id: str, format: str = "md"):
    """研报下载（GET /tasks/{id}/report?format=md|docx|pptx|pdf）

    - 越权一律 404（_owned_task 按 owner 隔离，不返 403 防枚举探测）；
    - 仅 done/escalated 可下载，否则 409 TASK_NOT_COMPLETED；
    - 格式非法 400 UNSUPPORTED_FORMAT；渲染库缺失 503 RENDERER_UNAVAILABLE。
    """
    task = _owned_task(task_id)

    if task.status not in [TaskStatus.DONE, TaskStatus.ESCALATED]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ErrorResponse(
                error="TASK_NOT_COMPLETED",
                message="任务未完成",
                timestamp=datetime.utcnow().isoformat(),
            ).dict(),
        )

    fmt = (format or "md").lower().strip()
    if fmt not in _REPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="UNSUPPORTED_FORMAT",
                message=f"不支持的格式：{format}（可选 md / docx / pptx / pdf）",
                timestamp=datetime.utcnow().isoformat(),
            ).dict(),
        )

    md = task.report_markdown or ""
    topic = getattr(task.user_task, "topic", None) or "report"

    if fmt == "md":
        data = md.encode("utf-8")
    else:
        meta = {
            "title": _md_title(md, topic),
            "topic": topic,
            "task_id": task_id,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        }
        try:
            data = _REPORT_RENDERERS[fmt](parse_markdown(md), meta)
        except RendererUnavailable as e:
            # 缺库即如实报 503，绝不返回空字节冒充成功
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ErrorResponse(
                    error="RENDERER_UNAVAILABLE",
                    message=str(e),
                    timestamp=datetime.utcnow().isoformat(),
                ).dict(),
            )

    return Response(
        content=data,
        media_type=_REPORT_FORMATS[fmt],
        headers={"Content-Disposition": _content_disposition(f"{topic}-{task_id}.{fmt}")},
    )


# 旧「调试用」list_tasks（返回全进程任务、不按 owner 过滤）已移除：
# 它与上方 tenant-aware 的 list_tasks 同路径，会遮蔽正确实现，造成越权读。
# 需要调试请用 tenant-aware 版本或临时 ADMIN 通道。

# ============================================================================
# ChatAgent 入口（阶段 1：对话优先，对话中可触发研报技能）
# ============================================================================

class ChatRequest(BaseModel):
    """对话请求（POST /chat）"""
    message: str
    # 历史消息 [{role: "user"|"assistant", content: str}]
    history: List[dict] = Field(default_factory=list)
    # 可选：覆盖默认模型接口（沿用 create_task 的 model 字段语义）
    model: Optional[str] = None
    # 可选：data source 限制
    plugins: Optional[List[str]] = None


class ChatResponse(BaseModel):
    """对话响应"""
    reply: str
    intent: str  # "chat" | "generate_report"
    # 当 intent=generate_report 时，task_id 可让前端跳转 /tasks/{id} 看进度
    task_id: Optional[str] = None
    task_status: Optional[str] = None
    # 绑定的会话 id（POST /chat 自动建/复用 session 时返回）
    session_id: Optional[str] = None
    # 阶段 2：ChatAgent 自主调用的工具清单（web_search / data_proc / mcp:<s>:<t>），
    # 供前端展示「他能自主调工具」，直观回应 boss 诉求。
    tool_calls: List[str] = []
    # 工具产出的真实素材（检索记录 / MCP 结果），供前端溯源展示。
    sources: List[dict] = []


# 每账号单例（懒加载）：构造 NewApiLLMClient 较重（读 yaml + 解析接口），复用一次。
#
# CRITICAL（多租户）：原来是两个 process-global 单例，账号 A 登录后构造的 ChatAgent
# 会被账号 B 直接复用 → B 用的是 A 的模型端点/密钥（跨账号串号 + 密钥越权）。
# 改为按账号分桶后，各账号各持一份。
_CHAT_AGENTS: Dict[str, Any] = {}
_CHAT_LLMS: Dict[str, Any] = {}
_NO_ACCOUNT = "__no_account__"


def _chat_bucket() -> str:
    """当前账号的单例桶键。无租户上下文（CLI/旧链路）时落到 __no_account__。"""
    try:
        from server import tenancy
        return tenancy.current_account_id(allow_none=True) or _NO_ACCOUNT
    except Exception:
        return _NO_ACCOUNT


def _get_chat_agent():
    """懒加载 ChatAgent（含 NewApiLLMClient），**按账号隔离**。"""
    from orchestrator import NewApiLLMClient, load_model_interfaces_and_endpoints
    from chat_agent import ChatAgent

    bucket = _chat_bucket()
    hit = _CHAT_AGENTS.get(bucket)
    if hit is not None:
        return hit

    endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
    # ChatAgent 走同步 /chat 端点（浏览器等待完整响应），须比研报引擎更激进地限时+少重试，
    # 否则 new-api 偶发卡顿会让单次对话挂数分钟 → 前端超时(499)。
    # max_retries=1（仅 1 次兜底重试）、timeout=90s（单轮 LLM 上限）。
    llm = NewApiLLMClient(
        endpoints=endpoints,
        interfaces=interfaces,
        default_endpoint_id=default_endpoint_id,
        max_retries=1,
        timeout=90,
    )
    agent = ChatAgent(llm=llm)
    _CHAT_LLMS[bucket] = llm
    _CHAT_AGENTS[bucket] = agent
    return agent


def _reset_chat_agent_singleton() -> None:
    """重置 ChatAgent 单例（CRITICAL fix · 2026-09-11 独立审议）。

    NewApiLLMClient 在构造时一次性快照 endpoints/interfaces dict，无热刷新机制。
    当用户通过 POST /admin/providers 新增或删除自定义 provider 时，磁盘 yaml/json 已更新，
    但单例持有的旧 endpoints 仍指向删除前的列表——下次 /chat 调用会路由到旧（甚至已删）的端点。

    此函数由 add/delete_custom_provider 在审计落盘后调用，下次 _get_chat_agent 会重建 LLM 客户端。
    多租户下只清当前账号的桶（清别的账号会导致它们无谓重建）；无租户上下文时清全部（旧行为）。
    """
    bucket = _chat_bucket()
    if bucket == _NO_ACCOUNT:
        _CHAT_AGENTS.clear()
        _CHAT_LLMS.clear()
        return
    _CHAT_AGENTS.pop(bucket, None)
    _CHAT_LLMS.pop(bucket, None)


# /chat 的 session-aware 实现见下方「/chat 端点集成 session」段（单一注册，避免重复定义被 Starlette 遮蔽）。


# ============================================================================
# 自定义 API Provider 管理（DESIGN_chat_model_picker.md round 2）
# 用户可自助接入任意 OpenAI 兼容服务（SiliconFlow / DeepSeek 官方 / 自部署 vLLM 等）。
# 配置：config/custom_providers.yaml（元数据）+ .secrets/custom_providers.json（api_key）
# ============================================================================

from pathlib import Path as _P

# LEGACY 回退常量（全局根）。注意：模块级不得调用需租户上下文的函数，故这里求值不经过 tenancy。
LEGACY_CUSTOM_PROVIDERS_CFG = BASE_DIR / "config" / "custom_providers.yaml"
LEGACY_CUSTOM_PROVIDERS_SEC = BASE_DIR / ".secrets" / "custom_providers.json"
CUSTOM_PROVIDERS_CFG = LEGACY_CUSTOM_PROVIDERS_CFG  # 兼容旧引用（读路径请用 _custom_providers_cfg()）
CUSTOM_PROVIDERS_SEC = LEGACY_CUSTOM_PROVIDERS_SEC


def _custom_providers_cfg() -> _P:
    """按当前账号命名空间解析自定义 Provider 配置（各自的 provider 列表）。

    无租户上下文时（CLI / 旧链路）回落全局根，避免一 import 就抛错。
    """
    try:
        from server import tenancy
        return tenancy.config_path("custom_providers.yaml")
    except Exception:
        return LEGACY_CUSTOM_PROVIDERS_CFG


def _custom_providers_sec() -> _P:
    """按当前账号命名空间解析自定义 Provider 密钥（**绝不跨账号共享**）。"""
    try:
        from server import tenancy
        return tenancy.secrets_path("custom_providers.json")
    except Exception:
        return LEGACY_CUSTOM_PROVIDERS_SEC


def _read_yaml_or_default(path: _P, default):
    import yaml as _yaml
    if not path.exists():
        return default
    try:
        return _yaml.safe_load(path.read_text(encoding="utf-8")) or default
    except Exception:
        return default


def _write_yaml_atomic(path: _P, data) -> None:
    """原子写 yaml：写临时文件 → os.replace → 失败回退原始重写。

    临时文件名带 PID + 时间戳 + 计数器，杜绝并发写共享 .tmp 的竞争（NIT 修复）。
    """
    import yaml as _yaml
    import time as _time
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{int(_time.time() * 1000)}")
    content = _yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _read_json_or_default(path: _P, default):
    import json as _json
    if not path.exists():
        return default
    try:
        return _json.loads(path.read_text(encoding="utf-8")) or default
    except Exception:
        return default


def _write_json_atomic(path: _P, data) -> None:
    import json as _json
    import time as _time
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{int(_time.time() * 1000)}")
    try:
        tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 限制权限（容器内）：密钥文件不应让同组/其他用户读。
        # MINOR 修复：失败时 warning 日志 + 保留默认权限回退（不静默吞）。
        try:
            os.chmod(tmp, 0o600)
        except Exception as chmod_err:
            print(f"⚠️ chmod 0o600 失败 ({path}): {chmod_err}; 密钥文件可能以默认权限落盘")
        os.replace(tmp, path)
        # 已落盘的文件也 chmod（防止 tmp 在 replace 前被读到默认权限的 race window）
        try:
            os.chmod(path, 0o600)
        except Exception as chmod_err:
            print(f"⚠️ chmod 0o600 失败 ({path}): {chmod_err}")
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _list_custom_providers() -> list[dict]:
    """列出全部 custom provider（含 disabled），但**不返回 api_key 明文**。"""
    cfg = _read_yaml_or_default(_custom_providers_cfg(), {"providers": []})
    keys = _read_json_or_default(_custom_providers_sec(), {})
    items: list[dict] = []
    for p in cfg.get("providers") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        has_key = bool((keys.get(p["id"]) or "").strip())
        items.append({
            "id": p["id"],
            "name": p.get("name") or p["id"],
            "base_url": p.get("base_url", ""),
            "model_ids": list(p.get("model_ids") or []),
            "enabled": bool(p.get("enabled", True)),
            "has_api_key": has_key,  # 不返回明文
        })
    return items


def _validate_custom_provider_payload(data: dict) -> tuple[bool, str]:
    """校验 POST payload 的基本格式。"""
    pid = (data.get("id") or "").strip()
    if not pid:
        return False, "id 必填"
    # 注意：str.isalnum() 在 Python 里把下划线视作非法字符，须用正则精确放行 - _ .
    import re as _re
    if not _re.match(r"^[A-Za-z0-9._-]+$", pid):
        return False, "id 只能含字母数字 - _ ."
    if len(pid) > 64:
        return False, "id 长度 ≤ 64"
    # 规范化 base_url：去尾斜杠；OpenAI 兼容根统一为 /v1（缺失则自动补，
    # 避免用户漏写 /v1 直接 400 的"假壳"体验，见 DESIGN_chat_model_picker.md round 2）。
    url = (data.get("base_url") or "").strip().rstrip("/")
    if not url:
        return False, "base_url 必填"
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "base_url 必须以 http:// 或 https:// 开头"
    if not url.endswith("/v1"):
        url = url + "/v1"
    data["base_url"] = url  # 写回规范化结果，供后续落盘使用
    if not isinstance(data.get("model_ids") or [], list):
        return False, "model_ids 必须是数组"
    return True, ""


class CustomProviderCreate(BaseModel):
    """添加自定义 provider 请求体。"""
    id: str
    name: Optional[str] = None
    base_url: str
    api_key: Optional[str] = None
    model_ids: List[str] = Field(default_factory=list)
    enabled: bool = True


class CustomProviderItem(BaseModel):
    """单个 provider 列表项（不含 api_key 明文）。"""
    id: str
    name: str
    base_url: str
    model_ids: List[str]
    enabled: bool
    has_api_key: bool


class CustomProvidersListResponse(BaseModel):
    items: List[CustomProviderItem]


class KBEntryItem(BaseModel):
    id: Optional[int] = None
    kind: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None
    metadata: dict = {}
    score: float = 0.0


class KBQueryResponse(BaseModel):
    query: str
    count: int
    results: List[KBEntryItem]


@router.get("/kb", response_model=KBQueryResponse)
async def query_knowledge_base(q: str = "", k: int = 5):
    """知识库语义/全文召回（M11-1）。DB 或嵌入不可达时降级返回空列表，绝不抛错。"""
    from tools.kb_store import kb_retrieve

    rows = await kb_retrieve(q, k=max(1, min(int(k), 50)))
    return KBQueryResponse(
        query=q,
        count=len(rows),
        results=[KBEntryItem(**r) for r in rows],
    )


# ---------------------------------------------------------------------------
# M11-3：MCP server 管理（镜像 /admin/providers，真·控制器：UI/Admin 增删改即时生效）
# ---------------------------------------------------------------------------
LEGACY_MCP_SERVERS_CFG = BASE_DIR / "config" / "mcp_servers.yaml"
MCP_SERVERS_CFG = LEGACY_MCP_SERVERS_CFG  # 兼容旧引用（读路径请用 _mcp_servers_cfg()）


def _mcp_servers_cfg() -> _P:
    """按当前账号命名空间解析 MCP server 配置（各自一份）。"""
    try:
        from server import tenancy
        return tenancy.config_path("mcp_servers.yaml")
    except Exception:
        return LEGACY_MCP_SERVERS_CFG


class McpServerItem(BaseModel):
    """单个 MCP server 列表项（header 值不回显，仅标是否配置）。"""
    id: str
    name: str
    transport: str
    url: str
    enabled: bool
    has_headers: bool


class McpServerCreate(BaseModel):
    id: str
    name: Optional[str] = None
    transport: str = "sse"
    url: str
    headers: Optional[dict] = None
    enabled: bool = True
    tools_whitelist: Optional[List[str]] = None


class McpServersListResponse(BaseModel):
    items: List[McpServerItem]


def _list_mcp_servers() -> List[dict]:
    cfg = _read_yaml_or_default(_mcp_servers_cfg(), {"servers": []})
    out = []
    for s in (cfg.get("servers") or []):
        if not isinstance(s, dict) or not s.get("id"):
            continue
        out.append({
            "id": s["id"],
            "name": s.get("name") or s["id"],
            "transport": s.get("transport") or "sse",
            "url": s.get("url") or "",
            "enabled": bool(s.get("enabled", True)),
            "has_headers": bool(s.get("headers")),
        })
    return out


@router.get("/admin/mcp-servers", response_model=McpServersListResponse)
async def list_mcp_servers():
    """列出全部 MCP server（header 值不回显）。"""
    return McpServersListResponse(items=[McpServerItem(**s) for s in _list_mcp_servers()])


@router.post("/admin/mcp-servers", response_model=McpServerItem)
async def add_mcp_server(payload: McpServerCreate):
    """新增/更新 MCP server（id 存在则覆盖）。"""
    sid = (payload.id or "").strip()
    if not sid or not re.match(r"^[A-Za-z0-9_-]+$", sid):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=ErrorResponse(error="INVALID_ID",
                                                message="id 须为字母数字/下划线/连字符").dict())
    if payload.transport not in ("sse", "http", "streamable_http", "streamable"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=ErrorResponse(error="INVALID_TRANSPORT",
                                                message="transport 仅支持 sse/http").dict())
    if not (payload.url or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=ErrorResponse(error="INVALID_URL",
                                                message="url 必填").dict())
    cfg = _read_yaml_or_default(_mcp_servers_cfg(), {"servers": []})
    servers = cfg.setdefault("servers", [])
    entry = {
        "id": sid,
        "name": payload.name or sid,
        "transport": payload.transport,
        "url": payload.url.strip(),
        "headers": payload.headers or {},
        "enabled": bool(payload.enabled),
        "tools_whitelist": list(payload.tools_whitelist or []),
    }
    replaced = False
    for i, s in enumerate(servers):
        if isinstance(s, dict) and s.get("id") == sid:
            servers[i] = entry
            replaced = True
            break
    if not replaced:
        servers.append(entry)
    _mcp_servers_cfg().parent.mkdir(parents=True, exist_ok=True)
    _write_yaml_atomic(_mcp_servers_cfg(), cfg)
    _audit_admin_change(action="upsert_mcp_server", target=sid,
                         meta={"transport": entry["transport"], "enabled": entry["enabled"]})
    return McpServerItem(**{k: entry[k] for k in ("id", "name", "transport", "url", "enabled")},
                        has_headers=bool(entry["headers"]))


@router.delete("/admin/mcp-servers/{server_id}", response_model=McpServersListResponse)
async def delete_mcp_server(server_id: str):
    """删除 MCP server（按 id）。"""
    cfg = _read_yaml_or_default(_mcp_servers_cfg(), {"servers": []})
    servers = cfg.get("servers") or []
    new = [s for s in servers if not (isinstance(s, dict) and s.get("id") == server_id)]
    if len(new) == len(servers):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=ErrorResponse(error="NOT_FOUND",
                                                message=f"server '{server_id}' 不存在").dict())
    cfg["servers"] = new
    _write_yaml_atomic(_mcp_servers_cfg(), cfg)
    _audit_admin_change(action="delete_mcp_server", target=server_id, meta={})
    return McpServersListResponse(items=[McpServerItem(**s) for s in _list_mcp_servers()])


@router.get("/admin/providers", response_model=CustomProvidersListResponse)
async def list_custom_providers():
    """列出全部自定义 API provider（不含密钥明文）。"""
    return CustomProvidersListResponse(items=[CustomProviderItem(**p) for p in _list_custom_providers()])


@router.post("/admin/providers", response_model=CustomProviderItem)
async def add_custom_provider(payload: CustomProviderCreate):
    """新增自定义 API provider。

    元数据落 config/custom_providers.yaml（git-ignored 视配置而定，便于复盘），
    api_key 落 .secrets/custom_providers.json（容器内 600 权限，不入库）。

    id 重复则 409；id 格式不合规则 400；base_url 不合法则 400。
    """
    data = payload.model_dump()
    ok, msg = _validate_custom_provider_payload(data)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_PROVIDER", "message": msg},
        )

    cfg = _read_yaml_or_default(_custom_providers_cfg(), {"providers": []})
    items = cfg.setdefault("providers", [])
    if any(isinstance(p, dict) and p.get("id") == data["id"] for p in items):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "PROVIDER_EXISTS", "message": f"provider '{data['id']}' 已存在"},
        )

    # 元数据落盘（api_key 不入 yaml）
    items.append({
        "id": data["id"],
        "name": data.get("name") or data["id"],
        "base_url": data["base_url"].strip(),
        "model_ids": list(data.get("model_ids") or []),
        "enabled": bool(data.get("enabled", True)),
    })
    _custom_providers_cfg().parent.mkdir(parents=True, exist_ok=True)
    _write_yaml_atomic(_custom_providers_cfg(), cfg)

    # 密钥落盘（仅当用户传了非空 api_key；不传=沿用现有 / 标记未配）
    key = (data.get("api_key") or "").strip()
    if key:
        sec = _read_json_or_default(_custom_providers_sec(), {})
        sec[data["id"]] = key
        _custom_providers_sec().parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(_custom_providers_sec(), sec)

    # 审计（不打明文）
    _audit_admin_change(
        action="add_custom_provider",
        target=data["id"],
        meta={"name": data.get("name"), "base_url": data["base_url"],
              "model_count": len(data.get("model_ids") or []),
              "has_key": bool(key)},
    )

    # CRITICAL（独立审议 2026-09-11 发现）：_chat_llm_client 是懒加载单例，构造时一次性快照 endpoints。
    # 新增 provider 后必须 reset 才能让下一次 /chat 调用看到新端点（NewApiLLMClient 不支持热刷新）。
    _reset_chat_agent_singleton()

    refreshed = next(p for p in _list_custom_providers() if p["id"] == data["id"])
    return CustomProviderItem(**refreshed)


@router.delete("/admin/providers/{provider_id}", response_model=dict)
async def delete_custom_provider(provider_id: str):
    """删除自定义 API provider；同时清掉 .secrets 里的 api_key。"""
    cfg = _read_yaml_or_default(_custom_providers_cfg(), {"providers": []})
    items = cfg.get("providers") or []
    new_items = [p for p in items if not (isinstance(p, dict) and p.get("id") == provider_id)]
    if len(new_items) == len(items):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "PROVIDER_NOT_FOUND", "message": f"provider '{provider_id}' 不存在"},
        )
    cfg["providers"] = new_items
    _write_yaml_atomic(_custom_providers_cfg(), cfg)

    sec = _read_json_or_default(_custom_providers_sec(), {})
    if provider_id in sec:
        sec.pop(provider_id, None)
        _write_json_atomic(_custom_providers_sec(), sec)

    _audit_admin_change(
        action="delete_custom_provider",
        target=provider_id,
        meta={},
    )

    # 同 add：单例重建（已删 provider 必须从 ChatAgent 路由表剔除）
    _reset_chat_agent_singleton()

    return {"ok": True, "deleted": provider_id}


def _audit_admin_change(action: str, target: str, meta: dict) -> None:
    """审计日志：admin 改配置（追加写 .audit/admin.log.jsonl，绝不打密钥）。"""
    try:
        from server import tenancy
        audit_dir = tenancy.audit_path("admin")
    except Exception:
        audit_dir = BASE_DIR / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log = audit_dir / "admin.log.jsonl"
    rec = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "target": target,
        "meta": meta,
    }
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ admin audit 写盘失败: {e}")


# ============================================================================
# 聊天会话 + 跨会话记忆（M10 chat_memory）
# ============================================================================

from server.database import get_async_session_dependency, get_async_session
from server.models import ChatSession, ChatMessage, ChatSessionMemory
from server import tenancy
from sqlalchemy import select, desc, func


def _current_aid() -> Optional[str]:
    """
    当前请求所属账号 id。

    - 正常链路：鉴权依赖已 set_current_account
    - 迁移期显式逃生阀 RAT_LEGACY_GLOBAL=1：无上下文时返回 None（=不按账号过滤，等同旧行为）
    - 其余情况一律 401 —— 绝不「没人说话就当全局」，那是假隔离
    """
    try:
        return tenancy.current_account_id()
    except tenancy.TenancyError:
        if tenancy.LEGACY_GLOBAL:
            return None
        raise HTTPException(status_code=401, detail="未登录或缺少账号上下文")


def _task_state_dir() -> Path:
    """当前账号的引擎状态目录（各自一份，文件级隔离）。

    无租户上下文（CLI/旧链路）：回落全局 STATE_DIR（迁移期行为）。
    """
    try:
        from server import tenancy
        return tenancy.tenant_path(".engine_state")
    except Exception:
        return STATE_DIR


def _owned_task(task_id: str) -> TaskResponse:
    """取任务并强制按 owner 隔离；越权一律 404（不返 403，防枚举探测）。

    - 内存任务：按 owner_id 校验（_tasks 是全进程共享字典，必须查）。
    - 文件恢复任务：状态目录已按账号物理隔离，但 owner_id 若有值也再校验一层。
    """
    aid = _current_aid()
    t = _tasks.get(task_id)
    if t is not None:
        if aid and t.owner_id and t.owner_id != aid:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=ErrorResponse(error="TASK_NOT_FOUND",
                                                    message="任务不存在",
                                                    timestamp=datetime.utcnow().isoformat()).dict())
        return t
    # 内存无 → 按账号状态目录恢复（天然不跨账号）
    rest = _load_task_from_file(task_id)
    if rest is not None:
        if aid and rest.owner_id and rest.owner_id != aid:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=ErrorResponse(error="TASK_NOT_FOUND",
                                                    message="任务不存在",
                                                    timestamp=datetime.utcnow().isoformat()).dict())
        return rest
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=ErrorResponse(error="TASK_NOT_FOUND",
                                            message="任务不存在",
                                            timestamp=datetime.utcnow().isoformat()).dict())


class ChatSessionCreate(BaseModel):
    topic: Optional[str] = None
    model: str = "auto-chat"
    agents: Optional[List[str]] = None
    plugins: Optional[List[str]] = None


class SessionTopicUpdate(BaseModel):
    """更新会话主题（PUT body）"""
    topic: str


class ChatSessionItem(BaseModel):
    id: str
    topic: Optional[str]
    model: str
    agents: List[str]
    plugins: List[str]
    created_at: str
    updated_at: str
    message_count: int = 0

    @classmethod
    def from_model(cls, s: ChatSession, msg_count: int = 0):
        return cls(
            id=s.id,
            topic=s.topic,
            model=s.model,
            agents=s.agents.split(",") if s.agents else [],
            plugins=s.plugins.split(",") if s.plugins else [],
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
            message_count=msg_count,
        )


class ChatMessageItem(BaseModel):
    id: str
    role: str
    content: str
    task_id: Optional[str]
    task_status: Optional[str]
    created_at: str

    @classmethod
    def from_model(cls, m: ChatMessage):
        return cls(
            id=m.id,
            role=m.role,
            content=m.content,
            task_id=m.task_id,
            task_status=m.task_status,
            created_at=m.created_at.isoformat(),
        )


async def _list_sessions():
    """列出**当前账号自己的** session（按 updated_at 降序）"""
    aid = _current_aid()
    async with get_async_session() as session:
        stmt = select(ChatSession).order_by(desc(ChatSession.updated_at)).limit(50)
        if aid:
            stmt = stmt.where(ChatSession.owner_id == aid)
        result = await session.execute(stmt)
        sessions = result.scalars().all()
        # 并发查消息数
        items = []
        for s in sessions:
            count_result = await session.execute(
                select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == s.id)
            )
            count = count_result.scalar() or 0
            items.append(ChatSessionItem.from_model(s, count))
        return {"items": items}


async def _create_session(payload: ChatSessionCreate):
    """新建 session（归属写入当前账号）"""
    aid = _current_aid()
    async with get_async_session() as session:
        agents_str = ",".join(payload.agents or [])
        plugins_str = ",".join(payload.plugins or [])
        s = ChatSession(
            id=str(uuid.uuid4()),
            topic=payload.topic,
            model=payload.model,
            agents=agents_str,
            plugins=plugins_str,
            owner_id=aid,
        )
        session.add(s)
        await session.commit()
        return ChatSessionItem.from_model(s)


async def _owned_session(session_id: str, sess):
    """取会话并校验归属：越权/不存在一律 404（不返 403，防枚举探测）。"""
    s = await sess.get(ChatSession, session_id)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    aid = _current_aid()
    if aid and s.owner_id != aid:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


async def _get_session(session_id: str):
    """获取 session 详情 + 消息列表"""
    async with get_async_session() as session:
        s = await _owned_session(session_id, session)
        msgs = await session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
        )
        messages = [ChatMessageItem.from_model(m) for m in msgs.scalars().all()]
        return {
            "session": ChatSessionItem.from_model(s),
            "messages": messages,
        }


async def _delete_session(session_id: str):
    """删除 session（cascade 删除消息 + 记忆）"""
    async with get_async_session() as session:
        s = await _owned_session(session_id, session)
        await session.delete(s)
        await session.commit()
        return {"ok": True, "deleted": session_id}


async def _update_session_topic(session_id: str, topic: str):
    """更新 session 主题"""
    async with get_async_session() as session:
        s = await _owned_session(session_id, session)
        s.topic = topic
        await session.commit()
        return ChatSessionItem.from_model(s)


# ---- 记忆 API ----

class SessionMemoryItem(BaseModel):
    id: str
    key: str
    value: str
    created_at: str

    @classmethod
    def from_model(cls, m: ChatSessionMemory):
        return cls(
            id=m.id,
            key=m.key,
            value=m.value,
            created_at=m.created_at.isoformat(),
        )


async def _get_session_memory(session_id: str):
    """获取 session 的记忆键值对"""
    async with get_async_session() as session:
        result = await session.execute(
            select(ChatSessionMemory).where(ChatSessionMemory.session_id == session_id).order_by(ChatSessionMemory.created_at)
        )
        memories = [SessionMemoryItem.from_model(m) for m in result.scalars().all()]
        return {"items": memories}


async def _set_session_memory(session_id: str, key: str, value: str):
    """写入/更新记忆"""
    async with get_async_session() as session:
        s = await session.get(ChatSession, session_id)
        if not s:
            raise HTTPException(status_code=404, detail="会话不存在")
        # 如果已有同 key 的记录则更新，否则新增
        result = await session.execute(
            select(ChatSessionMemory).where(ChatSessionMemory.session_id == session_id).where(ChatSessionMemory.key == key)
        )
        existing = result.scalars().first()
        if existing:
            existing.value = value
        else:
            mem = ChatSessionMemory(
                id=str(uuid.uuid4()),
                session_id=session_id,
                key=key,
                value=value,
            )
            session.add(mem)
        await session.commit()
        return {"ok": True}


# ---- 路由 ----

@router.get("/chat/sessions")
async def list_chat_sessions():
    """列出聊天会话"""
    return await _list_sessions()


@router.post("/chat/sessions")
async def create_chat_session(payload: ChatSessionCreate):
    """新建聊天会话"""
    return await _create_session(payload)


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    """获取聊天会话详情 + 消息"""
    return await _get_session(session_id)


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    """删除聊天会话"""
    return await _delete_session(session_id)


@router.put("/chat/sessions/{session_id}")
async def update_chat_session(session_id: str, payload: SessionTopicUpdate):
    """更新聊天会话主题"""
    return await _update_session_topic(session_id, payload.topic)


@router.get("/chat/sessions/{session_id}/memory")
async def get_session_memory(session_id: str):
    """获取会话记忆"""
    return await _get_session_memory(session_id)


@router.post("/chat/sessions/{session_id}/memory")
async def set_session_memory(session_id: str, payload: dict):
    """设置会话记忆（{key, value}）"""
    key = payload.get("key", "")
    value = payload.get("value", "")
    if not key:
        raise HTTPException(status_code=400, detail="记忆 key 不能为空")
    return await _set_session_memory(session_id, key, value)


async def _fetch_recent_session_memories(exclude_session_id: str, limit_sessions: int = 5):
    """取最近 limit_sessions 个 session（排除当前 session）的记忆，供跨会话上下文注入。

    按 created_at 倒序取若干行，按 session_id 去重（每 session 仅取最新一条），对齐 DESIGN 的「最近 5 个 session」。

    多租户：必须按当前账号 owner 过滤——绝不把 A 的记忆注入 B 的对话（跨会话记忆泄漏 = 越权读）。
    迁移期 RAT_LEGACY_GLOBAL=1（aid 为 None）则不叠加 owner 过滤，维持旧行为。
    """
    aid = _current_aid()
    async with get_async_session() as session:
        stmt = (
            select(
                ChatSessionMemory.session_id,
                ChatSessionMemory.key,
                ChatSessionMemory.value,
            )
            .join(ChatSession, ChatSessionMemory.session_id == ChatSession.id)
        )
        if aid:
            stmt = stmt.where(ChatSession.owner_id == aid)
        stmt = (
            stmt.where(ChatSessionMemory.session_id != exclude_session_id)
            .order_by(desc(ChatSessionMemory.created_at))
            .limit(limit_sessions * 5)
        )
        rows = await session.execute(stmt)
        seen_sessions: set = set()
        out = []
        for sid, key, value in rows.all():
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            out.append({"key": key, "value": value})
        return out


# ---- /chat 端点集成 session ----

class ChatRequestWithSession(ChatRequest):
    """扩展 ChatRequest：支持 session_id"""
    session_id: Optional[str] = None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequestWithSession):
    """对话优先入口：支持 session_id 绑定会话 + 跨会话记忆注入。

    - 无 session_id → 自动新建 session，返回 session_id
    - 有 session_id → 从 DB 读历史消息拼成 history，追加新消息
    - 从最近 N 个 session 的记忆中提取上下文，注入 user 消息（轻量跨会话记忆）
    """
    if not (request.message or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "EMPTY_MESSAGE", "message": "消息不能为空"},
        )

    # 记忆注入只作用于传给 agent 的消息，绝不改写持久化的原始用户消息
    user_msg_for_agent = request.message
    history = request.history or []
    session_id = request.session_id

    try:
        agent = _get_chat_agent()

        if session_id:
            aid = _current_aid()
            async with get_async_session() as session:
                s = await session.get(ChatSession, session_id)
                if not s:
                    raise HTTPException(status_code=404, detail="会话不存在")
                # 多租户：越权访问按 404（防枚举），绝不把他人会话喂给 agent
                if aid and s.owner_id and s.owner_id != aid:
                    raise HTTPException(status_code=404, detail="会话不存在或无权访问")
                msgs = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at)
                )
                db_messages = msgs.scalars().all()
                db_history = [{"role": m.role, "content": m.content} for m in db_messages]
                # 合并：前端 history + DB 历史（去重，保留顺序）
                seen = set()
                merged = []
                for h in history + db_history:
                    key = (h.get("role"), h.get("content"))
                    if key not in seen:
                        seen.add(key)
                        merged.append(h)
                history = merged[-20:]  # 截断最近 20 条防超长

                # 跨会话记忆注入：取最近 5 个 session 的 conclusion 注入
                recent_mems = await _fetch_recent_session_memories(session_id, limit_sessions=5)
                if recent_mems:
                    mem_lines = [f"- {m['key']}: {m['value'][:200]}" for m in recent_mems]
                    memory_prompt = (
                        "\n【你可能感兴趣的上下文（来自之前的对话）】\n"
                        + "\n".join(mem_lines)
                        + "\n"
                    )
                    user_msg_for_agent = request.message + memory_prompt

        chat_start = time.time()
        logger.info(
            "ChatAgent step start: session=%s model=%s msg_len=%d history_len=%d",
            session_id, request.model, len(user_msg_for_agent), len(history)
        )
        try:
            # agent.step 是同步阻塞调用（内含 OpenAI SDK HTTP 请求+工具执行）。
            # 直接在 async def 里调用会阻塞 FastAPI 的 event loop，导致该 worker
            # 在该请求期间无法处理其他请求，并可能让浏览器/代理误判服务器无响应。
            # 放到 threadpool + 5min 服务端上限，既能跑又不卡住整个 API。
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    agent.step,
                    history=history,
                    user_msg=user_msg_for_agent,
                    model=request.model,
                ),
                timeout=300,
            )
        except asyncio.TimeoutError as e:
            logger.error("ChatAgent step timeout after 300s: session=%s", session_id)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "error": "CHAT_AGENT_TIMEOUT",
                    "message": "ChatAgent 处理超时（300s），请稍后重试或检查 new-api 网关是否可达",
                },
            ) from e
        chat_elapsed = time.time() - chat_start
        logger.info(
            "ChatAgent step done: session=%s elapsed=%.2fs intent=%s reply_len=%d",
            session_id, chat_elapsed, result.get("intent"), len(result.get("reply", ""))
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("ChatAgent 异常: session=%s", session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "CHAT_AGENT_FAILED",
                "message": str(e),
                "traceback": traceback.format_exc()[-3000:],
            },
        )

    reply = result.get("reply", "")
    intent = result.get("intent", "chat")
    task_id: Optional[str] = None
    task_status: Optional[str] = None
    tool_calls: List[str] = result.get("tool_calls", []) or []
    sources: List[dict] = result.get("sources", []) or []

    # 触发研报技能：复用 create_task（异步创建任务，引擎子进程在后台跑）
    if intent == "generate_report" and "report" in result:
        report = result["report"]
        try:
            create_req = CreateTaskRequest(
                user_task=UserTask(
                    topic=report.get("topic") or "未命名主题",
                    scope=report.get("scope") or [],
                    output_format_spec="",
                    constraints=report.get("constraints") or [],
                ),
                plugins=request.plugins or report.get("plugins"),
                model=request.model,
            )
            create_resp = await create_task(create_req)
            task_id = create_resp.task_id
            task_status = (
                create_resp.status.value
                if hasattr(create_resp.status, "value")
                else str(create_resp.status)
            )
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ ChatAgent 触发 create_task 失败: {e}")
            # 不让任务创建失败掩盖对话回复；task_id 留空，前端可提示用户手动去'新任务'页
            reply = f"{reply}\n（自动启动任务失败：{e}，请去'新任务'页手动启动）"

    # 保存消息到 session
    # DB 持久化是「增强」而非对话正确性的前提：DB 不可达 / 表缺失 / 连接超时
    # 一律降级（记日志、不阻断），保证即使记忆层挂了，对话回复仍正常 200 返回，
    # 避免「答案已算对却被 500」的伪失败（2026-09 实测根因）。
    try:
        if not session_id:
            # 自动新建 session（首条用户消息前 80 字作主题）
            async with get_async_session() as session:
                new_session = ChatSession(
                    id=str(uuid.uuid4()),
                    topic=(request.message[:80] if len(request.message) > 80 else request.message),
                    model=request.model or "auto-chat",
                    agents="",
                    plugins=",".join(request.plugins or []),
                    owner_id=_current_aid(),  # 多租户：会话归属当前账号
                )
                session.add(new_session)
                await session.commit()
                session_id = new_session.id
        # 持久化 user + assistant 消息（原始内容，不受记忆注入影响）
        async with get_async_session() as session:
            session.add(ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="user",
                content=request.message,
            ))
            session.add(ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="assistant",
                content=reply,
                task_id=task_id,
                task_status=task_status,
            ))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("ChatAgent 会话持久化失败（降级：仍返回对话回复）: %s", e)

    # 跨会话记忆：每 session 仅存一份 conclusion（upsert），避免无限膨胀
    try:
        await _set_session_memory(session_id, "conclusion", reply[:300])
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 记忆写入失败: {e}")

    return ChatResponse(
        reply=reply,
        intent=intent,
        task_id=task_id,
        task_status=task_status,
        session_id=session_id,
        tool_calls=tool_calls,
        sources=sources,
    )


# ============================================================================
# ChatAgent 经验手册（进化·人工 accept 门禁，对齐 M11-2 D4）
# - 用户说「记住：…」→ ChatAgent 存为 proposed 草稿
# - 管理员在此查看草稿、采纳（accept 后写入 config/chat_playbook.yaml，下轮对话生效）
# ============================================================================

class ChatLessonAccept(BaseModel):
    text: str


@router.get("/admin/chat-lessons")
async def list_chat_lessons():
    """列出待采纳的经验草稿（proposed）。"""
    from tools.chat_learning import list_proposed

    return {"proposed": list_proposed()}


@router.post("/admin/chat-lessons/accept")
async def accept_chat_lesson(payload: ChatLessonAccept):
    """采纳一条经验草稿进 playbook（热加载，下轮对话生效）。"""
    from tools.chat_learning import accept_lesson

    ok = accept_lesson(payload.text)
    return {"ok": ok, "text": payload.text}
