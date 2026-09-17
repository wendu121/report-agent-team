// 统一 API 错误类型（对齐 API_SPEC §6 ErrorResponse）
import type { ErrorResponse } from '@/types';

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, body: Partial<ErrorResponse>) {
    super(body.message || body.error || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.error || 'unknown_error';
    this.details = body.details ?? {};
  }
}
