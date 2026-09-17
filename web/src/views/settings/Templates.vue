<template>
  <div class="page settings-page">
    <PageHead
      icon="Files"
      title="任务模板"
      sub="预置 Researcher / Analyst / Writer 与三道闸的组合，供一键发起研报任务。模板驱动引擎 build_graph 编排。"
    >
      <template #actions>
        <el-button type="primary" :icon="Plus" @click="goNew">新增模板</el-button>
      </template>
    </PageHead>
    <StatStrip :stats="stats" />

    <el-alert v-if="error" type="error" :closable="false" :title="error" class="blk" />
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">模板列表</span>
          <span class="blk-sub">编辑 / 删除即时生效（至少保留 1 个）</span>
        </div>
      </template>
      <el-table :data="items" v-loading="loading" empty-text="暂无模板" row-key="id">
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column label="Agents" min-width="160">
          <template #default="{ row }">{{ (row.agents || []).join(' / ') }}</template>
        </el-table-column>
        <el-table-column label="Gates" min-width="140">
          <template #default="{ row }">{{ (row.gates || []).join(' / ') }}</template>
        </el-table-column>
        <el-table-column label="输出格式" min-width="100">
          <template #default="{ row }">{{ (row.output_format || []).join(' / ') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="goEdit(row.id)">编辑</el-button>
            <el-button size="small" type="danger" @click="onDelete(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Plus } from '@element-plus/icons-vue';
import type { TemplateInfo } from '@/types';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

const API = '/api/v1/admin';
const router = useRouter();
const items = ref<TemplateInfo[]>([]);
const loading = ref(false);
const error = ref('');

const stats = computed(() => [
  { label: '模板总数', value: items.value.length, tone: 'brand' as const, hint: '当前已注册' },
  { label: '固定 Agents', value: 3, tone: 'muted' as const, hint: 'Researcher / Analyst / Writer' },
  { label: '固定 Gates', value: 3, tone: 'muted' as const, hint: 'GateA / GateB / GateC' },
]);

async function load(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${API}/templates`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const data = await res.json();
    items.value = (data.items ?? []) as TemplateInfo[];
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function goNew(): void {
  router.push('/settings/templates/new');
}
function goEdit(id: string): void {
  router.push(`/settings/templates/${id}`);
}
async function onDelete(id: string): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除模板「${id}」？至少保留 1 个`, '删除确认', { type: 'warning' });
  } catch {
    return;
  }
  const res = await fetch(`${API}/templates/${id}`, { method: 'DELETE' });
  if (res.ok) {
    ElMessage.success('已删除');
    await load();
  } else {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    ElMessage.error(d.message || '删除失败');
  }
}

onMounted(load);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.settings-page :deep(.el-table) {
  --el-table-border-color: var(--line);
  --el-table-header-bg-color: var(--surface-2);
}
</style>
