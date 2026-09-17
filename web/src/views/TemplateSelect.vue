<template>
  <div class="tpl-page">
    <h2>选择模板</h2>
    <el-row :gutter="16">
      <el-col v-for="t in templates" :key="t.id" :span="8">
        <el-card shadow="hover" class="tpl-card" :class="{ active: t.id === selectedId }" @click="select(t.id)">
          <template #header>
            <span class="tpl-name">{{ t.name }}</span>
          </template>
          <p class="tpl-desc">{{ t.description }}</p>
          <div class="tpl-meta">Agents：{{ t.agents.join(' / ') }}</div>
          <div class="tpl-meta">Gates：{{ t.gates.join(' / ') }}</div>
        </el-card>
      </el-col>
    </el-row>
    <el-button type="primary" :disabled="!selectedId" @click="goSubmit">下一步：提交任务</el-button>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useTemplateStore } from '@/stores/template';

const router = useRouter();
const tpl = useTemplateStore();
const { templates, selectedId } = storeToRefs(tpl);

function select(id: string): void {
  tpl.select(id);
}

function goSubmit(): void {
  router.push({ name: 'TaskSubmit' });
}

// M8-4：模板已后端化，挂载时从 /api/v1/templates 动态拉取（解除前端硬编码）
onMounted(() => {
  tpl.fetchTemplates();
});
</script>
