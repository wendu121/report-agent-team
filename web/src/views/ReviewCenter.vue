<template>
  <div class="page">
    <PageHead
      icon="Stamp"
      title="审核中心"
      sub="所有放行决策在你手上：技能提案、专家提案、已过三道闸待推送的变更集，统一在此逐一点通过 / 打回。"
    />

    <!-- 概览：待审总数 + 三类分项 -->
    <div class="review-summary">
      <div class="rs-card">
        <span class="rs-num">{{ counts.total || 0 }}</span>
        <span class="rs-label">待审核项</span>
      </div>
      <div class="rs-card">
        <span class="rs-num">{{ counts.skill_proposal || 0 }}</span>
        <span class="rs-label">技能提案</span>
      </div>
      <div class="rs-card">
        <span class="rs-num">{{ counts.expert_proposal || 0 }}</span>
        <span class="rs-label">专家提案</span>
      </div>
      <div class="rs-card">
        <span class="rs-num">{{ counts.pending_push || 0 }}</span>
        <span class="rs-label">待推送变更</span>
      </div>
    </div>

    <div v-if="loading" class="rc-empty">加载中…</div>
    <div v-else-if="items.length === 0" class="rc-empty">暂无待审核项。</div>

    <template v-else>
      <!-- 技能提案 -->
      <el-card shadow="never" class="blk" v-if="skillItems.length">
        <template #header><span class="blk-title">技能提案（{{ skillItems.length }}）</span></template>
        <el-table :data="skillItems" row-key="id" size="default">
          <el-table-column label="名称 / ID" min-width="200">
            <template #default="{ row }">
              <div class="sc-name">{{ row.title || row.name }}</div>
              <div class="sc-sub">{{ row.id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="100">
            <template #default="{ row }"><el-tag size="small">{{ row.level || '—' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button type="success" link size="small" :loading="row._busy" @click="onAcceptSkill(row)">通过</el-button>
              <el-button type="danger" link size="small" :loading="row._busy" @click="onRejectSkill(row)">驳回</el-button>
              <el-button text size="small" @click="openDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 专家提案 -->
      <el-card shadow="never" class="blk" v-if="expertItems.length">
        <template #header><span class="blk-title">专家提案（{{ expertItems.length }}）</span></template>
        <el-table :data="expertItems" row-key="id" size="default">
          <el-table-column label="名称 / ID" min-width="200">
            <template #default="{ row }">
              <div class="sc-name">{{ row.title || row.name }}</div>
              <div class="sc-sub">{{ row.id }}</div>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="100">
            <template #default="{ row }"><el-tag size="small">{{ row.level || '—' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button type="success" link size="small" :loading="row._busy" @click="onAcceptExpert(row)">通过</el-button>
              <el-button type="danger" link size="small" :loading="row._busy" @click="onRejectExpert(row)">驳回</el-button>
              <el-button text size="small" @click="openDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 待推送变更集 -->
      <el-card shadow="never" class="blk" v-if="pushItems.length">
        <template #header><span class="blk-title">待推送变更（{{ pushItems.length }}）</span></template>
        <el-table :data="pushItems" row-key="id" size="default">
          <el-table-column label="标题 / ID" min-width="220">
            <template #default="{ row }">
              <div class="sc-name">{{ row.title }}</div>
              <div class="sc-sub">{{ row.id }} · {{ (row.commits || []).length }} commits</div>
            </template>
          </el-table-column>
          <el-table-column label="闸门" width="150">
            <template #default="{ row }">
              <el-tag size="small" :type="gateTagType(row)">{{ row.gate?.status || '—' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="决策状态" min-width="160">
            <template #default="{ row }">
              <el-tag size="small" :type="decisionTagType(row)" v-if="row.decision && row.decision !== 'pending'">
                {{ decisionLabel(row) }}
              </el-tag>
              <span v-else class="sc-sub">待审核</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <template v-if="row.decision === 'pushered'">
                <span class="sc-sub">已推送 {{ row.pushed_ref || '' }}</span>
              </template>
              <template v-else-if="row.decision === 'approved'">
                <span class="sc-sub">已批准·待代理推送</span>
              </template>
              <template v-else-if="row.decision === 'rejected'">
                <span class="sc-sub danger">已打回{{ row.reject_reason ? '：' + row.reject_reason : '' }}</span>
              </template>
              <template v-else>
                <el-button type="primary" link size="small" :loading="row._busy" @click="onApprovePush(row)">批准推送</el-button>
                <el-button type="danger" link size="small" :loading="row._busy" @click="onRejectPush(row)">打回</el-button>
                <el-button text size="small" @click="openDetail(row)">详情</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 详情抽屉 -->
    <el-dialog v-model="detailVisible" :title="detailTitle" width="640px">
      <pre class="rc-detail" v-if="detailItem">{{ detailText }}</pre>
      <div v-if="detailItem && detailItem.kind === 'pending_push'">
        <div class="rc-detail-sub">commit 列表</div>
        <ul class="rc-commits">
          <li v-for="c in (detailItem.commits || [])" :key="c">{{ c }}</li>
        </ul>
        <div class="rc-detail-sub">闸门文档</div>
        <div class="sc-sub">{{ (detailItem.gate_docs || []).join('、') || '—' }}</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageHead from '@/components/PageHead.vue';
import {
  listReviewQueue,
  approvePendingPush,
  rejectPendingPush,
  type ReviewQueueItem,
} from '@/services/reviewService';
import { acceptSkillProposal, rejectSkillProposal } from '@/services/skillService';
import { acceptExpertProposal, rejectExpertProposal } from '@/services/expertService';

const items = ref<ReviewQueueItem[]>([]);
const loading = ref(false);
const detailVisible = ref(false);
const detailItem = ref<ReviewQueueItem | null>(null);

const counts = computed(() => {
  const c: Record<string, number> = {};
  for (const it of items.value) c[it.kind] = (c[it.kind] || 0) + 1;
  c.total = items.value.length;
  return c;
});
const skillItems = computed(() => items.value.filter((i) => i.kind === 'skill_proposal'));
const expertItems = computed(() => items.value.filter((i) => i.kind === 'expert_proposal'));
const pushItems = computed(() => items.value.filter((i) => i.kind === 'pending_push'));

const detailTitle = computed(() => (detailItem.value ? detailItem.value.title || detailItem.value.id : ''));
const detailText = computed(() => {
  if (!detailItem.value) return '';
  const { kind, decision, _busy, ...rest } = detailItem.value as any;
  return JSON.stringify(rest, null, 2);
});

async function load() {
  loading.value = true;
  try {
    items.value = await listReviewQueue();
  } catch (e: any) {
    ElMessage.error(e?.message || '加载审核队列失败');
  } finally {
    loading.value = false;
  }
}

function openDetail(row: ReviewQueueItem) {
  detailItem.value = row;
  detailVisible.value = true;
}

function gateTagType(row: ReviewQueueItem): 'success' | 'warning' | 'info' {
  const s = (row.gate?.status || '').toUpperCase();
  if (s.includes('BLOCKED')) return 'info';
  if (s.includes('NOTES')) return 'warning';
  return 'success';
}
function decisionTagType(row: ReviewQueueItem): 'success' | 'danger' | 'info' | 'warning' {
  if (row.decision === 'pushered') return 'success';
  if (row.decision === 'approved') return 'warning';
  if (row.decision === 'rejected') return 'danger';
  return 'info';
}
function decisionLabel(row: ReviewQueueItem): string {
  if (row.decision === 'pushered') return `已推送 ${row.pushed_ref || ''}`.trim();
  if (row.decision === 'approved') return '已批准·待代理推送';
  if (row.decision === 'rejected') return '已打回';
  return '待审核';
}

async function onAcceptSkill(row: ReviewQueueItem) {
  row._busy = true;
  try {
    await acceptSkillProposal(row.id);
    ElMessage.success('技能提案已通过');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '通过失败');
  } finally {
    row._busy = false;
  }
}
async function onRejectSkill(row: ReviewQueueItem) {
  row._busy = true;
  try {
    await rejectSkillProposal(row.id);
    ElMessage.success('技能提案已驳回');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '驳回失败');
  } finally {
    row._busy = false;
  }
}
async function onAcceptExpert(row: ReviewQueueItem) {
  row._busy = true;
  try {
    await acceptExpertProposal(row.id);
    ElMessage.success('专家提案已通过');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '通过失败');
  } finally {
    row._busy = false;
  }
}
async function onRejectExpert(row: ReviewQueueItem) {
  row._busy = true;
  try {
    await rejectExpertProposal(row.id);
    ElMessage.success('专家提案已驳回');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '驳回失败');
  } finally {
    row._busy = false;
  }
}
async function onApprovePush(row: ReviewQueueItem) {
  row._busy = true;
  try {
    await approvePendingPush(row.id);
    ElMessage.success('已批准推送，等待代理执行');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '批准失败');
  } finally {
    row._busy = false;
  }
}
async function onRejectPush(row: ReviewQueueItem) {
  try {
    const { value } = await ElMessageBox.prompt('打回原因（可回流代理重做）', '打回待推送变更', {
      confirmButtonText: '打回',
      cancelButtonText: '取消',
      inputType: 'textarea',
    });
    row._busy = true;
    await rejectPendingPush(row.id, value || '');
    ElMessage.success('已打回');
    await load();
  } catch (e: any) {
    if (e === 'cancel' || e?.action === 'cancel') return;
    ElMessage.error(e?.message || '打回失败');
  } finally {
    row._busy = false;
  }
}

onMounted(load);
</script>

<style scoped>
.review-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}
.rs-card {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #ebeef5);
  border-radius: var(--r-lg, 12px);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rs-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--brand, #0f9d76);
}
.rs-label {
  font-size: 13px;
  color: var(--ink-400, #86909c);
}
.blk {
  margin-bottom: 16px;
}
.blk-title {
  font-weight: 600;
  color: var(--ink-700, #1d2129);
}
.sc-name {
  font-weight: 600;
  color: var(--ink-700, #1d2129);
}
.sc-sub {
  font-size: 12px;
  color: var(--ink-400, #86909c);
}
.sc-sub.danger {
  color: var(--el-color-danger, #f56c6c);
}
.rc-empty {
  padding: 40px;
  text-align: center;
  color: var(--ink-400, #86909c);
}
.rc-detail {
  background: var(--bg-soft, #f7f8fa);
  border-radius: var(--r-md, 8px);
  padding: 12px;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.rc-detail-sub {
  margin: 12px 0 6px;
  font-weight: 600;
  color: var(--ink-600, #475569);
}
.rc-commits {
  margin: 0;
  padding-left: 18px;
  font-family: monospace;
  font-size: 12px;
  color: var(--ink-500, #606266);
}
</style>
