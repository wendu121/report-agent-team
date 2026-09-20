// 格式化工具
import type { TaskStatus } from '@/types';

// 后端存 naive UTC（isoformat 无时区后缀，如 2026-09-08T05:12:33.123456），
// 若直接 `new Date(无Z串)` 会按浏览器**本地时区**解析 → 显示慢 8 小时。
// 规则：串尾无时区标记（无 Z、无 ±HH:MM）→ 视为 UTC，补 Z 再解析。
const TZ_SUFFIX_RE = /(?:Z$|[+-]\d{2}:?\d{2}$)/;

export function parseDbTime(iso: string): Date {
  if (TZ_SUFFIX_RE.test(iso)) return new Date(iso);
  return new Date(`${iso}Z`);
}

export function formatTime(iso: string): string {
  const d = parseDbTime(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { hour12: false });
}

const STATUS_LABEL: Record<TaskStatus, string> = {
  created: '已创建',
  running: '运行中',
  rework: '返工中',
  done: '已完成',
  escalated: '待人工复核',
  aborted: '已中止',
};

export function statusLabel(s: TaskStatus): string {
  return STATUS_LABEL[s] ?? s;
}

// 状态 → Element Plus tag type
export function statusTagType(s: TaskStatus): 'success' | 'info' | 'warning' | 'danger' | 'primary' {
  switch (s) {
    case 'done':
      return 'success';
    case 'running':
    case 'created':
      return 'primary';
    case 'rework':
      return 'warning';
    case 'escalated':
      return 'danger';
    case 'aborted':
      return 'info';
    default:
      return 'info';
  }
}
