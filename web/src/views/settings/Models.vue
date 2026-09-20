<template>
  <div class="page settings-page">
    <PageHead
      icon="Cpu"
      title="模型映射"
      sub="配置各角色与审核闸使用的 AI 模型。保存后写入 config/model_mapping.yaml，下一个任务即生效（引擎每次运行重新加载，无需重启容器）。"
    />
    <StatStrip :stats="stats" />

    <!-- 本账号没配模型 API 时，映射填了也是空转 —— 先说清楚怎么配（DESIGN_subaccount_model_isolation.md §3-D5） -->
    <el-alert v-if="noModels" type="warning" :closable="false" show-icon class="blk">
      <template #title>本账号尚未配置模型 API，下面填了也不会生效</template>
      <div class="sm-guide">
        <div>1) 先到「自定义 API」添加你自己的 OpenAI 兼容 Provider（base_url + API Key）。子账号不共享主账号的 new-api 网关。</div>
        <div>2) 添加 Provider 时把模型 id 填进去（或留空由系统自动发现）。本页的 <code>model</code> 请选那个模型的<strong>接口 id</strong>（形如 <code>custom:&lt;provider&gt;:&lt;模型&gt;</code>）。</div>
        <div>3) <code>base</code> 是「来源标签」，硬约束要求 <code>gates.X.base</code> ≠ <code>roles[所审角色].base</code>（防自审包庇）→ 至少需要两个不同来源的 Provider 才能保存。</div>
        <el-button size="small" type="primary" class="sm-guide-btn" @click="router.push('/settings/custom-providers')">
          去配置自定义 API
        </el-button>
      </div>
    </el-alert>

    <!-- 解析预检口径说明：把「base 不参与路由」讲清楚（DESIGN_model_mapping_uf.md 档 1） -->
    <el-alert v-if="!noModels" type="info" :closable="false" show-icon class="blk">
      <template #title>「实际端点」列 = 引擎真正把请求发到哪一家</template>
      <div class="sm-guide">
        决定路由的是 <code>model</code> 能否命中一个<strong>接口 id</strong>；<code>base</code> 只是来源标签，
        <strong>不参与路由</strong>，仅用于「异基座」防自审校验。
        <span v-if="unmatchedCount">
          当前有 <strong>{{ unmatchedCount }}</strong> 条未命中接口，将<strong>回落默认端点</strong>
          （依赖端点排序，属高危写法，建议改用接口 id）。
        </span>
      </div>
    </el-alert>

    <!-- 校验状态 -->
    <el-alert v-if="errors.length" type="error" :closable="false" class="blk">
      <template #title>配置校验未通过（保存已禁用）</template>
      <ul class="sm-errlist">
        <li v-for="e in errors" :key="e">{{ e }}</li>
      </ul>
    </el-alert>
    <!-- 不能再用「没有 errors ⇒ 绿色通过」：那样会同时挂绿色「校验通过」横幅和
         「N 条未命中（高危）」告警，两块牌子互相打架（MAJOR-1）。 -->
    <el-alert v-else-if="previewFailed" type="warning" :closable="false" class="blk">
      <template #title>校验未知 · 接口预检不可用</template>
      <span class="blk-sub">
        无法确认当前的 model 是否命中接口 id，请勿据此认为配置健康。
      </span>
    </el-alert>
    <el-alert v-else-if="unmatchedCount" type="warning" :closable="false" class="blk">
      <template #title>需注意 · {{ unmatchedCount }} 条未命中接口 id</template>
      <span class="blk-sub">
        Gate 与所审 Agent 的异基座约束满足，但未命中的条目将回落默认端点（高危写法，见上方提示）。
      </span>
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
          <el-table-column prop="name" label="角色" width="140" />
          <el-table-column label="base（来源标签）" width="200">
            <template #default="{ row }">
              <el-select
                v-model="row.base"
                size="small"
                filterable
                allow-create
                default-first-option
                placeholder="如 agent"
                style="width: 100%"
                @change="refreshPreview"
              >
                <el-option v-for="b in baseOptions" :key="b" :label="b" :value="b" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="model（接口 id）">
            <template #default="{ row }">
              <el-select
                v-model="row.model"
                size="small"
                filterable
                allow-create
                default-first-option
                placeholder="如 custom:agent:agnes-3.0-flash"
                style="width: 100%"
                @change="refreshPreview"
              >
                <el-option-group v-for="g in modelGroups" :key="g.label" :label="g.label">
                  <el-option v-for="m in g.options" :key="m.id" :label="m.label" :value="m.id" />
                </el-option-group>
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="实际端点" width="200">
            <template #default="{ row }">
              <template v-if="pvMap['roles.' + row.name]">
                <el-tag size="small" :type="pvTagType(pvMap['roles.' + row.name])">
                  {{ pvMap['roles.' + row.name].endpoint_id || '无可用端点' }}
                </el-tag>
                <div class="pv-note">{{ pvNote(pvMap['roles.' + row.name]) }}</div>
              </template>
              <span v-else class="pv-empty">—</span>
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
          <el-table-column prop="name" label="闸" width="110" />
          <el-table-column prop="reviews" label="所审角色" width="120" />
          <el-table-column label="base（来源标签）" width="190">
            <template #default="{ row }">
              <el-select
                v-model="row.base"
                size="small"
                filterable
                allow-create
                default-first-option
                style="width: 100%"
                :class="{ 'is-violation': isViolation(row) }"
                @change="refreshPreview"
              >
                <el-option v-for="b in baseOptions" :key="b" :label="b" :value="b" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="model（接口 id）">
            <template #default="{ row }">
              <el-select
                v-model="row.model"
                size="small"
                filterable
                allow-create
                default-first-option
                placeholder="如 custom:discovery:deepseek-v4-flash-vision"
                style="width: 100%"
                @change="refreshPreview"
              >
                <el-option-group v-for="g in modelGroups" :key="g.label" :label="g.label">
                  <el-option v-for="m in g.options" :key="m.id" :label="m.label" :value="m.id" />
                </el-option-group>
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="实际端点" width="200">
            <template #default="{ row }">
              <template v-if="pvMap['gates.' + row.name]">
                <el-tag size="small" :type="pvTagType(pvMap['gates.' + row.name])">
                  {{ pvMap['gates.' + row.name].endpoint_id || '无可用端点' }}
                </el-tag>
                <div class="pv-note">{{ pvNote(pvMap['gates.' + row.name]) }}</div>
              </template>
              <span v-else class="pv-empty">—</span>
            </template>
          </el-table-column>
          <el-table-column label="异基座" width="150">
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
            <el-select
              v-model="evalCfg.base"
              size="small"
              filterable
              allow-create
              default-first-option
              style="width: 200px"
              @change="refreshPreview"
            >
              <el-option v-for="b in baseOptions" :key="b" :label="b" :value="b" />
            </el-select>
          </el-form-item>
          <el-form-item label="model">
            <el-select
              v-model="evalCfg.model"
              size="small"
              filterable
              allow-create
              default-first-option
              style="width: 340px"
              @change="refreshPreview"
            >
              <el-option-group v-for="g in modelGroups" :key="g.label" :label="g.label">
                <el-option v-for="m in g.options" :key="m.id" :label="m.label" :value="m.id" />
              </el-option-group>
            </el-select>
          </el-form-item>
          <el-form-item label="实际端点">
            <el-tag v-if="pvMap['eval']" size="small" :type="pvTagType(pvMap['eval'])">
              {{ pvMap['eval'].endpoint_id || '无可用端点' }}
            </el-tag>
            <span v-else class="pv-empty">—</span>
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
        <el-table-column label="校验" width="90">
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
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import api from '@/api/client';
import PageHead from '@/components/PageHead.vue';
import StatStrip from '@/components/StatStrip.vue';

