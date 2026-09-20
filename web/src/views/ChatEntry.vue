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
          <!-- 编辑这条消息：hover 才出现，避免气泡流里到处是按钮 -->
          <button
            v-if="m.role === 'user' && !m.thinking"
            type="button"
            class="edit-msg-btn"
            title="编辑这条消息并重发（会删除该条及其后的记录）"
            @click="startEdit(i)"
          >
            <el-icon><Edit /></el-icon>
          </button>
          <div class="bubble-card">
            <div v-if="m.thinking" class="thinking">
              <span class="dot" /> <span class="dot" /> <span class="dot" /> 思考中…
            </div>
            <div v-if="!m.thinking && m.tool_calls && m.tool_calls.length" class="toolcall-note">
              <el-icon><Connection /></el-icon> 自主调用工具：{{ m.tool_calls.join(' · ') }}
            </div>
            <div v-if="!m.thinking && m.source_warnings && m.source_warnings.length" class="srcwarn-note">
              <el-alert type="warning" :closable="false" show-icon>
                <template #title>本次检索数据不完整，回答可能未经检索验证</template>
                <ul class="srcwarn-list">
                  <li v-for="(w, wi) in m.source_warnings" :key="wi">{{ w }}</li>
                </ul>
              </el-alert>
            </div>
            <div v-if="!m.thinking && m.sources && m.sources.length && m.sources_are_real !== false" class="sources-note">
              <div class="sources-head"><el-icon><Collection /></el-icon> 来源（{{ m.sources.length }}）</div>
              <ul class="sources-list">
                <li v-for="(s, si) in m.sources" :key="si">
                  <a v-if="s.url" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title || s.source }}</a>
                  <span v-else-if="s.tool" class="src-tool">{{ s.tool }}<span v-if="s.content" class="src-content">：{{ truncate(s.content) }}</span></span>
                  <span v-else>{{ s.source }}</span>
                </li>
              </ul>
            </div>
            <div v-else-if="!m.thinking && m.sources && m.sources.length" class="sources-note">
              <!-- sources_are_real === false：来源不可信（含未配置密钥的源 / 占位数据），
                   不展示成「引用」。与其给用户 8 条无法核验的「来源」，不如明说没有。 -->
              <div class="sources-head"><el-icon><WarningFilled /></el-icon> 本次无可核验来源</div>
              <div class="sources-hint">检索数据不完整，已隐去不可信来源，详见上方提示。</div>
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
      <!-- 子账号自备模型 API：一个模型都没有时，先把话说清楚，并禁用发送（DESIGN_subaccount_model_isolation.md §3-D5） -->
      <el-alert
        v-if="noModelConfigured"
        type="warning"
        :closable="false"
        show-icon
        class="no-model-alert"
      >
        <template #title>尚未配置模型 API</template>
        <div class="no-model-body">
          <span>
            本账号需要你自备模型 API（子账号不共享主账号的 new-api 网关）。请先到「自定义 API」
            添加你自己的 Provider（base_url + API Key）与模型，再回到这里对话。
          </span>
          <el-button size="small" type="primary" @click="router.push('/settings/custom-providers')">
            去配置
          </el-button>
        </div>
      </el-alert>
      <div v-if="modelWarn" class="model-warn">⚠ {{ modelWarn }}</div>
      <!-- 编辑态横幅：把「发送 = 删掉这条及其后并重新生成」讲清楚，不让人以为只是改个字 -->
      <div v-if="editingIndex !== null" class="edit-banner">
        <span class="edit-banner__text">
          <el-icon><Edit /></el-icon>
          正在编辑第 {{ editingIndex + 1 }} 条消息 · 发送后将删除该条及其后的
          {{ messages.length - editingIndex - 1 }} 条记录并重新生成
        </span>
        <el-button size="small" text @click="cancelEdit">取消编辑</el-button>
      </div>
      <div class="composer-row">
        <el-input
          v-model="draft"
          ref="inputRef"
          type="textarea"
          :rows="2"
          resize="none"
          class="composer-input"
          placeholder="跟研报助手聊聊…（Enter 发送，Shift+Enter 换行）"
          :disabled="sending"
          @keydown.enter.exact.prevent="onSend"
        />
        <!-- 生成中：发送按钮换成「停止」，是真的中断请求（服务端会据此放弃落库） -->
        <el-button
          v-if="sending"
          type="danger"
          plain
          :icon="VideoPause"
          class="stop-btn"
          @click="onStop"
        >停止</el-button>
        <el-button
          v-else
          type="primary"
          :icon="Promotion"
          class="send-btn"
          :disabled="!draft.trim() || noModelConfigured"
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
import { ElMessage } from 'element-plus';
import { Promotion, DocumentChecked, Plus, Notebook, Connection, Collection, Cpu, Edit, VideoPause } from '@element-plus/icons-vue';
import { useHistoryStore } from '@/stores/history';
import { useChatSessionStore } from '@/stores/chatSessionStore';
import { chat as chatApi, type ChatMessage } from '@/services/chatService';
// 模型/插件列表是**账号作用域**数据（后端已加鉴权）：必须带 Bearer，否则 401。
// 原先的裸 fetch 不带 token，会导致无租户上下文 → 读到全局/主账号配置（跨账号泄漏）。
import { authFetch } from '@/api/client';
import { getSession, truncateFrom } from '@/services/chatSessionService';
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
  /** 诚实降级告警：本次未参与检索的数据源 / 被剔除的占位数据（后端 source_warnings） */
  source_warnings?: string[];
  /** sources 是否为纯真实来源；false 时不得把 sources 当引用展示 */
  sources_are_real?: boolean;
  /** DB 消息 id：编辑重发时用它精确截断（本地未落库的消息没有此值） */
  dbId?: string;
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
        dbId: m.id,
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
    cancelEdit();   // 切换会话：编辑态里的下标属于旧列表，必须清掉
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
/** ElInput 实例句柄：只用到 focus()，故不引 ElInput 类型，避免无谓耦合 */
const inputRef = ref<{ focus?: () => void } | null>(null);

