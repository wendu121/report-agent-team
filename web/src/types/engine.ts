// WebSocket 事件类型（对齐 API_SPEC.md §3.2 权威契约）
// 重要：API_SPEC §3.2 的 payload 是【平铺在消息顶层】，并非包裹在 data 字段。
// 前端解析以消息顶层字段为准；WSEvent 采用【判别联合】，每个 event_type 直接携带其专属字段，
// 消费点通过 switch (ev.event_type) 类型收窄即可直读，无需 any / as 断言（消除 F6 技术债）。
import type { AgentRole, GateName, GateDecision } from './api';

// 7 种业务事件 + 连接建立系统事件
export type WSEventType =
  | 'agent_complete'
  | 'gate_complete'
  | 'rework_trigger'
  | 'round_update'
  | 'tool_error'
  | 'agent_output_unusable'
  | 'task_done'
  | 'task_escalated'
  | 'connection_established';

// 事件公共信封（所有事件均携带的字段）
type WSEnvelope = { event_type: WSEventType; task_id: string; timestamp: string };
// 判别联合成员：信封 + 专属 payload（D），并将 event_type 收窄为字面量以驱动类型收窄
type WSMember<E extends WSEventType, D> = WSEnvelope & D & { event_type: E };

// WebSocket 事件判别联合（payload 平铺在顶层，强类型、无 any、无 as 断言）
export type WSEvent =
  | WSMember<'agent_complete', AgentCompleteData>
  | WSMember<'gate_complete', GateCompleteData>
  | WSMember<'rework_trigger', ReworkTriggerData>
  | WSMember<'round_update', RoundUpdateData>
  | WSMember<'tool_error', ToolErrorData>
  | WSMember<'agent_output_unusable', AgentOutputUnusableData>
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
