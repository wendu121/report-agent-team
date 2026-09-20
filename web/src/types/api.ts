// 核心类型（对齐 API_SPEC.md v1.0 §2 + UIDESIGN §3/§8）
// 注意：TaskStatus 以 API_SPEC §2 冻结契约为准（6 态），设计文档 M7_FRONTEND_DESIGN §4.1 漏列 created/rework，此处补齐。

// 任务状态枚举（API_SPEC §2 TaskStatus）
export type TaskStatus =
  | 'created'
  | 'running'
  | 'rework'
  | 'done'
  | 'escalated'
  | 'aborted';

// Gate 决策类型
export type GateDecision = 'advance' | 'rework' | 'escalate';

// Agent 角色
// M10-P4：Agent 名由后端 config/agents_library.yaml 驱动，支持自定义 Agent
// （如 MarketScout / EcomAnalyst / SourcingAdvisor）。故此处保留内置名的自动补全提示，
// 同时允许任意字符串——不可用联合类型闭集，否则自定义 Agent 会在类型层被判非法。
export type BuiltinAgentRole = 'Researcher' | 'Analyst' | 'Writer';
export type AgentRole = BuiltinAgentRole | (string & {});

// Gate 名称
// M10-P4：同上，闸名可自定义（GateD/GateE…），不做闭集。
export type BuiltinGateName = 'GateA' | 'GateB' | 'GateC';
export type GateName = BuiltinGateName | (string & {});

// 引擎事件类型（4 种，对齐 schema §4.1 + UIDESIGN §8）
// 修正（M13-gate-degradation）：原先只声明 3 种，**漏了 `researcher_records_synthesized`**
// ——后端 `server/api.py` 的 Literal 里有 4 种，实跑中该事件真的出现过。
// 漏声明的两种表现形式（均已实测复现）：
//   ① 快照重建路径 —— `utils/timeline.ts::engineEventToNode` 的 titleMap 查不到键 → **标题为空**的节点；
//   ② 实时 WS 路径 —— 引擎事件经 `_stream_engine_events` 原样广播，落到 `wsEventToNode` 的
//      default 分支 → 渲染成「未知事件 researcher_records_synthesized」。
// 故本类型同时被 `types/engine.ts::WSEventType` 复用；两条路径共用同一套映射（ENGINE_EVENT_NODES）。
export type EngineEventType =
  | 'agent_output_unusable'
  | 'writer_citation_regenerated'
  | 'gate_llm_unavailable_degraded_advance'
  | 'researcher_records_synthesized';

// ==================== 用户任务 ====================

export interface UserTask {
  topic: string;
  scope: string[];
  output_format_spec: string;
  constraints: string[];
}

// ==================== 产出层 ====================

export interface RetrievalRecord {
  id: string;
  url: string;
  title: string;
  snippet: string;
  credibility: 'high' | 'medium' | 'low';
  // M9-2/M10-P3：多源聚合后每条记录带来源插件 id（如 taobao_suggest）。
  source?: string | null;
}

export interface AnalysisConclusion {
  id: string;
  claim: string;
  source_ids: string[];
  // M10-P4：引擎/prompt 契约产出的是字符串枚举 high|medium|low（非数值）。
  // 原声明 number 与后端实现不一致，且无组件消费该字段做数值运算，故对齐为 string。
  confidence?: string | null;
}

export interface DraftSegment {
  id: string;
  section: string;
  content: string;
  conclusion_ids: string[];
}

export interface ToolStatus {
  agent: AgentRole;
  tool: string;
  // M9-2：多源聚合后带来源插件 id。
  source?: string | null;
  ok: boolean;
  error: string | null;
}

// ==================== Gate 审计 ====================

/** 本条闸记录的**依据来源**（与后端引擎的 4 个分支 1:1，不新增判定逻辑） */
export type GateReviewStatus = 'llm_reviewed' | 'code_verified' | 'degraded_unavailable';

/** eval_score 的来源：闸 LLM 自评 / 独立评分器（同一字段承载两种语义，必须可区分） */
export type EvalScoreSource = 'gate_llm' | 'independent_scorer';

export interface GateReview {
  decision: GateDecision;
  reason: string;
  /** 可空：闸降级且独立评分器也失败时为 null（后端 eval_score 已改可空） */
  eval_score: number | null;
  /** 依据来源；历史数据可能缺失（前端由 utils/reviewStatus.ts 兜底推断，不在此处判断） */
  review_status?: GateReviewStatus | null;
  /** 分数来源；历史数据无从判断时为 null */
  eval_score_source?: EvalScoreSource | null;
  problem_points: string[];
  gate: GateName;
  round: number;
  timestamp: string;
}

// ==================== 引擎事件 ====================

export interface EngineEvent {
  event: EngineEventType;
  agent: AgentRole | null;
  gate: GateName | null;
  reason: string;
  round: number;
  timestamp: string;
}

// ==================== 路由状态 ====================

export interface RoutingState {
  round: number;
  max_rounds: number;
  last_gate: GateName | null;
  status: TaskStatus;
  rework_target_agent: AgentRole | null;
  rework_reason: string | null;
  gate_review_history: GateReview[];
  engine_events: EngineEvent[];
}

// ==================== 任务完整状态 ====================

export interface TaskResponse {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  user_task: UserTask;
  routing_state: RoutingState;
  retrieval_records: RetrievalRecord[];
  analysis_conclusions: AnalysisConclusion[];
  draft_segments: DraftSegment[];
  tool_status: ToolStatus[];
  prior_versions: Record<string, unknown>;
  report_markdown: string | null;
  escalate_reason: string | null;
  // M9-5：本次任务的数据源 + 模型接口（回显）。
  plugins?: string[];
  model?: string | null;
}

// 任务历史列表项（侧边栏）
export interface TaskHistoryItem {
  task_id: string;
  topic: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
}

// ==================== API 请求/响应 ====================

export interface CreateTaskRequest {
  user_task: UserTask;
  template_id?: string;
  // M9-1：显式 Agent 子集（智能体市场「对话」入口）。非空时覆盖模板的 agents/gates。
  agents?: string[];
  // M9-5：本次任务使用的数据源 id 列表（ChatEntry「插件」多选）。非空=仅使用所选源。
  plugins?: string[];
  // M9-5：模型接口（ChatEntry「模型接口」下拉）。"auto" 或不传=沿用 model_mapping.yaml。
  model?: string;
}

export interface CreateTaskResponse {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  user_task: UserTask;
  plugins?: string[];
  model?: string | null;
}

export interface ReviewAction {
  action: 'confirm' | 'retry' | 'abort';
  reviewer_comment: string;
  rework_target_agent?: AgentRole;
}

export interface ReviewResponse {
  task_id: string;
  status: TaskStatus;
  message: string;
}

export interface ErrorResponse {
  error: string;
  message: string;
  details: Record<string, unknown>;
  timestamp: string;
}
