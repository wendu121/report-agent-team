<template>
  <div class="page">
    <PageHead
      icon="Cpu"
      title="智能体市场"
      sub="浏览与新增研报智能体。新增的角色会真实驱动流水线（引擎每次任务热加载，零重启）。"
    >
      <template #actions>
        <el-button type="primary" @click="openCreate">+ 新增智能体</el-button>
      </template>
    </PageHead>

    <el-tabs v-model="tab" class="market-tabs">
      <el-tab-pane label="公开" name="public" />
      <el-tab-pane label="个人" name="private" />
    </el-tabs>

    <div class="filter-bar">
      <el-input v-model="keyword" placeholder="搜索名称 / 标签 / 描述" clearable class="search" />
      <el-select v-model="shapeFilter" placeholder="全部形态" clearable class="shape">
        <el-option label="研究员 researcher" value="researcher" />
        <el-option label="分析师 analyst" value="analyst" />
        <el-option label="撰稿人 writer" value="writer" />
      </el-select>
    </div>

    <el-alert v-if="error" type="error" :closable="false" :title="error" />

    <div v-loading="loading">
      <section v-if="featured.length" class="cat-section">
        <h3 class="cat-title">精选</h3>
        <div class="card-grid">
          <EntityCard
            v-for="a in featured"
            :key="a.id"
            :icon="agentIcon(a.shape)"
            :color="avatarColor(a.id)"
            :name="a.name || a.id"
            :sub="`${shapeLabel(a.shape)} · 审核闸 ${a.gate}`"
            :desc="a.description || '（暂无描述）'"
            :tags="a.tags"
          >
            <template #badges>
              <code class="id-chip">{{ a.id }}</code>
              <el-tag size="small" type="info">官方</el-tag>
            </template>
            <template #actions>
              <el-button size="small" type="primary" plain @click="goChat(a)">对话</el-button>
              <el-button size="small" @click="goConfig(a)">配置</el-button>
              <el-button v-if="!a.builtin" size="small" type="danger" plain @click="onDelete(a)">删除</el-button>
            </template>
          </EntityCard>
        </div>
      </section>
      <section v-if="others.length" class="cat-section">
        <h3 class="cat-title">更多</h3>
        <div class="card-grid">
          <EntityCard
            v-for="a in others"
            :key="a.id"
            :icon="agentIcon(a.shape)"
            :color="avatarColor(a.id)"
            :name="a.name || a.id"
            :sub="`${shapeLabel(a.shape)} · 审核闸 ${a.gate}`"
            :desc="a.description || '（暂无描述）'"
            :tags="a.tags"
          >
            <template #badges>
              <code class="id-chip">{{ a.id }}</code>
              <el-tag size="small" type="warning" effect="plain">自定义</el-tag>
            </template>
            <template #actions>
              <el-button size="small" type="primary" plain @click="goChat(a)">对话</el-button>
              <el-button size="small" @click="goConfig(a)">配置</el-button>
              <el-button v-if="!a.builtin" size="small" type="danger" plain @click="onDelete(a)">删除</el-button>
            </template>
          </EntityCard>
        </div>
      </section>
      <div v-if="!loading && !filtered.length" class="empty">没有符合条件的智能体</div>
    </div>

    <!-- 新增向导 -->
    <el-dialog v-model="dialog" title="新增智能体" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：代码分析师" />
        </el-form-item>
        <el-form-item label="角色逻辑名">
          <el-input v-model="form.id" placeholder="英文，如 Coder" @change="onIdChange" />
          <div class="hint">只允许字母/数字/-/_，用于 agents/&lt;id&gt;.md 与 registry 键</div>
        </el-form-item>
        <el-form-item label="形态 shape">
          <el-select v-model="form.shape" class="full">
            <el-option label="researcher（检索 → retrieval_records）" value="researcher" />
            <el-option label="analyst（分析 → analysis_conclusions）" value="analyst" />
            <el-option label="writer（撰稿 → draft_segments）" value="writer" />
          </el-select>
          <div class="hint">决定工具分发、产出字段与闸机器校验形态（不可自由填产出字段）</div>
        </el-form-item>
        <el-form-item label="闸门名">
          <el-input v-model="form.gate" placeholder="如 GateD" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="tagsInput" placeholder="逗号分隔，如 代码,工程" />
        </el-form-item>
        <el-divider content-position="left">模型绑定</el-divider>
        <el-form-item label="角色 base">
          <el-input v-model="form.role_base" />
        </el-form-item>
        <el-form-item label="角色 model">
          <el-input v-model="form.role_model" />
        </el-form-item>
        <el-form-item label="闸门 base">
          <el-input v-model="form.gate_base" />
        </el-form-item>
        <el-form-item label="闸门 model">
          <el-input v-model="form.gate_model" />
        </el-form-item>
        <el-alert
          v-if="sameBase"
          type="error"
          :closable="false"
          title="硬约束违反：闸门与所审角色必须异基座，否则等于自审包庇"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="sameBase || !form.id" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { AgentLibItem } from '@/types';