// ---- 停止 / 编辑重发（DESIGN_chat_interrupt_edit.md）----
/** 在飞请求的中止句柄。点「停止」真的 abort（服务端探测断连后放弃落库）。 */
let abortCtl: AbortController | null = null;
/** 编辑态：正在编辑第几条（null = 不在编辑） */
const editingIndex = ref<number | null>(null);

function startEdit(i: number): void {
  const m = messages.value[i];
  if (!m || m.role !== 'user' || m.thinking) return;
  editingIndex.value = i;
  draft.value = m.content;
  nextTick(() => {
    inputRef.value?.focus?.();
  });
}

function cancelEdit(): void {
  editingIndex.value = null;
  draft.value = '';
}

function onStop(): void {
  // 只 abort，不在这里改消息列表：收尾统一走 onSend 的 catch（那里才知道要不要
  // 把文本还回输入框），避免两条路径各删一半。
  abortCtl?.abort();
}

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

const selectedModel = ref<string>('');
const modelWarn = ref<string | null>(null);

/**
 * 真正**可用**的模型：必须有真实端点（endpoint_id），且不是 kind=auto / id=auto 的占位项。
 *
 * 为什么不能沿用旧的「默认 auto-chat」：`auto-chat` / `auto-reasoning` 是**主账号 new-api 网关**
 * 的 universal alias。子账号从 2026-09-15 起不再继承主账号网关（endpoints 为空、网关拉取关闭），
 * 列表里根本不会有这些条目；此时若仍预选 auto-chat，请求会带着一个不存在于任何端点的模型名
 * 打到子账号自己的 provider 上 → 400「model not found」，用户看到一个莫名其妙的错误。
 * 设计依据：DESIGN_subaccount_model_isolation.md §3-D5
 */
const usableModels = computed(() =>
  models.value.filter((m) => !!m.endpoint_id && m.kind !== 'auto' && m.id !== 'auto'),
);
/** 一个可用模型都没有 = 尚未配置自己的模型 API */
const noModelConfigured = computed(() => usableModels.value.length === 0);

