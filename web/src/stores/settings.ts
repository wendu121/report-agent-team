// 设置 store（setup-style + 持久化）
// 设计清单第 3 项修正：option-store 顶层不能用 useStorage；
// 改用 setup-style store + pinia-plugin-persistedstate（在 main.ts 挂载插件）。
import { defineStore } from 'pinia';
import { ref } from 'vue';

export const useSettingsStore = defineStore('settings', () => {
  // 调试模式开关：默认关闭；开启后时间线展示原始事件透传
  const debugMode = ref(false);

  function toggleDebug(): void {
    debugMode.value = !debugMode.value;
  }

  function setDebug(v: boolean): void {
    debugMode.value = v;
  }

  return { debugMode, toggleDebug, setDebug };
}, {
  persist: true,
});
