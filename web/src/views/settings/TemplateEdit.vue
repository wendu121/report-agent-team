<template>
  <div class="page settings-page">
    <PageHead icon="EditPen" :title="isNew ? '新增模板' : '编辑模板'" sub="配置任务编排的 Agents / Gates / 输出格式组合。" />

    <el-alert
      type="info"
      :closable="false"
      class="blk"
      title="M8-4 阶段：Agents / Gates 固定为已知 3+3 组合（引擎 build_graph 硬编码，子集编排待 M8-5）；输出格式仅 markdown（G6 渲染器待 M9）"
    />
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">基础配置</span>
          <span class="blk-sub">{{ isNew ? '新增后不可改 id' : '编辑基础信息' }}</span>
        </div>
      </template>
      <el-form :model="form" label-width="100px" v-loading="loading" class="tpl-form">
        <el-form-item label="名称">
          <el-input v-model="form.name" :disabled="!isNew" placeholder="模板显示名（新增后不可改 id）" />
        </el-form-item>
        <el-form-item v-if="isNew" label="模板 ID">
          <el-input v-model="form.id" placeholder="小写字母/数字/连字符/下划线；中文名必须显式填写" />
          <div class="form-hint">目录与 API 标识使用此 ID（ASCII），缺省时由名称派生；含中文时请填写。</div>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="Agents">
          <el-select v-model="form.agents" multiple placeholder="选择 Agents">
            <el-option v-for="a in KNOWN_AGENTS" :key="a" :label="a" :value="a" />
          </el-select>
        </el-form-item>
        <el-form-item label="Gates">
          <el-select v-model="form.gates" multiple placeholder="选择 Gates">
            <el-option v-for="g in KNOWN_GATES" :key="g" :label="g" :value="g" />
          </el-select>
        </el-form-item>
        <el-form-item label="输出格式">
          <el-select v-model="form.output_format" multiple placeholder="输出格式">
            <el-option label="markdown" value="markdown" />
          </el-select>
        </el-form-item>
        <el-form-item label="UI 模式">
          <el-select v-model="form.ui_mode">
            <el-option label="unified_shell" value="unified_shell" />
            <el-option label="custom_component" value="custom_component" />
          </el-select>
        </el-form-item>
        <el-alert v-if="backendError" type="error" :closable="false" :title="backendError" />
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
        <el-button @click="goList">取消</el-button>
      </el-form>
    </el-card>

    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">改动审计</span>
          <span class="blk-sub">最近 {{ audit.length }} 条</span>
        </div>
      </template>
      <el-timeline v-if="audit.length">
        <el-timeline-item v-for="(a, i) in audit" :key="i" :timestamp="a.ts">
          {{ a.action }} · {{ a.operator }} · {{ a.validation }}
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无审计记录" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { authFetch } from '@/api/client';
import PageHead from '@/components/PageHead.vue';

const API = '/api/v1/admin';
const KNOWN_AGENTS = ['Researcher', 'Analyst', 'Writer'];
const KNOWN_GATES = ['GateA', 'GateB', 'GateC'];

const route = useRoute();
const router = useRouter();
const isNew = computed(() => route.name === 'SettingsTemplatesNew');
const name = computed(() => route.params.name as string);

const loading = ref(false);
const saving = ref(false);
const backendError = ref('');
const audit = ref<any[]>([]);

const form = reactive({
  name: '',
  id: '',
  description: '',
  agents: [...KNOWN_AGENTS],
  gates: [...KNOWN_GATES],
  output_format: ['markdown'],
  ui_mode: 'unified_shell',
});

async function loadOne(): Promise<void> {
  if (isNew.value) return;
  loading.value = true;
  backendError.value = '';
  try {
    const res = await authFetch(`${API}/templates/${name.value}`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const d = await res.json();
    form.name = d.name ?? '';
    form.description = d.description ?? '';
    form.agents = d.agents && d.agents.length ? d.agents : [...KNOWN_AGENTS];
    form.gates = d.gates && d.gates.length ? d.gates : [...KNOWN_GATES];
    form.output_format = d.output_format && d.output_format.length ? d.output_format : ['markdown'];
    form.ui_mode = d.ui_mode ?? 'unified_shell';
    const ar = await authFetch(`${API}/templates/${name.value}/audit`);
    const ad = await ar.json();
    audit.value = ad.items ?? [];
  } catch (e) {
    backendError.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

async function onSave(): Promise<void> {
  saving.value = true;
  backendError.value = '';
  const body = {
    name: form.name,
    id: isNew.value ? form.id : undefined,
    description: form.description,
    agents: form.agents,
    gates: form.gates,
    output_format: form.output_format,
    ui_mode: form.ui_mode,
  };
  try {
    const url = isNew.value ? `${API}/templates` : `${API}/templates/${name.value}`;
    const method = isNew.value ? 'POST' : 'PUT';
    const res = await authFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      const d = (e as any).detail ?? e;
      backendError.value =
        (d.message ?? '保存失败') + (d.errors && d.errors.length ? '：' + d.errors.join('；') : '');
      return;
    }
    ElMessage.success('已保存');
    router.push('/settings/templates');
  } finally {
    saving.value = false;
  }
}

function goList(): void {
  router.push('/settings/templates');
}

onMounted(loadOne);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.tpl-form {
  max-width: 640px;
}
</style>
