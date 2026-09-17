<template>
  <div class="market-shell">
    <!-- Accio 招牌：一个市场页内用三主 Tab 承载能力类型（数据源/研报技能/推送渠道），
         各 Tab 内嵌既有视图组件（复用，不重写）。智能体市场单列（/agents）保持 Accio 结构。 -->
    <el-tabs v-model="active" class="main-tabs">
      <el-tab-pane label="数据源" name="plugins" />
      <el-tab-pane label="研报技能" name="skills" />
      <el-tab-pane label="推送渠道" name="channels" />
    </el-tabs>

    <!-- 仅挂载当前 Tab 的视图（切换即按需加载，避免三页同时拉取） -->
    <Plugins v-if="active === 'plugins'" />
    <Skills v-else-if="active === 'skills'" />
    <Channels v-else-if="active === 'channels'" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import Plugins from '@/views/Plugins.vue';
import Skills from '@/views/Skills.vue';
import Channels from '@/views/Channels.vue';

// 默认落在数据源（与左导航「能力市场」语义一致）
const active = ref<'plugins' | 'skills' | 'channels'>('plugins');
</script>

<style scoped>
.market-shell {
  /* 复用各内嵌视图的浅灰背景，避免双层留白 */
  background: transparent;
}
.main-tabs {
  margin-bottom: 14px;
}
.main-tabs :deep(.el-tabs__header) {
  margin: 0;
}
.main-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink-500);
  height: 44px;
}
.main-tabs :deep(.el-tabs__item:hover) {
  color: var(--brand);
}
.main-tabs :deep(.el-tabs__item.is-active) {
  color: var(--brand-strong);
}
.main-tabs :deep(.el-tabs__active-bar) {
  background: var(--brand);
}
</style>
