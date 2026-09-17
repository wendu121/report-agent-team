<template>
  <div class="page">
    <PageHead
      icon="Promotion"
      title="研报推送渠道"
      sub="浏览与配置研报完成后的推送渠道。启用 + 端点已填的渠道会在研报完成(on_complete)/升级(on_gate_fail)时真实外发（每次任务热加载，零重启）。"
    >
      <template #actions>
        <el-button type="primary" @click="openCreate">+ 新增渠道</el-button>
      </template>
    </PageHead>

    <el-tabs v-model="tab" class="market-tabs">
      <el-tab-pane label="全部" name="all" />
      <el-tab-pane label="已启用" name="enabled" />
    </el-tabs>

    <div class="filter-bar">
      <el-input v-model="keyword" placeholder="搜索名称 / 分类 / 描述" clearable class="search" />
      <el-select v-model="catFilter" placeholder="全部分类" clearable class="shape">
        <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
      </el-select>
    </div>

    <el-alert v-if="error" type="error" :closable="false" :title="error" />

    <div v-loading="loading" class="card-grid">
      <EntityCard
        v-for="p in filtered"
        :key="p.id"
        :icon="channelIcon(p.name)"
        :brand="brandKey(p.name) || undefined"
        :color="avatarColor(p.id)"
        :name="p.name || p.id"
        :sub="`${p.category} · ${typeLabel(p.channel_type)} · ${strategyText(p.strategy)}`"
        :desc="p.description || '（暂无描述）'"
        :class="{ 'is-disabled': p.status === 'coming_soon' }"
      >
        <template #badges>
          <code class="id-chip">{{ p.id }}</code>
          <el-tag v-if="p.builtin" size="small" type="info">内置</el-tag>
        </template>
        <template #status>
          <el-tag :type="statusType(p.status)" size="small">{{ statusLabel(p.status) }}</el-tag>
        </template>
        <template #extra>
          <!-- webhook 端点配置（内置/自定义均可填用户自己的 webhook 地址） -->
          <div v-if="p.channel_type === 'webhook'" class="endpoint-row">
            <el-input
              v-model="editEndpoint[p.id]"
              size="small"
              placeholder="webhook 地址（填后启用才会真实外发）"
              @input="markDirty(p.id)"
            />
            <el-button size="small" @click="onSaveEndpoint(p)">保存端点</el-button>
          </div>
          <div v-else-if="p.status === 'coming_soon'" class="coming-soon-note">
            该渠道尚未接入，引擎永不推送（不冒充可用）。
          </div>
        </template>
        <template #actions>
          <el-button
            v-if="p.status !== 'coming_soon' && !p.enabled"
            size="small" type="primary" plain
            @click="onEnable(p)"
          >启用</el-button>
          <el-button
            v-if="p.status !== 'coming_soon' && p.enabled"
            size="small" @click="onDisable(p)"
          >停用</el-button>
          <el-button
            v-if="p.status !== 'coming_soon'"
            size="small" type="success" plain
            @click="onTest(p)"
          >测试发送</el-button>
          <el-button
            v-if="!p.builtin && p.status !== 'coming_soon'"
            size="small" type="danger" plain
            @click="onDelete(p)"
          >删除</el-button>
        </template>
      </EntityCard>
      <div v-if="!loading && !filtered.length" class="empty">没有符合条件的渠道</div>
    </div>

    <!-- 新增渠道 -->
    <el-dialog v-model="dialog" title="新增推送渠道" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="id">
          <el-input v-model="form.id" placeholder="英文，如 slack" />
          <div class="hint">只允许字母/数字/-/_，对应 channels.yaml 条目</div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：Slack" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" placeholder="如：即时通讯" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.channel_type" class="full">
            <el-option label="webhook（群机器人，需 URL）" value="webhook" />
            <el-option label="mock（本地测试，落盘不发送）" value="mock" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.channel_type === 'webhook'" label="消息格式">
          <el-select v-model="form.payload_format" class="full">
            <el-option label="dingtalk（钉钉 markdown）" value="dingtalk" />
            <el-option label="feishu（飞书 text）" value="feishu" />
            <el-option label="wecom（企业微信 markdown）" value="wecom" />
            <el-option label="discord（content）" value="discord" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发策略">
          <el-select v-model="form.strategy" multiple class="full">
            <el-option label="研报完成 on_complete" value="on_complete" />
            <el-option label="升级告警 on_gate_fail" value="on_gate_fail" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.channel_type === 'webhook'" label="webhook 地址">
          <el-input v-model="form.endpoint" placeholder="https://..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="!form.id || !form.name" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { ChannelItem } from '@/types';
