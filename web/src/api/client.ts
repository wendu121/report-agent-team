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
export const FORBIDDEN_EVENT = 'rat:forbidden';

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}
export function setToken(t: string): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

// 原生 fetch 包装：自动注入 Bearer（与上方 axios 请求拦截器行为一致）。
// 控制台页面用原生 fetch 调 /admin/* 时必须走它，否则请求不带 token →
// 后端 _require_admin 判 401 → 前端响应拦截器清 token 并广播 rat:unauthorized → 级联登出。
// 给公开端点带 Bearer 无害（后端忽略多余头），故控制台页面一律用本函数即可。
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const t = getToken();
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (t) headers.set('Authorization', `Bearer ${t}`);
  return fetch(input, { ...init, headers });
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
    // FastAPI HTTPException 实际返回 {detail: "..."}，与前端 ErrorResponse 类型不一致，
    // 故读取原始响应体取提示文案（detail 优先，其次 message）。
    const raw = (error.response?.data ?? {}) as Record<string, any>;
    if (status === 401) {
      // 鉴权失败（token 失效/缺失）：清本地 token 并广播，由 main.ts 跳登录。
      // 注意：admin 端点的 401 已由后端 _require_admin 修复（Bearer 注入 ContextVar），
      // 此处仅在「确实未登录 / token 过期」时触发，不再误伤正常会话。
      setToken('');
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    } else if (status === 403) {
      // 权限不足（如子账号访问仅主账号可见的【账号管理】）：保留会话，仅提示无权限。
      const msg = typeof raw.detail === 'string' ? raw.detail
        : typeof raw.message === 'string' ? raw.message : '';
      window.dispatchEvent(new CustomEvent(FORBIDDEN_EVENT, { detail: msg }));
    }
    return Promise.reject(new ApiError(status, data));
  }
);

export default api;
