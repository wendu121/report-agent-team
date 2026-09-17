<template>
  <div class="page settings-page">
    <PageHead
      icon="Connection"
      title="自定义 API"
      sub="接入任意 OpenAI 兼容端点（SiliconFlow、DeepSeek、自部署 vLLM 等）。密钥落 .secrets，新增/删除立即生效，无需重启。"
    />
    <StatStrip :stats="stats" />

    <el-alert v-if="formError" type="error" :closable="false" class="blk">{{ formError }}</el-alert>

    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">已有自定义 Provider</span>
          <el-button type="primary" size="small" @click="openDialog">＋ 新增</el-button>
        </div>
      </template>
      <el-table :data="items" size="default" row-key="id">
        <el-table-column prop="name" label="显示名" width="140" />
        <el-table-column prop="id" label="ID" width="140" />
        <el-table-column prop="base_url" label="Base URL" />
        <el-table-column label="密钥" width="80">
          <template #default="{ row }">
            <el-tag :type="row.has_api_key ? 'success' : 'warning'" size="small">
              {{ row.has_api_key ? '已配置' : '缺' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="模型数" width="70">
          <template #default="{ row }">{{ row.model_count }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="onDelete(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="loading" class="sc-empty">加载中…</div>
      <div v-else-if="items.length === 0" class="sc-empty">暂无自定义 provider，点击上方「新增」添加。</div>
    </el-card>

    <!-- 新增弹窗 -->
    <el-dialog
      v-model="dialog.show"
      title="添加自定义 API"
      width="540"
      :close-on-click-modal="false"
    >
      <el-form :model="dialog.form" label-width="110px" size="default">
        <el-form-item label="ID" required>
          <el-input v-model="dialog.form.id" placeholder="例如 siliconflow / my-deepseek / vllm-prod" />
          <div class="form-hint">仅字母数字 / - _ .，64 字以内</div>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="dialog.form.name" placeholder="留空 = 用 ID" />
        </el-form-item>
        <el-form-item label="Base URL" required>
          <el-input v-model="dialog.form.base_url" placeholder="https://api.siliconflow.cn/v1" />
          <div class="form-hint">OpenAI 兼容根；省略 <code>/v1</code> 时系统自动补全（无需手填）</div>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="dialog.form.api_key" type="password" show-password placeholder="sk-…" />
          <div class="form-hint">可选；保存到 .secrets/custom_providers.json（600 权限）</div>
        </el-form-item>
        <el-form-item label="模型 IDs">
          <el-input
            v-model="dialog.form.model_ids_raw"
            type="textarea"
            :rows="3"
            placeholder="一行一个模型 ID，例如：&#10;Qwen/Qwen2.5-72B-Instruct&#10;deepseek-ai/DeepSeek-V3"
          />
          <div class="form-hint">留空 = 启动时按 base_url 拉 /v1/models 自动发现</div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="dialog.form.enabled" />
        </el-form-item>
      </el-form>
      <div v-if="dialog.error" class="note-err">⚠ {{ dialog.error }}</div>
      <template #footer>
        <el-button @click="dialog.show = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="onSubmit">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, reactive, computed } from 'vue';
import { ElMessage } from 'element-plus';
import {
  listProviders,
  addProvider,
  deleteProvider,
  type CustomProvider,
} from '@/services/customProviderService';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

interface TableItem extends CustomProvider {
  model_count: number;
}

const items = ref<TableItem[]>([]);
const loading = ref(false);
const formError = ref<string | null>(null);

const stats = computed(() => [
  { label: '自定义 Provider', value: items.value.length, tone: 'brand' as const, hint: '当前已注册' },
  { label: '启用中', value: items.value.filter((i) => i.enabled).length, tone: 'muted' as const, hint: '下个任务可用' },
  { label: '已配密钥', value: items.value.filter((i) => i.has_api_key).length, tone: 'muted' as const },
]);

const dialog = reactive({
  show: false,
  saving: false,
  error: null as string | null,
  form: {
    id: '',
    name: '',
    base_url: '',
    api_key: '',
    model_ids_raw: '',
    enabled: true,
  },
});

async function load(): Promise<void> {
  loading.value = true;
  formError.value = null;
  try {
    const list = await listProviders();
    items.value = list.map((p) => ({
      ...p,
      model_count: (p as any).model_ids?.length ?? 0,
    }));
  } catch (e) {
    formError.value = `加载失败：${(e as Error).message}`;
  } finally {
    loading.value = false;
  }
}

function openDialog(): void {
  dialog.form = { id: '', name: '', base_url: '', api_key: '', model_ids_raw: '', enabled: true };
  dialog.error = null;
  dialog.saving = false;
  dialog.show = true;
}

async function onSubmit(): Promise<void> {
  dialog.error = null;
  const id = dialog.form.id.trim();
  const base_url = dialog.form.base_url.trim();
  if (!id) { dialog.error = 'ID 必填'; return; }
  if (!base_url) { dialog.error = 'Base URL 必填'; return; }
  const model_ids = dialog.form.model_ids_raw
    .split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
  try {
    dialog.saving = true;
    await addProvider({
      id,
      name: dialog.form.name.trim() || undefined,
      base_url,
      api_key: dialog.form.api_key.trim() || undefined,
      model_ids,
      enabled: dialog.form.enabled,
    });
    dialog.show = false;
    ElMessage.success('已添加，立即可用');
    await load();
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    dialog.error = detail || (e as Error).message;
  } finally {
    dialog.saving = false;
  }
}

async function onDelete(id: string): Promise<void> {
  if (!confirm(`确定删除 provider「${id}」？已选中的模型将自动回退。`)) return;
  try {
    await deleteProvider(id);
    ElMessage.success('已删除');
    await load();
  } catch (e) {
    ElMessage.error(`删除失败：${(e as Error).message}`);
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
.sc-empty { padding: 24px 0; text-align: center; color: var(--ink-400); font-size: 13px; }
</style>