import EntityCard from '@/components/EntityCard.vue';
import PageHead from '@/components/PageHead.vue';
import { channelIcon } from '@/utils/emoji';
import { brandKey } from '@/utils/brands';

const API = '/api/v1';
const items = ref<ChannelItem[]>([]);
const loading = ref(false);
const error = ref('');
const tab = ref('all');
const keyword = ref('');
const catFilter = ref('');
const dialog = ref(false);
const editEndpoint = reactive<Record<string, string>>({});
const dirty = reactive<Record<string, boolean>>({});

const form = reactive({
  id: '',
  name: '',
  category: '',
  description: '',
  channel_type: 'webhook',
  payload_format: 'discord',
  strategy: ['on_complete'] as string[],
  endpoint: '',
});

const categories = computed(() =>
  Array.from(new Set(items.value.map((p) => p.category).filter(Boolean))).sort(),
);

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((p) => {
    if (tab.value === 'enabled' && !p.enabled) return false;
    if (catFilter.value && p.category !== catFilter.value) return false;
    if (!kw) return true;
    return [p.id, p.name, p.description, p.category, p.channel_type]
      .join(' ')
      .toLowerCase()
      .includes(kw);
  });
});

function statusType(s: ChannelItem['status']): 'success' | 'info' | 'warning' {
  return s === 'connected' ? 'success' : s === 'coming_soon' ? 'warning' : 'info';
}
function statusLabel(s: ChannelItem['status']): string {
  return s === 'connected' ? '已连接' : s === 'coming_soon' ? '即将推出' : '未连接';
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${API}/channels`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const data = await res.json();
    items.value = (data.items ?? []) as ChannelItem[];
    // 初始化端点编辑框
    for (const p of items.value) {
      if (!(p.id in editEndpoint)) editEndpoint[p.id] = p.endpoint || '';
    }
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

/** 渠道类型 → 人话（避免卡片副标题直接暴露 webhook/mock 这类内部标识） */
function typeLabel(t: string): string {
  if (t === 'webhook') return '群机器人 webhook';
  if (t === 'mock') return '本地模拟（不真发）';
  // 接口实测里 telegram/wechat 的 channel_type 就是 coming_soon：
  // 绝不能把内部标识原样吐给用户，也不能让它看起来像可用渠道。
  if (t === 'coming_soon') return '尚未接入';
  return t || '未知类型';
}

/** 触发策略 → 人话；空策略说明永不外发，必须显式暴露而非留白 */
function strategyText(strategy?: string[]): string {
  const list = strategy || [];
  if (!list.length) return '无触发策略（永不外发）';
  return list
    .map((s) => (s === 'on_complete' ? '完成时推送' : s === 'on_gate_fail' ? '升级时告警' : s))
    .join(' / ');
}

function markDirty(id: string): void {
  dirty[id] = true;
}

function openCreate(): void {
  dialog.value = true;
}

async function onEnable(p: ChannelItem): Promise<void> {
  await putChannel(p.id, { enabled: true });
}
async function onDisable(p: ChannelItem): Promise<void> {
  await putChannel(p.id, { enabled: false });
}
async function onSaveEndpoint(p: ChannelItem): Promise<void> {
  if (!editEndpoint[p.id] || !editEndpoint[p.id].trim()) {
    ElMessage.warning('请先填写 webhook 地址');
    return;
  }
  await putChannel(p.id, { endpoint: editEndpoint[p.id].trim() });
  dirty[p.id] = false;
}
async function putChannel(id: string, body: Record<string, unknown>): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/channels/${id}`, {
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

async function onTest(p: ChannelItem): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/channels/${p.id}/test`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = (data as any).detail ?? data;
      throw new Error([d.message, ...(d.errors || [])].filter(Boolean).join('；') || '测试失败');
    }
    const r = (data as any).result ?? {};
    ElMessage.success(`测试发送成功：${r.detail || 'ok'}`);
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '测试失败');
  }
}

async function onCreate(): Promise<void> {
  try {
    const res = await fetch(`${API}/admin/channels`, {
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

async function onDelete(p: ChannelItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除「${p.name || p.id}」？`, '删除确认', { type: 'warning' });
  } catch {
    return;
  }
  const res = await fetch(`${API}/admin/channels/${p.id}`, { method: 'DELETE' });
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
.is-disabled {
  opacity: 0.7;
}
.endpoint-row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.coming-soon-note {
  font-size: 12px;
  color: #f59e0b;
  margin-bottom: 10px;
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
</style>
