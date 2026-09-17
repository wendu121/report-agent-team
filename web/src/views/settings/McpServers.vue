<template>
  <div class="page settings-page">
    <PageHead
      icon="Share"
      title="MCP Server"
      sub="接入 Model Context Protocol 服务器，Researcher / Analyst 可自主调用其工具（形如 mcp:<server>:<tool>）。新增/删除下个任务立即生效。"
    />
    <StatStrip :stats="stats" />

    <el-alert v-if="formError" type="error" :closable="false" class="blk">{{ formError }}</el-alert>

    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">已接入的 MCP Server</span>
          <el-button type="primary" size="small" @click="openDialog">＋ 新增</el-button>
        </div>
      </template>
      <el-table :data="items" size="default" row-key="id">
        <el-table-column prop="name" label="显示名" width="160" />
        <el-table-column prop="id" label="ID" width="140" />
        <el-table-column prop="transport" label="传输" width="90" />
        <el-table-column prop="url" label="URL" />
        <el-table-column label="Headers" width="90">
          <template #default="{ row }">
            <el-tag :type="row.has_headers ? 'success' : 'info'" size="small">
              {{ row.has_headers ? '已配置' : '无' }}
            </el-tag>
          </template>
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
      <div v-else-if="items.length === 0" class="sc-empty">暂无 MCP server，点击上方「新增」添加。</div>
    </el-card>

    <!-- 新增弹窗 -->
    <el-dialog
      v-model="dialog.show"
      title="添加 MCP Server"
      width="560"
      :close-on-click-modal="false"
    >
      <el-form :model="dialog.form" label-width="110px" size="default">
        <el-form-item label="ID" required>
          <el-input v-model="dialog.form.id" placeholder="例如 deepwiki / my-internal-mcp" />
          <div class="form-hint">仅字母数字 / - _，工具前缀将形如 mcp:&lt;id&gt;:&lt;tool&gt;</div>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="dialog.form.name" placeholder="留空 = 用 ID" />
        </el-form-item>
        <el-form-item label="传输方式" required>
          <el-select v-model="dialog.form.transport" style="width: 100%">
            <el-option label="Streamable HTTP（推荐）" value="http" />
            <el-option label="Streamable HTTP（别名）" value="streamable_http" />
            <el-option label="SSE" value="sse" />
          </el-select>
        </el-form-item>
        <el-form-item label="URL" required>
          <el-input v-model="dialog.form.url" placeholder="https://mcp.deepwiki.com/mcp" />
          <div class="form-hint">MCP 端点完整地址（容器内需可直连；需鉴权时用 Headers）</div>
        </el-form-item>
        <el-form-item label="Headers">
          <el-input
            v-model="dialog.form.headers_raw"
            type="textarea"
            :rows="3"
            placeholder='JSON 格式，例如：&#10;{ "Authorization": "Bearer sk-…" }'
          />
          <div class="form-hint">可选；鉴权头等自定义请求头（值不回显）</div>
        </el-form-item>
        <el-form-item label="工具白名单">
          <el-input
            v-model="dialog.form.whitelist_raw"
            type="textarea"
            :rows="2"
            placeholder="一行一个工具名，留空 = 放行该 server 全部工具"
          />
          <div class="form-hint">留空 = 全部放行；填写后仅白名单内工具对 Agent 可见</div>
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
  listMcpServers,
  addMcpServer,
  deleteMcpServer,
  type McpServer,
} from '@/services/mcpServerService';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

const items = ref<McpServer[]>([]);
const loading = ref(false);
const formError = ref<string | null>(null);

const stats = computed(() => [
  { label: 'MCP Server', value: items.value.length, tone: 'brand' as const, hint: '当前已注册' },
  { label: '启用中', value: items.value.filter((i) => i.enabled).length, tone: 'muted' as const, hint: '下个任务可用' },
  { label: '已配 Headers', value: items.value.filter((i) => i.has_headers).length, tone: 'muted' as const },
]);

const dialog = reactive({
  show: false,
  saving: false,
  error: null as string | null,
  form: {
    id: '',
    name: '',
    transport: 'http',
    url: '',
    headers_raw: '',
    whitelist_raw: '',
    enabled: true,
  },
});

async function load(): Promise<void> {
  loading.value = true;
  formError.value = null;
  try {
    items.value = await listMcpServers();
  } catch (e) {
    formError.value = `加载失败：${(e as Error).message}`;
  } finally {
    loading.value = false;
  }
}

function openDialog(): void {
  dialog.form = { id: '', name: '', transport: 'http', url: '', headers_raw: '', whitelist_raw: '', enabled: true };
  dialog.error = null;
  dialog.saving = false;
  dialog.show = true;
}

async function onSubmit(): Promise<void> {
  dialog.error = null;
  const id = dialog.form.id.trim();
  const url = dialog.form.url.trim();
  if (!id) { dialog.error = 'ID 必填'; return; }
  if (!/^[A-Za-z0-9_-]+$/.test(id)) { dialog.error = 'ID 仅允许字母数字 / - _'; return; }
  if (!url) { dialog.error = 'URL 必填'; return; }

  let headers: Record<string, string> | undefined;
  if (dialog.form.headers_raw.trim()) {
    try {
      headers = JSON.parse(dialog.form.headers_raw);
    } catch {
      dialog.error = 'Headers 不是合法 JSON';
      return;
    }
  }
  const tools_whitelist = dialog.form.whitelist_raw
    .split(/[\n,]/).map((s) => s.trim()).filter(Boolean);

  try {
    dialog.saving = true;
    await addMcpServer({
      id,
      name: dialog.form.name.trim() || undefined,
      transport: dialog.form.transport,
      url,
      headers,
      tools_whitelist,
      enabled: dialog.form.enabled,
    });
    dialog.show = false;
    ElMessage.success('已添加，下个任务即可用');
    await load();
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    dialog.error = detail || (e as Error).message;
  } finally {
    dialog.saving = false;
  }
}

async function onDelete(id: string): Promise<void> {
  if (!confirm(`确定删除 MCP server「${id}」？其工具将对 Agent 立即不可见。`)) return;
  try {
    await deleteMcpServer(id);
    ElMessage.success('已删除');
    await load();
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    ElMessage.error(`删除失败：${detail || (e as Error).message}`);
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
