// 统一审核中心服务（对齐 skillService.ts 风格，走 /api/v1 前缀）。
// 后端聚合端点：GET /api/v1/admin/review/queue
// 待推送变更集决策：POST /api/v1/admin/review/queue/{id}/approve | /reject | /mark-pushed
import { authFetch } from '@/api/client';

export type ReviewItemKind = 'skill_proposal' | 'expert_proposal' | 'pending_push';
export type ReviewDecision = 'pending' | 'approved' | 'rejected' | 'pushered' | null;

/** 审核中心条目（三类合一；pending_push 带 commits/gate/decision）。 */
export interface ReviewQueueItem {
  kind: ReviewItemKind;
  id: string;
  title?: string;
  name?: string;
  level?: string;
  status?: string;
  decision?: ReviewDecision;
  detail?: any;
  commits?: string[];
  commit_subjects?: string[];
  gate?: { verification?: string | null; review?: string | null; status?: string };
  approved_by?: string | null;
  approved_at?: string | null;
  pushed_ref?: string | null;
  reject_reason?: string | null;
  gate_docs?: string[];
  /** 前端临时加载态标记（非后端字段）。 */
  _busy?: boolean;
}

export interface ReviewQueueResult {
  ok: boolean;
  items: ReviewQueueItem[];
  counts: Record<string, number>;
}

export async function listReviewQueue(): Promise<ReviewQueueItem[]> {
  const res = await authFetch('/api/v1/admin/review/queue');
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `加载审核队列失败：${res.status}`);
  }
  const data = (await res.json()) as ReviewQueueResult;
  return data.items ?? [];
}

export async function approvePendingPush(id: string): Promise<void> {
  const res = await authFetch(`/api/v1/admin/review/queue/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `批准推送失败：${res.status}`);
  }
}

export async function rejectPendingPush(id: string, reason: string): Promise<void> {
  const res = await authFetch(`/api/v1/admin/review/queue/${encodeURIComponent(id)}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `打回失败：${res.status}`);
  }
}