import EntityCard from '@/components/EntityCard.vue';
import PageHead from '@/components/PageHead.vue';
import { agentIcon } from '@/utils/emoji';

const API = '/api/v1';
const router = useRouter();
const items = ref<AgentLibItem[]>([]);
const loading = ref(false);
const error = ref('');
const tab = ref('public');
const keyword = ref('');
const shapeFilter = ref('');
const dialog = ref(false);
const tagsInput = ref('');

const form = reactive({
  id: '',
  name: '',
  shape: 'researcher' as AgentLibItem['shape'],
  gate: '',
  description: '',
  role_base: 'custom',
  role_model: 'LongCat-2.0',
  gate_base: 'cloudflare',
  gate_model: 'llama-3.3-70b',
});

const sameBase = computed(() => form.role_base.trim() !== '' && form.role_base === form.gate_base);

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((a) => {
    if (tab.value === 'public' && a.visibility !== 'public') return false;
    if (tab.value === 'private' && a.visibility === 'public') return false;
    if (shapeFilter.value && a.shape !== shapeFilter.value) return false;
    if (!kw) return true;
    return [a.id, a.name, a.description, (a.tags || []).join(',')]
      .join(' ')
      .toLowerCase()
      .includes(kw);
  });
});

const featured = computed(() => filtered.value.filter((a) => a.builtin));
const others = computed(() => filtered.value.filter((a) => !a.builtin));

async function load(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${API}/agents-library`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const data = await res.json();
    items.value = (data.items ?? []) as AgentLibItem[];
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败';
  } finally {
    loading.value = false;
  }
}

function avatarColor(id: string): string {
  const palette = ['#10B981', '#3B82F6', '#8B5CF6', '#F59E0B', '#EF4444', '#06B6D4'];
  let sum = 0;
  for (const ch of id) sum += ch.charCodeAt(0);
  return palette[sum % palette.length];
}

/** 形态 → 人话（引擎内部用 researcher/analyst/writer，界面不直接暴露） */
function shapeLabel(shape?: string): string {
  if (shape === 'researcher') return '检索研究员';
  if (shape === 'analyst') return '分析师';
  if (shape === 'writer') return '撰稿人';
  return shape || '未知形态';
}

function goChat(a: AgentLibItem): void {
  // M9-5 闭环：市场「对话」回跳至对话入口，预置该 Agent 为编排子集
  router.push({ path: '/', query: { agents: a.id } });
}
function goConfig(a: AgentLibItem): void {
  router.push({ path: '/settings/agents', query: { name: a.id.toLowerCase() } });
}

function onIdChange(): void {
  if (!form.gate && form.id) form.gate = `Gate${form.id.slice(0, 1).toUpperCase()}`;
}
function openCreate(): void {
  dialog.value = true;
}

async function onCreate(): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/agents-library`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...form,
        tags: tagsInput.value
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      const d = (e as any).detail ?? e;
      throw new Error([d.message, ...(d.errors || [])].filter(Boolean).join('；'));
    }
    ElMessage.success('已创建，下一个任务即生效');
    dialog.value = false;
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败');
  }
}

async function onDelete(a: AgentLibItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除「${a.name || a.id}」？将同时清理 prompt / 闸门 / 模型映射 / 注册表`,
      '删除确认',
      { type: 'warning' }
    );
  } catch {
    return;
  }
  const res = await fetch(`${API}/admin/agents-library/${a.id}`, { method: 'DELETE' });
  if (res.ok) {
    ElMessage.success('已删除');
    await load();
  } else {
    const e = await res.json().catch(() => ({}));
    const d = (e as any).detail ?? e;
    ElMessage.error([d.message, ...(d.errors || [])].filter(Boolean).join('；') || '删除失败');
  }
}

onMounted(load);
</script>

<style scoped>
.sub {
  margin: 0;
  color: var(--ink-500);
  font-size: 13px;
  line-height: 1.5;
}
.search {
  max-width: 320px;
}
.shape {
  width: 220px;
}
.hint {
  font-size: 12px;
  color: #6b7280;
}
.full {
  width: 100%;
}
.empty {
  grid-column: 1 / -1;
  color: var(--ink-500);
  padding: 24px 0;
}
.cat-section {
  margin-bottom: 24px;
}
.cat-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-700);
  margin: 0 0 12px;
  padding-left: 2px;
}
</style>
