<!--
  DefaultLayout.vue · 应用外壳（左侧栏 + 主区）
  侧边栏结构（自上而下）：
    ① 品牌区  ② 主导航（新任务 / 智能体 / 能力市场）
    ③ 聊天记录（可折叠，含搜索 + 新建）④ 历史记录（可折叠）⑤ 用户区（调试开关 + 设置）
  布局要点：③④ 按 flex 权重分配剩余高度，列表自身 flex:1 + min-height:0 内部滚动，
  折叠时整段退化为 flex:0 0 auto —— 避免出现「大片空白」的空洞感。
-->
<template>
  <el-container class="app-shell">
    <el-aside width="232px" class="app-aside">
      <div class="brand">
        <div class="brand-logo"><el-icon><Notebook /></el-icon></div>
        <div class="brand-text">
          <div class="brand-name">研报协作平台</div>
          <div class="brand-sub">Multi-Agent Research</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" router class="app-menu">
        <!-- M9-5 对话式研报入口（首页默认落地，真·控制器） -->
        <el-menu-item index="/">
          <el-icon><DocumentAdd /></el-icon>
          <span>新任务</span>
        </el-menu-item>
        <!-- M9-1 智能体市场（Accio 智能体市场单列） -->
        <el-menu-item index="/agents">
          <el-icon><Cpu /></el-icon>
          <span>智能体</span>
        </el-menu-item>
        <!-- Accio 统一能力市场：一页内 数据源/研报技能/推送渠道 三主 Tab -->
        <el-menu-item index="/market">
          <el-icon><Grid /></el-icon>
          <span>能力市场</span>
        </el-menu-item>
        <!-- 统一审核中心：技能/专家提案 + 待推送变更，老板逐一点通过/打回 -->
        <el-menu-item index="/review">
          <el-icon><Stamp /></el-icon>
          <span>审核中心</span>
        </el-menu-item>
      </el-menu>

      <!-- ③ 聊天记录（每个聊天一个窗口：可切换 / 新建 / 删除 / 折叠） -->
      <section class="sect" :class="{ collapsed: !chatExpanded }">
        <div class="sect-head" @click="toggleChat">
          <el-icon class="sect-head__icon"><ChatLineRound /></el-icon>
          <span class="sect-head__label">聊天记录</span>
          <span v-if="chatSessions.length" class="sect-head__count">{{ chatSessions.length }}</span>
          <el-icon class="sect-head__caret"><ArrowDown /></el-icon>
        </div>

        <div v-show="chatExpanded" class="sect-tools">
          <el-input
            v-model="searchQuery"
            class="chat-search"
            size="small"
            clearable
            placeholder="搜索聊天记录…"
            :prefix-icon="Search"
          />
          <button type="button" class="icon-btn" title="新对话" @click="newChat">
            <el-icon><Plus /></el-icon>
          </button>
          <button
            type="button"
            class="icon-btn"
            title="清空全部聊天记录"
            :disabled="!chatSessions.length"
            @click="clearAllChats"
          >
            <el-icon><Delete /></el-icon>
          </button>
        </div>

        <div v-show="chatExpanded" class="sect-list">
          <div
            v-for="s in filteredSessions"
            :key="s.id"
            class="chat-item"
            :class="{ active: s.id === currentSessionId }"
            @click="openChat(s.id)"
          >
            <el-icon class="chat-item-icon"><ChatRound /></el-icon>
            <div class="chat-item-topic" :title="s.topic || '新对话'">{{ s.topic || '新对话' }}</div>
            <el-icon class="chat-item-del" title="删除该对话" @click.stop="delChat(s.id)">
              <Delete />
            </el-icon>
          </div>
          <div v-if="chatSessions.length === 0" class="sect-empty">暂无聊天记录</div>
          <div v-else-if="filteredSessions.length === 0" class="sect-empty">无匹配的聊天记录</div>
        </div>
      </section>

      <!-- ④ 历史记录（研报任务，可折叠） -->
      <section class="sect" :class="{ collapsed: !historyExpanded }">
        <div class="sect-head" @click="toggleHistory">
          <el-icon class="sect-head__icon"><Clock /></el-icon>
          <span class="sect-head__label">历史记录</span>
          <span v-if="history.length" class="sect-head__count">{{ history.length }}</span>
          <el-icon
            v-if="history.length"
            class="sect-head__clear"
            title="清空全部历史记录"
            @click.stop="clearAllTasks"
          ><Delete /></el-icon>
          <el-icon class="sect-head__caret"><ArrowDown /></el-icon>
        </div>

        <div v-show="historyExpanded" class="sect-list">
          <div
            v-for="t in history"
            :key="t.task_id"
            class="history-item"
            @click="goTask(t.task_id)"
          >
            <div class="history-row">
              <div class="history-topic" :title="t.topic || '无主题'">
                {{ t.topic || '无主题' }}
              </div>
              <el-icon
                v-if="isRunning(t.status)"
                class="history-item-del is-disabled"
                title="运行中不可删除"
                @click.stop
              ><Delete /></el-icon>
              <el-icon
                v-else
                class="history-item-del"
                title="删除该任务"
                @click.stop="delTask(t.task_id)"
              ><Delete /></el-icon>
              <el-icon class="history-arrow"><ArrowRight /></el-icon>
            </div>
            <div class="history-meta">
              <span :class="['history-status', `status-${t.status}`]">
                {{ statusLabel[t.status] ?? t.status }}
              </span>
              <span class="history-time">{{ formatTime(t.updated_at) }}</span>
            </div>
          </div>
          <div v-if="history.length === 0" class="sect-empty">暂无历史任务</div>
        </div>
      </section>

      <!-- ⑤ 左下角用户区：含调试开关 + 设置入口（原配置控制台折叠至此） -->
      <div class="user-zone">
        <el-dropdown trigger="click" @command="onUserCommand" class="user-row-dropdown">
          <div class="user-row">
            <div class="user-avatar">{{ avatarText }}</div>
            <div class="user-meta">
              <div class="user-name">{{ displayName }}</div>
              <div class="user-role">{{ roleLabel }}</div>
            </div>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="change-password">
                <el-icon><Key /></el-icon>
                <span>修改密码</span>
              </el-dropdown-item>
              <el-dropdown-item command="logout" divided>
                <el-icon><SwitchButton /></el-icon>
                <span>退出登录</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <div class="user-actions">
          <div class="debug-row">
            <span class="debug-label">调试模式</span>
            <el-switch v-model="debugMode" size="small" @change="onToggleDebug" />
          </div>
          <el-dropdown trigger="click" @command="goSettings" class="settings-dropdown">
            <button type="button" class="settings-trigger">
              <el-icon><Setting /></el-icon>
              <span>设置</span>
              <el-icon class="caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="models">模型映射</el-dropdown-item>
                <el-dropdown-item command="agents">Agent 提示词</el-dropdown-item>
                <el-dropdown-item command="gates">Gate 审核</el-dropdown-item>
                <el-dropdown-item command="templates">任务模板</el-dropdown-item>
                <el-dropdown-item command="custom-providers">自定义 API</el-dropdown-item>
                <el-dropdown-item command="mcp-servers">MCP Server</el-dropdown-item>
                <el-dropdown-item command="experts">专家团</el-dropdown-item>
                <el-dropdown-item command="asset-center">经验 / 反思</el-dropdown-item>
                <el-dropdown-item v-if="authStore.me?.role === 'main'" command="accounts" divided>账号管理</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </el-aside>

    <el-container>
      <el-main class="app-main">
        <slot />
      </el-main>
    </el-container>

    <!-- 当前账号自助改密（无需旧密码，属「重置」语义） -->
    <el-dialog v-model="pwdVisible" title="修改密码" width="420px" destroy-on-close @closed="onPwdClosed">
      <el-form :model="pwdForm" label-width="86px" @submit.prevent>
        <el-form-item label="新密码" :error="pwdError">
          <el-input
            v-model="pwdForm.newPwd"
            type="password"
            show-password
            placeholder="至少 8 位"
            @input="pwdError = ''"
          />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input
            v-model="pwdForm.confirmPwd"
            type="password"
            show-password
            placeholder="再次输入新密码"
            @input="pwdError = ''"
            @keyup.enter="submitPwd"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdLoading" @click="submitPwd">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useSettingsStore } from '@/stores/settings';
