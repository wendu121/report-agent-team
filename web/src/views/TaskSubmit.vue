<template>
  <div class="submit-page">
    <h2>提交任务</h2>
    <el-alert
      v-if="!selected"
      type="warning"
      :closable="false"
      title="请先在模板选择页选择模板"
    />
    <el-form v-else :model="form" label-width="100px" class="submit-form">
      <!-- M9-1：市场页「对话」入口带来的显式 Agent 子集，覆盖模板默认编排 -->
      <el-alert
        v-if="presetAgents.length"
        type="success"
        :closable="false"
        :title="`本次将仅用指定智能体编排：${presetAgents.join(' → ')}（覆盖模板默认 agents）`"
      />
      <el-form-item label="主题" prop="topic">
        <el-input v-model="form.topic" placeholder="研究主题" />
      </el-form-item>
      <el-form-item label="范围" prop="scope">
        <el-input v-model="scopeInput" type="textarea" :rows="2" placeholder="每行一个范围" />
      </el-form-item>
      <el-form-item label="约束条件" prop="constraints">
        <el-input v-model="constraintsInput" type="textarea" :rows="2" placeholder="每行一个约束" />
      </el-form-item>
      <el-form-item label="输出格式" prop="output_format_spec">
        <el-input v-model="form.output_format_spec" type="textarea" :rows="2" placeholder="对输出格式的要求" />
      </el-form-item>
      <el-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="onSubmit">
        提交
      </el-button>
      <span v-if="task.error" class="err">{{ task.error }}</span>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useTemplateStore } from '@/stores/template';
import { useTaskStore } from '@/stores/task';
import type { UserTask } from '@/types';

const router = useRouter();
const route = useRoute();
const tpl = useTemplateStore();
const task = useTaskStore();
const { selected } = storeToRefs(tpl);

const form = reactive<UserTask>({ topic: '', scope: [], output_format_spec: '', constraints: [] });
const scopeInput = ref('');
const constraintsInput = ref('');
const submitting = ref(false);
// M9-1：/submit?agents=Researcher,Coder —— 由智能体市场「对话」进入，显式指定编排子集
const presetAgents = ref<string[]>(
  String((route.query.agents as string) || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
);

// 设计清单第 6 项：补全 watch 导入，约束/范围按行解析
watch(scopeInput, (val) => {
  form.scope = val
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
});
watch(constraintsInput, (val) => {
  form.constraints = val
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
});

const canSubmit = computed(() => !!selected.value && form.topic.trim().length > 0);

async function onSubmit(): Promise<void> {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const taskId = await task.createTask({
      user_task: { ...form },
      template_id: selected.value?.id,
      ...(presetAgents.value.length ? { agents: presetAgents.value } : {}),
    });
    router.push({ name: 'TaskTracking', params: { taskId } });
  } catch (e) {
    task.setError(e instanceof Error ? e.message : '提交失败');
  } finally {
    submitting.value = false;
  }
}
</script>
