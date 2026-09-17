<template>
  <div class="result-page">
    <h2 class="page-title">研报结果</h2>

    <el-alert v-if="error" type="error" :closable="false" :title="error" class="block" />

    <!-- 任务 meta 卡 -->
    <el-card class="block" shadow="never">
      <div class="meta-row">
        <span class="meta-label">主题</span>
        <span class="meta-value">{{ task?.user_task.topic || '—' }}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">状态</span>
        <StatusBadge v-if="status" :status="status" />
        <span v-else>—</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">完成时间</span>
        <span class="meta-value">{{ task?.updated_at ? formatTime(task.updated_at) : '—' }}</span>
      </div>
      <div class="meta-row" v-if="task?.escalate_reason">
        <span class="meta-label">升级原因</span>
        <span class="meta-value escalate">{{ task.escalate_reason }}</span>
      </div>
    </el-card>

    <!-- 研报正文（marked 渲染 + DOMPurify XSS 清洗，设计安全红线） -->
    <el-card class="block" shadow="never">
      <template #header>
        <span class="card-title">研报正文</span>
        <el-button class="card-action" type="primary" plain size="small" :loading="downloading" @click="onDownload">
          下载审计包 (ZIP)
        </el-button>
      </template>
      <div class="markdown-body" v-html="sanitizedHtml" />
    </el-card>

    <!-- 评审留痕（Gate 审核历史） -->
    <el-card class="block" shadow="never">
      <template #header>
        <span class="card-title">评审留痕（{{ gateHistory.length }} 条）</span>
      </template>
      <el-empty v-if="gateHistory.length === 0" description="暂无审核记录" />
      <el-timeline v-else>
        <el-timeline-item
          v-for="(r, i) in gateHistory"
          :key="i"
          :type="decisionType(r.decision)"
          :timestamp="r.timestamp ? formatTime(r.timestamp) : `第 ${r.round} 轮`"
        >
          <div class="review-head">
            <strong>{{ r.gate }}</strong>
            <el-tag size="small" :type="decisionTag(r.decision)">{{ decisionLabel(r.decision) }}</el-tag>
            <span class="score">评分 {{ r.eval_score }}</span>
          </div>
          <p class="review-reason">{{ r.reason }}</p>
          <p v-if="r.problem_points.length" class="review-points">
            问题点：{{ r.problem_points.join('、') }}
          </p>
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <div class="block result-footer">
      <el-button v-if="status === 'escalated'" type="warning" @click="goReview">前往人工复核</el-button>
      <el-button @click="router.back()">返回追踪</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { useTaskStore } from '@/stores/task';
import { downloadAuditPackage } from '@/utils/download';
import { formatTime } from '@/utils/formatter';
import StatusBadge from '@/components/common/StatusBadge.vue';
import type { GateDecision } from '@/types';

const route = useRoute();
const router = useRouter();
const taskStore = useTaskStore();
const { currentTask: task, status, error } = storeToRefs(taskStore);

const taskId = computed(() => route.params.taskId as string);
const downloading = ref(false);

const gateHistory = computed(() => task.value?.routing_state.gate_review_history ?? []);

const rawMarkdown = computed(
  () => task.value?.report_markdown ?? '# 暂无研报\n任务尚未完成或内容为空。'
);

// marked 同步渲染后必须经 DOMPurify 清洗，杜绝 XSS（设计安全红线）
const sanitizedHtml = computed(() =>
  DOMPurify.sanitize(marked.parse(rawMarkdown.value, { async: false }) as string)
);

function decisionLabel(d: GateDecision): string {
  switch (d) {
    case 'advance':
      return '放行';
    case 'rework':
      return '打回';
    case 'escalate':
      return '升级';
  }
}
function decisionTag(d: GateDecision): 'success' | 'warning' | 'danger' {
  switch (d) {
    case 'advance':
      return 'success';
    case 'rework':
      return 'warning';
    case 'escalate':
      return 'danger';
  }
}
function decisionType(d: GateDecision): 'success' | 'warning' | 'danger' | 'info' {
  switch (d) {
    case 'advance':
      return 'success';
    case 'rework':
      return 'warning';
    case 'escalate':
      return 'danger';
  }
}

onMounted(() => {
  taskStore.syncFullSnapshot(taskId.value);
});

async function onDownload(): Promise<void> {
  downloading.value = true;
  try {
    await downloadAuditPackage(taskId.value);
  } catch (e) {
    taskStore.setError(e instanceof Error ? e.message : '下载失败');
  } finally {
    downloading.value = false;
  }
}

function goReview(): void {
  router.push({ name: 'TaskReview', params: { taskId: taskId.value } });
}
</script>

<style scoped>
.page-title {
  margin: 0 0 16px;
}
.block {
  margin-bottom: 16px;
}
.meta-row {
  display: flex;
  gap: 12px;
  padding: 6px 0;
  font-size: 14px;
}
.meta-label {
  width: 72px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
.meta-value {
  color: var(--el-text-color-primary);
}
.meta-value.escalate {
  color: var(--el-color-danger);
}
.card-title {
  font-weight: 600;
}
.card-action {
  float: right;
}
.markdown-body {
  line-height: 1.7;
  word-break: break-word;
}
.review-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.score {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.review-reason {
  margin: 4px 0;
  font-size: 13px;
}
.review-points {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.result-footer {
  display: flex;
  gap: 12px;
}
</style>
