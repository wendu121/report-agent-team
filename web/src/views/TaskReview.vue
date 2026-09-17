<template>
  <div class="review-page">
    <h2 class="page-title">人工复核</h2>

    <el-alert
      v-if="!task || status !== 'escalated'"
      type="info"
      :closable="false"
      title="当前任务无需复核或尚未升级"
    />

    <template v-else>
      <!-- 升级上下文 -->
      <el-card class="block" shadow="never">
        <div class="meta-row">
          <span class="meta-label">主题</span>
          <span class="meta-value">{{ task.user_task.topic }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">升级原因</span>
          <span class="meta-value escalate">{{ task.escalate_reason || '—' }}</span>
        </div>
        <div class="meta-row" v-if="task.routing_state.last_gate">
          <span class="meta-label">最近闸</span>
          <span class="meta-value">{{ task.routing_state.last_gate }}</span>
        </div>
      </el-card>

      <el-card class="block" shadow="never">
        <template #header><span class="card-title">复核决策</span></template>
        <el-form label-width="96px" class="review-form">
          <el-form-item label="决策">
            <el-radio-group v-model="action">
              <el-radio value="confirm">确认放行</el-radio>
              <el-radio value="retry">重试</el-radio>
              <el-radio value="abort">中止</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item v-if="action === 'retry'" label="重试目标">
            <el-select v-model="reworkTarget" placeholder="选择重跑的 Agent">
              <el-option v-for="a in agents" :key="a" :label="a" :value="a" />
            </el-select>
          </el-form-item>

          <el-form-item label="复核意见">
            <el-input v-model="comment" type="textarea" :rows="3" placeholder="可选" />
          </el-form-item>

          <el-button type="primary" :loading="submitting" @click="onSubmit">提交复核</el-button>
          <span v-if="error" class="err">{{ error }}</span>
        </el-form>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useTaskStore } from '@/stores/task';
import { submitReview } from '@/services/taskService';
import type { AgentRole, ReviewAction } from '@/types';

const route = useRoute();
const router = useRouter();
const taskStore = useTaskStore();
const { currentTask: task, status, error } = storeToRefs(taskStore);

const taskId = computed(() => route.params.taskId as string);
const action = ref<'confirm' | 'retry' | 'abort'>('confirm');
const reworkTarget = ref<AgentRole>('Writer');
const comment = ref('');
const submitting = ref(false);

const agents: AgentRole[] = ['Researcher', 'Analyst', 'Writer'];

async function onSubmit(): Promise<void> {
  submitting.value = true;
  try {
    const req: ReviewAction = {
      action: action.value,
      reviewer_comment: comment.value,
      ...(action.value === 'retry' ? { rework_target_agent: reworkTarget.value } : {}),
    };
    await submitReview(taskId.value, req);
    router.push({ name: 'TaskTracking', params: { taskId: taskId.value } });
  } catch (e) {
    taskStore.setError(e instanceof Error ? e.message : '提交失败');
  } finally {
    submitting.value = false;
  }
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
.err {
  color: var(--el-color-danger);
  margin-left: 12px;
  font-size: 13px;
}
</style>