import { useHistoryStore } from '@/stores/history';
import { useChatSessionStore } from '@/stores/chatSessionStore';
import { useAuthStore } from '@/stores/authStore';
import { Plus, Delete, Search, Key, Stamp } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { formatTime } from '@/utils/formatter';
import type { TaskStatus } from '@/types';

const route = useRoute();
const router = useRouter();
const settingsStore = useSettingsStore();
const { debugMode } = storeToRefs(settingsStore);

// 用户区：显示真实登录账号（此前硬编码「管理员」，对所有账号都错）
const authStore = useAuthStore();
const displayName = computed(() => authStore.me?.username || '未登录');
const roleLabel = computed(() => {
  const r = authStore.me?.role;
  if (r === 'main') return '主账号';
  if (r === 'sub') return '子账号';
  return '本地部署';
});
const avatarText = computed(() => (authStore.me?.username || 'U').charAt(0).toUpperCase());

// 侧边栏历史记录（共享 Pinia store：ChatEntry 提交后自动 refresh）
const historyStore = useHistoryStore();
const { items: history } = storeToRefs(historyStore);

// 聊天记录（共享 Pinia store：每次聊天一个窗口，跨会话记忆由后端注入）
const chatSessionStore = useChatSessionStore();
const { sessions: chatSessions } = storeToRefs(chatSessionStore);
const currentSessionId = computed(() => (route.params.sessionId as string | undefined) || null);