const router = useRouter();
/**
 * 本账号是否"一个可用模型都没有"。
 *
 * 子账号自 2026-09-15 起不再继承主账号网关（endpoints 为空、fetch_from_gateway=false），
 * 所以新注册的子账号进本页时必然命中这个状态——此时 roles/gates 的 base/model 全是空的，
 * 校验也过不了。与其让用户对着红色报错猜，不如直接给出三步配置指引。
 * 判定口径与 ChatEntry.vue 的 usableModels 保持一致：必须有 endpoint_id 且 kind !== 'auto'。
 */
const noModels = ref(false);

/** 后端解析预检的单条结果（POST /admin/models/preview） */
interface PvInfo {
  path: string;
  model: string;
  endpoint_id: string | null;
  real_model: string | null;
  matched: boolean;
  disambiguated: boolean;
  error?: string | null;
}
interface RoleRow { name: string; base: string; model: string }
interface GateRow { name: string; reviews: string; base: string; model: string }
interface AuditItem { ts: string; operator: string; action: string; validation: string; errors?: string[] }
interface ModelOpt { id: string; name: string; label: string }
interface ModelGroup { label: string; options: ModelOpt[] }

const roleRows = ref<RoleRow[]>([]);
const gateRows = ref<GateRow[]>([]);
const evalCfg = ref<{ base: string; model: string }>({ base: '', model: '' });
const auditItems = ref<AuditItem[]>([]);

