"""
数据库模型定义
M6-4 产出：PostgreSQL 表结构设计
对齐 API_SPEC.md §4 状态投影 schema
"""

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, JSON,
    ForeignKey, Index, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


# ============================================================================
# 任务主表
# ============================================================================


class Task(Base):
    """任务主表（对齐 API_SPEC.md §4.1 顶层结构）"""
    __tablename__ = "tasks"

    task_id = Column(String(36), primary_key=True, comment="UUID-v4 任务 ID")
    status = Column(String(20), nullable=False, index=True, comment="任务状态：running/done/escalated/aborted")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    # user_task（JSON 存储）
    topic = Column(String(500), nullable=False, comment="研究主题")
    scope = Column(JSON, nullable=False, comment="研究范围维度列表")
    output_format_spec = Column(Text, nullable=False, comment="输出格式规范")
    constraints = Column(JSON, nullable=False, default=list, comment="约束条件列表")

    # 产出层（JSON 存储）
    retrieval_records = Column(JSON, nullable=False, default=list, comment="检索记录")
    analysis_conclusions = Column(JSON, nullable=False, default=list, comment="分析结论")
    draft_segments = Column(JSON, nullable=False, default=list, comment="草稿片段")
    tool_status = Column(JSON, nullable=False, default=list, comment="工具调用结果")
    prior_versions = Column(JSON, nullable=False, default=dict, comment="产出快照（每轮）")
    report_markdown = Column(Text, nullable=True, comment="完整研报内容（仅 status=done/escalated）")
    escalate_reason = Column(Text, nullable=True, comment="升级原因（仅 status=escalated）")

    # 关系
    routing_state = relationship("RoutingState", back_populates="task", uselist=False, cascade="all, delete-orphan")
    engine_events = relationship("EngineEvent", back_populates="task", cascade="all, delete-orphan")
    gate_reviews = relationship("GateReview", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_status_created', 'status', 'created_at'),
    )


# ============================================================================
# 路由状态表
# ============================================================================


class RoutingState(Base):
    """路由状态表（对齐 API_SPEC.md §4.2 routing_state）"""
    __tablename__ = "routing_states"

    task_id = Column(String(36), ForeignKey("tasks.task_id", ondelete="CASCADE"), primary_key=True)
    round = Column(Integer, nullable=False, default=1, comment="当前轮次")
    max_rounds = Column(Integer, nullable=False, default=2, comment="最大轮次")
    last_gate = Column(String(10), nullable=True, comment="最后一道闸：GateA/GateB/GateC/null")
    status = Column(String(20), nullable=False, index=True, comment="路由状态：running/done/escalated")
    rework_target_agent = Column(String(20), nullable=True, comment="返工目标 Agent：Researcher/Analyst/Writer/null")
    rework_reason = Column(Text, nullable=True, comment="返工原因")

    # 关系
    task = relationship("Task", back_populates="routing_state")

    __table_args__ = (
        Index('idx_task_id', 'task_id'),
    )


# ============================================================================
# 引擎事件表
# ============================================================================


class EngineEvent(Base):
    """引擎事件表（对齐 schema.md §4.1 engine_events）"""
    __tablename__ = "engine_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True)

    event = Column(String(60), nullable=False, comment="事件类型：agent_output_unusable/writer_citation_regenerated/gate_llm_unavailable_degraded_advance/researcher_records_synthesized")
    agent = Column(String(20), nullable=True, comment="Agent 名称：Researcher/Analyst/Writer/null")
    gate = Column(String(10), nullable=True, comment="Gate 名称：GateA/GateB/GateC/null")
    reason = Column(Text, nullable=False, comment="事件原因")
    round = Column(Integer, nullable=False, comment="事件发生轮次")
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, comment="事件时间")

    # 关系
    task = relationship("Task", back_populates="engine_events")

    __table_args__ = (
        Index('idx_engine_event_task_id_round', 'task_id', 'round'),
        Index('idx_event_type', 'event'),
    )


# ============================================================================
# Gate 审计表
# ============================================================================


