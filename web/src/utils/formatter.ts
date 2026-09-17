// 格式化工具
import type { TaskStatus } from '@/types';

export function formatTime(iso: string): string {
  const d = new Date(iso);
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
