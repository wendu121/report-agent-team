<template>
  <div class="page">
    <PageHead
      icon="Box"
      title="数据源插件"
      sub="浏览与接入研报数据源。启用的数据源会真实驱动引擎检索（每次任务热加载，零重启）。"
    >
      <template #actions>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新增数据源
        </el-button>
      </template>
    </PageHead>

    <!-- 概览：先看清「有几个真能用、还差什么」，再往下挑具体数据源 -->
    <StatStrip :stats="stats" />

    <el-tabs v-model="tab" class="market-tabs">
      <el-tab-pane label="市场" name="market" />
      <el-tab-pane label="已安装" name="installed" />
    </el-tabs>

    <div class="filter-bar">
      <el-input v-model="keyword" placeholder="搜索名称 / 分类 / 描述" clearable class="search" />
      <el-select v-model="sourceFilter" placeholder="全部鉴权方式" clearable class="shape">
        <el-option label="免鉴权" value="none" />
        <el-option label="需 API Key" value="api_key" />
        <el-option label="需登录 Cookie" value="oauth" />
      </el-select>
      <span class="filter-count">共 {{ items.length }} 项 · 当前 {{ filtered.length }} 项</span>
    </div>

    <!-- 分类导航 chips（取代分类下拉：一屏看全分类 + 计数，少一次点击） -->
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
            :icon="pluginIcon(p.category)"
            :color="avatarColor(p.id)"
            :name="p.name || p.id"
            :sub="`${p.category} · ${authLabel(p.auth_type)}`"
            :desc="p.description || '（暂无描述）'"
          >
            <template #badges>
              <code class="id-chip">{{ p.id }}</code>
              <el-tag v-if="p.builtin" size="small" type="info">官方</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">自定义</el-tag>
            </template>
            <template #status>
              <el-tag :type="pluginState(p).type" size="small" :title="pluginState(p).note">{{ pluginState(p).label }}</el-tag>
            </template>
            <template #actions>
              <!-- 已连接：可停用 -->
              <template v-if="p.status === 'connected'">
                <el-button size="small" @click="onDisable(p)">停用</el-button>
                <el-button v-if="!p.builtin" size="small" type="danger" plain @click="onDelete(p)">删除</el-button>
              </template>

              <!-- 已实现但需密钥（tavily 等真源）：启用 + 配置密钥 -->
              <template v-else-if="p.provider !== 'coming_soon' && p.auth_type === 'api_key'">
                <el-button v-if="!p.enabled" size="small" type="primary" plain @click="onEnable(p)">启用</el-button>
                <el-button v-else size="small" @click="onDisable(p)">停用</el-button>
                <el-button size="small" type="success" plain @click="openConnect(p)">配置密钥</el-button>
                <el-button v-if="!p.builtin" size="small" type="danger" plain @click="onDelete(p)">删除</el-button>
              </template>

              <!-- 未接入后端但可预配密钥（qcc 等）：给键盘输入，诚实标注待接入 -->
              <template v-else-if="p.provider === 'coming_soon' && p.auth_type === 'api_key'">
                <el-button size="small" type="success" plain @click="openConnect(p)">配置密钥</el-button>
                <el-button v-if="!p.builtin" size="small" type="danger" plain @click="onDelete(p)">删除</el-button>
              </template>

              <!-- oauth 类（雪球等）：真实录入 Cookie/会话凭证，诚实标注后端未接入 -->
              <template v-else-if="p.auth_type === 'oauth'">
                <el-button size="small" type="success" plain @click="openConnect(p)">配置凭证</el-button>
                <el-button v-if="!p.builtin" size="small" type="danger" plain @click="onDelete(p)">删除</el-button>
              </template>

              <!-- 未接入后端（none：cninfo/scholar 等）：明示未接入，给官网链接 -->
              <template v-else>
                <el-tag size="small" type="info" effect="plain">{{ pluginState(p).label }}</el-tag>
                <el-button v-if="!p.builtin" size="small" type="danger" plain @click="onDelete(p)">删除</el-button>
              </template>

              <!-- 通用：真实教程/官网链接（仅展示，不进引擎逻辑；不伪造 URL）。
                   两者独立显示：有教程给「获取密钥教程」，有官网给「打开官网」（可并存） -->
              <a v-if="p.key_guide_url" class="ext-link" :href="p.key_guide_url" target="_blank" rel="noopener">获取密钥教程 ↗</a>
              <a v-if="p.doc_url" class="ext-link" :href="p.doc_url" target="_blank" rel="noopener">打开官网 ↗</a>
            </template>
          </EntityCard>
        </div>
      </section>
      <div v-if="!loading && !filtered.length" class="empty-state">
        <el-icon class="empty-state__icon"><Box /></el-icon>
        <div class="empty-state__title">没有符合条件的数据源</div>
        <div class="empty-state__hint">试试清空搜索关键词，或切换上方的分类</div>
      </div>
    </div>

    <!-- 新增数据源 -->
    <el-dialog v-model="dialog" title="新增数据源" width="520px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="id">
          <el-input v-model="form.id" placeholder="英文，如 mynews" />
          <div class="hint">只允许字母/数字/-/_，对应引擎 provider 与密钥 DS_&lt;ID&gt;_API_KEY</div>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：我的新闻源" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" placeholder="如：新闻" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="auth_type">
          <el-select v-model="form.auth_type" class="full">
            <el-option label="none（无需密钥）" value="none" />
            <el-option label="api_key（需密钥）" value="api_key" />
            <el-option label="oauth（暂未接）" value="oauth" />
          </el-select>
        </el-form-item>
        <el-form-item label="provider">
          <el-select v-model="form.provider" class="full">
            <el-option label="coming_soon（占位，引擎跳过）" value="coming_soon" />
            <el-option label="mock（占位检索，带 source 标记）" value="mock" />
          </el-select>
          <div class="hint">新增真源须后端在 data_sources.PROVIDER_REGISTRY 登记；否则用 coming_soon 占位</div>
        </el-form-item>
        <el-form-item label="官网地址">
          <el-input v-model="form.doc_url" placeholder="如 https://www.qcc.com/（选填，真实根域）" />
          <div class="hint">用户「打开官网」拿密钥/登录的入口，留空则卡片无官网按钮</div>
        </el-form-item>
        <el-form-item label="密钥教程地址">
          <el-input v-model="form.key_guide_url" placeholder="如 https://www.qcc.com/open（选填，去哪拿 key/cookie）" />
          <div class="hint">卡片「获取密钥教程」链接；oauth 类可填官网登录页</div>
        </el-form-item>
        <el-form-item label="密钥变量名">
          <el-input v-model="form.key_env" placeholder="如 DS_QCC_API_KEY（选填，配置时的回填提示）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :disabled="!form.id" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 配置凭证（API Key / 会话 Cookie 二选一，按 auth_type 分支） -->
    <el-dialog v-model="connectDialog" title="配置数据源凭证" width="480px">
      <p class="sub" v-if="connectTarget?.auth_type === 'oauth'">为 <b>{{ connectTarget?.name }}</b> 配置会话 Cookie（写入 .secrets/plugins.env，不入库、不回显）。请先点下方「打开官网」登录，再从浏览器开发者工具复制 Cookie 粘贴。</p>
      <p class="sub" v-else>为 <b>{{ connectTarget?.name }}</b> 配置 API Key（写入 .secrets/plugins.env，不入库、不回显）</p>
      <div v-if="connectTarget?.doc_url" class="hint">
        🌐 打开官网登录拿凭证：<a :href="connectTarget.doc_url" target="_blank" rel="noopener">{{ connectTarget.doc_url }}</a>
      </div>
      <div v-if="connectTarget?.key_env" class="hint">密钥环境变量：<code>{{ connectTarget.key_env }}</code></div>

      <!-- api_key：密码框 -->
      <el-input
        v-if="connectTarget?.auth_type !== 'oauth'"
        v-model="secret"
        type="password"
        show-password
        :placeholder="connectTarget?.key_env || ('DS_' + ((connectTarget?.id || '').toUpperCase()) + '_API_KEY')"
      />
      <!-- oauth：多行 Cookie 粘贴框 -->
      <el-input
        v-else
        v-model="secret"
        type="textarea"
        :rows="4"
        placeholder="粘贴会话 Cookie（官网登录后，F12 → Network → 任意请求头复制 Cookie 值）"
      />

      <div v-if="connectTarget?.key_guide_url" class="hint">
        🔗 如何获取凭证：<a :href="connectTarget.key_guide_url" target="_blank" rel="noopener">{{ connectTarget.key_guide_url }}</a>
      </div>
      <div v-if="connectTarget?.provider === 'coming_soon'" class="warn-note">
        ⚠️ 该数据源后端 Provider 尚未接入，凭证先预留到 .secrets；后端实现接入后，下一个任务即生效（当前引擎仍会跳过）。
      </div>
      <template #footer>
        <el-button @click="connectDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!secret" @click="onConnect">保存凭证</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { authFetch } from '@/api/client';
