<template>
  <div class="page settings-page">
    <PageHead
      icon="User"
      title="账号管理"
      sub="审批新注册的账号；停用 / 启用 / 重置密码 / 删除子账号。资源随账号归档，不做级联删除。"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHead>

    <StatStrip :stats="stats" />

    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">子账号</span>
          <span class="blk-sub">仅显示由本主账号管理的子账号</span>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" row-key="id" size="default">
        <el-table-column prop="username" label="用户名" min-width="160" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" min-width="170">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="280">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'pending'"
              link type="primary"
              @click="act(row, 'approve')"
            >通过</el-button>
            <el-button
              v-if="row.status === 'pending'"
              link type="danger"
              @click="act(row, 'reject')"
            >驳回</el-button>
            <el-button
              v-if="row.status === 'active'"
              link
              @click="act(row, 'disable')"
            >停用</el-button>
            <el-button
              v-if="row.status === 'disabled'"
              link type="primary"
              @click="act(row, 'enable')"
            >启用</el-button>
            <el-button link @click="resetPwd(row)">重置密码</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
            <el-button
              v-if="row.status !== 'pending'"
              link type="primary"
              @click="viewRecords(row)"
            >查阅操作记录</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="sc-empty">暂无子账号。新注册账号会出现在这里等待审批。</div>
        </template>
      </el-table>
    </el-card>

    <el-dialog
      v-model="recordsVisible"
      :title="`操作记录 · ${recordsUser}`"
      width="760px"
      destroy-on-close
    >
      <el-table :data="records" v-loading="recordsLoading" size="small" max-height="420">
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.kind === 'model' ? 'primary' : 'info'">
              {{ kindLabel(row.kind) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target" label="目标" min-width="180" show-overflow-tooltip />
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.ok ? 'success' : 'danger'">
              {{ row.ok ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ row.latency_ms != null ? row.latency_ms + ' ms' : '—' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="detail" label="说明" min-width="160" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <span class="rec-hint">最近 {{ records.length }} 条（倒序）</span>
        <el-button @click="recordsVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Refresh } from '@element-plus/icons-vue';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';
import http from '@/api/client';
import type { AccountInfo } from '@/stores/authStore';

const rows = ref<AccountInfo[]>([]);
const loading = ref(false);

// 操作记录（调用流水）对话框
const records = ref<any[]>([]);
const recordsLoading = ref(false);
const recordsVisible = ref(false);
const recordsUser = ref('');

const stats = computed(() => [
  { label: '子账号总数', value: rows.value.length, tone: 'muted' as const, hint: '由本主账号管理' },
  {
    label: '待审批',
    value: rows.value.filter((r) => r.status === 'pending').length,
    tone: 'warn' as const,
    hint: '需你通过或驳回',
  },
  {
    label: '启用中',
    value: rows.value.filter((r) => r.status === 'active').length,
    tone: 'brand' as const,
    hint: '可正常登录',
  },
]);

function statusLabel(s: string) {
  return { pending: '待审批', active: '启用中', disabled: '已停用', rejected: '已驳回' }[s] || s;
}
function statusType(s: string) {
  return { pending: 'warning', active: 'success', disabled: 'info', rejected: 'danger' }[s] || 'info';
}
function fmt(v?: string | null) {
  return v ? new Date(v).toLocaleString('zh-CN') : '—';
}
function kindLabel(k: string) {
  return { model: '模型', tool: '工具', mcp: 'MCP', plugin: '插件' }[k] || k;
}

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get('/auth/accounts');
    rows.value = data;
  } catch (e: any) {
    ElMessage.error(e?.payload?.data?.detail || '加载失败');
  } finally {
    loading.value = false;
  }
}

async function act(row: AccountInfo, op: string) {
  try {
    await http.post(`/auth/accounts/${row.id}/${op}`);
    ElMessage.success('操作成功');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.payload?.data?.detail || '操作失败');
  }
}

async function resetPwd(row: AccountInfo) {
  const { value } = await ElMessageBox.prompt(
    `为「${row.username}」设置新密码（至少 8 位）`,
    '重置密码',
    { inputType: 'password', inputPattern: /.{8,}/, inputErrorMessage: '密码至少 8 位' }
  );
  try {
    await http.post(`/auth/accounts/${row.id}/reset-password`, { new_password: value });
    ElMessage.success('密码已重置');
  } catch (e: any) {
    ElMessage.error(e?.payload?.data?.detail || '重置失败');
  }
}

async function remove(row: AccountInfo) {
  await ElMessageBox.confirm(
    `确定删除「${row.username}」？账号资源会被归档而非删除。`,
    '删除账号',
    { type: 'warning' }
  );
  try {
    await http.delete(`/auth/accounts/${row.id}`);
    ElMessage.success('已删除（资源已归档）');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.payload?.data?.detail || '删除失败');
  }
}

async function viewRecords(row: AccountInfo) {
  recordsUser.value = row.username;
  recordsVisible.value = true;
  recordsLoading.value = true;
  records.value = [];
  try {
    const { data } = await http.get(`/auth/accounts/${row.id}/call-records?limit=50`);
    records.value = data;
  } catch (e: any) {
    ElMessage.error(e?.payload?.data?.detail || '加载操作记录失败');
  } finally {
    recordsLoading.value = false;
  }
}

onMounted(load);
</script>
