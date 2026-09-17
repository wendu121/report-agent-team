<template>
  <div class="page settings-page">
    <PageHead
      icon="Medal"
      title="专家团"
      sub="从 URL 转化技能包 / 启停专家 / 审批提案。专家是上层封装，执行时展开为 角色 + 方法点 + 包内技能，交给现有 Agent 流水线。"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      </template>
    </PageHead>
    <StatStrip :stats="stats" />

    <el-alert v-if="formError" type="error" :closable="false" class="blk">{{ formError }}</el-alert>

    <!-- 从 URL 导入 -->
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">从 URL 导入</span>
          <span class="blk-sub">确定性启发式转化：抓取 → 拆 frontmatter → 生成技能包草案（待审批）</span>
        </div>
      </template>
      <el-form :model="convertForm" label-width="96px" size="default" @submit.prevent>
        <el-form-item label="URL" required>
          <el-input
            v-model="convertForm.url"
            placeholder="专家包 / Agent MD 的源地址，例如 https://.../my-expert.md"
            @keyup.enter="onConvert"
          />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="convertForm.name" placeholder="留空 = 用 URL 中的标题 / 文件名" />
        </el-form-item>
        <el-form-item label="形态 shape">
          <el-select v-model="convertForm.shape" style="width: 100%" placeholder="留空 = 启发式默认">
            <el-option label="researcher（研究型）" value="researcher" />
            <el-option label="analyst（分析型）" value="analyst" />
            <el-option label="writer（写作型）" value="writer" />
          </el-select>
        </el-form-item>
        <el-form-item label="team 降级">
          <el-switch
            v-model="convertForm.allow_team_downgrade"
            active-text="允许"
            inactive-text="拒绝"
          />
          <div class="form-hint">源为 team 型且本引擎无多角色并发等价物时，是否降级为「单专家 + 主理人提示」（语义有损）。</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="converting" @click="onConvert">转化为提案</el-button>
        </el-form-item>
      </el-form>
      <div v-if="convertMsg" class="note-ok">✓ {{ convertMsg }}</div>
      <div v-if="convertError" class="note-err">⚠ {{ convertError }}</div>
    </el-card>

    <!-- 专家列表 -->
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">已注册专家（{{ expertCount }}）</span>
          <el-switch
            v-model="enabledOnly"
            active-text="仅启用"
            inactive-text="全部"
            inline-prompt
            @change="loadExperts"
          />
        </div>
      </template>
      <el-table :data="experts" size="default" row-key="id">
        <el-table-column label="名称" min-width="160">
          <template #default="{ row }">
            <div class="sc-name">{{ row.display_name || row.id }}</div>
            <div v-if="row.profession" class="sc-sub">{{ row.profession }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="150" />
        <el-table-column label="类型" width="96">
          <template #default="{ row }">
            <el-tag :type="row.expert_type === 'team' ? 'warning' : 'success'" size="small">
              {{ row.expert_type === 'team' ? 'team' : 'agent' }}
            </el-tag>
            <el-tag v-if="row.degraded_team" type="info" size="small" class="sc-ml">已降级</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="shape" label="shape" width="96" />
        <el-table-column prop="model" label="模型" width="120">
          <template #default="{ row }">
            <span class="sc-sub">{{ row.model || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="源 URL" min-width="180">
          <template #default="{ row }">
            <a v-if="row.plugin?.source_url || row.dir" class="sc-link" :href="row.plugin?.source_url" target="_blank" rel="noopener">
              {{ row.plugin?.source_url || row.dir }}
            </a>
            <span v-else class="sc-sub">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :loading="row._toggling"
              @change="(val: boolean) => onToggle(row, val)"
            />
          </template>
        </el-table-column>
      </el-table>
      <div v-if="loading" class="sc-empty">加载中…</div>
      <div v-else-if="experts.length === 0" class="sc-empty">暂无专家，用上方「从 URL 导入」转化一个。</div>
    </el-card>

    <!-- 待审批提案 -->
    <el-card shadow="never" class="blk">
      <template #header>
        <div class="blk-head">
          <span class="blk-title">待审批提案（{{ proposals.length }}）</span>
          <el-button size="small" text :icon="Refresh" @click="loadProposals">刷新提案</el-button>
        </div>
      </template>
      <el-table :data="proposals" size="default" row-key="id">
        <el-table-column label="名称" min-width="150">
          <template #default="{ row }">
            <div class="sc-name">{{ row.name }}</div>
            <div class="sc-sub">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="96">
          <template #default="{ row }">
            <el-tag :type="row.expert_type === 'team' ? 'warning' : 'success'" size="small">
              {{ row.expert_type === 'team' ? 'team' : 'agent' }}
            </el-tag>
            <el-tag v-if="row.allow_team_downgrade" type="info" size="small" class="sc-ml">可降级</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="shape" label="shape" width="96" />
        <el-table-column label="源 URL" min-width="180">
          <template #default="{ row }">
            <a v-if="row.source_url" class="sc-link" :href="row.source_url" target="_blank" rel="noopener">
              {{ row.source_url }}
            </a>
            <span v-else class="sc-sub">—</span>
          </template>
        </el-table-column>
        <el-table-column label="取证" min-width="120">
          <template #default="{ row }">
            <span v-if="row.provenance?.source_type" class="sc-sub">{{ row.provenance.source_type }}</span>
            <span v-if="row.provenance?.fetched_at" class="sc-sub">{{ row.provenance.fetched_at }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button
              type="success"
              link
              size="small"
              :loading="row._accepting"
              @click="onAccept(row)"
            >通过</el-button>
            <el-button
              type="danger"
              link
              size="small"
              :loading="row._rejecting"
              @click="onReject(row)"
            >驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="proposalsLoading" class="sc-empty">加载中…</div>
      <div v-else-if="proposals.length === 0" class="sc-empty">暂无待审批提案。</div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Refresh } from '@element-plus/icons-vue';
import {
  listExperts,
  toggleExpert,
  convertExpert,
  listProposals,
  acceptProposal,
  rejectProposal,
  type ExpertItem,
  type ProposalItem,
} from '@/services/expertService';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

// 表格行：在后端类型上附加本地 UI 标志位（_toggling / _accepting / _rejecting）
type ExpertRow = ExpertItem & { _toggling?: boolean };
type ProposalRow = ProposalItem & { _accepting?: boolean; _rejecting?: boolean };

const experts = ref<ExpertRow[]>([]);
const proposals = ref<ProposalRow[]>([]);
const expertCount = ref(0);
const loading = ref(false);
const proposalsLoading = ref(false);
const enabledOnly = ref(false);
const formError = ref<string | null>(null);

const stats = computed(() => [
  { label: '专家总数', value: expertCount.value, tone: 'brand' as const, hint: '当前注册' },
  { label: '启用中', value: experts.value.filter((e) => e.enabled).length, tone: 'muted' as const, hint: '参与编排' },
  { label: '待审批提案', value: proposals.value.length, tone: 'warn' as const, hint: '待你通过 / 驳回' },
]);

const convertForm = reactive({
  url: '',
  name: '',
  shape: '',
  allow_team_downgrade: false,
});
const converting = ref(false);
const convertError = ref<string | null>(null);
const convertMsg = ref<string | null>(null);

function msgOf(e: unknown): string {
  const anyE = e as { response?: { data?: { detail?: { message?: string } } }; message?: string };
  const detail = anyE?.response?.data?.detail?.message;
  return detail || anyE?.message || '未知错误';
}

async function loadExperts(): Promise<void> {
  loading.value = true;
  formError.value = null;
  try {
    experts.value = await listExperts(enabledOnly.value);
    expertCount.value = experts.value.length;
  } catch (e) {
    formError.value = `加载专家失败：${msgOf(e)}`;
  } finally {
    loading.value = false;
  }
}

async function loadProposals(): Promise<void> {
  proposalsLoading.value = true;
  try {
    proposals.value = await listProposals();
  } catch (e) {
    ElMessage.error(`加载提案失败：${msgOf(e)}`);
  } finally {
    proposalsLoading.value = false;
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadExperts(), loadProposals()]);
}

async function onToggle(row: ExpertItem & Record<string, unknown>, val: boolean): Promise<void> {
  const old = !!row.enabled;
  row._toggling = true;
  try {
    await toggleExpert(row.id, val);
    row.enabled = val;
    ElMessage.success(`已${val ? '启用' : '禁用'} ${row.display_name || row.id}`);
  } catch (e) {
    row.enabled = old; // 失败回滚开关状态
    ElMessage.error(`启停失败：${msgOf(e)}`);
  } finally {
    row._toggling = false;
  }
}

async function onConvert(): Promise<void> {
  convertError.value = null;
  convertMsg.value = null;
  const url = convertForm.url.trim();
  if (!url) {
    convertError.value = 'URL 必填';
    return;
  }
  converting.value = true;
  try {
    const res = await convertExpert({
      url,
      name: convertForm.name.trim() || undefined,
      shape: convertForm.shape || undefined,
      allow_team_downgrade: convertForm.allow_team_downgrade || undefined,
    });
    const et = res.expert_type === 'team' ? 'team（多角色，需审批降级）' : 'agent';
    convertMsg.value = `已生成草案（类型 ${et}），待审批。提案 ID：${res.id}`;
    ElMessage.success('转化成功，进入待审批列表');
    await loadProposals();
  } catch (e) {
    convertError.value = msgOf(e);
  } finally {
    converting.value = false;
  }
}

async function onAccept(row: ProposalItem & Record<string, unknown>): Promise<void> {
  row._accepting = true;
  try {
    await acceptProposal(row.id);
    ElMessage.success(`已通过并注册：${row.name}`);
    await loadAll();
  } catch (e) {
    ElMessage.error(`审批失败：${msgOf(e)}`);
  } finally {
    row._accepting = false;
  }
}

async function onReject(row: ProposalRow): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定驳回提案「${row.name}（${row.id}）」？该操作不可撤销。`,
      '驳回提案',
      { type: 'warning', confirmButtonText: '驳回', cancelButtonText: '取消' },
    );
  } catch {
    return; // 用户取消
  }
  row._rejecting = true;
  try {
    await rejectProposal(row.id);
    ElMessage.success('已驳回');
    await loadAll();
  } catch (e) {
    ElMessage.error(`驳回失败：${msgOf(e)}`);
  } finally {
    row._rejecting = false;
  }
}

onMounted(loadAll);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.settings-page :deep(.el-table) {
  --el-table-border-color: var(--line);
  --el-table-header-bg-color: var(--surface-2);
}
.sc-sub { font-size: 11px; color: var(--ink-400); line-height: 1.5; word-break: break-all; }
.sc-name { font-size: 13px; color: var(--ink-900); font-weight: 500; }
.sc-ml { margin-left: 4px; }
.sc-link { color: var(--brand); font-size: 12px; text-decoration: none; word-break: break-all; }
.sc-link:hover { text-decoration: underline; }
.sc-empty { padding: 24px 0; text-align: center; color: var(--ink-400); font-size: 13px; }
</style>
