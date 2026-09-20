// 聊天会话 store（左侧「聊天记录」栏共享：DefaultLayout 渲染 + ChatEntry 提交/切换后刷新）
import { defineStore } from 'pinia';
import { ref } from 'vue';
import {
  listSessions,
  createSession,
  deleteSession,
  renameSession,
  clearSessions,
  type ChatSessionItem,
} from '@/services/chatSessionService';

export const useChatSessionStore = defineStore('chat-session', () => {
  const sessions = ref<ChatSessionItem[]>([]);
  const loading = ref(false);
  const loaded = ref(false);

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      sessions.value = await listSessions();
      loaded.value = true;
    } catch (e) {
      console.error('刷新聊天会话失败', e);
    } finally {
      loading.value = false;
    }
  }

  async function create(payload?: {
    topic?: string | null;
    model?: string;
    agents?: string[];
    plugins?: string[];
  }): Promise<ChatSessionItem> {
    const s = await createSession(payload ?? {});
    // 新建的会话置顶
    sessions.value = [s, ...sessions.value.filter((x) => x.id !== s.id)];
    return s;
  }

  async function remove(id: string): Promise<void> {
    await deleteSession(id);
    sessions.value = sessions.value.filter((x) => x.id !== id);
  }

  /** 清空全部会话：调 bulk 端点，本地置空列表。返回删除数量供 UI 提示。 */
  async function clearAll(): Promise<{ ok: boolean; deleted: number }> {
    const res = await clearSessions();
    sessions.value = [];
    return res;
  }

  async function rename(id: string, topic: string): Promise<void> {
    const s = await renameSession(id, topic);
    const idx = sessions.value.findIndex((x) => x.id === id);
    if (idx >= 0) sessions.value[idx] = s;
  }

  return { sessions, loading, loaded, refresh, create, remove, rename, clearAll };
});
