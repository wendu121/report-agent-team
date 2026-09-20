// ChatAgent 对话服务（阶段 1：对话优先入口）
import api from '@/api/client';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatRequest {
  message: string;
  history: ChatMessage[];
  model?: string;
  plugins?: string[];
  session_id?: string;
}

export interface ChatResponse {
  reply: string;
  intent: 'chat' | 'generate_report';
  task_id?: string | null;
  task_status?: string | null;
  session_id?: string | null;
  tool_calls?: string[];
  sources?: any[];
  /** 诚实降级告警：本次未参与检索的数据源 / 被剔除的占位数据（含修复指引） */
  source_warnings?: string[];
  /** sources 是否为纯真实来源；false 时不得把 sources 当引用展示 */
  sources_are_real?: boolean;
  /** 用户中途「停止」→ true。此时 reply 为空，且本轮**未写入历史**。 */
  cancelled?: boolean;
  /** 本轮落库生成的消息 id（用于「编辑历史消息」的截断定位）；持久化降级时为 null */
  user_message_id?: string | null;
  assistant_message_id?: string | null;
}

/**
 * 发一条对话。
 *
 * signal：用户点「停止」时 abort 它。**必须真的 abort** —— 服务端靠探测断连来
 * 中止工具循环并放弃落库（见 DESIGN_chat_interrupt_edit.md §3.1）；
 * 只关掉前端的 loading 是假按钮：模型照样跑完、消息照样进历史。
 */
export async function chat(req: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  // 显式长超时：ChatAgent 是 agentic 多轮循环，单次对话可能 1~数分钟，
  // 不能用实例默认超时（避免浏览器 499 断连）。
  const { data } = await api.post<ChatResponse>('/chat', req, { timeout: 600000, signal });
  return data;
}
