// WebSocket 事件服务（对齐 API_SPEC §3）
// 设计清单第 1/2 项：支持连接缓存复用（路由切换不重复建连）+ 全量兜底由 store 层触发。
import type { WSEvent } from '@/types';

type MessageListener = (event: WSEvent) => void;

// 对齐 API_SPEC §3.1：WS 端点为 /api/v1/tasks/{task_id}/stream
// vite proxy 已将 /api 转发到 M6 服务层（含 ws:true）
const WS_PATH = '/api/v1/tasks';
const connections = new Map<string, WebSocket>();
// 记录每个 taskId 当前绑定的 message handler，用于连接复用时去重（修复 F1：避免路由重入叠加监听器）
const messageHandlers = new Map<string, (ev: MessageEvent) => void>();

// 复用或新建连接（核心：连接缓存，避免路由切换重复建连）
export function getOrCreateWsConnection(taskId: string, debugMode = false): WebSocket {
  const existing = connections.get(taskId);
  if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
    return existing;
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}${WS_PATH}/${encodeURIComponent(taskId)}/stream?debug_mode=${debugMode ? 1 : 0}`;
  const ws = new WebSocket(url);
  connections.set(taskId, ws);
  ws.addEventListener('close', () => {
    // 仅当仍为同一实例时从缓存移除（避免误删重连后的新实例）
    if (connections.get(taskId) === ws) {
      connections.delete(taskId);
    }
    // 连接真正关闭时清理对应 message handler 引用，防止悬挂监听
    messageHandlers.delete(taskId);
  });
  return ws;
}

// 订阅消息。返回该连接（可被组件持有），不会重复建连。
// 同一 taskId 的 message handler 仅保留最新一个：复用时先移除旧 handler 再绑定，
// 彻底消除「路由重入 → 监听器叠加 → 单条 WS 事件被 handleEvent 处理 N 次 → 时间线节点重复」的反模式。
export function connect(taskId: string, onMessage: MessageListener, debugMode = false): WebSocket {
  const ws = getOrCreateWsConnection(taskId, debugMode);
  const prev = messageHandlers.get(taskId);
  if (prev) {
    ws.removeEventListener('message', prev);
  }
  const handler = (ev: MessageEvent) => {
    try {
      const parsed = JSON.parse(ev.data as string) as WSEvent;
      onMessage(parsed);
    } catch (err) {
      console.error('[wsService] 消息解析失败', err);
    }
  };
  messageHandlers.set(taskId, handler);
  ws.addEventListener('message', handler);
  return ws;
}

export function closeWs(taskId: string): void {
  const ws = connections.get(taskId);
  if (ws) {
    ws.close();
    connections.delete(taskId);
    messageHandlers.delete(taskId);
  }
}
