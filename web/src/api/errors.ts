// 统一 API 错误类型（对齐 API_SPEC §6 ErrorResponse）
import type { ErrorResponse } from '@/types';

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, body: Partial<ErrorResponse> & { detail?: string }) {
    // FastAPI HTTPException 实际返回 {detail: "..."}，与 ErrorResponse 的 message 字段不一致，
    // 故 detail 优先，其次 message / error，保证服务端提示文案能透传到 UI 的 e.message。
    super(body.detail || body.message || body.error || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.error || 'unknown_error';
    this.details = body.details ?? {};
  }
}
