<template>
  <header class="page-head">
    <div v-if="icon" class="page-head__chip">
      <el-icon><component :is="icon" /></el-icon>
    </div>
    <div class="page-head__titles">
      <h2>{{ title }}</h2>
      <p v-if="sub" class="sub">{{ sub }}</p>
    </div>
    <div class="page-head__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
defineProps<{
  title: string;
  sub?: string;
  icon?: string;
}>();
</script>
