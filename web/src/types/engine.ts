// WebSocket 事件类型（对齐 API_SPEC.md §3.2 权威契约）
// 重要：API_SPEC §3.2 的 payload 是【平铺在消息顶层】，并非包裹在 data 字段。
// 前端解析以消息顶层字段为准；WSEvent 采用【判别联合】，每个 event_type 直接携带其专属字段，
// 消费点通过 switch (ev.event_type) 类型收窄即可直读，无需 any / as 断言（消除 F6 技术债）。
import type { AgentRole, GateName, GateDecision, EngineEventType } from './api';

// 业务事件 + 引擎留痕事件 + 连接建立系统事件
export type WSEventType =
  | 'agent_complete'
  | 'gate_complete'
  | 'rework_trigger'
  | 'round_update'
  | 'tool_error'
  | 'agent_output_unusable'
  // M13-gate-degradation 修正：引擎层留痕事件（engine_events）**同样经 WS 原样广播** ——
  // 后端 `server/api.py::_stream_engine_events` 把 engine_events JSONL 逐条 broadcast，
  // 不区分「WS 事件」与「引擎事件」。原先只声明了 `agent_output_unusable`，其余 3 种落到
  // `utils/timeline.ts::wsEventToNode` 的 default 分支 → 实时时间线渲染成
  // 「未知事件 researcher_records_synthesized」（已实测复现，2026-09-19）。
  | 'researcher_records_synthesized'
  | 'writer_citation_regenerated'
  | 'gate_llm_unavailable_degraded_advance'
  | 'task_done'
  | 'task_escalated'
  | 'connection_established';

// 事件公共信封（所有事件均携带的字段）
type WSEnvelope = { event_type: WSEventType; task_id: string; timestamp: string };
// 判别联合成员：信封 + 专属 payload（D），并将 event_type 收窄为字面量以驱动类型收窄
type WSMember<E extends WSEventType, D> = WSEnvelope & D & { event_type: E };

// 引擎层留痕事件的 payload：字段随事件类型增减（count / reason / gate / round…），
// 故用宽松的可选字段统一承载，不为每种留痕事件各造一个近乎相同的接口。
export interface EngineEventData {
  event: EngineEventType;
  agent?: string;
  gate?: string;
  round?: number;
  reason?: string;
  count?: number;
  debug_state?: Record<string, unknown> | null;
}

// WebSocket 事件判别联合（payload 平铺在顶层，强类型、无 any、无 as 断言）
export type WSEvent =
  | WSMember<'agent_complete', AgentCompleteData>
  | WSMember<'gate_complete', GateCompleteData>
  | WSMember<'rework_trigger', ReworkTriggerData>
  | WSMember<'round_update', RoundUpdateData>
  | WSMember<'tool_error', ToolErrorData>
  | WSMember<'agent_output_unusable', AgentOutputUnusableData>
  | WSMember<'researcher_records_synthesized', EngineEventData>
  | WSMember<'writer_citation_regenerated', EngineEventData>
  | WSMember<'gate_llm_unavailable_degraded_advance', EngineEventData>
  | WSMember<'task_done', TaskDoneData>
  | WSMember<'task_escalated', TaskEscalatedData>
  | WSMember<'connection_established', ConnectionEstablishedData>;

// 各事件 payload（严格对齐 API_SPEC §3.2 字段名）
export interface AgentCompleteData {
  round: number;
  agent: AgentRole;
  tool_status: unknown[];
  engine_events: unknown[];
  debug_state?: Record<string, unknown> | null;
}

export interface GateCompleteData {
  round: number;
  gate: GateName;
  decision: GateDecision;
  reason: string;
  eval_score: number;
  problem_points: string[];
  debug_state?: Record<string, unknown> | null;
  // M13-gate-degradation：闸降级必须可区分。用字面量联合而非从 './api' 引入，
  // 是为了避免与 './api' 形成 import 环（engine.ts 已被 './api' 侧间接引用）。
  // 与 api.ts 的 GateReviewStatus / EvalScoreSource 保持同构，改动须同步。
  review_status?: 'llm_reviewed' | 'code_verified' | 'degraded_unavailable' | null;
  eval_score_source?: 'gate_llm' | 'independent_scorer' | null;
}

export interface ReworkTriggerData {
  round: number;
  rework_target_agent: AgentRole;
  rework_reason: string;
  last_gate: GateName;
  debug_state?: Record<string, unknown> | null;
}

export interface RoundUpdateData {
  round: number;
  max_rounds: number;
  debug_state?: Record<string, unknown> | null;
}

export interface ToolErrorData {
  round: number;
  agent: AgentRole;
  tool: string;
  error: string;
  debug_state?: Record<string, unknown> | null;
}

export interface AgentOutputUnusableData {
  event: 'agent_output_unusable';
  round: number;
  agent: AgentRole;
  reason: string;
  debug_state?: Record<string, unknown> | null;
}

export interface TaskDoneData {
  round: number;
  status: 'done';
  report_markdown?: string | null;
  audit_url: string;
  debug_state?: Record<string, unknown> | null;
}

export interface TaskEscalatedData {
  round: number;
  status: 'escalated';
  escalate_reason: string;
  last_gate: GateName;
  audit_url: string;
  debug_state?: Record<string, unknown> | null;
}

export interface ConnectionEstablishedData {
  debug_mode: number;
  message: string;
}
