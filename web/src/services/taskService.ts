// 任务 REST 服务（对齐 API_SPEC §2）
import api from '@/api/client';
import type {
  CreateTaskRequest,
  CreateTaskResponse,
  TaskResponse,
  TaskHistoryItem,
  ReviewAction,
  ReviewResponse,
} from '@/types';

export async function createTask(req: CreateTaskRequest): Promise<CreateTaskResponse> {
  const { data } = await api.post<CreateTaskResponse>('/tasks', req);
  return data;
}

export async function getTask(taskId: string): Promise<TaskResponse> {
  const { data } = await api.get<TaskResponse>(`/tasks/${encodeURIComponent(taskId)}`);
  return data;
}

export async function listTasks(): Promise<TaskHistoryItem[]> {
  const { data } = await api.get<TaskHistoryItem[]>('/tasks');
  return data;
}

export async function submitReview(taskId: string, action: ReviewAction): Promise<ReviewResponse> {
  const { data } = await api.post<ReviewResponse>(`/tasks/${encodeURIComponent(taskId)}/review`, action);
  return data;
}

/** 删除单个任务（运行/返工中后端返回 409，由调用方提示）。 */
export async function deleteTask(taskId: string): Promise<{ ok: boolean; task_id: string }> {
  const { data } = await api.delete<{ ok: boolean; task_id: string }>(
    `/tasks/${encodeURIComponent(taskId)}`,
  );
  return data;
}

/** 清空当前账号全部「已结束」任务，运行中/返工中保留（skipped_running 计数）。 */
export async function clearTasks(): Promise<{ ok: boolean; deleted: number; skipped_running: number }> {
  const { data } = await api.delete<{ ok: boolean; deleted: number; skipped_running: number }>('/tasks');
  return data;
}

// 审计包下载 URL（前端直接 fetch/blob 下载，见 utils/download.ts）
export function auditExportUrl(taskId: string): string {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}/audit-export`;
}

// 研报下载 URL（DESIGN_OUTPUT_RENDERING.md v1.0）
// format = md | docx | pptx | pdf；后端按格式渲染（md 为原样，其余按需派生）
export function reportDownloadUrl(taskId: string, format: string): string {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}/report?format=${encodeURIComponent(format)}`;
}
