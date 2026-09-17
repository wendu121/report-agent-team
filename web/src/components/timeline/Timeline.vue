<template>
  <div class="timeline">
    <el-empty v-if="nodes.length === 0" description="暂无运行事件" />
    <!-- 虚拟滚动预留（设计清单第 7 项）：nodes.length > 100 时切换为 el-virtual-list
         渲染；M7-1 先行基础 v-for，M7-3 接入虚拟滚动 -->
    <div v-for="(node, i) in nodes" :key="node.id" class="timeline-track">
      <TimelineNode :node="node" :index="i" @node-click="$emit('node-click', node)" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { TimelineNode as TimelineNodeType } from '@/types';
import TimelineNode from './TimelineNode.vue';

// 类型因与子组件同名，此处用别名 TimelineNodeType 避免冲突
defineProps<{ nodes: TimelineNodeType[] }>();
defineEmits<{ (e: 'node-click', node: TimelineNodeType): void }>();
</script>