import type { PluginItem } from '@/types';
import EntityCard from '@/components/EntityCard.vue';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';
import { pluginIcon } from '@/utils/emoji';

const API = '/api/v1';
const items = ref<PluginItem[]>([]);
const loading = ref(false);
const error = ref('');
const tab = ref('market');
const keyword = ref('');
const sourceFilter = ref('');
const catFilter = ref('');
const dialog = ref(false);
const connectDialog = ref(false);
const connectTarget = ref<PluginItem | null>(null);
const secret = ref('');

const form = reactive({
  id: '',
  name: '',
  category: '',
  description: '',
  auth_type: 'none' as PluginItem['auth_type'],
  provider: 'coming_soon' as string,
  doc_url: '',
  key_guide_url: '',
  key_env: '',
});

const categories = computed(() =>
  Array.from(new Set(filtered.value.map((p) => p.category).filter(Boolean))).sort(),
);
// 分类 chips 选项：用全量 items 派生（避免选中后选项折叠——Skills 的已知小问题在此规避）
const allCategories = computed(() =>
  Array.from(new Set(items.value.map((p) => p.category).filter(Boolean))).sort(),
);
/** 分类计数取自全量 items，不随搜索/筛选跳动（chip 上的数字必须稳定） */
function countByCat(c: string): number {
  return items.value.filter((p) => p.category === c).length;
}

