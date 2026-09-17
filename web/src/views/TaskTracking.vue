<template>
  <div class="track-page">
    <el-card class="head-card">
      <div class="head-row">
        <div class="head-left">
          <h2>{{ topic || '任务追踪' }}</h2>
          <StatusBadge v-if="status" :status="status" />
        </div>
        <div class="head-right">
          <span class="debug-label">调试模式</span>
          <el-switch v-model="debugLocal" />
        </div>
      </div>
      <el-progress
        class="round-progress"
        :percentage="progressPct"
        :format="() => `第 ${round} / ${maxRounds} 轮`"
        :status="progressStatus"
      />
    </el-card>

    <el-card v-if="showCitationChain" class="citation-card">
      <template #header>引用链状态</template>
      <div class="citation-chain">
        <div class="citation-step">
          <span class="step-label">检索</span>
          <span class="step-count">{{ retrievalCount }} 条</span>
        </div>
        <el-icon class="arrow"><Right /></el-icon>
        <div class="citation-step">
          <span class="step-label">分析</span>
          <span class="step-count">{{ conclusionCount }} 条</span>
        </div>
        <el-icon class="arrow"><Right /></el-icon>
        <div class="citation-step">
          <span class="step-label">撰稿</span>
          <span class="step-count">{{ segmentCount }} 段</span>
        </div>
      </div>
    </el-card>

    <el-card class="timeline-card">
      <Timeline :nodes="timelineNodes" @node-click="handleNodeClick" />
    </el-card>

    <el-dialog v-model="showTerminal" :title="terminalTitle" :close-on-click-modal="false" width="420px">
      <p v-if="status === 'done'">✅ 任务已完成，可查看最终研报。</p>
      <p v-else-if="status === 'escalated'">
        ⛔ 任务已升级：{{ escalateReason }}<br />请前往人工复核页处理。
      </p>
      <template #footer>
        <el-button v-if="status === 'escalated'" type="primary" @click="goReview">去复核</el-button>
        <el-button v-if="status === 'done'" type="primary" @click="goResult">查看研报</el-button>
        <el-button @click="showTerminal = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useTaskStore } from '@/stores/task';
import { useSettingsStore } from '@/stores/settings';
import type { TimelineNode } from '@/types';
import Timeline from '@/components/timeline/Timeline.vue';
import StatusBadge from '@/components/common/StatusBadge.vue';

const route = useRoute();
const router = useRouter();
const taskStore = useTaskStore();
const settingsStore = useSettingsStore();
const {
  currentTask,
  status,
  isTerminal,
  topic,
  round,
  maxRounds,
  timelineNodes,
} = storeToRefs(taskStore);

const taskId = computed(() => route.params.taskId as string);
const debugLocal = computed({
  get: () => settingsStore.debugMode,
  set: (v: boolean) => settingsStore.setDebug(v),
});

const progressPct = computed(() =>
  maxRounds.value > 0 ? Math.round((round.value / maxRounds.value) * 100) : 0
);
const progressStatus = computed<'success' | 'exception' | 'warning' | undefined>(() => {
  if (status.value === 'done') return 'success';
  if (status.value === 'escalated' || status.value === 'aborted') return 'exception';
  return undefined;
});

const retrievalCount = computed(() => currentTask.value?.retrieval_records.length ?? 0);
const conclusionCount = computed(() => currentTask.value?.analysis_conclusions.length ?? 0);
const segmentCount = computed(() => currentTask.value?.draft_segments.length ?? 0);
const showCitationChain = computed(() => retrievalCount.value > 0 || conclusionCount.value > 0);
const escalateReason = computed(() => currentTask.value?.escalate_reason ?? null);

// 终态弹窗可见性：用可写 ref 承载 el-dialog 的 v-model，由 isTerminal 单向同步驱动显示；
// 关闭按钮/右上角 X 写入 showTerminal.value=false 生效（修复 F2：原只读 computed 绑 v-model 无法关闭）
const showTerminal = ref(false);
watch(isTerminal, (v) => {
  showTerminal.value = v;
});
const terminalTitle = computed(() => (status.value === 'done' ? '任务完成' : '需要人工复核'));

onMounted(() => {
  // 先全量兜底同步（GET /tasks/{id}），再订阅 WS 增量（连接缓存复用，防重复建连）
  taskStore.connectTracking(taskId.value);
});
onUnmounted(() => {
  // 路由切换不主动关闭 WS（连接缓存复用）；如需彻底释放：taskStore.dispose(taskId.value)
});

function handleNodeClick(node: TimelineNode): void {
  // 节点展开详情（M7-3）：点击由 TimelineNode 内部切换 expanded
  console.log('节点点击', node);
}
function goResult(): void {
  router.push({ name: 'TaskResult', params: { taskId: taskId.value } });
}
function goReview(): void {
  router.push({ name: 'TaskReview', params: { taskId: taskId.value } });
}
</script>

<style scoped>
.track-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.head-card .head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.head-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.head-left h2 {
  margin: 0;
  font-size: 18px;
}
.round-progress {
  margin-top: 12px;
}
.citation-chain {
  display: flex;
  align-items: center;
  gap: 12px;
}
.citation-step {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.step-label {
  font-size: 13px;
  color: #606266;
}
.step-count {
  font-weight: 600;
  color: #303133;
}
.arrow {
  color: #c0c4cc;
}
</style>