class GateReview(Base):
    """Gate 审计表（对齐 API_SPEC.md §4.2 gate_review_history）"""
    __tablename__ = "gate_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True)

    decision = Column(String(20), nullable=False, comment="决策：advance/rework/escalate")
    reason = Column(Text, nullable=False, comment="决策原因")
    eval_score = Column(Float, nullable=False, comment="评估分数（0-1）")
    problem_points = Column(JSON, nullable=False, default=list, comment="问题点列表")
    gate = Column(String(10), nullable=False, comment="Gate 名称：GateA/GateB/GateC")
    round = Column(Integer, nullable=False, comment="决策轮次")
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, comment="决策时间")

    # 关系
    task = relationship("Task", back_populates="gate_reviews")

    __table_args__ = (
        Index('idx_gate_review_task_id_round', 'task_id', 'round'),
        Index('idx_gate_decision', 'gate', 'decision'),
    )


# ============================================================================
# 数据库工具函数
# ============================================================================


def task_to_dict(task: Task) -> dict:
    """Task 模型转字典（对齐 API_SPEC.md §4.1）"""
    return {
        "task_id": task.task_id,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "user_task": {
            "topic": task.topic,
            "scope": task.scope or [],
            "output_format_spec": task.output_format_spec,
            "constraints": task.constraints or []
        },
        "routing_state": {
            "round": task.routing_state.round if task.routing_state else 1,
            "max_rounds": task.routing_state.max_rounds if task.routing_state else 2,
            "last_gate": task.routing_state.last_gate if task.routing_state else None,
            "status": task.routing_state.status if task.routing_state else task.status,
            "rework_target_agent": task.routing_state.rework_target_agent if task.routing_state else None,
            "rework_reason": task.routing_state.rework_reason if task.routing_state else None,
            "gate_review_history": [
                {
                    "decision": gr.decision,
                    "reason": gr.reason,
                    "eval_score": gr.eval_score,
                    "problem_points": gr.problem_points or [],
                    "gate": gr.gate,
                    "round": gr.round,
                    "timestamp": gr.timestamp.isoformat() if gr.timestamp else None
                }
                for gr in task.gate_reviews
            ],
            "engine_events": [
                {
                    "event": ee.event,
                    "agent": ee.agent,
                    "gate": ee.gate,
                    "reason": ee.reason,
                    "round": ee.round,
                    "timestamp": ee.timestamp.isoformat() if ee.timestamp else None
                }
                for ee in task.engine_events
            ]
        },
        "retrieval_records": task.retrieval_records or [],
        "analysis_conclusions": task.analysis_conclusions or [],
        "draft_segments": task.draft_segments or [],
        "tool_status": task.tool_status or [],
        "prior_versions": task.prior_versions or {},
        "report_markdown": task.report_markdown,
        "escalate_reason": task.escalate_reason
    }


def dict_to_task(task_data: dict) -> Task:
    """字典转 Task 模型"""
    user_task = task_data.get("user_task", {})
    return Task(
        task_id=task_data["task_id"],
        status=task_data["status"],
        created_at=datetime.fromisoformat(task_data["created_at"]) if task_data.get("created_at") else datetime.utcnow(),
        updated_at=datetime.fromisoformat(task_data["updated_at"]) if task_data.get("updated_at") else datetime.utcnow(),
        topic=user_task.get("topic"),
        scope=user_task.get("scope", []),
        output_format_spec=user_task.get("output_format_spec", ""),
        constraints=user_task.get("constraints", []),
        retrieval_records=task_data.get("retrieval_records", []),
        analysis_conclusions=task_data.get("analysis_conclusions", []),
        draft_segments=task_data.get("draft_segments", []),
        tool_status=task_data.get("tool_status", []),
        prior_versions=task_data.get("prior_versions", {}),
        report_markdown=task_data.get("report_markdown"),
        escalate_reason=task_data.get("escalate_reason")
    )


# ============================================================================
# 聊天会话表（M10 chat_memory）
# ============================================================================


class ChatSession(Base):
    """聊天会话主表。每次打开 ChatEntry 即产生一个 session，可跨会话恢复。"""
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, comment="UUID-v4 会话 ID")
    topic = Column(String(200), nullable=True, comment="会话主题（首条用户消息自动生成）")
    model = Column(String(100), nullable=False, comment="当时选中的模型 id")
    agents = Column(String(500), nullable=False, default="", comment="逗号分隔参与 agent 列表")
    plugins = Column(String(500), nullable=False, default="", comment="逗号分隔启用插件列表")
    owner_id = Column(String(36), nullable=True, index=True, comment="所属账号 id（多租户隔离，DESIGN_account_hierarchy §4.3）")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    # 关系
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    memories = relationship("ChatSessionMemory", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_created_at', 'created_at'),
        Index('idx_owner_created', 'owner_id', 'created_at'),
    )


