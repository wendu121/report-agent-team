// CustomProvider 服务：管理用户自定义 API 接入（DESIGN_chat_model_picker.md round 2）
import api from '@/api/client';

export interface CustomProvider {
  id: string;
  name: string;
  base_url: string;
  model_ids: string[];
  enabled: boolean;
  has_api_key: boolean;
}

export interface CustomProviderCreate {
  id: string;
  name?: string;
  base_url: string;
  api_key?: string;
  model_ids?: string[];
  enabled?: boolean;
}

export async function listProviders(): Promise<CustomProvider[]> {
  const { data } = await api.get<{ items: CustomProvider[] }>('/admin/providers');
  return data.items;
}

export async function addProvider(payload: CustomProviderCreate): Promise<CustomProvider> {
  const { data } = await api.post<CustomProvider>('/admin/providers', payload);
  return data;
}

export async function deleteProvider(id: string): Promise<{ ok: boolean; deleted: string }> {
  const { data } = await api.delete<{ ok: boolean; deleted: string }>(`/admin/providers/${id}`);
  return data;
}