// 聊天记录实时搜索（按主题关键词过滤；空查询 = 全部）
const searchQuery = ref('');
const filteredSessions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return chatSessions.value;
  return chatSessions.value.filter((s) =>
    (s.topic || '').toLowerCase().includes(q) || (s.id || '').toLowerCase().includes(q)
  );
});

// 可折叠状态（用户偏好记忆到 localStorage）
const LS_HISTORY_EXPANDED = 'report-agent-team:history-expanded';
const historyExpanded = ref<boolean>(
  typeof localStorage !== 'undefined'
    ? localStorage.getItem(LS_HISTORY_EXPANDED) !== 'false'
    : true
);
function toggleHistory(): void {
  historyExpanded.value = !historyExpanded.value;
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(LS_HISTORY_EXPANDED, String(historyExpanded.value));
  }
}

// 聊天记录栏同款折叠交互（点击标题收缩/展开，偏好记忆 localStorage）
const LS_CHAT_EXPANDED = 'report-agent-team:chat-expanded';
const chatExpanded = ref<boolean>(
  typeof localStorage !== 'undefined'
    ? localStorage.getItem(LS_CHAT_EXPANDED) !== 'false'
    : true
);
function toggleChat(): void {
  chatExpanded.value = !chatExpanded.value;
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(LS_CHAT_EXPANDED, String(chatExpanded.value));
  }
}
onMounted(() => {
  // 首次进入外壳时补齐账号信息（persist 已有 me 则不重复请求）
  if (!authStore.me) void authStore.fetchMe();
  void historyStore.refresh();
  void chatSessionStore.refresh();
});

function goTask(taskId: string): void {
  router.push({ name: 'TaskTracking', params: { taskId } });
}

// ---- 聊天记录：每个会话一个窗口 ----
function openChat(id: string): void {
  router.push(`/chat/${id}`);
}
async function newChat(): Promise<void> {
  try {
    const s = await chatSessionStore.create({});
    router.push(`/chat/${s.id}`);
  } catch (e) {
    console.error('新建对话失败', e);
    router.push('/');
  }
}
async function delChat(id: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除后该对话全部消息不可恢复，确定删除吗？',
      '删除对话',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return; // 用户取消，不动
  }
  try {
    await chatSessionStore.remove(id);
    if (currentSessionId.value === id) router.push('/');
  } catch (e) {
    console.error('删除会话失败', e);
  }
}

const statusLabel: Record<TaskStatus, string> = {
  created: '待运行',
  running: '运行中',
  rework: '返工中',
  done: '已完成',
  escalated: '已升级',
  aborted: '已中止',
};

function isRunning(status: string): boolean {
  return status === 'running' || status === 'rework';
}

// ---- 历史记录：单删（运行中/返工中由后端 409 拦截）----
async function delTask(taskId: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除后该任务及其关联记录不可恢复，确定删除吗？',
      '删除任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }
  try {
    await historyStore.remove(taskId);
    ElMessage.success('已删除该任务');
  } catch (e: any) {
    if (e?.status === 409) {
      ElMessage.warning('运行中的任务不可删除');
    } else {
      console.error('删除任务失败', e);
      ElMessage.error('删除失败，请重试');
    }
  }
}

