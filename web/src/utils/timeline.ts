// 时间线语义转译（原始事件 / Gate 审核 → 用户叙事节点）
// 对齐 UIDESIGN §5.1.1 语义分档 + API_SPEC §3.2（payload 平铺在顶层）
import type {
  WSEvent,
  GateCompleteData,
  GateReview,
  EngineEvent,
  TimelineNode,
} from '@/types';
import { normalizeReviewStatus, isDegraded, REVIEW_STATUS_TIMELINE } from '@/utils/reviewStatus';

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}-${Date.now()}-${seq}`;
}

// 7 种 WS 事件 → 用户叙事节点
// 注意：真实 payload 平铺在 ev 顶层（无 data 包裹）。WSEvent 为判别联合，
// 经 switch (ev.event_type) 收窄后可直接访问专属字段，无需 as 断言（消除 F6）。
export function wsEventToNode(ev: WSEvent): TimelineNode {
  const ts = ev.timestamp;
  switch (ev.event_type) {
    case 'agent_complete': {
      const toolCount = Array.isArray(ev.tool_status) ? ev.tool_status.length : 0;
      return {
        id: nextId('agent'),
        type: 'agent',
        timestamp: ts,
        title: `🔍 ${ev.agent} 调研完成`,
        description: `第 ${ev.round} 轮产出完成（工具调用 ${toolCount} 次）`,
        status: 'success',
        rawEvent: ev,
        meta: { agent: ev.agent, round: ev.round },
      };
    }
    case 'gate_complete': {
      const label: Record<GateCompleteData['decision'], string> = {
        advance: '✅ 审核放行',
        rework: '🔁 打回重做',
        escalate: '⚠️ 升级人工',
      };
      const statusMap: Record<GateCompleteData['decision'], TimelineNode['status']> = {
        advance: 'success',
        rework: 'warning',
        escalate: 'error',
      };
      // M13-gate-degradation：闸降级时**不得**显示成"审核放行"。
      // 后端在事件里带了 review_status；缺失（旧事件）时按 reason 文本兜底推断。
      const degraded = normalizeReviewStatus(ev) === 'degraded_unavailable';
      return {
        id: nextId('gate'),
        type: 'gate',
        timestamp: ts,
        title: degraded ? `⚠️ 未经 AI 审核 · ${ev.gate}` : `${label[ev.decision]} · ${ev.gate}`,
        description: ev.reason || `评分 ${ev.eval_score}`,
        status: degraded ? REVIEW_STATUS_TIMELINE.degraded_unavailable : statusMap[ev.decision],
        rawEvent: ev,
        meta: { gate: ev.gate, decision: ev.decision, eval_score: ev.eval_score, problem_points: ev.problem_points },
      };
    }
    case 'rework_trigger': {
      return {
        id: nextId('rework'),
        type: 'rework',
        timestamp: ts,
        title: `🔁 返工 · ${ev.rework_target_agent}`,
        description: ev.rework_reason,
        status: 'warning',
        rawEvent: ev,
        meta: { rework_target_agent: ev.rework_target_agent, reason: ev.rework_reason, last_gate: ev.last_gate, round: ev.round },
      };
    }
    case 'round_update': {
      return {
        id: nextId('round'),
        type: 'round',
        timestamp: ts,
        title: `🔄 进入第 ${ev.round} / ${ev.max_rounds} 轮`,
        description: '多 Agent 协作进入新一轮迭代',
        status: 'info',
        rawEvent: ev,
        meta: { round: ev.round, max_rounds: ev.max_rounds },
      };
    }
    case 'tool_error': {
      return {
        id: nextId('tool_error'),
        type: 'tool_error',
        timestamp: ts,
        title: `⚠️ 工具调用异常 · ${ev.agent}`,
        description: `${ev.tool}: ${ev.error}`,
        status: 'error',
        rawEvent: ev,
        meta: { agent: ev.agent, tool: ev.tool, error: ev.error, round: ev.round },
      };
    }
    case 'agent_output_unusable': {
      return {
        id: nextId('agent_fail'),
        type: 'tool_error',
        timestamp: ts,
        title: `⚠️ Agent 产出不可用 · ${ev.agent}`,
        description: ev.reason,
        status: 'error',
        rawEvent: ev,
        meta: { event: ev.event, agent: ev.agent, reason: ev.reason, round: ev.round },
      };
    }
    case 'task_done': {
      return {
        id: nextId('done'),
        type: 'done',
        timestamp: ts,
        title: '🎉 研报生成完成',
        description: `终稿已产出（第 ${ev.round} 轮）`,
        status: 'success',
        rawEvent: ev,
        meta: { round: ev.round, audit_url: ev.audit_url },
      };
    }
    case 'task_escalated': {
      return {
        id: nextId('escalated'),
        type: 'escalated',
        timestamp: ts,
        title: '⚠️ 任务升级人工复核',
        description: ev.escalate_reason,
        status: 'error',
        rawEvent: ev,
        meta: { last_gate: ev.last_gate, round: ev.round, audit_url: ev.audit_url },
      };
    }
    case 'connection_established': {
      return {
        id: nextId('sys'),
        type: 'round',
        timestamp: ts,
        title: '🔌 连接已建立',
        description: '',
        status: 'info',
        rawEvent: ev,
        meta: {},
      };
    }
    case 'researcher_records_synthesized':
    case 'writer_citation_regenerated':
    case 'gate_llm_unavailable_degraded_advance': {
      // 引擎层留痕事件经 WS **原样广播**：后端 `_stream_engine_events` 把 engine_events JSONL
      // 逐条 broadcast，不区分「WS 事件」与「引擎事件」（实测：同一行同时带 event_type 与 event）。
      // 此处复用 `ENGINE_EVENT_NODES`，与快照重建路径（engineEventToNode）保持标题/状态一致。
      const style = ENGINE_EVENT_NODES[ev.event_type];
      return {
        id: nextId('engine'),
        type: style.type,
        timestamp: ts,
        title: style.title,
        description: ev.reason ?? '',
        status: style.status,
        rawEvent: ev,
        meta: { event: ev.event_type, agent: ev.agent, round: ev.round, count: ev.count },
      };
    }
    default: {
      // 运行时兜底：若后端新增事件类型，TS 会在编译期报错（ev 被收窄为 never），
      // 此处通过安全方式提取 event_type 字符串用于展示；主路径 11 个事件类型零断言。
      const unknownType = (ev as { event_type?: unknown }).event_type ?? 'unknown';
      return {
        id: nextId('sys'),
        type: 'round',
        timestamp: ts,
        title: `未知事件 ${String(unknownType)}`,
        description: '',
        status: 'info',
        rawEvent: ev,
        meta: {},
      };
    }
  }
}

// Gate 审核历史 → 节点（用于全量快照重建）
export function gateReviewToNode(review: GateReview): TimelineNode {
  const label: Record<GateReview['decision'], string> = {
    advance: '✅ 审核放行',
    rework: '🔁 打回重做',
    escalate: '⚠️ 升级人工',
  };
  // M13-gate-degradation：与实时路径（wsEventToNode 的 `gate_complete` 分支）**必须一致** ——
  // 降级闸绝不可显示成「✅ 审核放行」。
  // 实测教训（2026-09-19 夜）：初版只改了实时路径，本函数仍渲染「✅ 审核放行 · GateA」，
  // 于是同一张追踪页上出现「✅ 审核放行」紧挨着「🔓 闸 LLM 不可用·降级放行」的自相矛盾陈述
  // ——**比不显示更糟**，因为它把降级又包装成了审核结论。
  const degraded = isDegraded(review);
  return {
    id: nextId('gate'),
    type: 'gate',
    timestamp: review.timestamp,
    title: degraded ? `⚠️ 未经 AI 审核 · ${review.gate}` : `${label[review.decision]} · ${review.gate}`,
    description: review.reason,
    status: degraded
      ? REVIEW_STATUS_TIMELINE.degraded_unavailable
      : review.decision === 'advance'
        ? 'success'
        : review.decision === 'rework'
          ? 'warning'
          : 'error',
    meta: { gate: review.gate, decision: review.decision, eval_score: review.eval_score, problem_points: review.problem_points },
  };
}

// 引擎层留痕事件（4 种）→ 节点标题 / 类型 / 状态色。
//
// **单一事实源**：实时路径（WS 原样广播 engine_events）与快照重建路径（GET /tasks/{id}）共用。
// M13 实现期实测教训：最初只补了快照路径的 titleMap，实时时间线仍渲染
// 「未知事件 researcher_records_synthesized」—— 同一份映射在两条路径各写一遍，必漏其一。
//
// 另注（实现期对既有语义的修正，已记入 VERIFICATION）：原实现对除降级放行外的引擎事件
// 一律给 `tool_error` + `error`（红），但 `researcher_records_synthesized` 是**纯告知性**
// 事件（引擎把检索记录合成了，count=N），`writer_citation_regenerated` 是补救性动作，
// 均非错误。把信息性事件渲染成红色错误同样是「挂羊头卖狗肉」，故按语义分开着色。
const ENGINE_EVENT_NODES: Record<
  EngineEvent['event'],
  Pick<TimelineNode, 'title' | 'type' | 'status'>
> = {
  agent_output_unusable: { title: '⚠️ Agent 产出不可用', type: 'tool_error', status: 'error' },
  writer_citation_regenerated: { title: '🔗 引用链已重建', type: 'agent', status: 'warning' },
  gate_llm_unavailable_degraded_advance: { title: '🔓 闸 LLM 不可用·降级放行', type: 'gate', status: 'warning' },
  researcher_records_synthesized: { title: '🧩 检索记录由引擎合成', type: 'agent', status: 'info' },
};

// 引擎层事件 → 节点（用于全量快照重建，调试留痕）
export function engineEventToNode(ev: EngineEvent): TimelineNode {
  const style = ENGINE_EVENT_NODES[ev.event];
  return {
    id: nextId('engine'),
    type: style.type,
    timestamp: ev.timestamp,
    title: style.title,
    description: ev.reason,
    status: style.status,
    meta: { event: ev.event, agent: ev.agent, gate: ev.gate, round: ev.round },
  };
}

// 全量快照重建时间线（兜底同步：mount/重连后调用 GET /tasks/{id}）
export function buildNodesFromTask(
  gateReviews: GateReview[],
  engineEvents: EngineEvent[]
): TimelineNode[] {
  const fromReviews = gateReviews.map(gateReviewToNode);
  const fromEngine = engineEvents.map(engineEventToNode);
  // 按时间排序融合
  return [...fromReviews, ...fromEngine].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
}
