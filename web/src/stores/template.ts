// 模板 store（M8-4：从后端 /api/v1/templates 动态拉取，解除前端硬编码死数组）
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { TemplateInfo } from '@/types';
import { authFetch } from '@/api/client';

const API_BASE = '/api/v1';

export const useTemplateStore = defineStore('template', () => {
  const templates = ref<TemplateInfo[]>([]);
  const selectedId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchTemplates(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      // /templates 已纳入账号作用域（子账号隔离里程碑加了 _tenant_required），
      // 必须走 authFetch 注入 Bearer，否则 401 → 提交页模板下拉对所有账号加载失败。
      const res = await authFetch(`${API_BASE}/templates`);
      if (!res.ok) throw new Error(`加载模板失败：${res.status}`);
      const data = await res.json();
      templates.value = (data.items ?? []) as TemplateInfo[];
      // 若当前选中不存在，默认选中第一个（保证提交页有有效模板）
      if (
        templates.value.length &&
        (!selectedId.value ||
          !templates.value.some((t) => t.id === selectedId.value))
      ) {
        selectedId.value = templates.value[0].id;
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载模板失败';
    } finally {
      loading.value = false;
    }
  }

  function select(id: string): void {
    selectedId.value = id;
  }

  const selected = computed<TemplateInfo | null>(
    () => templates.value.find((t) => t.id === selectedId.value) ?? null
  );

  return { templates, selectedId, selected, loading, error, fetchTemplates, select };
});
