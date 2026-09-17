<template>
  <div class="tl-node" :class="`tl-${node.status}`" @click="expanded = !expanded">
    <div class="tl-dot"></div>
    <div class="tl-body">
      <div class="tl-title">
        <el-icon v-if="icon" class="tl-icon"><component :is="icon" /></el-icon>
        <span>{{ node.title }}</span>
        <el-icon class="tl-chevron" :class="{ open: expanded }"><ArrowRight /></el-icon>
      </div>
      <div class="tl-desc">{{ node.description }}</div>
      <div class="tl-time">{{ formatTime(node.timestamp) }}</div>

      <div v-if="expanded" class="tl-detail">
        <!-- 调试模式：透传原始事件 JSON -->
        <pre v-if="settingsStore.debugMode" class="tl-raw">{{ formatRaw(node.rawEvent) }}</pre>
        <!-- 普通模式：业务友好的元数据字段 -->
        <div v-else class="tl-meta">
          <div v-for="(val, key) in node.meta" :key="String(key)" class="meta-row">
            <span class="meta-key">{{ String(key) }}</span>
            <span class="meta-val">{{ formatVal(val) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import type { TimelineNode } from '@/types';
import { formatTime } from '@/utils/formatter';
import { useSettingsStore } from '@/stores/settings';

const props = defineProps<{ node: TimelineNode; index: number }>();
const settingsStore = useSettingsStore();
const expanded = ref(false);

const iconMap: Record<string, string> = {
  agent: 'Search',
  gate: 'Stamp',
  rework: 'RefreshLeft',
  round: 'Refresh',
  tool_error: 'WarningFilled',
  done: 'CircleCheckFilled',
  escalated: 'CircleCloseFilled',
};
const icon = computed(() => iconMap[props.node.type] ?? 'InfoFilled');

const formatRaw = (ev?: TimelineNode['rawEvent']): string =>
  ev ? JSON.stringify(ev, null, 2) : '';

function formatVal(val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'object') return JSON.stringify(val);
  return String(val);
}
</script>

<style scoped>
.tl-node {
  cursor: pointer;
}
.tl-title {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tl-icon {
  color: inherit;
}
.tl-chevron {
  margin-left: auto;
  transition: transform 0.2s;
  color: #909399;
}
.tl-chevron.open {
  transform: rotate(90deg);
}
.tl-detail {
  margin-top: 8px;
  border-top: 1px dashed #e4e7ed;
  padding-top: 8px;
}
.tl-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.meta-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
}
.meta-key {
  color: #909399;
  min-width: 120px;
  text-align: right;
}
.meta-val {
  color: #303133;
  word-break: break-all;
}
.tl-raw {
  background: #1f2937;
  color: #d1fae5;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  overflow: auto;
  max-height: 240px;
}
</style>
