// 审核闸「是否真审过」的归一化（M13-gate-degradation）
//
// 背景：审核闸在审核模型返回不可解析 JSON 时会**降级放行**（后端既定设计），
// 但界面上只看得到「放行 + 评分 0.85」——用户会读成"AI 审核通过"。
// 实测（子账号租户）三道闸全部降级，报告仍标 done、仍带分数。
//
// 后端已把「依据来源」结构化：`gate_review_history[*].review_status`。
// 历史任务的数据里没有该字段，故在此按 `reason` 文本兜底推断。
// **这是唯一一处兜底逻辑**，时间线与结果页共用，不重复实现。

import type { GateReview, GateReviewStatus } from '@/types';

/** 降级文案指纹（与后端 orchestrator.py 的降级 reason 一致） */
export const DEGRADED_REASON_MARKS = ['降级放行', '审核 LLM 不可用'];

/**
 * 归一化闸记录的「依据来源」。
 *
 * - 后端给了 `review_status` → 直接采用（新数据路径）；
 * - 缺失（历史数据）→ 按 `reason` 文本兜底：含降级指纹即判为降级放行，
 *   否则视为「已由审核模型评审」（与改动前行为一致，不给历史数据制造噪音）。
 */
export function normalizeReviewStatus(
  r: Pick<GateReview, 'review_status'> & { reason?: string | null },
): GateReviewStatus {
  if (r.review_status) return r.review_status;
  const reason = r.reason ?? '';
  return DEGRADED_REASON_MARKS.some((mk) => reason.includes(mk))
    ? 'degraded_unavailable'
    : 'llm_reviewed';
}

/** 该闸是否「未真正执行」（审核模型不可用 → 降级放行） */
export function isDegraded(
  r: Pick<GateReview, 'review_status'> & { reason?: string | null },
): boolean {
  return normalizeReviewStatus(r) === 'degraded_unavailable';
}

/** 依据来源 → 界面文案 */
export const REVIEW_STATUS_LABEL: Record<GateReviewStatus, string> = {
  llm_reviewed: '已由审核模型评审',
  code_verified: '代码硬校验裁决',
  degraded_unavailable: '未经 AI 审核（降级放行）',
};

/** 依据来源 → Element Plus tag 类型 */
export const REVIEW_STATUS_TAG: Record<GateReviewStatus, 'success' | 'info' | 'danger'> = {
  llm_reviewed: 'success',
  code_verified: 'info',
  degraded_unavailable: 'danger',
};

/** 依据来源 → 时间线条目类型（降级须显示为警示而非成功） */
export const REVIEW_STATUS_TIMELINE: Record<GateReviewStatus, 'success' | 'info' | 'warning'> = {
  llm_reviewed: 'success',
  code_verified: 'info',
  degraded_unavailable: 'warning',
};