/**
 * 顶部概览：只回答四个问题——共几个 / 几个真能用 / 几个只差密钥 / 几个后端没接。
 * 口径与 pluginState() 保持一致（不另造一套判断，避免与卡片上的状态标签自相矛盾）。
 */
const stats = computed(() => {
  const all = items.value;
  const connected = all.filter((p) => p.status === 'connected').length;
  const needKey = all.filter(
    (p) => p.auth_type === 'api_key' && p.provider !== 'coming_soon' && p.status !== 'connected'
  ).length;
  const notWired = all.filter((p) => p.provider === 'coming_soon').length;
  return [
    { label: '数据源总数', value: all.length, tone: 'muted' as const },
    { label: '已连接·引擎可取数', value: connected, tone: 'brand' as const, hint: '真实参与检索' },
    { label: '待配置密钥', value: needKey, tone: 'warn' as const, hint: '填 Key 后即可用' },
    { label: '后端未接入', value: notWired, tone: 'muted' as const, hint: '诚实标注·不冒充可用' },
  ];
});

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((p) => {
    if (tab.value === 'installed' && !p.enabled) return false;
    if (sourceFilter.value && p.auth_type !== sourceFilter.value) return false;
    if (catFilter.value && p.category !== catFilter.value) return false;
    if (!kw) return true;
    return [p.id, p.name, p.description, p.category]
      .join(' ')
      .toLowerCase()
      .includes(kw);
  });
});
function itemsByCat(cat: string): PluginItem[] {
  return filtered.value.filter((p) => p.category === cat);
}

