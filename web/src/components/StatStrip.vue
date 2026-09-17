<!--
  StatStrip.vue · 页面概览统计条
  用途：市场类页面顶部用 3–4 个 KPI 交代「总量 / 可用 / 待处理」，
  让页面一眼有信息量，而不是只有一串卡片（boss 反馈「页面是不是很简单」）。
  语义色：brand=正向可用（绿）/ warn=待处理（琥珀）/ danger=异常（红）/ muted=中性总量（灰）。
-->
<template>
  <div class="stat-strip">
    <div v-for="s in stats" :key="s.label" class="stat" :class="`tone-${s.tone || 'muted'}`">
      <div class="stat__value">{{ s.value }}</div>
      <div class="stat__label">{{ s.label }}</div>
      <div v-if="s.hint" class="stat__hint">{{ s.hint }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  stats: Array<{
    label: string;
    value: number | string;
    tone?: 'brand' | 'warn' | 'danger' | 'muted';
    hint?: string;
  }>;
}>();
</script>

<style scoped>
.stat-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
  gap: 12px;
  margin: 18px 0 4px;
}
.stat {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: 14px 16px 13px;
  overflow: hidden;
  transition: border-color 0.18s var(--ease), box-shadow 0.18s var(--ease);
}
.stat:hover {
  border-color: var(--line-strong, var(--line));
  box-shadow: var(--sh-sm);
}
/* 左侧语义色条：不用大色块，保持克制 */
.stat::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--ink-300, #cbd5e1);
}
.stat__value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--ink-900);
}
.stat__label {
  margin-top: 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-500);
}
.stat__hint {
  margin-top: 3px;
  font-size: 11px;
  color: var(--ink-400);
}
.tone-brand::before { background: var(--brand); }
.tone-brand .stat__value { color: var(--brand-strong); }
.tone-warn::before { background: #d97706; }
.tone-warn .stat__value { color: #b45309; }
.tone-danger::before { background: var(--danger, #ef4444); }
.tone-danger .stat__value { color: var(--danger, #ef4444); }
.tone-muted::before { background: var(--ink-300, #cbd5e1); }
</style>
