<template>
  <div class="page settings-page">
    <PageHead
      icon="User"
      title="Agent 提示词"
      sub="编辑各角色的行为定义（agents/*.md）。保存后下一个任务即生效——引擎每次运行都现读这些文件，无需重启容器。"
    />
    <StatStrip :stats="stats" />

    <!-- 角色切换 -->
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">选择角色</span>
          <span class="blk-sub">共 {{ agents.length }} 个可编辑角色（派生自目录，新增 md 即自动出现）</span>
        </div>
      </template>
      <div v-loading="loading">
        <el-radio-group v-model="current" size="large" @change="onSwitch">
          <el-radio-button v-for="a in agents" :key="a.name" :value="a.name">
            {{ a.title || a.name }}
          </el-radio-button>
        </el-radio-group>
        <div v-if="meta" class="ag-meta">
          文件 <code>agents/{{ current }}.md</code> ·
          {{ meta.chars }} 字符 / {{ meta.lines }} 行 ·
          最后更新 {{ fmtTime(meta.updated) }}
        </div>
      </div>
    </el-card>

    <!-- 编辑区 + 预览 -->
    <div v-loading="loading" class="ag-split">
      <el-card shadow="never" class="ag-pane blk">
        <template #header>
          <div class="blk-head">
            <span class="blk-title">编辑</span>
            <span class="blk-sub">
              Markdown · <span :class="{ 'ag-dirty': dirty }">{{ dirty ? '● 已修改未保存' : '○ 无改动' }}</span>
            </span>
          </div>
        </template>
        <textarea
          v-model="content"
          class="ag-editor"
          spellcheck="false"
          placeholder="在此编辑角色提示词（Markdown）"
          @keydown.tab.prevent="onTab"
        ></textarea>
      </el-card>

      <el-card shadow="never" class="ag-pane blk">
        <template #header>
          <div class="blk-head">
            <span class="blk-title">预览</span>
            <span class="blk-sub">marked 渲染 + DOMPurify 清洗</span>
          </div>
        </template>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="ag-preview" v-html="renderedHtml"></div>
      </el-card>
    </div>

    <!-- 校验提示 -->
    <el-alert
      v-if="errors.length" type="error" :closable="false" class="blk"
      title="校验未通过（保存已禁用）"
    >
      <ul class="ag-errlist"><li v-for="e in errors" :key="e">{{ e }}</li></ul>
    </el-alert>
    <el-alert
      v-else-if="warnings.length" type="warning" :closable="false" class="blk"
      title="提示（不影响保存）"
    >
      <ul class="ag-errlist"><li v-for="w in warnings" :key="w">{{ w }}</li></ul>
    </el-alert>

    <!-- 操作栏 -->
    <div class="ag-actions blk">
      <el-button type="primary" :disabled="!dirty || errors.length > 0" :loading="saving" @click="save">
        保存
      </el-button>
      <el-button :disabled="!dirty" @click="reset">撤销更改</el-button>
      <span v-if="lastSaved" class="ag-hint">上次保存：{{ lastSaved }}</span>
    </div>

    <!-- 审计 -->
    <el-collapse class="blk">
      <el-collapse-item :title="`改动历史（最近 ${auditItems.length} 条）`" name="audit">
        <el-empty v-if="!auditItems.length" description="暂无改动记录" :image-size="60" />
        <el-timeline v-else>
          <el-timeline-item
            v-for="it in auditItems" :key="it.ts"
            :type="it.validation === 'pass' ? 'success' : 'danger'"
            :timestamp="fmtTs(it.ts)"
          >
            <div>
              <el-tag size="small" :type="it.validation === 'pass' ? 'success' : 'danger'">
                {{ it.action }}
              </el-tag>
              <span class="ag-hint"> by {{ it.operator }}</span>
            </div>
            <div class="ag-hint" v-if="it.after?.chars !== undefined">
              {{ it.before?.chars ?? '-' }} → {{ it.after.chars }} 字符
              （{{ it.before?.lines ?? '-' }} → {{ it.after.lines }} 行）
            </div>
            <div v-if="it.errors?.length" class="ag-err">
              <div v-for="e in it.errors" :key="e">{{ e }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { onBeforeRouteLeave, useRoute } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import api from '@/api/client';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';
import { formatTime } from '@/utils/formatter';

interface AgentMeta {
  name: string;
  title: string;
  size: number;
  chars: number;
  lines: number;
  updated: string;
  validation: { ok: boolean; errors: string[]; warnings: string[] };
}
interface AuditItem {
  ts: string;
  operator: string;
  action: string;
  validation: string;
  errors?: string[];
  before?: { chars: number; lines: number };
  after?: { chars: number; lines: number };
}

const agents = ref<AgentMeta[]>([]);
const route = useRoute();
const current = ref('');
const content = ref('');
const original = ref('');        // 服务端原文，用于 dirty 判定与撤销
const loading = ref(false);
const saving = ref(false);
const lastSaved = ref('');
const auditItems = ref<AuditItem[]>([]);

const meta = computed(() => agents.value.find((a) => a.name === current.value));
const dirty = computed(() => content.value !== original.value);

const stats = computed(() => [
  { label: '可编辑角色', value: agents.value.length, tone: 'brand' as const, hint: '派生自目录' },
  { label: '当前字符数', value: meta.value?.chars ?? 0, tone: 'muted' as const, hint: 'agents/*.md' },
  {
    label: '校验',
    value: errors.value.length ? '异常' : warnings.value.length ? '提示' : '通过',
    tone: (errors.value.length ? 'danger' : warnings.value.length ? 'warn' : 'brand') as 'danger' | 'warn' | 'brand',
    hint: errors.value.length ? `${errors.value.length} 处待修正` : warnings.value.length ? `${warnings.value.length} 处提示` : '可保存',
  },
]);

/** 前端实时校验（与后端 server/admin.py::_validate_prompt 保持一致） */
const errors = computed<string[]>(() => {
  const errs: string[] = [];
  const c = content.value;
  if (!c.trim()) errs.push('提示词内容不能为空（清空会导致该角色失去行为定义）');
  if (c.length > 200000) errs.push(`内容过长：${c.length} 字符 > 上限 200000，疑似误粘贴`);
  return errs;
});

const warnings = computed<string[]>(() => {
  const w: string[] = [];
  const c = content.value;
  if (c.trim() && !/^\s*#\s+/m.test(c)) w.push('缺少一级标题（# ...）：引擎仍可运行，但不利于维护');
  if ((c.match(/```/g) || []).length % 2 === 1) w.push('代码块围栏 ``` 数量为奇数，疑似未闭合');
  return w;
});

/** marked 渲染后必须过 DOMPurify，杜绝 XSS（设计安全红线） */
const renderedHtml = computed(() =>
  DOMPurify.sanitize(marked.parse(content.value || '_（空）_', { async: false }) as string)
);

async function load(): Promise<void> {
  loading.value = true;
  try {
    const { data } = await api.get('/admin/agents');
    agents.value = data.items ?? [];
    // M9-1：市场页「配置」入口带 ?name=<id>，命中则直接定位到该角色
    const want = String((route.query.name as string) || '');
    if (want && agents.value.some((a) => a.name === want)) {
      current.value = want;
    } else if (!current.value && agents.value.length) {
      current.value = agents.value[0].name;
    }
    await loadContent();
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`);
  } finally {
    loading.value = false;
  }
}

async function loadContent(): Promise<void> {
  if (!current.value) return;
  try {
    const { data } = await api.get(`/admin/agents/${encodeURIComponent(current.value)}`);
    content.value = data.content ?? '';
    original.value = content.value;
    await loadAudit();
  } catch (e) {
    ElMessage.error(`读取角色失败：${(e as Error).message}`);
  }
}

async function loadAudit(): Promise<void> {
  if (!current.value) return;
  try {
    const { data } = await api.get(
      `/admin/agents/${encodeURIComponent(current.value)}/audit`,
      { params: { limit: 10 } }
    );
    auditItems.value = data.items ?? [];
  } catch {
    auditItems.value = [];     // 审计不可读不阻断主流程
  }
}

async function onSwitch(): Promise<void> {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('当前角色有未保存的修改，切换后将丢失。确定切换？', '未保存', {
        type: 'warning',
        confirmButtonText: '放弃修改并切换',
        cancelButtonText: '留在当前',
      });
    } catch {
      return;                  // 用户取消 —— 但 radio 已变更，需回滚视觉状态
    }
  }
  await loadContent();
}

async function save(): Promise<void> {
  if (errors.value.length) return;
  saving.value = true;
  try {
    const { data } = await api.put(`/admin/agents/${encodeURIComponent(current.value)}`, {
      content: content.value,
    });
    original.value = content.value;
    lastSaved.value = new Date().toLocaleString('zh-CN');
    ElMessage.success(data.changed ? '已保存，下一个任务生效' : '内容无变化，未写盘');
    await load();              // 刷新元信息（字符数/更新时间）与审计
  } catch (e) {
    const err = e as { response?: { data?: { detail?: { errors?: string[] } | string } } };
    const detail = err.response?.data?.detail;
    const msg = typeof detail === 'object' && detail?.errors
      ? detail.errors.join('；')
      : (detail as string) || (e as Error).message;
    ElMessage.error(`保存失败：${msg}`);
  } finally {
    saving.value = false;
  }
}

function reset(): void {
  content.value = original.value;
  ElMessage.info('已撤销未保存的修改');
}

/** Tab 键插入两个空格（否则焦点会跳出编辑器） */
function onTab(e: KeyboardEvent): void {
  const ta = e.target as HTMLTextAreaElement;
  const { selectionStart: s, selectionEnd: t, value } = ta;
  content.value = value.slice(0, s) + '  ' + value.slice(t);
  // DOM 更新后恢复光标
  requestAnimationFrame(() => {
    ta.selectionStart = ta.selectionEnd = s + 2;
  });
}

function fmtTime(iso: string): string {
  if (!iso) return '-';
  return formatTime(iso);
}

/** 审计时间戳形如 2026-09-08T05-12-33-123456Z */
function fmtTs(ts: string): string {
  if (!ts) return '';
  const m = ts.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : ts;
}

// 离开页面前拦截未保存修改（切角色 + 路由跳转都覆盖）
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm('有未保存的修改，确定离开本页？', '未保存', { type: 'warning' });
    return true;
  } catch {
    return false;
  }
});

// 关闭标签页/刷新拦截
function onBeforeUnload(e: BeforeUnloadEvent): void {
  if (dirty.value) {
    e.preventDefault();
    e.returnValue = '';
  }
}
window.addEventListener('beforeunload', onBeforeUnload);
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload));

onMounted(load);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.settings-page :deep(.ag-pane .el-card__body) { padding: 0; }
.ag-meta { margin-top: 12px; font-size: 12px; color: var(--ink-500); }
.ag-meta code {
  background: var(--surface-2); border: 1px solid var(--line);
  padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, Consolas, monospace;
}
.ag-split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 1100px) { .ag-split { grid-template-columns: 1fr; } }
.ag-editor {
  width: 100%; height: 520px; border: 0; outline: none; resize: vertical;
  padding: 14px 16px; box-sizing: border-box;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 13px; line-height: 1.7; color: var(--ink-900); background: var(--surface);
  tab-size: 2;
}
.ag-editor:focus { background: var(--surface-2); }
.ag-preview {
  height: 520px; overflow: auto; padding: 14px 16px; box-sizing: border-box;
  font-size: 13px; line-height: 1.8; color: var(--ink-700); background: var(--surface-2);
}
.ag-preview :deep(h1) { font-size: 18px; margin: 0 0 12px; color: var(--ink-900); }
.ag-preview :deep(h2) { font-size: 15px; margin: 18px 0 8px; }
.ag-preview :deep(h3) { font-size: 14px; margin: 14px 0 6px; }
.ag-preview :deep(code) {
  background: var(--surface); border: 1px solid var(--line);
  padding: 1px 5px; border-radius: 4px; font-size: 12px;
}
.ag-preview :deep(pre) {
  background: var(--surface); border: 1px solid var(--line);
  padding: 10px 12px; border-radius: 6px; overflow: auto;
}
.ag-preview :deep(ul), .ag-preview :deep(ol) { padding-left: 20px; }
.ag-dirty { color: var(--accent); font-weight: 600; }
.ag-errlist { margin: 6px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.8; }
.ag-actions { display: flex; align-items: center; gap: 12px; }
.ag-hint { font-weight: 400; font-size: 12px; color: var(--ink-400); }
.ag-err { color: var(--danger); font-size: 12px; margin-top: 4px; }
</style>
