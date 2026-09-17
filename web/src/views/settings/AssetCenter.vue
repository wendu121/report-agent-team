<template>
  <div class="page settings-page">
    <PageHead
      icon="Collection"
      title="经验 / 反思（Asset Center）"
      sub="引擎反思建议与对话经验草稿的统一入口。二者均不自动改系统——须你在此采纳后才生效（下个任务热加载）。"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHead>
    <StatStrip :stats="stats" />

    <el-alert v-if="error" type="error" :closable="false" class="blk">{{ error }}</el-alert>

    <el-tabs v-model="tab" class="blk">
      <!-- 反思建议 -->
      <el-tab-pane label="反思建议" name="reflections">
        <el-card shadow="never" class="blk">
          <div v-if="loading" class="sc-empty">加载中…</div>
          <div v-else-if="reflections.length === 0" class="sc-empty">暂无反思建议。任务出现 escalate / 低可信源 / 素材偏少时会自动产生。</div>

          <el-collapse v-else>
            <el-collapse-item v-for="r in reflections" :key="r.id">
              <template #title>
                <span class="r-title">
                  <el-tag size="small" :type="statusType(r.status)">{{ statusLabel(r.status) }}</el-tag>
                  <el-tag size="small" type="warning" class="r-kind">{{ r.kind }}</el-tag>
                  <span class="r-summary">{{ r.summary }}</span>
                </span>
              </template>
              <div class="r-meta">
                <div><b>ID</b>：{{ r.id }}</div>
                <div><b>任务主题</b>：{{ r.task_topic }}</div>
                <div><b>时间</b>：{{ r.created_at }}</div>
                <div v-if="r.target"><b>影响配置</b>：{{ r.target.file }} · {{ r.target.key }}</div>
                <div><b>变更类型</b>：{{ r.proposed_change?.op === 'none' ? '仅提示（人工去配置台修改）' : r.proposed_change?.op }}</div>
              </div>
              <div class="r-ev" v-if="r.evidence?.length">
                <b>证据链</b>
                <ul>
                  <li v-for="(e, i) in r.evidence" :key="i">{{ e }}</li>
                </ul>
              </div>
              <div class="r-actions" v-if="r.status === 'proposed'">
                <el-button type="success" size="small" :loading="busy === r.id" @click="acceptReflection(r.id)">采纳</el-button>
                <el-button type="info" size="small" :loading="busy === r.id" @click="rejectReflection(r.id)">驳回</el-button>
              </div>
              <div class="r-actions" v-else>
                <el-tag size="small">{{ r.status === 'accepted' ? '已采纳（留痕）' : '已驳回（留痕）' }}</el-tag>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </el-tab-pane>

      <!-- 经验草稿 -->
      <el-tab-pane label="经验草稿" name="lessons">
        <el-card shadow="never" class="blk">
          <div v-if="loading" class="sc-empty">加载中…</div>
          <div v-else-if="lessons.length === 0" class="sc-empty">暂无经验草稿。在对话入口说「记住：某条经验」即会出现在此，采纳后注入 ChatAgent system prompt。</div>

          <div v-else>
            <div v-for="(t, i) in lessons" :key="i" class="lesson-row">
              <span class="lesson-text">{{ t }}</span>
              <el-button type="success" size="small" @click="acceptLesson(t)">采纳</el-button>
            </div>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { Refresh } from '@element-plus/icons-vue';
import api from '@/api/client';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

interface Reflection {
  id: string;
  kind: string;
  summary: string;
  status: string;
  task_topic?: string;
  created_at?: string;
  evidence?: string[];
  target?: { file?: string; key?: string } | null;
  proposed_change?: { op?: string };
}

const tab = ref<'reflections' | 'lessons'>('reflections');
const reflections = ref<Reflection[]>([]);
const lessons = ref<string[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const busy = ref<string | null>(null);

const stats = computed(() => [
  { label: '反思建议', value: reflections.value.length, tone: 'brand' as const, hint: '引擎生成' },
  { label: '待审', value: reflections.value.filter((r) => r.status === 'proposed').length, tone: 'warn' as const, hint: '待你采纳 / 驳回' },
  { label: '经验草稿', value: lessons.value.length, tone: 'muted' as const, hint: '对话沉淀' },
]);

function statusType(s: string): 'success' | 'info' | 'danger' {
  if (s === 'accepted') return 'success';
  if (s === 'rejected') return 'info';
  return 'danger';
}
function statusLabel(s: string): string {
  return s === 'accepted' ? '已采纳' : s === 'rejected' ? '已驳回' : '待审';
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [rf, ls] = await Promise.all([
      api.get<{ items: Reflection[] }>('/admin/reflections'),
      api.get<{ items: string[] }>('/admin/lessons'),
    ]);
    reflections.value = rf.data.items || [];
    lessons.value = ls.data.items || [];
  } catch (e) {
    error.value = `加载失败：${(e as Error).message}`;
  } finally {
    loading.value = false;
  }
}

async function acceptReflection(id: string): Promise<void> {
  busy.value = id;
  try {
    await api.post(`/admin/reflections/${encodeURIComponent(id)}/accept`);
    ElMessage.success('已采纳');
    await load();
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    ElMessage.error(`采纳失败：${msg || (e as Error).message}`);
  } finally {
    busy.value = null;
  }
}

async function rejectReflection(id: string): Promise<void> {
  busy.value = id;
  try {
    await api.post(`/admin/reflections/${encodeURIComponent(id)}/reject`);
    ElMessage.info('已驳回');
    await load();
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    ElMessage.error(`驳回失败：${msg || (e as Error).message}`);
  } finally {
    busy.value = null;
  }
}

async function acceptLesson(text: string): Promise<void> {
  try {
    await api.post('/admin/lessons/accept', { text });
    ElMessage.success('已采纳，下轮对话生效');
    await load();
  } catch (e: unknown) {
    const msg = (e as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message;
    ElMessage.error(`采纳失败：${msg || (e as Error).message}`);
  }
}

onMounted(load);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.sc-empty { padding: 24px 0; text-align: center; color: var(--ink-400); font-size: 13px; }
.r-title { display: flex; align-items: center; gap: 8px; }
.r-kind { margin-left: 4px; }
.r-summary { color: var(--ink-700); font-size: 13px; }
.r-meta { font-size: 12px; color: var(--ink-500); line-height: 1.8; }
.r-ev { margin-top: 8px; font-size: 12px; color: var(--ink-500); }
.r-ev ul { margin: 4px 0 0; padding-left: 18px; }
.r-ev li { font-family: monospace; }
.r-actions { margin-top: 10px; }
.lesson-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--line); font-size: 13px;
}
.lesson-row:last-child { border-bottom: none; }
.lesson-text { color: var(--ink-700); }
</style>
