<!--
  ChatEntry.vue · 对话优先入口（模型下拉升级 + 数据源插件）
  - 顶部：模型下拉（常驻，三分组：通用 / new-api 具体 / 自定义）+ 切换智能体
  - 中部：消息流（user/assistant 气泡 + 报告卡片）
  - 底部：输入框 + 发送按钮 + 场景建议 chips + 数据源插件按钮
  - 自定义 API 管理移至「设置 → 自定义 API」
  - 与 /chat 端点对话；当 ChatAgent 决定 generate_report 时返回 task_id，前端跳转任务追踪
-->
<template>
  <div class="chat-shell">
    <!-- 顶部 Agent 头 + 模型下拉 -->
    <div class="chat-head">
      <div class="bot-avatar"><el-icon><Notebook /></el-icon></div>
      <div class="bot-meta">
        <div class="bot-name">研报助手</div>
        <div class="bot-desc">能聊天、能调用多智能体研报技能</div>
      </div>

      <!-- 模型下拉：常驻 -->
      <div class="model-picker">
        <el-select
          v-model="selectedModel"
          size="default"
          class="model-select"
          popper-class="model-picker-popper"
          :disabled="sending"
          @change="onModelChange"
        >
          <template #prefix>
            <el-icon class="mp-prefix"><Cpu /></el-icon>
          </template>
          <el-option-group label="通用（new-api 自动路由）">
            <el-option
              v-for="m in groups.universal"
              :key="m.id"
              :value="m.id"
              :label="m.name"
              :disabled="m.disabled"
            >
              <div class="opt-row">
                <span class="opt-name">{{ m.name }}</span>
                <span v-if="m.disabled" class="opt-tag-warn">暂不支持</span>
              </div>
            </el-option>
          </el-option-group>
          <el-option-group label="new-api 具体模型">
            <el-option
              v-for="m in groups.concrete"
              :key="m.id"
              :value="m.id"
              :label="m.name"
            >
              <div class="opt-row">
                <span class="opt-name">{{ m.name }}</span>
                <span class="opt-tag-source">new-api</span>
              </div>
            </el-option>
          </el-option-group>
          <el-option-group v-if="groups.custom.length > 0" label="自定义 API">
            <el-option
              v-for="m in groups.custom"
              :key="m.id"
              :value="m.id"
              :label="m.name"
            >
              <div class="opt-row">
                <span class="opt-name">{{ m.name }}</span>
                <span class="opt-tag-custom">自定义</span>
              </div>
            </el-option>
          </el-option-group>
          <el-option-group v-if="groups.custom.length === 0" label="自定义 API">
            <el-option value="__add_custom__" class="add-custom-option">
              <div class="opt-row add-custom">
                <span>＋ 添加自定义 API（前往设置）</span>
              </div>
            </el-option>
          </el-option-group>
        </el-select>
      </div>

      <el-button text class="new-chat-btn" :icon="Plus" @click="onNewChat">新对话</el-button>

      <el-popover placement="bottom-end" :width="280" trigger="click" v-model:visible="agentPop">
        <template #reference>
          <el-button text class="agent-switch" :class="{ active: selectedAgents.length < knownAgents.length }">
            <span class="agent-avatars">
              <span
                v-for="a in knownAgents"
                :key="a"
                class="mini-avatar"
                :class="{ off: !selectedAgents.includes(a) }"
              >
                <el-icon><component :is="agentIcon(a.toLowerCase())" /></el-icon>
              </span>
            </span>
            切换智能体
          </el-button>
        </template>
        <div class="pop-title">选择参与研报的智能体</div>
        <el-checkbox-group v-model="selectedAgents" class="pop-agents">
          <el-checkbox v-for="a in knownAgents" :key="a" :value="a">
            <el-icon class="pop-agent-icon"><component :is="agentIcon(a.toLowerCase())" /></el-icon>{{ a }}
          </el-checkbox>
        </el-checkbox-group>
      </el-popover>
    </div>

    <!-- 消息流：空态渲染「欢迎 hero」并垂直居中，避免满屏空白；有对话才走气泡列表 -->
    <div class="chat-stream" ref="streamRef" :class="{ 'is-empty': isEmpty }">
      <div v-if="isEmpty" class="hero">
        <div class="hero-avatar"><el-icon><Notebook /></el-icon></div>
        <h3 class="hero-title">研报助手</h3>
        <p class="hero-desc">{{ GREETING.content }}</p>

        <div class="hero-scenes">
          <div class="hero-scenes__label">试试这些场景</div>
          <div class="hero-scenes__grid">
            <button
              v-for="p in presets"
              :key="p.id"
              type="button"
              class="scene-card"
              @click="usePreset(p)"
            >
              <el-icon class="scene-card__icon"><component :is="presetIcon(p.id)" /></el-icon>
              <span class="scene-card__body">
                <span class="scene-card__title">{{ p.label }}</span>
                <span class="scene-card__scope">{{ p.scope.slice(0, 3).join(' · ') }}</span>
              </span>
            </button>
          </div>
        </div>
      </div>

      <template v-else>
        <div
          v-for="(m, i) in messages"
          :key="i"
          :class="['bubble-row', m.role === 'user' ? 'user' : 'assistant']"
        >
          <div v-if="m.role === 'assistant'" class="msg-avatar"><el-icon><Notebook /></el-icon></div>
          <div class="bubble-card">
            <div v-if="m.thinking" class="thinking">
              <span class="dot" /> <span class="dot" /> <span class="dot" /> 思考中…
            </div>
            <div v-if="!m.thinking && m.tool_calls && m.tool_calls.length" class="toolcall-note">
              <el-icon><Connection /></el-icon> 自主调用工具：{{ m.tool_calls.join(' · ') }}
            </div>
            <div v-if="!m.thinking && m.sources && m.sources.length" class="sources-note">
              <div class="sources-head"><el-icon><Collection /></el-icon> 来源（{{ m.sources.length }}）</div>
              <ul class="sources-list">
                <li v-for="(s, si) in m.sources" :key="si">
                  <a v-if="s.url" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title || s.source }}</a>
                  <span v-else-if="s.tool" class="src-tool">{{ s.tool }}<span v-if="s.content" class="src-content">：{{ truncate(s.content) }}</span></span>
                  <span v-else>{{ s.source }}</span>
                </li>
              </ul>
            </div>
            <div v-if="!m.thinking" class="bubble-text" v-text="m.content" />
            <!-- 报告卡片：当 ChatAgent 触发了 generate_report -->
            <div v-if="m.task_id" class="report-card">
              <div class="report-card-head">
                <el-icon><DocumentChecked /></el-icon>
                <span class="report-card-title">研报任务已启动</span>
                <el-tag size="small" :type="m.task_status === 'running' ? 'warning' : 'info'">{{ m.task_status || 'running' }}</el-tag>
              </div>
              <div class="report-card-task">任务 ID：<code>{{ m.task_id.slice(0, 8) }}…</code></div>
              <el-button type="primary" size="small" plain @click="goTask(m.task_id)">查看进度 →</el-button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- 输入框 -->
    <div class="chat-composer">
      <div v-if="modelWarn" class="model-warn">⚠ {{ modelWarn }}</div>
      <div class="composer-row">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          class="composer-input"
          placeholder="跟研报助手聊聊…（Enter 发送，Shift+Enter 换行）"
          :disabled="sending"
          @keydown.enter.exact.prevent="onSend"
        />
        <el-button
          type="primary"
          :icon="Promotion"
          class="send-btn"
          :loading="sending"
          :disabled="!draft.trim()"
          @click="onSend"
        >发送</el-button>
      </div>
      <div v-if="error" class="err">
        <span class="err-icon">⚠</span>
        <span class="err-text">{{ error }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Promotion, DocumentChecked, Plus, Notebook, Connection, Collection, Cpu } from '@element-plus/icons-vue';
