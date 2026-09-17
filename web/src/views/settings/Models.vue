<template>
  <div class="page settings-page">
    <PageHead
      icon="Cpu"
      title="模型映射"
      sub="配置各角色与审核闸使用的 AI 模型。保存后写入 config/model_mapping.yaml，下一个任务即生效（引擎每次运行重新加载，无需重启容器）。"
    />
    <StatStrip :stats="stats" />

    <!-- 校验状态 -->
    <el-alert v-if="errors.length" type="error" :closable="false" class="blk">
      <template #title>配置校验未通过（保存已禁用）</template>
      <ul class="sm-errlist">
        <li v-for="e in errors" :key="e">{{ e }}</li>
      </ul>
    </el-alert>
    <el-alert v-else type="success" :closable="false" class="blk">
      <template #title>校验通过 · Gate 与所审 Agent 均异基座</template>
    </el-alert>

    <div v-loading="loading">
      <!-- roles -->
      <el-card shadow="never" class="blk">
        <template #header>
          <div class="blk-head">
            <span class="blk-title">角色（roles）</span>
            <span class="blk-sub">决定 Researcher / Analyst / Writer 用哪个模型产出</span>
          </div>
        </template>
        <el-table :data="roleRows" size="small" row-key="name">
          <el-table-column prop="name" label="角色" width="150" />
          <el-table-column label="base">
            <template #default="{ row }">
              <el-input v-model="row.base" size="small" placeholder="如 custom" />
            </template>
          </el-table-column>
          <el-table-column label="model">
            <template #default="{ row }">
              <el-input v-model="row.model" size="small" placeholder="如 LongCat-2.0" />
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- gates -->
      <el-card shadow="never" class="blk">
        <template #header>
          <div class="blk-head">
            <span class="blk-title">审核闸（gates）</span>
            <span class="blk-sub">硬约束：base 必须与所审角色的 base 不同</span>
          </div>
        </template>
        <el-table :data="gateRows" size="small" row-key="name">
          <el-table-column prop="name" label="闸" width="120" />
          <el-table-column prop="reviews" label="所审角色" width="140" />
          <el-table-column label="base">
            <template #default="{ row }">
              <el-input
                v-model="row.base"
                size="small"
                :class="{ 'is-violation': isViolation(row) }"
              />
            </template>
          </el-table-column>
          <el-table-column label="model">
            <template #default="{ row }">
              <el-input v-model="row.model" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="200">
            <template #default="{ row }">
              <el-tag v-if="isViolation(row)" type="danger" size="small">
                与 {{ row.reviews }} 同基座
              </el-tag>
              <el-tag v-else type="success" size="small">异基座 ✅</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- eval -->
      <el-card shadow="never" class="blk">
        <template #header>
          <div class="blk-head">
            <span class="blk-title">评分模型（eval）</span>
            <span class="blk-sub">仅辅助 Gate 评分，不替代业务规则</span>
          </div>
        </template>
        <el-form :inline="true" size="small">
          <el-form-item label="base">
            <el-input v-model="evalCfg.base" style="width: 180px" />
          </el-form-item>
          <el-form-item label="model">
            <el-input v-model="evalCfg.model" style="width: 220px" />
          </el-form-item>
        </el-form>
      </el-card>
    </div>

    <!-- 操作 -->
    <div class="sm-actions">
      <el-button type="primary" :disabled="!!errors.length || saving" @click="onSave">
        {{ saving ? '保存中…' : '保存' }}
      </el-button>
      <el-button :disabled="loading" @click="load">放弃修改并重新加载</el-button>
      <span v-if="lastSaved" class="sm-saved">已保存 · {{ lastSaved }}</span>
    </div>

    <!-- 审计 -->
    <el-card v-if="auditItems.length" shadow="never" class="blk">
      <template #header><div class="blk-head"><span class="blk-title">最近改动（审计）</span></div></template>
      <el-table :data="auditItems" size="small">
        <el-table-column prop="ts" label="时间" width="200" />
        <el-table-column prop="operator" label="操作者" width="140" />
        <el-table-column prop="validation" label="校验" width="90">
          <template #default="{ row }">
            <el-tag :type="row.validation === 'pass' ? 'success' : 'danger'" size="small">
              {{ row.validation }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="错误">
          <template #default="{ row }">
            <span v-if="!row.errors?.length">—</span>
            <span v-else class="sm-err">{{ row.errors.join('；') }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import api from '@/api/client';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

interface RoleRow { name: string; base: string; model: string }
interface GateRow { name: string; reviews: string; base: string; model: string }
interface AuditItem { ts: string; operator: string; action: string; validation: string; errors?: string[] }

const roleRows = ref<RoleRow[]>([]);
const gateRows = ref<GateRow[]>([]);
const evalCfg = ref<{ base: string; model: string }>({ base: '', model: '' });
const auditItems = ref<AuditItem[]>([]);

const loading = ref(false);
const saving = ref(false);
const lastSaved = ref('');

const stats = computed(() => [
  { label: '角色数', value: roleRows.value.length, tone: 'brand' as const, hint: 'Researcher/Analyst/Writer' },
  { label: '审核闸数', value: gateRows.value.length, tone: 'muted' as const, hint: 'GateA/B/C' },
  {
    label: '校验状态',
    value: errors.value.length ? '异常' : '通过',
    tone: (errors.value.length ? 'danger' : 'brand') as 'danger' | 'brand',
    hint: errors.value.length ? `${errors.value.length} 处待修正` : '异基座约束满足',
  },
]);

/** 某闸是否违反「与所审角色异基座」硬约束 */
function isViolation(row: GateRow): boolean {
  const target = roleRows.value.find((r) => r.name === row.reviews);
  return !!target && !!row.base && !!target.base && row.base === target.base;
}

/** 前端实时校验（与后端 server/admin.py::_validate_mapping 保持一致） */
const errors = computed<string[]>(() => {
  const errs: string[] = [];
  for (const r of roleRows.value) {
    if (!r.base) errs.push(`roles.${r.name}.base 不能为空`);
    if (!r.model) errs.push(`roles.${r.name}.model 不能为空`);
  }
  for (const g of gateRows.value) {
    if (!g.base) errs.push(`gates.${g.name}.base 不能为空`);
    if (!g.model) errs.push(`gates.${g.name}.model 不能为空`);
    if (!roleRows.value.some((r) => r.name === g.reviews)) {
      errs.push(`gates.${g.name}.reviews 指向不存在的角色：${g.reviews}`);
      continue;
    }
    if (isViolation(g)) {
      const t = roleRows.value.find((r) => r.name === g.reviews)!;
      errs.push(
        `硬约束违反：gates.${g.name}.base(${g.base}) 与所审角色 roles.${g.reviews}.base(${t.base}) 相同，会导致自审包庇`
      );
    }
  }
  return errs;
});

async function load(): Promise<void> {
  loading.value = true;
  try {
    const { data } = await api.get('/admin/models');
    const m = data.mapping ?? {};
    roleRows.value = Object.entries(m.roles ?? {}).map(([name, c]) => ({
      name,
      base: (c as RoleRow).base ?? '',
      model: (c as RoleRow).model ?? '',
    }));
    gateRows.value = Object.entries(m.gates ?? {}).map(([name, c]) => {
      const cfg = c as GateRow;
      return { name, reviews: cfg.reviews ?? '', base: cfg.base ?? '', model: cfg.model ?? '' };
    });
    evalCfg.value = { base: m.eval?.base ?? '', model: m.eval?.model ?? '' };
    await loadAudit();
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`);
  } finally {
    loading.value = false;
  }
}

async function loadAudit(): Promise<void> {
  try {
    const { data } = await api.get('/admin/models/audit', { params: { limit: 5 } });
    auditItems.value = data.items ?? [];
  } catch {
    auditItems.value = [];   // 审计不可读不阻断主流程
  }
}

async function onSave(): Promise<void> {
  if (errors.value.length) return;
  saving.value = true;
  try {
    const payload = {
      roles: Object.fromEntries(roleRows.value.map((r) => [r.name, { base: r.base, model: r.model }])),
      gates: Object.fromEntries(gateRows.value.map((g) => [g.name, { base: g.base, model: g.model }])),
      eval: evalCfg.value,
    };
    await api.put('/admin/models', payload);
    lastSaved.value = new Date().toLocaleTimeString('zh-CN');
    ElMessage.success('已保存，下一个任务即生效（无需重启）');
    await load();
  } catch (e) {
    const err = e as { status?: number; detail?: unknown; message?: string };
    const detail = err.detail as { errors?: string[] } | undefined;
    if (detail?.errors?.length) {
      ElMessage.error(`保存被拒：${detail.errors.join('；')}`);
    } else {
      ElMessage.error(`保存失败：${err.message ?? '未知错误'}`);
    }
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.settings-page :deep(.el-card) {
  border-color: var(--line);
}
.settings-page :deep(.el-table) {
  --el-table-border-color: var(--line);
  --el-table-header-bg-color: var(--surface-2);
}
.sm-errlist { margin: 6px 0 0; padding-left: 18px; font-size: 12px; }
.sm-actions { margin-top: 20px; display: flex; align-items: center; gap: 12px; }
.sm-saved { font-size: 12px; color: var(--brand); }
.sm-err { font-size: 12px; color: var(--danger); }
:deep(.is-violation .el-input__wrapper) { box-shadow: 0 0 0 1px var(--danger) inset; }
</style>
