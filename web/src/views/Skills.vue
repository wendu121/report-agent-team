<template>
  <div class="page">
    <PageHead
      icon="MagicStick"
      title="研报技能库"
      sub="浏览与启用研报分析框架技能。启用的技能会真实注入对应 Agent 的 system prompt（每次任务热加载，零重启）。"
    >
      <template #actions>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新增技能
        </el-button>
      </template>
    </PageHead>

    <!-- 概览：先看清技能库「有多少、开了多少、覆盖哪些 Agent」 -->
    <StatStrip :stats="stats" />

    <el-tabs v-model="tab" class="market-tabs">
      <el-tab-pane label="市场" name="market" />
      <el-tab-pane label="已安装" name="installed" />
    </el-tabs>

    <div class="filter-bar">
      <el-input v-model="keyword" placeholder="搜索名称 / 分类 / 描述" clearable class="search" />
      <span class="filter-count">共 {{ items.length }} 项 · 当前 {{ filtered.length }} 项</span>
    </div>

    <!-- 分类导航 chips（取代分类下拉；计数取自全量，不随搜索跳动） -->
    <div class="chip-row">
      <button
        type="button"
        class="chip"
        :class="{ 'is-active': !catFilter }"
        @click="catFilter = ''"
      >
        全部<span class="chip-n">{{ items.length }}</span>
      </button>
      <button
        v-for="c in allCategories"
        :key="c"
        type="button"
        class="chip"
        :class="{ 'is-active': catFilter === c }"
        @click="catFilter = catFilter === c ? '' : c"
      >
        {{ c }}<span class="chip-n">{{ countByCat(c) }}</span>
      </button>
    </div>

    <el-alert v-if="error" type="error" :closable="false" :title="error" />

    <div v-loading="loading">
      <section v-for="cat in categories" :key="cat" class="cat-section">
        <h3 v-if="!catFilter" class="cat-title">{{ cat }}<span class="count">{{ itemsByCat(cat).length }}</span></h3>
        <div class="card-grid">
          <EntityCard
            v-for="p in itemsByCat(cat)"
            :key="p.id"
            :icon="skillIcon(p.category)"
            :color="avatarColor(p.id)"
            :name="p.name || p.id"
            :sub="p.category"
            :desc="p.description || '（暂无描述）'"
          >
            <template #badges>
              <code class="id-chip">{{ p.id }}</code>
              <el-tag v-if="p.builtin" size="small" type="info">官方</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">自定义</el-tag>
            </template>
            <template #status>
              <el-tag :type="statusType(p.status)" size="small">{{ statusLabel(p.status) }}</el-tag>
            </template>
            <!-- 作用域可视化：这条技能会注入哪些 Agent 的 system prompt -->
            <template #extra>
              <div class="meta-row">
                <template v-if="p.target_roles && p.target_roles.length">
                  <span v-for="r in p.target_roles" :key="r" class="meta-chip">
                    <el-icon><component :is="agentIcon(r)" /></el-icon>{{ roleLabel(r) }}
                  </span>
                </template>
                <span v-else class="meta-chip is-plain">
                  <el-icon><Cpu /></el-icon>全部 Agent
                </span>
              </div>
            </template>
            <template #actions>
              <el-button
                v-if="!p.enabled"
                size="small" type="primary" plain
                @click="onEnable(p)"
              >启用</el-button>
              <el-button
                v-else
                size="small" @click="onDisable(p)"
              >停用</el-button>
              <el-button
                v-if="!p.builtin"
                size="small" type="danger" plain
                @click="onDelete(p)"
              >删除</el-button>
            </template>
          </EntityCard>
        </div>
      </section>
      <div v-if="!loading && !filtered.length" class="empty-state">
        <el-icon class="empty-state__icon"><MagicStick /></el-icon>
        <div class="empty-state__title">没有符合条件的技能</div>
        <div class="empty-state__hint">试试清空搜索关键词，或切换上方的分类</div>
      </div>
    </div>

    <!-- 新增技能 -->
    <el-dialog v-model="dialog" title="新增技能" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="id">
          <el-input v-model="form.id" placeholder="英文，如 esg_rating" />
          <div class="hint">只允许字母/数字/-/_，对应 skills/&lt;id&gt;.md 与 skills.yaml 条目</div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：ESG 评级分析" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" placeholder="如：ESG" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="作用 Agent">
          <el-select v-model="form.target_roles" multiple class="full">
            <el-option label="researcher（调研员）" value="researcher" />
            <el-option label="analyst（分析师）" value="analyst" />
            <el-option label="writer（撰稿人）" value="writer" />
          </el-select>
          <div class="hint">技能片段将注入这些 Agent 的 system prompt（作用域控制）</div>
        </el-form-item>
        <el-form-item label="技能片段">
          <el-input v-model="form.prompt" type="textarea" :rows="6" placeholder="写该技能的 prompt 框架片段，如分析步骤/结构要求。启用前必须有内容。" />
          <div class="hint">片段将写入 skills/&lt;id&gt;.md，拼入对应 Agent 的 system prompt（包裹【已启用技能：&lt;name&gt;】标记）</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="!form.id || !form.prompt.trim()" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { SkillItem } from '@/types';