import { useHistoryStore } from '@/stores/history';
import { useChatSessionStore } from '@/stores/chatSessionStore';
import { chat as chatApi, type ChatMessage } from '@/services/chatService';
import { getSession } from '@/services/chatSessionService';
import { CHAT_PRESETS, KNOWN_AGENTS } from '@/utils/chatEntry';
import { agentIcon } from '@/utils/emoji';

interface PluginItem {
  id: string;
  name: string;
  category: string;
  status: string;
  checked: boolean;
}
interface ModelItem {
  id: string;
  name: string;
  description?: string;
  kind?: string;
  source?: string; // builtin | custom | gateway
  endpoint_id?: string;
  disabled?: boolean; // 标记「Chat 暂不支持」（vision/tts 等）
}

const route = useRoute();
const router = useRouter();
const presets = CHAT_PRESETS;
const knownAgents = KNOWN_AGENTS;

type UIMessage = ChatMessage & {
  thinking?: boolean;
  task_id?: string;
  task_status?: string;
  tool_calls?: string[];
  sources?: Array<{ source?: string; url?: string; title?: string; tool?: string; content?: string }>;
};

const GREETING: UIMessage = {
  role: 'assistant',
  content: '你好，我是研报助手。可以跟我闲聊、问问题；想看完整研报就说"帮我生成一份关于 X 的报告"，我会自动调多智能体系统跑一份给你。',
};

