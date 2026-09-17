// 审计包下载（对齐 API_SPEC §5）
// 设计清单「审计包下载异常处理」：Blob 类型/大小检查，识别后端错误页包装
import { auditExportUrl } from '@/services/taskService';

export async function downloadAuditPackage(taskId: string): Promise<void> {
  const resp = await fetch(auditExportUrl(taskId));
  if (!resp.ok) {
    throw new Error(`审计包下载失败：HTTP ${resp.status}`);
  }
  const blob = await resp.blob();
  const contentType = blob.type || '';

  // 异常检测：正常应为 application/zip；若后端返回错误 JSON（如 404 页/异常），
  // content-type 不含 zip 且体积很小，尝试读取文本给出可读错误。
  if (!contentType.includes('zip') && blob.size < 4096) {
    const text = await blob.text();
    throw new Error(`审计包下载异常（非 ZIP）：${text.slice(0, 200)}`);
  }

  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `audit_${taskId}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}