import EntityCard from '@/components/EntityCard.vue';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';
import { skillIcon, agentIcon } from '@/utils/emoji';

const API = '/api/v1';
const items = ref<SkillItem[]>([]);
const loading = ref(false);
const error = ref('');
const tab = ref('market');
const keyword = ref('');
const catFilter = ref('');
const dialog = ref(false);

const form = reactive({
  id: '',
  name: '',
  category: '',
  description: '',
  target_roles: [] as string[],
  prompt: '',
});

const categories = computed(() =>
  Array.from(new Set(filtered.value.map((p) => p.category).filter(Boolean))).sort(),
);
function itemsByCat(cat: string): SkillItem[] {
  return filtered.value.filter((p) => p.category === cat);
}

// 分类 chips 选项：取全量 items（避免选中后选项随筛选折叠）
const allCategories = computed(() =>
  Array.from(new Set(items.value.map((p) => p.category).filter(Boolean))).sort(),
);
/** 分类计数取自全量，不随搜索跳动 */
function countByCat(c: string): number {
  return items.value.filter((p) => p.category === c).length;
}

/** 技能未声明作用域 = 对全部 Agent 生效（与后端注入语义一致） */
const ALL_ROLES = ['researcher', 'analyst', 'writer'];
function roleLabel(r: string): string {
  if (r === 'researcher') return '调研';
  if (r === 'analyst') return '分析';
  if (r === 'writer') return '撰稿';
  return r;
}

/**
 * 顶部概览：共几个 / 开了几个 / 覆盖哪些 Agent。
 * 「覆盖 Agent」只统计**已启用**技能的作用域——没生效的技能不算已覆盖。
 */
const stats = computed(() => {
  const all = items.value;
  const enabled = all.filter((p) => p.enabled);
  const covered = new Set<string>();
  for (const p of enabled) {
    const roles = p.target_roles?.length ? p.target_roles : ALL_ROLES;
    roles.forEach((r) => covered.add(r));
  }
  const cats = new Set(all.map((p) => p.category).filter(Boolean));
  return [
    { label: '技能总数', value: all.length, tone: 'muted' as const },
    { label: '已启用', value: enabled.length, tone: 'brand' as const, hint: '下个任务即注入' },
    {
      label: '覆盖 Agent',
      value: `${covered.size}/${ALL_ROLES.length}`,
      tone: (covered.size ? 'brand' : 'warn') as 'brand' | 'warn',
      hint: '按已启用技能统计',
    },
    { label: '分类', value: cats.size, tone: 'muted' as const },
  ];
});

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((p) => {
    if (tab.value === 'installed' && !p.enabled) return false;
    if (catFilter.value && p.category !== catFilter.value) return false;
    if (!kw) return true;
    return [p.id, p.name, p.description, p.category]
      .join(' ')
      .toLowerCase()
      .includes(kw);
  });
});

function statusType(s: SkillItem['status']): 'success' | 'info' {
  return s === 'active' ? 'success' : 'info';
}
function statusLabel(s: SkillItem['status']): string {
  return s === 'active' ? '已启用' : '未启用';
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${API}/skills`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const data = await res.json();
    items.value = (data.items ?? []) as SkillItem[];
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

function openCreate(): void {
  dialog.value = true;
}

async function onEnable(p: SkillItem): Promise<void> {
  await putSkill(p.id, { enabled: true });
}
async function onDisable(p: SkillItem): Promise<void> {
  await putSkill(p.id, { enabled: false });
}
async function putSkill(id: string, body: Record<string, unknown>): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/skills/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      const d = (e as any).detail ?? e;
      throw new Error([d.message, ...(d.errors || [])].filter(Boolean).join('；'));
    }
    ElMessage.success('已更新，下一个任务即生效');
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '更新失败');
  }
}

async function onCreate(): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form }),
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

async function onDelete(p: SkillItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除「${p.name || p.id}」？`, '删除确认', { type: 'warning' });
  } catch {
    return;
  }
  const res = await fetch(`${API}/admin/skills/${p.id}`, { method: 'DELETE' });
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
  color: #374151;
  margin: 0 0 12px;
  padding-left: 2px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.count {
  display: inline-block;
  padding: 1px 9px;
  font-size: 12px;
  font-weight: 600;
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-radius: 999px;
}
</style>
