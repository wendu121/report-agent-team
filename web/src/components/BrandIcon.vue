<!--
  BrandIcon.vue · 第三方品牌 logo（矢量，来自 utils/brands.ts）
  - 用真实品牌标记替代通用图标（钉钉/飞书/企微/微信/Telegram/Discord/Slack）
  - 无运行时网络依赖：SVG 已内联进 brands.ts
  - 未知品牌返回空（调用方负责回退通用 Element Plus 图标）
-->
<template>
  <span class="brand-icon" :style="{ color: '#' + hex }" v-html="svg" />
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { BRANDS } from '@/utils/brands';

const props = defineProps<{ brand: string }>();

const mark = computed(() => BRANDS[props.brand]);
const hex = computed(() => mark.value?.hex ?? '64748b');
const svg = computed(() => {
  const m = mark.value;
  if (!m) return '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${m.w} ${m.h}">${m.body}</svg>`;
});
</script>

<style scoped>
.brand-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}
/* 用 :deep 才能命中 v-html 注入的 svg；fill:currentColor 让无 fill 属性的
   图标（如 icon-park:lark）也继承品牌色，而不是退化成默认黑。 */
.brand-icon :deep(svg) {
  width: 60%;
  height: 60%;
  fill: currentColor;
}
</style>
