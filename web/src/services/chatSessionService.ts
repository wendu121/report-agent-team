// 聊天会话服务（聊天记录 + 跨会话记忆）
// 对应后端 server/api.py 的 /chat/sessions/* 端点（已端到端验证）
import api from '@/api/client';

export interface ChatSessionItem {
  id: string;
  topic: string | null;
  model: string;
  agents: string[];
  plugins: string[];
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessageItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  task_id?: string | null;
  task_status?: string | null;
  created_at: string;
}

export interface SessionMemoryItem {
  id: string;
  key: string;
  value: string;
  created_at: string;
}

export interface GetSessionResult {
  session: ChatSessionItem;
  messages: ChatMessageItem[];
}

export async function listSessions(): Promise<ChatSessionItem[]> {
  const { data } = await api.get<{ items: ChatSessionItem[] }>('/chat/sessions');
  return data.items ?? [];
}

export async function createSession(payload: {
  topic?: string | null;
  model?: string;
  agents?: string[];
  plugins?: string[];
}): Promise<ChatSessionItem> {
  const { data } = await api.post<ChatSessionItem>('/chat/sessions', payload);
  return data;
}

export async function getSession(id: string): Promise<GetSessionResult> {
  const { data } = await api.get<GetSessionResult>(`/chat/sessions/${id}`);
  return data;
}

export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/chat/sessions/${id}`);
}

export async function renameSession(id: string, topic: string): Promise<ChatSessionItem> {
  const { data } = await api.put<ChatSessionItem>(`/chat/sessions/${id}`, { topic });
  return data;
}

/** 清空当前账号全部会话（含消息与跨会话记忆，由后端逐表删）。返回删除数量。 */
export async function clearSessions(): Promise<{ ok: boolean; deleted: number }> {
  const { data } = await api.delete<{ ok: boolean; deleted: number }>('/chat/sessions');
  return data;
}

/**
 * 从某条消息开始截断会话历史（删除该条及其之后的全部消息）。
 *
 * 用于「编辑重发」：**必须先删后发**。只改页面不删库 = 假修正 ——
 * 刷新会「复活」错字，且后续每轮对话模型仍从 DB 读到那条错的历史。
 * 设计依据：DESIGN_chat_interrupt_edit.md §3.2。
 */
export async function truncateFrom(sessionId: string, fromMessageId: string): Promise<number> {
  const { data } = await api.post<{ ok: boolean; deleted: number }>(
    `/chat/sessions/${sessionId}/truncate`,
    { from_message_id: fromMessageId },
  );
  return data.deleted ?? 0;
}

export async function getMemory(id: string): Promise<SessionMemoryItem[]> {
  const { data } = await api.get<{ items: SessionMemoryItem[] }>(`/chat/sessions/${id}/memory`);
  return data.items ?? [];
}

export async function setMemory(id: string, key: string, value: string): Promise<void> {
  await api.post(`/chat/sessions/${id}/memory`, { key, value });
}
