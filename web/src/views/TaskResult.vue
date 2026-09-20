<template>
  <div class="result-page">
    <h2 class="page-title">研报结果</h2>

    <el-alert v-if="error" type="error" :closable="false" :title="error" class="block" />

    <!-- 闸降级警示（M13-gate-degradation）：无降级时**完全不渲染**，正常任务界面零变化。
         「质量未被审核」这件事必须在报告正文之前就被看见，不能只藏在评审留痕的小字里。 -->
    <el-alert
      v-if="degradedCount > 0"
      type="warning"
      :closable="false"
      show-icon
      class="block"
      :title="`本次报告有 ${degradedCount} / ${gateHistory.length} 道审核闸未真正执行`"
      :description="`审核模型不可用，已降级放行（${degradedGates.join('、')}）。质量结论未经 AI 审核，请谨慎采信。`"
    />

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
        <div class="card-action">
          <el-button-group class="format-group">
            <el-button
              v-for="f in formatOptions"
              :key="f.value"
              size="small"
              :type="f.value === 'md' ? 'primary' : 'default'"
              :loading="downloadingFormat === f.value"
              @click="onDownloadReport(f.value)"
            >
              {{ f.label }}
            </el-button>
          </el-button-group>
          <el-button type="primary" plain size="small" :loading="downloading" @click="onDownload">
            下载审计包 (ZIP)
          </el-button>
        </div>
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
          :type="reviewStatusOf(r) === 'degraded_unavailable' ? 'warning' : decisionType(r.decision)"
          :timestamp="r.timestamp ? formatTime(r.timestamp) : `第 ${r.round} 轮`"
        >
          <div class="review-head">
            <strong>{{ r.gate }}</strong>
            <el-tag size="small" :type="decisionTag(r.decision)">{{ decisionLabel(r.decision) }}</el-tag>
            <el-tag size="small" effect="plain" :type="REVIEW_STATUS_TAG[reviewStatusOf(r)]">
              {{ REVIEW_STATUS_LABEL[reviewStatusOf(r)] }}
            </el-tag>
            <!-- 分数：降级闸**绝不显示裸数字** —— 那个数字来自独立评分器（甚至可能是误读），
                 给它「认证」的视觉权重就是继续骗人。只以"非审核结论"的措辞出现。 -->
            <span v-if="reviewStatusOf(r) === 'degraded_unavailable'" class="score score-degraded">
              {{ r.eval_score === null ? '未评分' : `独立评分器 ${r.eval_score}（非审核结论）` }}
            </span>
            <span v-else class="score">评分 {{ r.eval_score ?? '—' }}</span>
          </div>
          <p class="review-reason">{{ r.reason }}</p>
          <p v-if="reviewStatusOf(r) === 'degraded_unavailable'" class="review-note">
            该闸的审核模型未返回可用结论，本条「放行」来自代码硬校验，不代表质量已获 AI 认可。
          </p>
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
import {
  downloadAuditPackage,
  downloadReport,
  REPORT_FORMAT_OPTIONS,
  type ReportFormat,
} from '@/utils/download';
import { formatTime } from '@/utils/formatter';
import StatusBadge from '@/components/common/StatusBadge.vue';
import type { GateDecision, GateReview, GateReviewStatus } from '@/types';
import {
  normalizeReviewStatus,
  REVIEW_STATUS_LABEL,
  REVIEW_STATUS_TAG,
} from '@/utils/reviewStatus';

const route = useRoute();
const router = useRouter();
const taskStore = useTaskStore();
const { currentTask: task, status, error } = storeToRefs(taskStore);

const taskId = computed(() => route.params.taskId as string);
const downloading = ref(false);
// 四格式下载：独立 loading 态，避免点 PDF 时四个按钮一起转圈
const downloadingFormat = ref<ReportFormat | null>(null);
const formatOptions = REPORT_FORMAT_OPTIONS;

const gateHistory = computed(() => task.value?.routing_state.gate_review_history ?? []);

// M13-gate-degradation：归一化「这道闸到底审没审」。
// 历史任务数据没有 review_status → 由 normalizeReviewStatus 按 reason 文本兜底
// （兜底逻辑只有那一个实现处，此处不重复判断）。
function reviewStatusOf(r: GateReview): GateReviewStatus {
  return normalizeReviewStatus(r);
}
/** 未真正执行的闸（降级放行）—— 任务级警示与计数都以此为准 */
const degradedGates = computed(() =>
  gateHistory.value
    .filter((r) => reviewStatusOf(r) === 'degraded_unavailable')
    .map((r) => r.gate as string),
);
const degradedCount = computed(() => degradedGates.value.length);

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

async function onDownloadReport(fmt: ReportFormat): Promise<void> {
  downloadingFormat.value = fmt;
  try {
    await downloadReport(taskId.value, fmt);
  } catch (e) {
    // 后端 503 RENDERER_UNAVAILABLE / 409 未完成等文案已由 download.ts 规整，原样透出
    taskStore.setError(e instanceof Error ? e.message : '下载失败');
  } finally {
    downloadingFormat.value = null;
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
  /* 新增「依据来源」标签与分数说明后，降级条目一行放不下 → 允许换行 */
  flex-wrap: wrap;
}
.score {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
/* 降级闸的分数说明：刻意与「放行」脱钩 —— 它来自独立评分器，不是审核结论 */
.score-degraded {
  color: var(--el-color-warning);
  font-size: 13px;
}
/* 降级闸的解释行：说明「放行」来自代码硬校验，不代表质量已获 AI 认可 */
.review-note {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-color-warning);
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