// M10-P5：状态重解释——不再出现「即将推出」死端。
// 按 (status, provider, auth_type) 推导展示标签；provider=coming_soon 即「后端未接入」，
// api_key 类显式给「配置密钥」动作；none 类给官网链接。所有表述诚实，不冒充可用。
function pluginState(p: PluginItem): { label: string; type: 'success' | 'info' | 'warning' | 'danger'; note: string } {
  if (p.status === 'connected') {
    return { label: '已连接', type: 'success', note: '引擎会真实取数' };
  }
  if (p.status === 'credential_saved') {
    return { label: '凭证已保存·待接入', type: 'warning', note: 'Cookie 已预留到 .secrets，后端 Provider 待实现' };
  }
  if (p.provider === 'coming_soon') {
    if (p.auth_type === 'api_key') {
      return { label: '后端未接入·可预配密钥', type: 'warning', note: '后端 Provider 待实现，配置密钥后待接入即可生效' };
    }
    if (p.auth_type === 'oauth') {
      return { label: '后端未接入·需凭证', type: 'warning', note: '需会话 Cookie，后端 Provider 待实现' };
    }
    return { label: '后端未接入', type: 'info', note: '后端 Provider 待实现' };
  }
  if (p.auth_type === 'api_key') {
    return { label: '待配置密钥', type: 'warning', note: '配置 API Key 后引擎即取数' };
  }
  if (p.auth_type === 'oauth') {
    return { label: '待配置凭证', type: 'warning', note: '需会话 Cookie' };
  }
  return { label: '未连接', type: 'info', note: '' };
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = '';
  try {
    const res = await authFetch(`${API}/plugins`);
    if (!res.ok) throw new Error(`加载失败：${res.status}`);
    const data = await res.json();
    items.value = (data.items ?? []) as PluginItem[];
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

/** 鉴权方式 → 人话（none/api_key/oauth 是引擎内部标识，界面不该直接暴露） */
function authLabel(t: string): string {
  if (t === 'api_key') return '需 API Key';
  if (t === 'oauth') return '需登录 Cookie';
  return '免鉴权';
}

function openCreate(): void {
  dialog.value = true;
}
function openConnect(p: PluginItem): void {
  connectTarget.value = p;
  secret.value = '';
  connectDialog.value = true;
}

async function onEnable(p: PluginItem): Promise<void> {
  await putPlugin(p.id, { enabled: true });
}
async function onDisable(p: PluginItem): Promise<void> {
  await putPlugin(p.id, { enabled: false });
}
async function putPlugin(id: string, body: Record<string, unknown>): Promise<void> {
  try {
    const res = await authFetch(`${API}/admin/plugins/${id}`, {
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
    const res = await authFetch(`${API}/admin/plugins`, {
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

async function onConnect(): Promise<void> {
  if (!connectTarget.value) return;
  const target = connectTarget.value;
  // api_key → {api_key}；oauth → {cookie}（后端按 auth_type 写入 DS_<ID>_API_KEY / DS_<ID>_COOKIE）
  const payload = target.auth_type === 'oauth'
    ? { cookie: secret.value }
    : { api_key: secret.value };
  try {
    const res = await authFetch(`${API}/admin/plugins/${target.id}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      const d = (e as any).detail ?? e;
      throw new Error([d.message, ...(d.errors || [])].filter(Boolean).join('；'));
    }
    ElMessage.success(target.auth_type === 'oauth' ? '凭证已保存' : '已连接');
    connectDialog.value = false;
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '连接失败');
  }
}

async function onDelete(p: PluginItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除「${p.name || p.id}」？`, '删除确认', { type: 'warning' });
  } catch {
    return;
  }
  const res = await authFetch(`${API}/admin/plugins/${p.id}`, { method: 'DELETE' });
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
.ext-link {
  font-size: 12px;
  color: #2563eb;
  text-decoration: none;
  margin-left: 4px;
}
.ext-link:hover {
  text-decoration: underline;
}
.warn-note {
  margin-top: 10px;
  padding: 8px 10px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  font-size: 12px;
  color: #92400e;
  line-height: 1.5;
}
.hint code {
  background: #f3f4f6;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: #374151;
}
</style>
