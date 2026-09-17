<template>
  <div class="login-shell">
    <div class="login-card">
      <div class="brand">
        <div class="brand-chip">
          <el-icon><Lock /></el-icon>
        </div>
        <div class="brand-text">
          <h1>研报协作平台</h1>
          <p>登录后使用你自己的技能、密钥、模型与智能体</p>
        </div>
      </div>

      <el-tabs v-model="mode" class="login-tabs">
        <el-tab-pane label="登录" name="login" />
        <el-tab-pane label="注册" name="register" />
      </el-tabs>

      <el-form @submit.prevent="submit" label-position="top" size="large">
        <el-form-item label="账号">
          <el-input
            v-model="username"
            autocomplete="username"
            placeholder="用户名"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            autocomplete="current-password"
            show-password
            placeholder="至少 8 位"
            @keyup.enter="submit"
          />
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="busy" @click="submit">
          {{ mode === 'login' ? '登录' : '注册' }}
        </el-button>
      </el-form>

      <div v-if="err" class="note-err">⚠ {{ err }}</div>
      <div v-if="ok" class="note-ok">✓ {{ ok }}</div>

      <p v-if="mode === 'register'" class="hint">
        注册后账号为「待审批」状态，需主账号在【设置 → 账号管理】通过后才能登录。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { Lock } from '@element-plus/icons-vue';
import { useAuthStore } from '@/stores/authStore';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const mode = ref<'login' | 'register'>('login');
const username = ref('');
const password = ref('');
const busy = ref(false);
const err = ref<string | null>(null);
const ok = ref<string | null>(null);

async function submit() {
  err.value = null;
  ok.value = null;
  if (!username.value.trim() || password.value.length < 8) {
    err.value = '请填写用户名，且密码至少 8 位';
    return;
  }
  busy.value = true;
  try {
    if (mode.value === 'login') {
      await auth.login(username.value.trim(), password.value);
      const redirect = (route.query.redirect as string) || '/';
      router.replace(redirect);
    } else {
      await auth.register(username.value.trim(), password.value);
      ok.value = '已提交注册，等待主账号审批。';
      mode.value = 'login';
      ElMessage.success('注册已提交，等待主账号审批');
    }
  } catch (e: any) {
    err.value = e?.payload?.data?.detail || e?.message || '操作失败';
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.login-shell {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--bg);
}
.login-card {
  width: 100%;
  max-width: 420px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-xl);
  box-shadow: var(--sh-lg);
  padding: 32px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
}
.brand-chip {
  width: 44px;
  height: 44px;
  border-radius: var(--r);
  background: var(--brand-soft);
  color: var(--brand-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex: none;
}
.brand-text h1 {
  font-size: 18px;
  font-weight: 600;
  color: var(--ink-900);
  margin: 0;
}
.brand-text p {
  font-size: 12px;
  color: var(--ink-400);
  margin: 4px 0 0;
}
.login-tabs {
  margin-bottom: 8px;
}
.submit-btn {
  width: 100%;
  margin-top: 4px;
}
.hint {
  font-size: 12px;
  color: var(--ink-400);
  line-height: 1.6;
  margin: 12px 0 0;
}
</style>