const messages = ref<UIMessage[]>([{ ...GREETING }]);

/**
 * 空态判定：只有「未动过的问候语」才渲染 hero。
 * 用内容比对而非 length<=1 —— 从 DB 载入的单条历史消息不应被误判为空态。
 */
const isEmpty = computed(() => {
  if (messages.value.length === 0) return true;
  if (messages.value.length > 1) return false;
  const m = messages.value[0];
  return !m.thinking && m.role === 'assistant' && m.content === GREETING.content;
});

/** 场景卡图标（Element Plus 组件名，全局注册；实测名称均存在） */
function presetIcon(id: string): string {
  const map: Record<string, string> = {
    standard: 'Document',
    competitor: 'Trophy',
    scan: 'Compass',
    earnings: 'Money',
  };
  return map[id] || 'MagicStick';
}

// ---- 会话（聊天记录 / 跨会话记忆）----
const chatSessionStore = useChatSessionStore();
const sessionId = ref<string | null>(null);

async function loadSession(id: string): Promise<void> {
  try {
    error.value = null;
    const { messages: dbMsgs } = await getSession(id);
    if (dbMsgs.length === 0) {
      messages.value = [{ ...GREETING }];
    } else {
      messages.value = dbMsgs.map((m) => ({
        role: m.role,
        content: m.content,
        task_id: m.task_id ?? undefined,
        task_status: m.task_status ?? undefined,
      }));
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载会话失败';
  }
}

// 路由切换会话（/ ↔ /chat/:id ↔ /chat/:otherId）时加载对应记录
watch(
  () => route.params.sessionId,
  (id) => {
    const next = (id as string | undefined) || null;
    if (next === sessionId.value) return;
    sessionId.value = next;
    if (next) {
      void loadSession(next);
    } else {
      messages.value = [{ ...GREETING }];
    }
  }
);

const draft = ref('');
const sending = ref(false);
const error = ref<string | null>(null);
const agentPop = ref(false);
const selectedAgents = ref<string[]>([...KNOWN_AGENTS]);

const streamRef = ref<HTMLElement | null>(null);

// 数据源
const plugins = ref<PluginItem[]>([]);
const selectablePlugins = computed(() => plugins.value.filter((p) => p.status !== 'coming_soon'));
const selectedPlugins = computed(() => selectablePlugins.value.filter((p) => p.checked).map((p) => p.id));

// 模型（来源：local + gateway-fetched + custom）
const models = ref<ModelItem[]>([]);
const LS_MODEL_KEY = 'rat.chat.selectedModel.v1';

// 三分组（universal = auto-* / kind=auto or universal；concrete = 其它 builtin；custom = 自定义）
const groups = computed(() => {
  const universal: ModelItem[] = [];
  const concrete: ModelItem[] = [];
  const custom: ModelItem[] = [];
  for (const m of models.value) {
    if (m.source === 'custom') custom.push(m);
    else if (m.kind === 'auto' || m.kind === 'universal' || m.id.startsWith('auto')) universal.push(m);
    else concrete.push(m);
  }
  // universal 排序：auto-chat > auto-reasoning > auto-fast > auto-vision > 其它
  const uniOrder = ['auto', 'auto-chat', 'auto-reasoning', 'auto-fast', 'auto-vision'];
  universal.sort((a, b) => uniOrder.indexOf(a.id) - uniOrder.indexOf(b.id));
  // vision/tts 标记「Chat 暂不支持」
  for (const m of universal) {
    if (m.id === 'auto-vision' || m.id.includes('vision') || m.id.includes('tts')) {
      m.disabled = true;
    }
  }
  return { universal, concrete, custom };
});

const selectedModel = ref<string>('auto-chat');
const modelWarn = ref<string | null>(null);

async function fetchPlugins(): Promise<void> {
  try {
    const res = await fetch('/api/v1/plugins');
    if (!res.ok) return;
    const data = await res.json();
    plugins.value = ((data.items ?? []) as Array<{ id: string; name: string; category: string; status: string }>)
      .filter((p) => p.status !== 'coming_soon')
      .map((p) => ({ id: p.id, name: p.name, category: p.category, status: p.status, checked: false }));
  } catch { plugins.value = []; }
}

async function fetchModels(): Promise<void> {
  try {
    const res = await fetch('/api/v1/models');
    if (!res.ok) return;
    const data = await res.json();
    models.value = (data.items ?? []) as ModelItem[];
  } catch { models.value = []; }
}

onMounted(() => {
  // localStorage 优先 → 否则默认 auto-chat（ChatAgent 后端 fallback）
  const saved = localStorage.getItem(LS_MODEL_KEY);
  if (saved && models.value.some((m) => m.id === saved)) {
    selectedModel.value = saved;
  }
  void fetchPlugins();
  void fetchModels();
  // 若直接以 /chat/:id 深链进入，加载该会话
  const rid = (route.params.sessionId as string | undefined) || null;
  if (rid) {
    sessionId.value = rid;
    void loadSession(rid);
  }
});

// 模型变更 → 持久化 + 检查警告
function onModelChange(val: string | number | undefined): void {
  const id = String(val || '');
  // 特殊项：下拉里的「添加自定义 API」占位 → 引导去设置页
  if (id === '__add_custom__') {
    nextTick(() => {
      selectedModel.value = (localStorage.getItem(LS_MODEL_KEY) || 'auto-chat');
    });
    router.push('/settings/custom-providers');
    return;
  }
  localStorage.setItem(LS_MODEL_KEY, id);
  // vision / tts 类模型 → 警告
  const m = models.value.find((x) => x.id === id);
  if (m && (id.includes('vision') || id.includes('tts') || id.includes('embedding'))) {
    modelWarn.value = `当前模型 ${m.name} 不是 Chat 友好型，调用可能失败或结果异常。`;
  } else {
    modelWarn.value = null;
  }
}

function usePreset(p: { label: string }): void {
  draft.value = `帮我生成一份关于「${p.label}」的报告`;
}

function scrollToBottom(): void {
  nextTick(() => {
    const el = streamRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

async function onSend(): Promise<void> {
  const text = draft.value.trim();
  if (!text || sending.value) return;

  const userMsg: ChatMessage = { role: 'user', content: text };
  messages.value.push(userMsg);
  draft.value = '';

  const placeholder = { role: 'assistant' as const, content: '', thinking: true };
  messages.value.push(placeholder);
  scrollToBottom();

  sending.value = true;
  error.value = null;
  try {
    const history: ChatMessage[] = messages.value
      .filter((m) => !m.thinking && m.content)
      .slice(0, -1)
      .slice(-16)
      .map((m) => ({ role: m.role, content: m.content }));

    const resp = await chatApi({
      message: text,
      // 已绑定会话时由后端从 DB 读取完整历史，前端不必重复传（避免重复+问候语泄漏）
      history: sessionId.value ? [] : history,
      model: selectedModel.value !== 'auto-chat' ? selectedModel.value : undefined,
      plugins: selectedPlugins.value.length ? selectedPlugins.value : undefined,
      session_id: sessionId.value ?? undefined,
    });

    const idx = messages.value.indexOf(placeholder);
    if (idx >= 0) {
      messages.value[idx] = {
        role: 'assistant',
        content: resp.reply,
        task_id: resp.task_id || undefined,
        task_status: resp.task_status || undefined,
        tool_calls: resp.tool_calls || undefined,
        sources: resp.sources || undefined,
      };
    }

    // 绑定 / 新建会话：后端在首次发送时自动建 session 并回传 session_id
    if (resp.session_id) {
      if (!sessionId.value) {
        sessionId.value = resp.session_id;
        // replace 让 URL 反映当前会话，且不产生多余的浏览器历史记录
        router.replace(`/chat/${resp.session_id}`);
      }
      void chatSessionStore.refresh();
    }

    if (resp.task_id) {
      void useHistoryStore().refresh();
    }
    scrollToBottom();
  } catch (e) {
    const msg = e instanceof Error ? e.message : '对话失败';
    const detail = (e as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
    const detailMsg = typeof detail === 'string' ? detail : detail?.message;
    error.value = detailMsg || msg;
    const idx = messages.value.indexOf(placeholder);
    if (idx >= 0) {
      const m = (detailMsg || msg);
      const isNet = m.includes('HTTP 0') || m.toLowerCase().includes('failed to fetch') || m.includes('NetworkError');
      messages.value[idx] = {
        role: 'assistant',
        content: isNet ? '⚠️ 网络错误，请刷新页面后重试。' : `⚠️ 服务出错：${m}`,
        tool_calls: [],
      };
    }
  } finally {
    sending.value = false;
  }
}

function goTask(taskId: string): void {
  router.push({ name: 'TaskTracking', params: { taskId } });
}

function truncate(text: string | undefined, n = 120): string {
  if (!text) return '';
  return text.length > n ? text.slice(0, n) + '…' : text;
}

function onNewChat(): void {
  sessionId.value = null;
  messages.value = [{ ...GREETING }];
  error.value = null;
  router.push('/');
}

// 同步刷新：custom provider 变化后 models 也变了，要刷新分组
watch(groups, () => {
  // no-op，目前仅依赖 selectedModel 锁定
}, { deep: true });
</script>

<style scoped>
.chat-shell {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 48px);
  max-width: 880px;
  margin: 0 auto;
  background: #fff;
  border: 1px solid #eef0f2;
  border-radius: 18px;
  box-shadow: 0 8px 30px rgba(17, 24, 39, 0.06);
  overflow: hidden;
}
.chat-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid #f3f4f6;
  flex-shrink: 0;
}
.bot-avatar {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--brand), #06b6d4);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex-shrink: 0;
  box-shadow: 0 6px 14px rgba(15, 157, 118, 0.22);
}
.bot-avatar :deep(.el-icon) { font-size: 19px; }
.bot-meta { flex: 1; min-width: 0; }
.bot-name {
  font-weight: 700;
  font-size: 15px;
  color: #111827;
  line-height: 1.3;
}
.bot-desc { font-size: 12px; color: #9ca3af; }

/* 模型选择 */
.model-picker { flex-shrink: 0; min-width: 220px; }
.model-select :deep(.el-select__wrapper) {
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  background: #f9fafb;
}
.mp-prefix { margin-right: 4px; color: var(--ink-400); }

.opt-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.opt-name { color: #1f2937; font-weight: 500; }
.opt-tag-warn {
  font-size: 11px;
  color: #d97706;
  background: #fef3c7;
  padding: 1px 6px;
  border-radius: 4px;
}
.opt-tag-source {
  font-size: 10px;
  color: #9ca3af;
  background: #f3f4f6;
  padding: 1px 5px;
  border-radius: 4px;
}
.opt-tag-custom {
  font-size: 10px;
  color: #fff;
  background: linear-gradient(135deg, #8b5cf6, #ec4899);
  padding: 1px 5px;
  border-radius: 4px;
}
.add-custom { color: #10b981; font-weight: 500; }

/* popover */
.agent-switch {
  color: #6b7280;
  font-size: 13px;
  border: 1px solid #eef0f2;
  border-radius: 999px;
  padding: 4px 12px;
  flex-shrink: 0;
}
.agent-switch.active { color: #10b981; border-color: #d1fae5; }
.new-chat-btn {
  color: #10b981;
  font-size: 13px;
  border: 1px solid #d1fae5;
  border-radius: 999px;
  padding: 4px 12px;
  flex-shrink: 0;
}
.new-chat-btn:hover { background: #ecfdf5; }
.agent-avatars { display: inline-flex; gap: 2px; margin-right: 6px; align-items: center; }
.mini-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
}
.mini-avatar :deep(.el-icon) { font-size: 14px; }
.mini-avatar.off { opacity: 0.3; filter: grayscale(1); }
.pop-agent-icon { margin-right: 6px; vertical-align: -2px; }

.pop-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }
.pop-title-spaced { margin-top: 14px; }
.pop-agents { display: flex; flex-direction: column; gap: 6px; }

.pop-empty { font-size: 12px; color: #9ca3af; padding: 6px 0; }

/* 消息流 */
.chat-stream {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 18px 18px 8px;
  background: #f9fafb;
  min-height: 0;
}
.bubble-row { display: flex; margin-bottom: 14px; gap: 10px; align-items: flex-start; }
.bubble-row.user { justify-content: flex-end; }
.msg-avatar {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--brand-soft);
  color: var(--brand-strong);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.msg-avatar :deep(.el-icon) { font-size: 16px; }
.bubble-card {
  max-width: 78%;
  border-radius: 14px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.55;
  word-break: break-word;
  white-space: pre-wrap;
}
.bubble-row.assistant .bubble-card {
  background: #fff;
  border: 1px solid #f3f4f6;
  color: #1f2937;
  border-top-left-radius: 4px;
}
.bubble-row.user .bubble-card {
  background: linear-gradient(135deg, #10b981, #06b6d4);
  color: #fff;
  border-top-right-radius: 4px;
}
.thinking {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #9ca3af;
}
.thinking .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9ca3af;
  animation: dot 1.2s infinite ease-in-out both;
}
.thinking .dot:nth-child(2) { animation-delay: 0.15s; }
.thinking .dot:nth-child(3) { animation-delay: 0.3s; }
@keyframes dot {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
.report-card {
  margin-top: 10px;
  padding: 10px 12px;
  background: #ecfdf5;
  border: 1px solid #d1fae5;
  border-radius: 10px;
  font-size: 13px;
  color: #065f46;
}
.report-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-weight: 600;
}
.report-card-title { color: #065f46; }
.report-card-task { color: #047857; font-size: 12px; margin-bottom: 8px; }
.report-card-task code { background: rgba(0,0,0,0.05); padding: 1px 5px; border-radius: 4px; }

.toolcall-note {
  margin-bottom: 8px;
  font-size: 12px;
  color: #0f766e;
  background: #f0fdfa;
  border: 1px solid #ccfbf1;
  border-radius: 8px;
  padding: 6px 10px;
}

.sources-note {
  margin-bottom: 8px;
  font-size: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 6px 10px;
}
.sources-head {
  font-weight: 600;
  color: #475569;
  margin-bottom: 4px;
}
.sources-list {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sources-list li { color: #475569; }
.sources-list a {
  color: #2563eb;
  text-decoration: none;
  word-break: break-all;
}
.sources-list a:hover { text-decoration: underline; }
.src-tool {
  color: #0f766e;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
.src-content { color: #64748b; }

/* 空态 hero：垂直居中，填满空白但不像"没做完" */
.chat-stream.is-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(900px 320px at 50% -8%, var(--brand-soft) 0%, rgba(231, 247, 240, 0) 70%),
    var(--surface-2);
}
.hero {
  width: 100%;
  max-width: 640px;
  text-align: center;
  padding: 10px 4px 30px;
}
.hero-avatar {
  width: 64px;
  height: 64px;
  margin: 0 auto 16px;
  border-radius: var(--r-lg);
  background: linear-gradient(135deg, var(--brand), #06b6d4);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 12px 28px rgba(15, 157, 118, 0.28);
}
.hero-avatar :deep(.el-icon) {
  font-size: 32px;
}
.hero-title {
  margin: 0 0 8px;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink-900);
}
.hero-desc {
  margin: 0 auto 26px;
  max-width: 540px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--ink-500);
}
.hero-scenes {
  text-align: left;
}
.hero-scenes__label {
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-400);
  margin-bottom: 12px;
}
.hero-scenes__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.scene-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: transform 0.18s var(--ease), box-shadow 0.18s var(--ease),
    border-color 0.18s var(--ease);
}
.scene-card:hover {
  transform: translateY(-2px);
  border-color: var(--brand);
  box-shadow: var(--sh-md);
}
.scene-card__icon {
  font-size: 20px;
  color: var(--brand);
  flex: none;
}
.scene-card__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.scene-card__title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink-900);
}
.scene-card__scope {
  font-size: 11.5px;
  color: var(--ink-400);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 620px) {
  .hero-scenes__grid {
    grid-template-columns: 1fr;
  }
}

.chat-composer {
  padding: 12px 18px 14px;
  border-top: 1px solid #f3f4f6;
  background: #fff;
  flex-shrink: 0;
}
.model-warn {
  font-size: 12px;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 8px;
}
.composer-row { display: flex; gap: 10px; align-items: flex-end; }
.composer-input :deep(.el-textarea__inner) {
  border-radius: 14px;
  font-size: 14px;
  padding: 10px 14px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  border-color: #e5e7eb;
}
.composer-input :deep(.el-textarea__inner):focus {
  box-shadow: 0 2px 10px rgba(16,185,129,0.10);
  border-color: #10b981;
}
.send-btn {
  height: 40px;
  border-radius: 12px;
  padding: 0 18px;
  font-weight: 500;
  background: linear-gradient(135deg, #10b981, #06b6d4);
  border-color: #10b981;
}
.send-btn:hover { background: linear-gradient(135deg, #0ea372, #0897b3); border-color: #0ea372; }
.err {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.err-icon { flex-shrink: 0; }
.err-text { word-break: break-word; }
</style>