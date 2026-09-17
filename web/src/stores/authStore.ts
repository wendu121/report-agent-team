// 账号状态（Phase 1 · DESIGN_account_hierarchy.md）
// token 与账号信息持久化，刷新后仍保持登录。
import { defineStore } from 'pinia';
import http from '@/api/client';
import { setToken, getToken } from '@/api/client';

export interface AccountInfo {
  id: string;
  username: string;
  role: 'main' | 'sub';
  status: string;
  parent_id?: string | null;
  created_at?: string | null;
}

interface AuthState {
  token: string;
  me: AccountInfo | null;
  loading: boolean;
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    token: getToken(),
    me: null,
    loading: false,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isMain: (s) => s.me?.role === 'main',
  },
  actions: {
    async login(username: string, password: string): Promise<void> {
      const { data } = await http.post('/auth/login', { username, password });
      this.token = data.access_token;
      this.me = {
        id: data.account_id,
        username: data.username,
        role: data.role,
        status: 'active',
      };
      setToken(this.token);
    },
    async register(username: string, password: string): Promise<AccountInfo> {
      const { data } = await http.post('/auth/register', { username, password });
      return data;
    },
    async fetchMe(): Promise<void> {
      if (!this.token) return;
      this.loading = true;
      try {
        const { data } = await http.get('/auth/me');
        this.me = data;
      } finally {
        this.loading = false;
      }
    },
    logout(): void {
      this.token = '';
      this.me = null;
      setToken('');
    },
  },
  persist: true,
});
