// axios 客户端封装
// 设计纪律（M7 审议清单第 9 项）：拦截器只负责构造并抛出 ApiError，
// 不直接调用任何 pinia store；状态更新由组件/store action 层捕获后处理。
import axios, { type AxiosInstance, type AxiosError } from 'axios';
import { ApiError } from './errors';
import type { ErrorResponse } from '@/types';

const api: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  // ChatAgent 是 agentic 多轮工具循环（LLM→工具→再 LLM），单次 /chat 可能 1~数分钟。
  // 30s 会触发浏览器主动断开（nginx 记 499 → UI 弹“网络错误 HTTP 0”）。
  // 提到 10 分钟为安全上限；后端 ChatAgent 专用 LLM 客户端已限 timeout=90s/重试1次，
  // new-api 真卡顿时后端会在 ~3 分钟内返回 500 而非让前端干等。
  timeout: 600000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ---- 账号鉴权 token（Phase 1 · DESIGN_account_hierarchy.md）----
// 纪律约束（M7 审议第 9 项）：拦截器不直接 import pinia store。
// 因此 token 从 localStorage 直读，401 通过 window 事件广播，由 store/路由层响应。
const TOKEN_KEY = 'rat_token';
export const UNAUTHORIZED_EVENT = 'rat:unauthorized';

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function setToken(t: string): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

// 请求拦截器：注入 Bearer token
api.interceptors.request.use((config) => {
  const t = getToken();
  if (t) {
    config.headers = config.headers || {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${t}`;
  }
  return config;
});

// 响应拦截器：仅将后端错误规范化为 ApiError 并 reject
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? 0;
    const data = (error.response?.data ?? {}) as Partial<ErrorResponse>;
    // 令牌失效：清本地 token 并广播，由上层跳登录（此处不碰 store/router，守纪律）
    if (status === 401) {
      setToken('');
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    }
    return Promise.reject(new ApiError(status, data));
  }
);

export default api;
