<template>
  <el-card shadow="never" class="entity-card">
    <div class="card-top">
      <div class="avatar" :style="avatarStyle">
        <BrandIcon v-if="brand" :brand="brand" />
        <el-icon v-else-if="icon"><component :is="icon" /></el-icon>
        <template v-else>{{ avatarText }}</template>
      </div>
      <div class="meta">
        <div class="name">
          {{ name }}
          <slot name="badges" />
        </div>
        <div class="sub-line"><slot name="sub">{{ sub }}</slot></div>
      </div>
      <slot name="status" />
    </div>
    <p class="desc">{{ desc }}</p>
    <slot name="extra" />
    <div class="tags" v-if="tags && tags.length">
      <el-tag v-for="t in tags" :key="t" size="small" effect="plain" class="tag">{{ t }}</el-tag>
    </div>
    <div class="actions">
      <slot name="actions" />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import BrandIcon from '@/components/BrandIcon.vue';
import { BRANDS } from '@/utils/brands';

const props = defineProps<{
  avatarText?: string;
  icon?: string;
  /** 品牌 key（utils/brands.ts）；命中则渲染真实品牌 logo，优先于 icon/avatarText */
  brand?: string;
  color: string;
  name: string;
  sub?: string;
  desc?: string;
  tags?: string[];
}>();

const mark = computed(() => (props.brand ? BRANDS[props.brand] : undefined));

// 命中品牌：浅品牌色底 + 品牌色字形（集成卡片观感）；否则沿用传入调色板纯色底
const avatarStyle = computed((): Record<string, string> => {
  const m = mark.value;
  if (m) return { background: `#${m.hex}1A`, color: `#${m.hex}`, boxShadow: 'none' };
  return { background: props.color, color: '#fff' };
});
</script>

<style scoped>
.entity-card {
  /* flex 列 + height:100% —— 让 .actions{margin-top:auto} 真正生效：
     .card-grid 里被 grid stretch 拉平的卡片因此等高，同排按钮底对齐、不再参差 */
  display: flex;
  flex-direction: column;
  height: 100%;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  transition: transform 0.2s var(--ease), box-shadow 0.2s var(--ease),
    border-color 0.2s var(--ease);
}
.entity-card:hover {
  transform: translateY(-3px);
  border-color: var(--brand);
  box-shadow: var(--sh-md);
}
.entity-card :deep(.el-card__body) {
  /* 卡片根是 flex 列，body 必须 flex:1 撑满，才有多余空间可供 margin-top:auto 吸收 */
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: var(--s-5);
}
.card-top {
  display: flex;
  gap: var(--s-3);
  align-items: flex-start;
}
.avatar {
  width: 46px;
  height: 46px;
  border-radius: var(--r);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 19px;
  flex-shrink: 0;
  box-shadow: var(--sh-sm);
}
.avatar :deep(.el-icon) {
  font-size: 23px;
  width: 23px;
  height: 23px;
}
.meta {
  flex: 1;
  min-width: 0;
}
.name {
  font-weight: 600;
  font-size: 15px;
  color: var(--ink-900);
  display: flex;
  gap: 7px;
  align-items: center;
  flex-wrap: wrap;
}
.sub-line {
  font-size: 12px;
  color: var(--ink-400);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desc {
  color: var(--ink-700);
  font-size: 13px;
  margin: var(--s-3) 0;
  min-height: 38px;
  line-height: 1.55;
}
.tags {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  margin-bottom: var(--s-3);
}
.tag {
  border-radius: 6px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  /* 推到卡片底部 → 同排卡片按钮齐平，视觉整齐 */
  margin-top: auto;
  padding-top: var(--s-3);
}
</style>
