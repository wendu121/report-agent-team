// 侧边栏历史任务 store（共享：DefaultLayout 渲染 / ChatEntry 提交后刷新）
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { listTasks, deleteTask, clearTasks } from '@/services/taskService';
import type { TaskHistoryItem } from '@/types';

export const useHistoryStore = defineStore('sidebar-history', () => {
  const items = ref<TaskHistoryItem[]>([]);
  const loading = ref(false);

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      items.value = await listTasks();
    } catch (e) {
      console.error('刷新任务历史失败', e);
    } finally {
      loading.value = false;
    }
  }

  /** 删除单个任务：调端点后从本地列表移除。运行/返工中由后端 409 拦截（不进此方法）。 */
  async function remove(id: string): Promise<{ ok: boolean; task_id: string }> {
    const res = await deleteTask(id);
    items.value = items.value.filter((x) => x.task_id !== id);
    return res;
  }

  /** 清空全部已结束任务：运行/返工中不被删，过滤后保留。返回计数供 UI 提示。 */
  async function clearAll(): Promise<{ ok: boolean; deleted: number; skipped_running: number }> {
    const res = await clearTasks();
    items.value = items.value.filter((x) => x.status === 'running' || x.status === 'rework');
    return res;
  }

  return { items, loading, refresh, remove, clearAll };
});