/** 把 selectedModel 归一化到「当前列表里真实可用」的值；空/失效则取第一个可用项。 */
function normalizeSelectedModel(): void {
  const usable = usableModels.value;
  if (!selectedModel.value || !usable.some((m) => m.id === selectedModel.value)) {
    const fallback = usable[0]?.id ?? '';
    if (fallback !== selectedModel.value) {
      selectedModel.value = fallback;
      if (fallback) localStorage.setItem(LS_MODEL_KEY, fallback);
    }
  }
}

async function fetchPlugins(): Promise<void> {
  try {
    const res = await authFetch('/api/v1/plugins');
    if (!res.ok) return;
    const data = await res.json();
    plugins.value = ((data.items ?? []) as Array<{ id: string; name: string; category: string; status: string }>)
      .filter((p) => p.status !== 'coming_soon')
      .map((p) => ({ id: p.id, name: p.name, category: p.category, status: p.status, checked: false }));
  } catch { plugins.value = []; }
}

async function fetchModels(): Promise<void> {
  try {
    const res = await authFetch('/api/v1/models');
    if (!res.ok) return;
    const data = await res.json();
    models.value = (data.items ?? []) as ModelItem[];
  } catch { models.value = []; }
  // 列表就绪后立刻归一化选中项（覆盖「localStorage 里存了主账号遗留的 auto-chat」这种情况）
  normalizeSelectedModel();
}

