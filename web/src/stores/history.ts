// 侧边栏历史任务 store（共享：DefaultLayout 渲染 / ChatEntry 提交后刷新）
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { listTasks } from '@/services/taskService';
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

  return { items, loading, refresh };
});