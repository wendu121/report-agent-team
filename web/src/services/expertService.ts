// Expert Corps 服务（M12-3）：专家团列表 / 启停 / URL 转化提案 / 审批。
// 字段名严格对齐 tools/experts.py 的 list_experts() 与 list_expert_proposals() 返回的 dict key。
import api from '@/api/client';

/** 单个专家（list_experts → read_expert 的 dict，body / skills_text 已由后端剥离）。 */
export interface ExpertItem {
  id: string;
  dir?: string;
  enabled: boolean;
  shape: string;
  model?: string;
  // plugin.json 原样透传（含 displayName / profession / tags / categoryId 等）
  plugin?: Record<string, unknown>;
  frontmatter?: Record<string, string>;
  // 包声明的专家类型：agent / team
  expert_type: 'agent' | 'team' | string;
  // team 型被降级为单专家时为 true
  degraded_team?: boolean;
  display_name?: string;
  profession?: string;
  tags?: string[];
  category_id?: string;
}

/** 待审批提案（list_expert_proposals → save_expert_proposal 落盘的 dict）。 */
export interface ProposalItem {
  id: string;
  name: string;
  source_url?: string;
  shape: string;
  expert_type: 'agent' | 'team' | string;
  allow_team_downgrade?: boolean;
  agent_body?: string;
  provenance?: {
    source_url?: string;
    source_type?: string;
    fetched_at?: string;
    sha256?: string;
  };
  model?: string;
  description?: string;
  category_id?: string;
}

/** POST /admin/experts/convert 请求体。 */
export interface ExpertConvertReq {
  url: string;
  id?: string;
  name?: string;
  shape?: string;
  allow_team_downgrade?: boolean;
}

/** POST /admin/experts/convert 返回体。 */
export interface ExpertConvertResult {
  ok: boolean;
  status: string;
  id: string;
  expert_type: string;
  proposal: ProposalItem;
}

/** GET /admin/experts 返回体。 */
export interface ExpertListResult {
  items: ExpertItem[];
  count: number;
  proposals: number;
}

/** GET /admin/experts/proposals 返回体。 */
export interface ProposalListResult {
  items: ProposalItem[];
  count: number;
}

export async function listExperts(enabledOnly = false): Promise<ExpertItem[]> {
  const { data } = await api.get<ExpertListResult>('/admin/experts', {
    params: { enabled_only: enabledOnly },
  });
  return data.items;
}

export async function toggleExpert(eid: string, enabled: boolean): Promise<void> {
  await api.post(`/admin/experts/${encodeURIComponent(eid)}/toggle`, { enabled });
}

export async function convertExpert(req: ExpertConvertReq): Promise<ExpertConvertResult> {
  const { data } = await api.post<ExpertConvertResult>('/admin/experts/convert', req);
  return data;
}

export async function listProposals(): Promise<ProposalItem[]> {
  const { data } = await api.get<ProposalListResult>('/admin/experts/proposals');
  return data.items;
}

export async function acceptProposal(pid: string): Promise<void> {
  await api.post(`/admin/experts/proposals/${encodeURIComponent(pid)}/accept`);
}

export async function rejectProposal(pid: string): Promise<void> {
  await api.delete(`/admin/experts/proposals/${encodeURIComponent(pid)}`);
}
