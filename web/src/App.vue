<template>
  <!--
    「裸页」（登录/注册，route.meta.bare）不套应用外壳：无侧边栏、占满全屏。
    侧边栏（新任务/智能体/能力市场/聊天记录/历史记录/设置）是登录后才有的功能。
  -->
  <router-view v-if="isBare" />
  <DefaultLayout v-else>
    <router-view />
  </DefaultLayout>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import DefaultLayout from '@/layouts/DefaultLayout.vue';

const route = useRoute();
const isBare = computed(() => route.meta.bare === true);
</script>