// ---- 聊天记录：一键清空全部 ----
async function clearAllChats(): Promise<void> {
  if (!chatSessions.value.length) return;
  try {
    await ElMessageBox.confirm(
      `将删除全部 ${chatSessions.value.length} 个会话，且不可恢复。确定吗？`,
      '清空全部聊天记录',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }
  try {
    const res = await chatSessionStore.clearAll();
    ElMessage.success(`已清空 ${res.deleted} 个会话`);
    // 当前会话必然已被清，若正停留在聊天页则退回首页
    if (route.path.startsWith('/chat/')) router.push('/');
  } catch (e) {
    console.error('清空聊天记录失败', e);
    ElMessage.error('清空失败，请重试');
  }
}

// ---- 历史记录：一键清空全部（运行中任务保留）----
async function clearAllTasks(): Promise<void> {
  if (!history.value.length) return;
  try {
    await ElMessageBox.confirm(
      '将清空全部已结束的历史任务（运行中的任务会保留），且不可恢复。确定吗？',
      '清空全部历史记录',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
    );
  } catch {
    return;
  }
  try {
    const res = await historyStore.clearAll();
    if (res.skipped_running > 0) {
      ElMessage.warning(`已清空 ${res.deleted} 个历史任务，${res.skipped_running} 个运行中任务已保留`);
    } else {
      ElMessage.success(`已清空 ${res.deleted} 个历史任务`);
    }
  } catch (e) {
    console.error('清空历史失败', e);
    ElMessage.error('清空失败，请重试');
  }
}

// 导航高亮：顶层项精确匹配；设置子页精确高亮；流转页回退到模板
const activeMenu = computed(() => {
  const p = route.path;
  if (p.startsWith('/settings')) return p === '/settings' ? '/settings/models' : p;
  if (p === '/') return '/';
  if (p === '/review') return '/review';
  if (p.startsWith('/agents')) return '/agents';
  // 能力市场（统一页）及其内嵌的三个直达路由，统一高亮「能力市场」
  if (p === '/market' || p.startsWith('/plugins') || p.startsWith('/skills') || p.startsWith('/channels')) return '/market';
  if (p.startsWith('/tasks')) return p.includes('/submit') ? '/submit' : '/templates';
  if (p.startsWith('/submit')) return '/submit';
  return '/';
});

function onToggleDebug(): void {
  settingsStore.toggleDebug();
  if (route.name === 'TaskTracking') {
    router.replace({ name: 'TaskTracking', params: { taskId: route.params.taskId } });
  }
}

function goSettings(cmd: string): void {
  if (cmd === 'custom-providers') {
    router.push('/settings/custom-providers');
    return;
  }
  if (cmd === 'mcp-servers') {
    router.push('/settings/mcp-servers');
    return;
  }
  router.push(`/settings/${cmd}`);
}

// ---- 当前账号自助改密 ----
const pwdVisible = ref(false);
const pwdLoading = ref(false);
const pwdForm = ref({ newPwd: '', confirmPwd: '' });
const pwdError = ref('');

function openChangePwd(): void {
  pwdForm.value = { newPwd: '', confirmPwd: '' };
  pwdError.value = '';
  pwdVisible.value = true;
}

async function submitPwd(): Promise<void> {
  const { newPwd, confirmPwd } = pwdForm.value;
  if (newPwd.length < 8) {
    pwdError.value = '密码至少 8 位';
    return;
  }
  if (newPwd !== confirmPwd) {
    pwdError.value = '两次输入的密码不一致';
    return;
  }
  pwdError.value = '';
  pwdLoading.value = true;
  try {
    await authStore.changePassword(newPwd);
    ElMessage.success('密码已修改，请重新登录');
    pwdVisible.value = false;
    // 密码已变，JWT 仍有效但强制重新登录以使新密码生效
    authStore.logout();
    if (router.currentRoute.value.name !== 'Login') {
      router.replace({ name: 'Login' });
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '修改失败');
  } finally {
    pwdLoading.value = false;
  }
}

function onPwdClosed(): void {
  pwdForm.value = { newPwd: '', confirmPwd: '' };
  pwdError.value = '';
}

function onUserCommand(cmd: string): void {
  if (cmd === 'logout') {
    authStore.logout();
    if (router.currentRoute.value.name !== 'Login') {
      router.replace({ name: 'Login' });
    }
  } else if (cmd === 'change-password') {
    openChangePwd();
  }
}
</script>

<style scoped>
.app-shell {
  height: 100vh;
}
.app-aside {
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  padding: 16px 14px 14px;
  box-shadow: 1px 0 6px rgba(17, 24, 39, 0.03);
}

/* ---------- 品牌区 ---------- */
.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 2px 8px 16px;
}
.brand-logo {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--brand), #06b6d4);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 6px 14px rgba(15, 157, 118, 0.22);
}
.brand-logo :deep(.el-icon) { font-size: 19px; }
.brand-text { line-height: 1.2; min-width: 0; }
.brand-name {
  font-weight: 700;
  font-size: 15px;
  color: var(--ink-900);
  letter-spacing: -0.01em;
  white-space: nowrap;
}
.brand-sub {
  font-size: 11px;
  color: var(--ink-400);
  margin-top: 2px;
  white-space: nowrap;
}

