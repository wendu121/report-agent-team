"""
WebSocket 事件推送实现
M6-2 产出：WebSocket 路由
实现 API_SPEC.md §3 定义的所有 WebSocket 事件
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from pydantic import BaseModel
from typing import Optional, Literal, Dict, Any
from datetime import datetime
from enum import Enum
import json

router = APIRouter()


# ============================================================================
# 连接管理器
# ============================================================================


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # task_id -> list[WebSocket]
        self.active_connections: Dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str):
        """接受连接"""
        await websocket.accept()

        # 限制单任务最大并发连接数（防止恶意连接）
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []

        if len(self.active_connections[task_id]) >= 10:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="任务并发连接数超限（最多 10 个）"
            )
            return

        self.active_connections[task_id].append(websocket)
        print(f"🔌 WebSocket 连接建立: task_id={task_id}, 当前连接数={len(self.active_connections[task_id])}")

    def disconnect(self, websocket: WebSocket, task_id: str):
        """断开连接"""
        if task_id in self.active_connections:
            if websocket in self.active_connections[task_id]:
                self.active_connections[task_id].remove(websocket)
                print(f"🔌 WebSocket 连接断开: task_id={task_id}, 当前连接数={len(self.active_connections[task_id])}")

            # 清理空列表
            if not self.active_connections[task_id]:
                del self.active_connections[task_id]

    async def broadcast(self, task_id: str, message: dict):
        """广播消息到任务的所有连接"""
        if task_id not in self.active_connections:
            return

        disconnected = []
        for connection in self.active_connections[task_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"⚠️ WebSocket 发送失败: {e}")
                disconnected.append(connection)

        # 清理断开的连接
        for conn in disconnected:
            self.disconnect(conn, task_id)


manager = ConnectionManager()


# ============================================================================
# WebSocket 事件模型（对齐 API_SPEC.md §3.2）
# ============================================================================


class EventType(str, Enum):
    """WebSocket 事件类型枚举"""
    AGENT_COMPLETE = "agent_complete"
    GATE_COMPLETE = "gate_complete"
    REWORK_TRIGGER = "rework_trigger"
    ROUND_UPDATE = "round_update"
    TOOL_ERROR = "tool_error"
    TASK_DONE = "task_done"
    TASK_ESCALATED = "task_escalated"


class AgentCompleteEvent(BaseModel):
    """agent_complete 事件"""
    event_type: Literal["agent_complete"] = "agent_complete"
    task_id: str
    timestamp: str
    round: int
    agent: Literal["Researcher", "Analyst", "Writer"]
    tool_status: list[dict] = []
    engine_events: list[dict] = []
    debug_state: Optional[dict] = None


class GateCompleteEvent(BaseModel):
    """gate_complete 事件"""
    event_type: Literal["gate_complete"] = "gate_complete"
    task_id: str
    timestamp: str
    round: int
    gate: Literal["GateA", "GateB", "GateC"]
    decision: Literal["advance", "rework", "escalate"]
    reason: str
    eval_score: float
    problem_points: list[str] = []
    debug_state: Optional[dict] = None
    # M13-gate-degradation：闸降级必须可区分（详见 DESIGN_gate_degradation_visibility.md）。
    # 可选而非必填，避免破坏既有事件契约；缺失时前端按 reason 文本兜底推断。
    review_status: Optional[Literal["llm_reviewed", "code_verified", "degraded_unavailable"]] = None
    eval_score_source: Optional[Literal["gate_llm", "independent_scorer"]] = None


class ReworkTriggerEvent(BaseModel):
    """rework_trigger 事件"""
    event_type: Literal["rework_trigger"] = "rework_trigger"
    task_id: str
    timestamp: str
    round: int
    rework_target_agent: Literal["Researcher", "Analyst", "Writer"]
    rework_reason: str
    last_gate: Literal["GateA", "GateB", "GateC"]
    debug_state: Optional[dict] = None


class RoundUpdateEvent(BaseModel):
    """round_update 事件"""
    event_type: Literal["round_update"] = "round_update"
    task_id: str
    timestamp: str
    round: int
    max_rounds: int
    debug_state: Optional[dict] = None


class ToolErrorEvent(BaseModel):
    """tool_error 事件"""
    event_type: Literal["tool_error"] = "tool_error"
    task_id: str
    timestamp: str
    round: int
    agent: Literal["Researcher", "Analyst", "Writer"]
    tool: str
    error: str
    debug_state: Optional[dict] = None


class TaskDoneEvent(BaseModel):
    """task_done 事件"""
    event_type: Literal["task_done"] = "task_done"
    task_id: str
    timestamp: str
    round: int
    status: Literal["done"]
    report_markdown: Optional[str] = None
    audit_url: str
    debug_state: Optional[dict] = None


class TaskEscalatedEvent(BaseModel):
    """task_escalated 事件"""
    event_type: Literal["task_escalated"] = "task_escalated"
    task_id: str
    timestamp: str
    round: int
    status: Literal["escalated"]
    escalate_reason: str
    last_gate: Literal["GateA", "GateB", "GateC"]
    audit_url: str
    debug_state: Optional[dict] = None


# ============================================================================
# 路由实现
# ============================================================================


@router.websocket("/tasks/{task_id}/stream")
async def websocket_task_stream(
    websocket: WebSocket,
    task_id: str,
    debug_mode: int = Query(default=0, ge=0, le=1, description="调试模式开关")
):
    """
    WebSocket 任务事件流（WS /tasks/{task_id}/stream）

    连接参数：
    - debug_mode: 0=语义化叙事事件，1=原始事件（技术可读）

    事件列表（对齐 API_SPEC.md §3.2）：
    - agent_complete: Agent 产出完成
    - gate_complete: Gate 决策完成
    - rework_trigger: 返工触发
    - round_update: 轮次更新
    - tool_error: 工具调用异常
    - task_done: 任务完成
    - task_escalated: 任务升级
    """
    await manager.connect(websocket, task_id)

    try:
        # 发送连接成功消息
        await websocket.send_json({
            "event_type": "connection_established",
            "task_id": task_id,
            "debug_mode": debug_mode,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "WebSocket 连接已建立"
        })

        # 保持连接，接收客户端消息（支持心跳保活）
        while True:
            data = await websocket.receive_json()

            # 处理客户端消息（心跳 ping/pong）
            if data.get("action") == "ping":
                await websocket.send_json({
                    "action": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })

            # 其他客户端消息可在此扩展

    except WebSocketDisconnect:
        manager.disconnect(websocket, task_id)
        print(f"🔌 WebSocket 客户端断开: task_id={task_id}")

    except Exception as e:
        manager.disconnect(websocket, task_id)
        print(f"⚠️ WebSocket 异常: {e}")


# ============================================================================
# 事件推送辅助函数（供 M6-3 引擎 CLI 封装调用）
# ============================================================================


async def emit_agent_complete(
    task_id: str,
    agent: Literal["Researcher", "Analyst", "Writer"],
    round: int,
    tool_status: list = None,
    engine_events: list = None,
    debug_state: dict = None
):
    """推送 agent_complete 事件"""
    event = AgentCompleteEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        agent=agent,
        tool_status=tool_status or [],
        engine_events=engine_events or [],
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())


async def emit_gate_complete(
    task_id: str,
    gate: Literal["GateA", "GateB", "GateC"],
    round: int,
    decision: Literal["advance", "rework", "escalate"],
    reason: str,
    eval_score: float,
    problem_points: list = None,
    debug_state: dict = None,
    review_status: Optional[str] = None,
    eval_score_source: Optional[str] = None
):
    """推送 gate_complete 事件"""
    event = GateCompleteEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        gate=gate,
        decision=decision,
        reason=reason,
        eval_score=eval_score,
        problem_points=problem_points or [],
        debug_state=debug_state,
        review_status=review_status,
        eval_score_source=eval_score_source
    )
    await manager.broadcast(task_id, event.dict())


async def emit_rework_trigger(
    task_id: str,
    round: int,
    rework_target_agent: Literal["Researcher", "Analyst", "Writer"],
    rework_reason: str,
    last_gate: Literal["GateA", "GateB", "GateC"],
    debug_state: dict = None
):
    """推送 rework_trigger 事件"""
    event = ReworkTriggerEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        rework_target_agent=rework_target_agent,
        rework_reason=rework_reason,
        last_gate=last_gate,
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())


async def emit_round_update(
    task_id: str,
    round: int,
    max_rounds: int,
    debug_state: dict = None
):
    """推送 round_update 事件"""
    event = RoundUpdateEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        max_rounds=max_rounds,
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())


async def emit_tool_error(
    task_id: str,
    round: int,
    agent: Literal["Researcher", "Analyst", "Writer"],
    tool: str,
    error: str,
    debug_state: dict = None
):
    """推送 tool_error 事件"""
    event = ToolErrorEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        agent=agent,
        tool=tool,
        error=error,
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())


async def emit_task_done(
    task_id: str,
    round: int,
    report_markdown: str = None,
    debug_state: dict = None
):
    """推送 task_done 事件"""
    event = TaskDoneEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        status="done",
        report_markdown=report_markdown,
        audit_url=f"/api/v1/tasks/{task_id}/audit-export",
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())


async def emit_task_escalated(
    task_id: str,
    round: int,
    escalate_reason: str,
    last_gate: Literal["GateA", "GateB", "GateC"],
    debug_state: dict = None
):
    """推送 task_escalated 事件"""
    event = TaskEscalatedEvent(
        task_id=task_id,
        timestamp=datetime.utcnow().isoformat(),
        round=round,
        status="escalated",
        escalate_reason=escalate_reason,
        last_gate=last_gate,
        audit_url=f"/api/v1/tasks/{task_id}/audit-export",
        debug_state=debug_state
    )
    await manager.broadcast(task_id, event.dict())