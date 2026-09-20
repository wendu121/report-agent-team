// Skill 提案审批服务（对齐 expertService.ts，但走 /api/v1 前缀，与 Skills.vue 现有 API 常量一致）。
// 后端审批路由已由 server/admin.py 提供：list / accept / reject。
import { authFetch } from '@/api/client';

/** 待审批 skill 提案（server/admin.py list_skill_proposals 返回的 dict）。 */
export interface SkillProposalItem {
  id: string;
  name: string;
  level: string;
  description?: string;
  target_roles?: string[];
  source_url?: string;
  provenance?: any;
}

/** GET /api/v1/admin/skills/proposals 返回体。 */
export interface SkillProposalListResult {
  items: SkillProposalItem[];
  count: number;
}

export async function listSkillProposals(): Promise<SkillProposalItem[]> {
  const res = await authFetch('/api/v1/admin/skills/proposals');
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `加载提案失败：${res.status}`);
  }
  const data = (await res.json()) as SkillProposalListResult;
  return data.items ?? [];
}

export async function acceptSkillProposal(pid: string): Promise<void> {
  const res = await authFetch(`/api/v1/admin/skills/proposals/${encodeURIComponent(pid)}/accept`, {
    method: 'POST',
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `采纳失败：${res.status}`);
  }
}

export async function rejectSkillProposal(pid: string): Promise<void> {
  const res = await authFetch(`/api/v1/admin/skills/proposals/${encodeURIComponent(pid)}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    throw new Error((d && d.message) || d || `驳回失败：${res.status}`);
  }
}
