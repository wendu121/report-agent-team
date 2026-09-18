import { createApp } from 'vue';
import { createPinia } from 'pinia';
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate';
import ElementPlus from 'element-plus';
import { ElMessage } from 'element-plus';
import 'element-plus/dist/index.css';
import * as ElementPlusIconsVue from '@element-plus/icons-vue';
import App from './App.vue';
import router from './router';
import { useAuthStore } from './stores/authStore';
import { UNAUTHORIZED_EVENT, FORBIDDEN_EVENT } from './api/client';
import './style.css';

const app = createApp(App);

const pinia = createPinia();
pinia.use(piniaPluginPersistedstate);
app.use(pinia);
app.use(router);
app.use(ElementPlus);

// 全局注册 Element Plus 图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component);
}

// ---- 鉴权事件总线（client.ts 拦截器广播，此处响应，守「拦截器不碰 store」纪律）----
// 401 令牌失效：清会话并跳登录；403 权限不足：保留会话，仅提示。
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  const auth = useAuthStore();
  auth.logout();
  if (router.currentRoute.value.name !== 'Login') {
    router.replace({ name: 'Login', query: { redirect: router.currentRoute.value.fullPath } });
  }
});
window.addEventListener(FORBIDDEN_EVENT, (e: Event) => {
  const detail = (e as CustomEvent).detail;
  ElMessage.warning(typeof detail === 'string' && detail ? detail : '无权限访问该资源');
});

app.mount('#app');