/** 下拉候选（账号作用域，来自 GET /models） */
const modelGroups = ref<ModelGroup[]>([]);
const endpointIds = ref<string[]>([]);
/** 解析预检结果：path（如 roles.Researcher）→ 解析详情 */
const pvMap = ref<Record<string, PvInfo>>({});
/** 预检是否失败（请求异常 → pvMap 被清空）。用于区分「全部命中」与「压根没验成」，
 *  避免预检挂掉时页面显示「通过 / 全部命中接口 id」这种伪造结论（MAJOR-2）。 */
const previewFailed = ref(false);

const loading = ref(false);
const saving = ref(false);
const lastSaved = ref('');

/** base 下拉候选 = 端点 id ∪ 当前已有值。
 *  必须并入已有值：主账号的 `custom` / `cloudflare` 是**网关内部的渠道名**，
 *  并不是端点 id，若不在候选里会被 el-select 显示为空、造成"配置丢了"的错觉。 */
const baseOptions = computed<string[]>(() => {
  const s = new Set<string>(endpointIds.value);
  for (const r of roleRows.value) if (r.base) s.add(r.base);
  for (const g of gateRows.value) if (g.base) s.add(g.base);
  if (evalCfg.value.base) s.add(evalCfg.value.base);
  return [...s];
});

/** 未命中接口（将回落默认端点）的条数 —— 用于顶部诚实提示 */
const unmatchedCount = computed<number>(() => {
  return Object.entries(pvMap.value).filter(([k, v]) => k !== 'eval' && !v.matched).length;
});

const stats = computed(() => [
  { label: '角色数', value: roleRows.value.length, tone: 'brand' as const, hint: 'Researcher/Analyst/Writer' },
  { label: '审核闸数', value: gateRows.value.length, tone: 'muted' as const, hint: 'GateA/B/C' },
  // 「校验状态」必须把未命中算进去（DESIGN_settings_honesty.md K2）：
  // 过去只看 errors（空值 / reviews 指向 / 异基座），于是页面能同时挂
  // 「校验通过」和「N 条未命中接口（高危写法）」两块互相打架的牌子。
  // 严重程度：真错误(danger) > 预检失败(未知) > 未命中(warn) > 通过(brand)。
  // 「未知」必须存在：refreshPreview 失败时 pvMap 被清空，此时既不知道命中也没有 metast，
  //  若沿用"通过"就是伪造结论 —— MAJOR-2。
  {
    label: '校验状态',
    value: errors.value.length
      ? '异常'
      : previewFailed.value
        ? '未知'
        : unmatchedCount.value
          ? '需注意'
          : '通过',
    tone: (errors.value.length
      ? 'danger'
      : previewFailed.value
        ? 'warn'
        : unmatchedCount.value
          ? 'warn'
          : 'brand') as 'danger' | 'warn' | 'brand',
    hint: errors.value.length
      ? `${errors.value.length} 处待修正`
      : previewFailed.value
        ? '接口预检不可用，无法确认是否命中'
        : unmatchedCount.value
          ? `${unmatchedCount.value} 条未命中接口 id`
          : '异基座约束满足',
  },
  {
    label: '未命中接口',
    value: previewFailed.value ? '—' : unmatchedCount.value,
    tone: (!previewFailed.value && unmatchedCount.value ? 'danger' : 'muted') as 'danger' | 'muted',
    hint: previewFailed.value
      ? '预检失败，暂无法判定'
      : unmatchedCount.value
        ? '将回落默认端点'
        : '全部命中接口 id',
  },
]);

/** 某闸是否违反「与所审角色异基座」硬约束。
 *  口径保持「比对 base 字符串」——主账号的 base 表达的是网关内上游渠道，
 *  改成比对解析后的端点会把它的三道闸全判同基座（见 DESIGN_model_mapping_uf.md §5.1）。 */
function isViolation(row: GateRow): boolean {
  const target = roleRows.value.find((r) => r.name === row.reviews);
  return !!target && !!row.base && !!target.base && row.base === target.base;
}

/** 解析结果 → 标签配色 */
function pvTagType(pv: PvInfo): 'success' | 'warning' | 'danger' | 'primary' | 'info' {
  if (pv.error) return 'danger';
  if (!pv.matched) return 'warning';
  if (pv.disambiguated) return 'primary';
  return 'success';
}

