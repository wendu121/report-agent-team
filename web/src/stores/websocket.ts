// WebSocket 连接状态 store（UI 反射层）
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { getOrCreateWsConnection, closeWs } from '@/services/wsService';

export const useWsStore = defineStore('websocket', () => {
  const connected = ref<Record<string, boolean>>({});

  function markConnected(taskId: string, state: boolean): void {
    connected.value = { ...connected.value, [taskId]: state };
  }

  function ensure(taskId: string): WebSocket {
    const ws = getOrCreateWsConnection(taskId);
    markConnected(taskId, ws.readyState === WebSocket.OPEN);
    return ws;
  }

  function disconnect(taskId: string): void {
    closeWs(taskId);
    markConnected(taskId, false);
  }

  return { connected, markConnected, ensure, disconnect };
});