class ChatMessage(Base):
    """聊天消息（属于一个 session）"""
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, comment="UUID-v4 消息 ID")
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True, comment="所属会话")
    role = Column(String(20), nullable=False, comment="user / assistant")
    content = Column(Text, nullable=False, comment="消息内容")
    task_id = Column(String(36), nullable=True, comment="关联研报任务 ID（可空）")
    task_status = Column(String(50), nullable=True, comment="关联任务状态")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="创建时间")

    # 关系
    session = relationship("ChatSession", back_populates="messages")


class ChatSessionMemory(Base):
    """跨会话记忆：从 session 对话中提取的关键词/实体/结论，注入后续 session 的 system prompt。"""
    __tablename__ = "chat_session_memories"

    id = Column(String(36), primary_key=True, comment="UUID-v4 记忆 ID")
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True, comment="源会话")
    key = Column(String(100), nullable=False, comment="记忆键：topic / conclusion / entities / user_preference")
    value = Column(Text, nullable=False, comment="记忆值（JSON 或纯文本）")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="记忆创建时间")

    # 关系
    session = relationship("ChatSession", back_populates="memories")

    __table_args__ = (
        Index('idx_key', 'key'),
    )


# ============================================================================
# 账号体系（Phase 1 · DESIGN_account_hierarchy.md）
# ============================================================================


class Account(Base):
    """
    账号表。

    - 主账号 role=main 且 parent_id 为空，首个注册者引导产生（is_system_main）
    - 子账号 role=sub，parent_id 指向主账号；开放注册后为 pending，需主账号 approve
    - resources 目录：tenants/<id>/（见 server/tenancy.py）
    """
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, comment="UUID-v4 账号 ID（同时是 tenants/<id> 目录名）")
    username = Column(String(64), nullable=False, unique=True, index=True, comment="登录名")
    password_hash = Column(Text, nullable=False, comment="bcrypt 口令哈希")
    role = Column(String(16), nullable=False, default="sub", comment="main / sub")
    parent_id = Column(String(36), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True, comment="所属主账号（主账号自身为 NULL）")
    status = Column(String(16), nullable=False, default="pending", index=True, comment="pending / active / disabled / rejected")
    is_system_main = Column(Boolean, nullable=False, default=False, comment="引导产生的唯一系统主账号")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="注册时间")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    approved_at = Column(DateTime, nullable=True, comment="审批通过时间")
    approved_by = Column(String(36), nullable=True, comment="审批人账号 ID")

    __table_args__ = (
        Index('idx_parent_status', 'parent_id', 'status'),
    )


class CallRecord(Base):
    """
    调用流水（老板裁定 3）：谁在何时调了什么模型/工具/数据源，耗时与成败。

    用途：① 自己只看自己的调用记录 ② Phase 2 主账号点开某个子账号「查阅」
    """
    __tablename__ = "call_records"

    id = Column(String(36), primary_key=True, comment="UUID-v4")
    account_id = Column(String(36), nullable=False, index=True, comment="发起账号 id")
    kind = Column(String(20), nullable=False, index=True, comment="model / tool / mcp / plugin")
    target = Column(String(200), nullable=True, comment="具体模型名 / 工具名 / 数据源 id")
    ok = Column(Boolean, nullable=False, default=True, comment="是否成功")
    latency_ms = Column(Integer, nullable=True, comment="耗时（毫秒）")
    cost_hint = Column(Float, nullable=True, comment="花费提示（可为 token 数等，非精确计费）")
    detail = Column(Text, nullable=True, comment="错误摘要等简短说明，不含敏感正文")
    content = Column(Text, nullable=True, comment="操作内容预览：用户输入/模型回复/工具参数与结果（截断）")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="调用时间")

    __table_args__ = (
        Index('idx_account_created', 'account_id', 'created_at'),
    )