/** 解析结果 → 一行说明 */
function pvNote(pv: PvInfo): string {
  if (pv.error) return pv.error;
  if (!pv.matched) return '未命中接口 → 回落默认端点';
  if (pv.disambiguated) return `裸名自动消歧 · ${pv.real_model}`;
  return String(pv.real_model ?? '');
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

/** 组装提交体（保存与预检共用，避免两处结构漂移） */
function buildPayload() {
  return {
    roles: Object.fromEntries(roleRows.value.map((r) => [r.name, { base: r.base, model: r.model }])),
    gates: Object.fromEntries(gateRows.value.map((g) => [g.name, { base: g.base, model: g.model }])),
    eval: { base: evalCfg.value.base, model: evalCfg.value.model },
  };
}

/** 拉取当前的解析结果（后端复用引擎同款规则，前端不重实现） */
async function refreshPreview(): Promise<void> {
  if (noModels.value) {
    pvMap.value = {};
    previewFailed.value = false;
    return;
  }
  try {
    const { data } = await api.post('/admin/models/preview', buildPayload());
    const map: Record<string, PvInfo> = {};
    for (const it of (data.items ?? []) as PvInfo[]) map[it.path] = it;
    pvMap.value = map;
    previewFailed.value = false;
  } catch {
    // 预检不可用时不假装通过：清空结果并**标记失败**，
    // UI 显示 "未知 / —" 而不是 "通过 / 全部命中接口 id"（诚实降级，MAJOR-2）。
    pvMap.value = {};
    previewFailed.value = true;
  }
}

/** 模型下拉候选：账号作用域的接口表（按来源分组，便于在 120+ 项里定位） */
async function loadAvailableModels(): Promise<void> {
  try {
    // /models 是**账号作用域**接口（后端已要求登录，否则无租户上下文 → 会读到
    // 全局/主账号的模型清单）。故走带 Bearer 的 api 客户端，不能用裸 fetch。
    const { data } = await api.get('/models');
    const items = (data.items ?? []) as Array<{
      id?: string; name?: string; kind?: string; source?: string; endpoint_id?: string;
    }>;
    // 排除 kind='auto' 的占位项（它没有 endpoint_id，选了也没用）
    const usable = items.filter((m) => !!m.id && m.id !== 'auto' && m.kind !== 'auto' && !!m.endpoint_id);
    const byGroup: Record<string, ModelOpt[]> = {};
    const groupLabel: Record<string, string> = {
      custom: '自定义 Provider',
      builtin: '内置',
      local: '本地',
      gateway: '网关模型',
    };
    for (const m of usable) {
      const g = m.source ?? 'local';
      const label = g === 'builtin' && m.kind === 'universal' ? '自动路由（网关别名）' : (groupLabel[g] ?? g);
      (byGroup[label] ??= []).push({
        id: m.id!,
        name: m.name || m.id!,
        label: `${m.name || m.id} · ${m.endpoint_id}`,
      });
    }
    modelGroups.value = Object.entries(byGroup).map(([label, options]) => ({ label, options }));
    endpointIds.value = [...new Set(items.map((i) => i.endpoint_id).filter(Boolean) as string[])];
    noModels.value = usable.length === 0;
  } catch {
    modelGroups.value = [];
    endpointIds.value = [];
    noModels.value = true; // 拉不到就引导去配（保守，避免"看着能填其实不可用"）
  }
}

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
    await Promise.all([loadAudit(), refreshPreview()]);
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

  // K2：未命中不再静默放行。保存前列出条目并要求显式确认；用户取消即不落盘。
  // 之所以不做「未命中即报错」：那是 DESIGN_model_mapping_uf.md 档 3 明确否决的方案
  // ——会当场废掉主账号历史裸名写法，而裸名回落是既定设计行为。
  if (unmatchedCount.value > 0) {
    const keys = Object.entries(pvMap.value)
      .filter(([k, v]) => k !== 'eval' && !v.matched)
      .map(([k]) => k);
    try {
      await ElMessageBox.confirm(
        `以下配置未命中接口 id，保存后将回落默认端点（可能打到意料之外的模型或直接报错）：\n` +
          `${keys.join('\n')}\n\n确定仍然保存？`,
        '高危配置确认',
        {
          type: 'warning',
          confirmButtonText: '我已知晓，仍然保存',
          cancelButtonText: '取消',
        }
      );
    } catch {
      return; // 用户明确取消：不落盘、不提示错误
    }
  }

  saving.value = true;
  try {
    await api.put('/admin/models', buildPayload());
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

onMounted(() => {
  void load();
  void loadAvailableModels();
});
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
.sm-guide { font-size: 12px; line-height: 1.9; }
.sm-guide code { background: var(--surface-2); padding: 0 4px; border-radius: 3px; }
.sm-guide-btn { margin-top: 8px; }
.sm-actions { margin-top: 20px; display: flex; align-items: center; gap: 12px; }
.sm-saved { font-size: 12px; color: var(--brand); }
.sm-err { font-size: 12px; color: var(--danger); }
.pv-note { font-size: 11px; color: var(--ink-500); margin-top: 2px; line-height: 1.4; }
.pv-empty { color: var(--ink-400); }
:deep(.is-violation .el-input__wrapper) { box-shadow: 0 0 0 1px var(--danger) inset; }
</style>
