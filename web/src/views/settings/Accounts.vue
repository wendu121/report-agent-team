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
      width="920px"
      top="6vh"
      destroy-on-close
      class="rec-dialog"
    >
      <template #header>
        <div class="rec-head">
          <div class="rec-head-main">
            <span class="rec-head-title">操作记录</span>
            <span class="rec-head-user">{{ recordsUser }}</span>
          </div>
          <div class="rec-head-sub">该子账号的模型调用与工具执行流水（倒序）</div>
        </div>
      </template>

      <div class="rec-stats">
        <div class="rec-stat">
          <div class="rec-stat-num">{{ records.length }}</div>
          <div class="rec-stat-lbl">总记录</div>
        </div>
        <div class="rec-stat is-ok">
          <div class="rec-stat-num">{{ okCount }}</div>
          <div class="rec-stat-lbl">成功</div>
        </div>
        <div class="rec-stat is-fail">
          <div class="rec-stat-num">{{ failCount }}</div>
          <div class="rec-stat-lbl">失败</div>
        </div>
        <div class="rec-stat">
          <div class="rec-stat-num">{{ avgLatencyText }}</div>
          <div class="rec-stat-lbl">平均耗时</div>
        </div>
      </div>

      <el-table
        :data="records"
        v-loading="recordsLoading"
        size="default"
        row-key="id"
        class="rec-table"
      >
        <el-table-column type="expand" width="42">
          <template #default="{ row }">
            <div class="rec-content">
              <div class="rec-content-head">
                <span class="rec-content-title">操作内容</span>
                <el-button v-if="row.content" link size="small" @click="copyContent(row)">复制</el-button>
              </div>
              <pre v-if="row.content" class="rec-pre">{{ row.content }}</pre>
              <div v-else class="rec-content-empty">
                该记录产生于「内容记录」上线前，仅保留成败与耗时；此后新产生的记录都会带上完整内容。
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="86">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :type="kindTagType(row.kind)">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target" label="目标" min-width="150" show-overflow-tooltip />
        <el-table-column label="结果" width="78">
          <template #default="{ row }">
            <span :class="['rec-pill', row.ok ? 'is-ok' : 'is-fail']">{{ row.ok ? '成功' : '失败' }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="耗时"
          width="100"
          sortable
          :sort-method="sortByLatency"
        >
          <template #default="{ row }">
            <span :class="['rec-lat', isSlow(row.latency_ms) ? 'is-slow' : '']">{{ fmtLatency(row.latency_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="164">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="detail" label="说明" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.detail || '—' }}</template>
        </el-table-column>
        <template #empty>
          <div class="sc-empty">该子账号暂无操作记录。</div>
        </template>
      </el-table>

      <template #footer>
        <span class="rec-hint">最近 {{ records.length }} 条（倒序）· 点行首箭头查看操作内容</span>
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
import { formatTime } from '@/utils/formatter';

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
  return v ? formatTime(v) : '—';
}
function kindLabel(k: string) {
  return { model: '模型', tool: '工具', mcp: 'MCP', plugin: '插件' }[k] || k;
}
function kindTagType(k: string) {
  return ({ model: 'primary', tool: 'success', mcp: 'warning', plugin: 'info' } as Record<string, string>)[k] || 'info';
}
function fmtLatency(ms?: number | null) {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}
function isSlow(ms?: number | null) {
  return typeof ms === 'number' && ms >= 10000; // ≥10s 标黄，提示可能异常
}
function sortByLatency(a: any, b: any) {
  return (a.latency_ms || 0) - (b.latency_ms || 0);
}
const okCount = computed(() => records.value.filter((r) => r.ok).length);
const failCount = computed(() => records.value.filter((r) => !r.ok).length);
const avgLatencyText = computed(() => {
  const lat = records.value
    .map((r) => r.latency_ms)
    .filter((v): v is number => typeof v === 'number');
  if (!lat.length) return '—';
  return fmtLatency(Math.round(lat.reduce((a, b) => a + b, 0) / lat.length));
});
async function copyContent(row: any) {
  try {
    await navigator.clipboard.writeText(row.content || '');
    ElMessage.success('已复制操作内容');
  } catch {
    ElMessage.error('复制失败（浏览器可能未授权剪贴板）');
  }
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

<style scoped>
/* ── 操作记录弹窗：统计条 + 可展开内容 ── */
.rec-head {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rec-head-main {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rec-head-title {
  font-size: 17px;
  font-weight: 650;
  color: #111827;
}
.rec-head-user {
  font-size: 12.5px;
  color: #4b5563;
  background: #f1f5f9;
  border-radius: 999px;
  padding: 2px 10px;
}
.rec-head-sub {
  font-size: 12px;
  color: #9ca3af;
}
.rec-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.rec-stat {
  border: 1px solid #eef0f3;
  background: #fafbfc;
  border-radius: 10px;
  padding: 10px 12px;
}
.rec-stat-num {
  font-size: 20px;
  font-weight: 650;
  line-height: 1.2;
  color: #111827;
  font-variant-numeric: tabular-nums;
}
.rec-stat-lbl {
  margin-top: 2px;
  font-size: 12px;
  color: #9ca3af;
}
.rec-stat.is-ok .rec-stat-num {
  color: #0f9d58;
}
.rec-stat.is-fail .rec-stat-num {
  color: #d93025;
}
.rec-table :deep(.el-table__cell) {
  padding: 9px 0;
}
.rec-pill {
  display: inline-block;
  padding: 1px 9px;
  border-radius: 999px;
  font-size: 12px;
}
.rec-pill.is-ok {
  color: #0f9d58;
  background: rgba(15, 157, 88, 0.1);
}
.rec-pill.is-fail {
  color: #d93025;
  background: rgba(217, 48, 37, 0.1);
}
.rec-lat {
  font-variant-numeric: tabular-nums;
  color: #4b5563;
}
.rec-lat.is-slow {
  color: #d97706;
  font-weight: 600;
}
.rec-content {
  padding: 10px 16px 14px 44px;
  background: #fafbfc;
}
.rec-content-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.rec-content-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: #6b7280;
}
.rec-pre {
  margin: 0;
  padding: 10px 12px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.65;
  color: #1f2937;
  background: #fff;
  border: 1px solid #eef0f3;
  border-radius: 8px;
}
.rec-content-empty {
  font-size: 12.5px;
  color: #9ca3af;
}
.rec-hint {
  float: left;
  font-size: 12px;
  line-height: 32px;
  color: #9ca3af;
}
</style>

