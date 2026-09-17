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
}

export async function chat(req: ChatRequest): Promise<ChatResponse> {
  // 显式长超时：ChatAgent 是 agentic 多轮循环，单次对话可能 1~数分钟，
  // 不能用实例默认超时（避免浏览器 499 断连）。
  const { data } = await api.post<ChatResponse>('/chat', req, { timeout: 600000 });
  return data;
}