/* ---------- 主导航 ---------- */
.app-menu {
  border: none;
  background: transparent;
  flex: 0 0 auto;
  padding: 0 2px;
}
.app-menu :deep(.el-menu-item) {
  border-radius: var(--r);
  height: 44px;
  margin: 2px 0;
  color: var(--ink-700);
  font-weight: 500;
  font-size: 13.5px;
}
.app-menu :deep(.el-menu-item .el-icon) {
  color: var(--ink-400);
  font-size: 17px;
}
.app-menu :deep(.el-menu-item:hover) {
  background: var(--surface-2);
  color: var(--ink-900);
}
.app-menu :deep(.el-menu-item.is-active) {
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-weight: 600;
}
.app-menu :deep(.el-menu-item.is-active .el-icon) {
  color: var(--brand);
}

/* ---------- 可折叠区段（聊天记录 / 历史记录）---------- */
.sect {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0 2px;
}
/* 两块按权重分享剩余高度；折叠后退化为「只占标题高度」，不再留空洞 */
.sect:not(.collapsed):first-of-type { flex: 3 1 90px; }
.sect:not(.collapsed):last-of-type { flex: 2 1 70px; }
.sect.collapsed { flex: 0 0 auto; }

.sect-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 8px;
  margin-top: 10px;
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  color: var(--ink-400);
  transition: color 0.15s var(--ease), background 0.15s var(--ease);
}
.sect-head:hover {
  color: var(--ink-700);
  background: var(--surface-2);
}
.sect-head__icon { font-size: 14px; flex-shrink: 0; }
.sect-head__label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
  /* 关键：不换行、不压缩 —— 旧版「聊天记录」被挤成两行就是这个原因 */
  white-space: nowrap;
  flex: 0 1 auto;
}
.sect-head__count {
  font-size: 11px;
  font-weight: 600;
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-radius: 999px;
  padding: 0 6px;
  line-height: 16px;
  flex-shrink: 0;
}
.sect-head__caret {
  margin-left: auto;
  font-size: 12px;
  color: var(--ink-300, #cbd5e1);
  transition: transform 0.2s var(--ease);
  flex-shrink: 0;
}
.sect.collapsed .sect-head__caret { transform: rotate(-90deg); }

/* 搜索 + 新建对话（图标按钮）同一行，避免与标题挤在一起 */
.sect-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px 6px;
}
.chat-search {
  flex: 1 1 auto;
  min-width: 0;
}
.chat-search :deep(.el-input__wrapper) {
  border-radius: 8px;
  background: var(--surface-2);
  box-shadow: 0 0 0 1px var(--line) inset;
}
.chat-search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--brand) inset;
}
.icon-btn {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface);
  color: var(--brand-strong);
  cursor: pointer;
  transition: background 0.15s var(--ease), border-color 0.15s var(--ease);
}
.icon-btn:hover {
  background: var(--brand-soft);
  border-color: var(--brand);
}
.icon-btn :deep(.el-icon) { font-size: 14px; }
.icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: var(--surface);
}
/* 历史记录区「清空全部」图标按钮（紧跟标题右侧，hover 变红） */
.sect-head__clear {
  margin-left: auto;
  font-size: 13px;
  color: var(--ink-300, #cbd5e1);
  cursor: pointer;
  flex-shrink: 0;
  transition: color 0.15s var(--ease);
}
.sect-head__clear:hover { color: var(--danger, #ef4444); }

.sect-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0 4px;
  border-top: 1px solid var(--line);
}
.sect-list::-webkit-scrollbar { width: 4px; }
.sect-list::-webkit-scrollbar-thumb {
  background: var(--line);
  border-radius: 2px;
}
.sect-list::-webkit-scrollbar-track { background: transparent; }

.chat-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s var(--ease);
}
.chat-item:hover { background: var(--surface-2); }
.chat-item.active { background: var(--brand-soft); }
.chat-item.active .chat-item-topic { color: var(--brand-strong); font-weight: 600; }
.chat-item-icon {
  font-size: 15px;
  color: var(--ink-400);
  flex-shrink: 0;
}
.chat-item.active .chat-item-icon { color: var(--brand); }
.chat-item-topic {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--ink-900);
  font-weight: 500;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chat-item-del {
  font-size: 13px;
  color: var(--ink-300, #cbd5e1);
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s var(--ease), color 0.15s var(--ease);
}
.chat-item:hover .chat-item-del { opacity: 1; }
.chat-item-del:hover { color: var(--danger, #ef4444); }

.history-item {
  padding: 8px 9px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s var(--ease);
}
.history-item:hover { background: var(--surface-2); }
/* 历史记录单条删除图标：hover 显出，运行/返工中置灰且常显、不可点 */
.history-item-del {
  font-size: 13px;
  color: var(--ink-300, #cbd5e1);
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s var(--ease), color 0.15s var(--ease);
}
.history-item:hover .history-item-del { opacity: 1; }
.history-item-del:hover { color: var(--danger, #ef4444); }
.history-item-del.is-disabled {
  opacity: 1;
  color: var(--ink-300, #cbd5e1);
  cursor: not-allowed;
}
.history-item-del.is-disabled:hover { color: var(--ink-300, #cbd5e1); }
.history-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.history-topic {
  font-size: 13px;
  color: var(--ink-900);
  font-weight: 500;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.history-arrow {
  font-size: 12px;
  color: var(--ink-300, #cbd5e1);
  flex-shrink: 0;
}
.history-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}
.history-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 500;
}
.history-status.status-running { color: #2563eb; background: #dbeafe; }
.history-status.status-rework { color: #d97706; background: #fef3c7; }
.history-status.status-done { color: #059669; background: #d1fae5; }
.history-status.status-escalated { color: #dc2626; background: #fee2e2; }
.history-status.status-aborted { color: #4b5563; background: #e5e7eb; }
.history-status.status-created { color: #6b7280; background: #f3f4f6; }
.history-time {
  font-size: 11px;
  color: var(--ink-400);
}
.sect-empty {
  font-size: 12px;
  color: var(--ink-400);
  padding: 16px 10px;
  text-align: center;
}

/* ---------- 用户区 ---------- */
.user-zone {
  flex: 0 0 auto;
  border-top: 1px solid var(--line);
  padding: 12px 2px 0;
  margin-top: 8px;
}
.user-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 8px 10px;
}
.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--brand), var(--brand-strong));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}
.user-meta { line-height: 1.25; min-width: 0; }
.user-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-900);
  white-space: nowrap;
}
.user-role {
  font-size: 11px;
  color: var(--ink-400);
  white-space: nowrap;
}
.user-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.debug-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  border-radius: var(--r);
  transition: background 0.15s var(--ease);
}
.debug-row:hover { background: var(--surface-2); }
.debug-label {
  font-size: 13px;
  color: var(--ink-700);
  white-space: nowrap;
}
.settings-dropdown { width: 100%; }
.settings-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  background: var(--surface);
  color: var(--ink-700);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s var(--ease), border-color 0.15s var(--ease);
}
.settings-trigger:hover {
  background: var(--surface-2);
  border-color: var(--line-strong, #d8dde5);
}
.settings-trigger :deep(.el-icon) { color: var(--ink-400); }
.settings-trigger .caret {
  margin-left: auto;
  color: var(--ink-300, #cbd5e1);
}

/* ---------- 主区 ---------- */
.app-main {
  background: var(--surface-2);
  padding: var(--s-6);
}

@media (max-width: 860px) {
  .app-main { padding: 12px; }
  .app-aside {
    width: 68px !important;
    padding: 16px 8px;
  }
  .brand { justify-content: center; padding: 2px 0 16px; }
  .brand-text,
  .app-menu :deep(.el-menu-item span),
  .user-meta,
  .debug-label,
  .settings-trigger :deep(span),
  .sect-head__label,
  .sect-head__count,
  .chat-search {
    display: none;
  }
  .sect-head { justify-content: center; }
  .sect-head__caret { margin-left: 0; }
  .sect-tools { justify-content: center; }
  /* 窄屏只留 icon 栏：列表隐藏后 section 只剩标题，
     必须同时丢掉上面 flex:3/2 的分高，否则会留一条空白带。
     选择器与基础规则同形（含 :first/:last-of-type）以保证优先级更高 */
  .sect:not(.collapsed):first-of-type,
  .sect:not(.collapsed):last-of-type {
    flex: 0 0 auto;
  }
  .sect-list { display: none; }
  .user-row { justify-content: center; padding: 2px 0 10px; }
  .debug-row { justify-content: center; padding: 6px 0; }
  .settings-trigger { justify-content: center; padding: 9px 0; }
}
</style>
