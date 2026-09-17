<template>
  <el-tag :type="tagType" effect="light">{{ label }}</el-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { TaskStatus } from '@/types';
import { statusLabel, statusTagType } from '@/utils/formatter';

const props = defineProps<{ status: TaskStatus }>();
const label = computed(() => statusLabel(props.status));
const tagType = computed(() => statusTagType(props.status));
</script>
