// McpServer 服务：管理 MCP 消费桥接入的 server（M11-3，镜像 customProviderService）
import api from '@/api/client';

export interface McpServer {
  id: string;
  name: string;
  transport: string;
  url: string;
  enabled: boolean;
  has_headers: boolean;
}

export interface McpServerCreate {
  id: string;
  name?: string;
  transport?: string;
  url: string;
  headers?: Record<string, string>;
  enabled?: boolean;
  tools_whitelist?: string[];
}

export async function listMcpServers(): Promise<McpServer[]> {
  const { data } = await api.get<{ items: McpServer[] }>('/admin/mcp-servers');
  return data.items;
}

export async function addMcpServer(payload: McpServerCreate): Promise<McpServer> {
  const { data } = await api.post<McpServer>('/admin/mcp-servers', payload);
  return data;
}

export async function deleteMcpServer(id: string): Promise<{ items: McpServer[] }> {
  const { data } = await api.delete<{ items: McpServer[] }>(`/admin/mcp-servers/${id}`);
  return data;
}
