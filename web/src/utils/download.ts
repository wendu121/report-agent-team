// 审计包 / 研报下载（对齐 API_SPEC §5 + DESIGN_OUTPUT_RENDERING.md v1.0）
// 设计清单「下载异常处理」：Blob 类型/大小检查，识别后端错误页包装
import { auditExportUrl, reportDownloadUrl } from '@/services/taskService';
import { getToken } from '@/api/client';

// 裸 fetch 不经过 axios 拦截器 → 必须自己带 Bearer token，
// 否则鉴权开启后下载一律 401（旧实现漏了这一步）。
function authHeaders(): HeadersInit {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function saveBlob(blob: Blob, filename: string): void {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

/** 把后端的 ErrorResponse（{detail:{error,message}}）翻成可读文案。 */
async function readErrorMessage(resp: Response, fallback: string): Promise<string> {
  try {
    const j = await resp.json();
    const d = j?.detail ?? j;
    if (d?.message) return `${d.error ?? 'ERROR'}：${d.message}`;
  } catch {
    /* 非 JSON 体：保留原始 HTTP 文案 */
  }
  return fallback;
}

export async function downloadAuditPackage(taskId: string): Promise<void> {
  const resp = await fetch(auditExportUrl(taskId), { headers: authHeaders() });
  if (!resp.ok) {
    throw new Error(await readErrorMessage(resp, `审计包下载失败：HTTP ${resp.status}`));
  }
  const blob = await resp.blob();
  const contentType = blob.type || '';

  // 异常检测：正常应为 application/zip；若后端返回错误 JSON（如 404 页/异常），
  // content-type 不含 zip 且体积很小，尝试读取文本给出可读错误。
  if (!contentType.includes('zip') && blob.size < 4096) {
    const text = await blob.text();
    throw new Error(`审计包下载异常（非 ZIP）：${text.slice(0, 200)}`);
  }

  saveBlob(blob, `audit_${taskId}.zip`);
}

// 研报四格式（md 为原样 Markdown；docx/pptx/pdf 由后端按需渲染）
const REPORT_FORMATS = ['md', 'docx', 'pptx', 'pdf'] as const;
export type ReportFormat = (typeof REPORT_FORMATS)[number];
export const REPORT_FORMAT_OPTIONS: { value: ReportFormat; label: string }[] = [
  { value: 'md', label: 'MD' },
  { value: 'docx', label: 'DOCX' },
  { value: 'pptx', label: 'PPTX' },
  { value: 'pdf', label: 'PDF' },
];

export async function downloadReport(taskId: string, format: ReportFormat): Promise<void> {
  if (!REPORT_FORMATS.includes(format)) {
    throw new Error(`不支持的格式：${format}`);
  }
  const resp = await fetch(reportDownloadUrl(taskId, format), { headers: authHeaders() });
  if (!resp.ok) {
    // 503 RENDERER_UNAVAILABLE / 409 未完成 / 400 格式非法 都会走这里，如实透传后端文案
    throw new Error(await readErrorMessage(resp, `研报下载失败：HTTP ${resp.status}`));
  }
  const blob = await resp.blob();
  // 后端若把错误包成小体积响应，读文本给出可读错误（与审计包同策略）
  if (blob.size < 256) {
    const text = await blob.text();
    throw new Error(`研报下载异常：${text.slice(0, 200)}`);
  }
  saveBlob(blob, `report_${taskId}.${format}`);
}
