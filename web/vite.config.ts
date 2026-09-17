import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// API 网关地址：默认指向本地 M6 服务层（server/main.py 运行端口 8000）
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // element-plus 全量引入体积约 1MB 属库固有上限，vendor 已拆分、应用块仅 27kB；将警告阈值提到 1100 静音，不误报
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        // 按 vendor 拆分，提升浏览器长缓存命中率与并行下载能力（Element Plus 为体积主力）
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia', 'pinia-plugin-persistedstate'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          markdown: ['marked', 'dompurify'],
          utils: ['axios', '@vueuse/core'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // REST + WebSocket 统一代理（对齐 API_SPEC：/api/v1/tasks、/api/v1/tasks/{id}/stream）
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