onMounted(() => {
  // localStorage 优先，但**只在它仍是当前列表里的可用项时**才采用；否则交给 normalizeSelectedModel
  // 回落到第一个真实可用模型（子账号：自配 provider 后才非空）。
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
      selectedModel.value = localStorage.getItem(LS_MODEL_KEY) || '';
      normalizeSelectedModel();
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
  // 未配置模型 API 时直接拦住并给指引，不要把一个注定 400 的请求打出去
  // （设计依据：DESIGN_subaccount_model_isolation.md §3-D3/D5）
  if (noModelConfigured.value) {
    ElMessage.warning('请先到「设置 → 自定义 API」配置你自己的模型 API（Provider + API Key）');
    return;
  }

  // ---- 编辑重发：**先删旧记录，再发新的** ----
  // 为什么不只改页面：库里若仍留着打错的那条，刷新会「复活」它，且后续每轮对话
  // 模型仍会从 DB 读到它 —— 那就是「看着改了、其实没改」。见设计 §3.2。
  if (editingIndex.value !== null) {
    const target = messages.value[editingIndex.value];
    if (sessionId.value && target?.dbId) {
      try {
        await truncateFrom(sessionId.value, target.dbId);
      } catch (e) {
        // 截断失败绝不继续发：否则历史里同时留着「旧的那条」和「新的一条」。
        ElMessage.error(
          '删除旧记录失败，已取消发送（避免历史里留下旧内容）：'
          + (e instanceof Error ? e.message : String(e)),
        );
        return;
      }
    }
    // 截断成功（或该条从未落库，本地删就对）→ 页面同步删掉该条及其后
    messages.value.splice(editingIndex.value);
    editingIndex.value = null;
    scrollToBottom();
  }

  const userMsg: UIMessage = { role: 'user', content: text };
  messages.value.push(userMsg);
  draft.value = '';

  const placeholder = { role: 'assistant' as const, content: '', thinking: true };
  messages.value.push(placeholder);
  scrollToBottom();

  abortCtl = new AbortController();
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
      model: selectedModel.value ? selectedModel.value : undefined,
      plugins: selectedPlugins.value.length ? selectedPlugins.value : undefined,
      session_id: sessionId.value ?? undefined,
    }, abortCtl.signal);

    // 回填 DB id：编辑重发时靠它精确定位要截断的起点（持久化降级时后端回 null）
    userMsg.dbId = resp.user_message_id ?? undefined;

    const idx = messages.value.indexOf(placeholder);
    if (idx >= 0) {
      messages.value[idx] = {
        role: 'assistant',
        content: resp.reply,
        task_id: resp.task_id || undefined,
        task_status: resp.task_status || undefined,
        tool_calls: resp.tool_calls || undefined,
        sources: resp.sources || undefined,
        source_warnings: resp.source_warnings || undefined,
        sources_are_real: resp.sources_are_real,
        dbId: resp.assistant_message_id ?? undefined,
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
    // ---- 用户点「停止」：这不是故障，绝不能渲染成「服务出错」 ----
    const err = e as { name?: string; code?: string };
    const aborted = err?.name === 'AbortError'
      || err?.name === 'CanceledError'
      || err?.code === 'ERR_CANCELED';
    if (aborted) {
      const pIdx = messages.value.indexOf(placeholder);
      if (pIdx >= 0) messages.value.splice(pIdx, 1);   // 去掉「思考中…」
      const uIdx = messages.value.indexOf(userMsg);
      if (uIdx >= 0) messages.value.splice(uIdx, 1);   // 去掉刚发的那条（后端本轮不落库，本地删即一致）

      // 竞态兜底：abort 的瞬间服务端可能刚好跑完并落库（窗口极窄但真实存在）。
      // 此时「本地删掉」会与 DB 不一致 → 以库为准重拉一次，并如实告知，不装作已取消。
      if (sessionId.value) {
        try {
          const { messages: dbMsgs } = await getSession(sessionId.value);
          if (dbMsgs.some((m) => m.content === text)) {
            messages.value = dbMsgs.map((m) => ({
              role: m.role,
              content: m.content,
              task_id: m.task_id ?? undefined,
              task_status: m.task_status ?? undefined,
              dbId: m.id,
            }));
            error.value = null;
            ElMessage.warning('这次没能及时停下，回答已保存到聊天记录。');
            return;
          }
        } catch { /* 重拉失败不影响本地清理 */ }
      }

      draft.value = text;   // 文本还回输入框，可直接改错字重发
      error.value = null;
      ElMessage.info('已停止生成，内容已放回输入框，可直接修改后重发');
      return;
    }

    const msg = e instanceof Error ? e.message : '对话失败';
    const detail = (e as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail;
    const detailMsg = typeof detail === 'string' ? detail : detail?.message;
    error.value = detailMsg || msg;
    const idx = messages.value.indexOf(placeholder);
    if (idx >= 0) {
      const m = (detailMsg || msg);
      const isNet = m.includes('HTTP 0') || m.toLowerCase().includes('failed to fetch') || m.includes('NetworkError');
      // 「没配模型 API」是配置缺失，后端返回的原文本身就是可执行指引 →
      // 不要再套「服务出错」的误导性前缀（DESIGN_subaccount_model_isolation.md §3-D5）。
      const isConfigMissing = m.includes('尚未配置模型 API') || m.includes('配置你自己的 Provider');
      messages.value[idx] = {
        role: 'assistant',
        content: isConfigMissing
          ? `⚠️ ${m}`
          : (isNet ? '⚠️ 网络错误，请刷新页面后重试。' : `⚠️ 服务出错：${m}`),
        tool_calls: [],
      };
    }
  } finally {
    sending.value = false;
    abortCtl = null;
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
/* 诚实降级告警：本次检索面不完整时必须显眼，不能藏在气泡底部小字里 */
.srcwarn-note {
  margin-bottom: 8px;
}
.srcwarn-list {
  margin: 4px 0 0;
  padding-left: 18px;
  line-height: 1.6;
}
.sources-hint {
  color: #94a3b8;
  font-size: 12px;
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

/* 未配置模型 API 的引导条：放在输入框正上方，离"会失败的那个动作"最近 */
.no-model-alert {
  margin-bottom: 8px;
}
.no-model-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  line-height: 1.6;
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
/* 「停止」：与发送同尺寸同圆角，视觉上是同一个位置换了语义 */
.stop-btn {
  height: 40px;
  border-radius: 12px;
  padding: 0 18px;
  font-weight: 500;
}
/* 编辑态横幅：把「发送 = 删掉这条及其后并重新生成」讲在明面上 */
.edit-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  padding: 7px 12px;
  border-radius: 10px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #92400e;
  font-size: 12px;
}
.edit-banner__text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
/* user 气泡左侧 hover 才出现的「编辑」 */
.edit-msg-btn {
  align-self: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #6b7280;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, border-color 0.15s;
}
.bubble-row.user:hover .edit-msg-btn { opacity: 1; }
.edit-msg-btn:hover { color: #0f9d76; border-color: #10b981; }
.edit-msg-btn:focus-visible { opacity: 1; outline: 2px solid #10b981; outline-offset: 1px; }
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