# M7 前端实现方案 · Vue3 SPA

> **状态**：设计草案（评审通过前不落地任何前端代码）
> **日期**：2026-09-05
> **姊妹文档**：`UIDESIGN.md` v1.4（UI 设计）+ `API_SPEC.md` v1.0（HTTP/WebSocket 接口）
> **纪律**：先出设计文档，不写代码。评审通过前不落地任何 `*.vue` / `*.ts` 前端实现。

---

## 0. 执行摘要

M6 HTTP/WebSocket 服务层已冻结（4 REST 端点 + 7 WebSocket 事件），引擎运行时契约已冻结（M5 回归验证）。M7 的目标是：**基于已冻结的接口面，实现 UIDESIGN.md v1.4 定义的 Vue3 SPA 前端**。

**核心决策**：
- 技术栈：Vue3 + Pinia + Vite + TypeScript + VueUse
- UI 组件库：Element Plus（与用户偏好一致，统一 Web 面板沿用 unified-inbox 风格）
- WebSocket 客户端：原生 WebSocket + @vueuse/core useWebSocket
- 状态管理：Pinia Store（按页面/功能拆分）
- 样式：CSS 变量主题 + 预设颜色（涨红跌绿约定，研报不涉及 K 线但保留品牌色）
- 路由：Vue Router 4

---

## 1. 架构总览（UI ↔ 服务层）

```
┌─────────────────────────────────────────────────────────┐
│                    Vue3 SPA (浏览器)                      │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ 模板选择  │  │ 任务提交  │  │ 运行追踪  │  │ 结果页  │ │
│  │ View     │  │ View     │  │ View     │  │ View   │ │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └───┬────┘ │
│        │              │              │             │      │
│  ┌─────┴────┐  ┌─────┴────┐  ┌─────┴────┐  ┌────┴────┐│
│  │ Template  │  │ TaskForm │  │ Timeline │  │ Result  ││
│  │ Store     │  │ Store    │  │ Store    │  │ Store   ││
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └────┬────┘│
│        │              │              │             │      │
│        └──────┬───────┴──────┬───────┴───────┬─────┘      │
│               │               │               │            │
│         ┌─────┴─────┐  ┌─────┴──────┐  ┌────┴────┐       │
│         │ API Client│  │ WS Client  │  │ Audit   │       │
│         │ (REST)    │  │ (Stream)   │  │ Export  │       │
│         └─────┬─────┘  └─────┬──────┘  └────┬────┘       │
│               │               │               │            │
└───────────────┼───────────────┼───────────────┼────────────┘
                │               │               │
                │  REST         │ WebSocket     │  REST
                │               │               │
                ▼               ▼               ▼
    ┌──────────────────────────────────────────────────┐
    │           FastAPI 服务层 (M6 已实现)               │
    │  POST /tasks          WS /tasks/{id}/stream      │
    │  GET /tasks/{id}      agent_complete / ...       │
    │  POST /tasks/{id}/review                         │
    │  GET /tasks/{id}/audit-export                    │
    └──────────────────────────────────────────────────┘
```

**关键约束**（防混淆，对齐 UIDESIGN §1）：
- UI 是**观测面与输入面**，不做编排、不改业务流
- 只消费引擎暴露的状态投影，不让用户修改业务工作流图
- 只回写两类用户输入：`user_task` 提交、escalate 后人工操作

---

## 2. 技术栈与依赖

| 项 | 选型 | 理由 |
|---|---|---|
| 框架 | Vue3 (Composition API) | 团队熟悉，与 unified-inbox 一致 |
| 构建 | Vite 5 | 快速热更新，开发体验好 |
| 状态 | Pinia | 轻量、TypeScript 友好、DevTools 集成 |
| UI 组件 | Element Plus | 中文友好、主题定制成熟、unified-inbox 沿用 |
| WebSocket | @vueuse/core useWebSocket | 自动重连、状态管理、类型安全 |
| HTTP | axios | 拦截器、错误处理成熟 |
| 路由 | Vue Router 4 | 官方路由 |
| 样式 | SCSS + CSS 变量 | 主题切换、统一设计系统 |
| 类型 | TypeScript 5.5+ | 类型安全、API 契约校验 |
| 图标 | @element-plus/icons-vue | 内置图标集 |
| 时间 | dayjs | 轻量时间库 |

### package.json 核心依赖

```json
{
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.2.0",
    "pinia": "^2.1.0",
    "element-plus": "^2.7.0",
    "@element-plus/icons-vue": "^2.3.1",
    "axios": "^1.7.0",
    "@vueuse/core": "^10.0.0",
    "dayjs": "^1.11.0",
    "sass": "^1.70.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.0.0",
    "typescript": "^5.5.0",
    "vite": "^5.2.0"
  }
}
```

---

## 3. 项目结构

```
frontend/
├── public/
│   └── index.html
├── src/
│   ├── assets/                  # 静态资源
│   │   └── styles/              # 全局样式
│   │       ├── variables.scss   # CSS 变量（主题色、间距、字体）
│   │       ├── reset.scss       # 重置样式
│   │       └── main.scss        # 入口样式
│   ├── components/              # 全局可复用组件
│   │   ├── common/
│   │   │   ├── PageHeader.vue   # 页面标题栏
│   │   │   ├── StatusBadge.vue  # 状态徽标（running/done/escalated/aborted）
│   │   │   └── LoadingBar.vue   # 顶部加载条
│   │   └── timeline/            # 时间线专用组件
│   │       ├── Timeline.vue     # 时间线容器
│   │       ├── TimelineNode.vue # 单个时间线节点
│   │       ├── AgentNode.vue    # Agent 产出节点
│   │       ├── GateNode.vue     # Gate 决策节点
│   │       ├── ReworkNode.vue   # rework 回退节点
│   │       ├── RoundNode.vue    # 轮次节点
│   │       └── ToolErrorNode.vue # 工具异常节点
│   ├── views/                   # 页面级组件
│   │   ├── TemplateSelect.vue   # 模板选择页
│   │   ├── TaskSubmit.vue       # 任务提交页
│   │   ├── TaskTracking.vue     # 运行追踪页（核心）
│   │   ├── TaskResult.vue       # 结果/研报页
│   │   └── TaskReview.vue       # 人工复核页
│   ├── stores/                  # Pinia Store
│   │   ├── template.ts          # 模板选择
│   │   ├── task.ts              # 任务状态（核心）
│   │   ├── websocket.ts         # WebSocket 连接管理
│   │   └── settings.ts          # 设置（调试模式开关）
│   ├── services/                # API / WS 服务层
│   │   ├── api.ts               # axios 实例 + 拦截器
│   │   ├── taskService.ts       # REST API 封装
│   │   └── wsService.ts         # WebSocket 封装
│   ├── types/                   # TypeScript 类型定义
│   │   ├── api.ts               # API 请求/响应类型
│   │   ├── engine.ts            # 引擎层事件/状态类型
│   │   └── ui.ts                # UI 层视图类型
│   ├── utils/                   # 工具函数
│   │   ├── timeline.ts          # 时间线语义转译（原始事件 → 用户叙事）
│   │   ├── formatter.ts         # 格式化（时间、金额、状态）
│   │   └── download.ts          # 文件下载（审计包）
│   ├── router/                  # 路由配置
│   │   └── index.ts
│   ├── App.vue
│   └── main.ts
├── .env                         # 环境变量（API 基础 URL）
├── vite.config.ts
├── tsconfig.json
└── package.json
```

---

## 4. 类型定义（API 契约绑定）

基于 API_SPEC.md v1.0 的接口定义，前端 TypeScript 类型如下：

### 4.1 核心类型（`src/types/api.ts`）

```typescript
// 任务状态枚举（对齐 API_SPEC §2 TaskStatus）
export type TaskStatus = 'running' | 'done' | 'escalated' | 'aborted';

// Gate 决策类型
export type GateDecision = 'advance' | 'rework' | 'escalate';

// Agent 角色
export type AgentRole = 'Researcher' | 'Analyst' | 'Writer';

// Gate 名称
export type GateName = 'GateA' | 'GateB' | 'GateC';

// 引擎事件类型（3 种，对齐 schema §4.1 + UIDESIGN §8）
export type EngineEventType =
  | 'agent_output_unusable'
  | 'writer_citation_regenerated'
  | 'gate_llm_unavailable_degraded_advance';

// ==================== 用户任务 ====================

export interface UserTask {
  topic: string;
  scope: string[];
  output_format_spec: string;
  constraints: string[];
}

// ==================== 产出层 ====================

export interface RetrievalRecord {
  id: string;
  url: string;
  title: string;
  snippet: string;
  credibility: 'high' | 'medium' | 'low';
}

export interface AnalysisConclusion {
  id: string;
  claim: string;
  source_ids: string[];
  confidence: number;
}

export interface DraftSegment {
  id: string;
  section: string;
  content: string;
  conclusion_ids: string[];
}

export interface ToolStatus {
  agent: AgentRole;
  tool: string;
  ok: boolean;
  error: string | null;
}

// ==================== Gate 审计 ====================

export interface GateReview {
  decision: GateDecision;
  reason: string;
  eval_score: number;
  problem_points: string[];
  gate: GateName;
  round: number;
  timestamp: string;
}

// ==================== 引擎事件 ====================

export interface EngineEvent {
  event: EngineEventType;
  agent: AgentRole | null;
  gate: GateName | null;
  reason: string;
  round: number;
  timestamp: string;
}

// ==================== 路由状态 ====================

export interface RoutingState {
  round: number;
  max_rounds: number;
  last_gate: GateName | null;
  status: TaskStatus;
  rework_target_agent: AgentRole | null;
  rework_reason: string | null;
  gate_review_history: GateReview[];
  engine_events: EngineEvent[];
}

// ==================== 任务完整状态 ====================

export interface TaskResponse {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  user_task: UserTask;
  routing_state: RoutingState;
  retrieval_records: RetrievalRecord[];
  analysis_conclusions: AnalysisConclusion[];
  draft_segments: DraftSegment[];
  tool_status: ToolStatus[];
  prior_versions: Record<string, unknown>;
  report_markdown: string | null;
  escalate_reason: string | null;
}

// ==================== API 请求/响应 ====================

export interface CreateTaskRequest {
  user_task: UserTask;
}

export interface CreateTaskResponse {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  user_task: UserTask;
}

export interface ReviewAction {
  action: 'confirm' | 'retry' | 'abort';
  reviewer_comment: string;
  rework_target_agent?: AgentRole;
}

export interface ReviewResponse {
  task_id: string;
  status: TaskStatus;
  message: string;
}

export interface ErrorResponse {
  error: string;
  message: string;
  details: Record<string, unknown>;
  timestamp: string;
}
```

### 4.2 WebSocket 事件类型（`src/types/engine.ts`）

```typescript
// 7 种事件类型（对齐 API_SPEC §3 + UIDESIGN §3）
export type WSEventType =
  | 'agent_complete'
  | 'gate_complete'
  | 'rework_trigger'
  | 'round_update'
  | 'tool_error'
  | 'task_done'
  | 'task_escalated';

// WebSocket 事件通用结构
export interface WSEvent<T = unknown> {
  event_type: WSEventType;
  task_id: string;
  timestamp: string;
  data: T;
}

// 各事件 payload
export interface AgentCompleteData {
  agent: AgentRole;
  round: number;
  output_summary: string;
  record_count?: number;
}

export interface GateCompleteData {
  gate: GateName;
  decision: GateDecision;
  reason: string;
  eval_score: number;
  problem_points: string[];
  round: number;
}

export interface ReworkTriggerData {
  rework_target_agent: AgentRole;
  reason: string;
  round: number;
}

export interface RoundUpdateData {
  round: number;
  max_rounds: number;
}

export interface ToolErrorData {
  agent: AgentRole;
  tool: string;
  error: string;
}

export interface TaskDoneData {
  report_markdown: string;
  round: number;
}

export interface TaskEscalatedData {
  reason: string;
  last_gate: GateName;
  round: number;
}
```

### 4.3 UI 视图类型（`src/types/ui.ts`）

```typescript
// 时间线节点（UI 层聚合后的节点）
export interface TimelineNode {
  id: string; // 唯一 key（event_type + timestamp + 序号）
  type: 'agent' | 'gate' | 'rework' | 'round' | 'tool_error' | 'done' | 'escalated';
  timestamp: string;
  title: string; // 用户叙事的标题
  description: string; // 用户叙事的描述
  status: 'success' | 'warning' | 'error' | 'info'; // 节点状态色
  rawEvent?: WSEvent; // 原始事件（调试模式展示）
  meta: Record<string, any>; // 附加元数据（用于展开详情）
}

// 页面视图状态
export interface TaskTrackView {
  task_id: string;
  topic: string;
  status: TaskStatus;
  round: number;
  max_rounds: number;
  nodes: TimelineNode[];
  currentAgent: AgentRole | null;
  lastGate: GateName | null;
  escalateReason: string | null;
}

// 模板信息
export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  agents: AgentRole[];
  gates: GateName[];
  ui_mode: 'unified_shell' | 'custom_component';
}
```

---

## 5. API 服务层（`src/services/`）

### 5.1 API 客户端（`src/services/api.ts`）

```typescript
import axios from 'axios';
import { useTaskStore } from '@/stores/task';

// 从环境变量读取 API 基础 URL
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
});

// 请求拦截器
api.interceptors.request.use((config) => {
  // 可在此添加认证 token
  return config;
});

// 响应拦截器
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;

    // 拦截器内禁止直接调用 pinia store，仅向上抛出错误，由组件层捕获后更新状态
    const apiError = new ApiError(
      error.response?.data?.error || 'UNKNOWN',
      error.response?.data?.message || error.message,
      status,
      error.response?.data?.details
    );

    return Promise.reject(apiError);
  }
);

// API 错误类（供组件层统一处理）
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
    public readonly details?: Record<string, unknown>
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
```

### 5.2 任务服务（`src/services/taskService.ts`）

```typescript
import { api } from './api';
import type {
  CreateTaskRequest,
  CreateTaskResponse,
  TaskResponse,
  ReviewAction,
  ReviewResponse,
} from '@/types/api';

export const taskService = {
  // 创建任务
  async createTask(request: CreateTaskRequest): Promise<CreateTaskResponse> {
    const response = await api.post<CreateTaskResponse>('/tasks', request);
    return response.data;
  },

  // 获取任务状态
  async getTask(taskId: string): Promise<TaskResponse> {
    const response = await api.get<TaskResponse>(`/tasks/${taskId}`);
    return response.data;
  },

  // 人工复核
  async reviewTask(taskId: string, action: ReviewAction): Promise<ReviewResponse> {
    const response = await api.post<ReviewResponse>(`/tasks/${taskId}/review`, action);
    return response.data;
  },

  // 下载审计包
  async downloadAudit(taskId: string): Promise<Blob> {
    const response = await api.get<Blob>(`/tasks/${taskId}/audit-export`, {
      responseType: 'blob',
    });
    return response.data;
  },

  // 列出所有任务（调试用）
  async listTasks() {
    const response = await api.get('/tasks');
    return response.data;
  },
};
```

### 5.3 WebSocket 服务（`src/services/wsService.ts`）

```typescript
import { useWebSocket } from '@vueuse/core';
import type { WSEvent, WSEventType } from '@/types/engine';
import { useTaskStore } from '@/stores/task';
import { useSettingsStore } from '@/stores/settings';

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000/api/v1';

export const wsService = {
  // 创建 WebSocket 连接（按 task_id 订阅）
  connect(taskId: string) {
    const taskStore = useTaskStore();
    const settingsStore = useSettingsStore();

    // 构造 WebSocket URL
    const url = `${WS_BASE}/tasks/${taskId}/stream`;

    // 使用 useWebSocket（带自动重连）
    const { data, send, open, close, isConnected } = useWebSocket(url, {
      autoReconnect: true,
      heartbeat: true,
      onConnected: () => {
        console.log(`🔌 WebSocket 已连接: ${taskId}`);
      },
      onDisconnected: () => {
        console.warn(`⚠️ WebSocket 已断开: ${taskId}`);
      },
      onData: (event: MessageEvent) => {
        try {
          const wsEvent = JSON.parse(event.data) as WSEvent;
          this.handleEvent(wsEvent);
        } catch (e) {
          console.error('解析 WebSocket 事件失败:', e);
        }
      },
    });

    return { data, send, open, close, isConnected };
  },

  // 处理 WebSocket 事件（核心调度）
  handleEvent(event: WSEvent) {
    const taskStore = useTaskStore();
    const settingsStore = useSettingsStore();

    // 调试模式：记录原始事件
    if (settingsStore.debugMode) {
      taskStore.addRawEvent(event);
    }

    // 根据事件类型更新状态
    switch (event.event_type) {
      case 'agent_complete':
        taskStore.handleAgentComplete(event.data);
        break;
      case 'gate_complete':
        taskStore.handleGateComplete(event.data);
        break;
      case 'rework_trigger':
        taskStore.handleReworkTrigger(event.data);
        break;
      case 'round_update':
        taskStore.handleRoundUpdate(event.data);
        break;
      case 'tool_error':
        taskStore.handleToolError(event.data);
        break;
      case 'task_done':
        taskStore.handleTaskDone(event.data);
        break;
      case 'task_escalated':
        taskStore.handleTaskEscalated(event.data);
        break;
    }

    // 将事件转为时间线节点
    const node = this.eventToTimelineNode(event);
    taskStore.addTimelineNode(node);
  },

  // 原始事件 → UI 时间线节点（语义转译，对齐 UIDESIGN §5.1）
  eventToTimelineNode(event: WSEvent): import('@/types/ui').TimelineNode {
    const { event_type, data } = event;

    const node: import('@/types/ui').TimelineNode = {
      id: `${event_type}-${event.timestamp}-${Math.random().toString(36).slice(2)}`,
      type: 'agent',
      timestamp: event.timestamp,
      title: '',
      description: '',
      status: 'info',
      rawEvent: event,
      meta: {},
    };

    // 语义转译表（对齐 UIDESIGN §5.1.1）
    switch (event_type) {
      case 'agent_complete':
        const agentMap = {
          Researcher: { icon: '🔍', title: '调研完成' },
          Analyst: { icon: '📊', title: '分析完成' },
          Writer: { icon: '📝', title: '撰稿完成' },
        };
        const agentInfo = agentMap[data.agent as string] || { icon: '🤖', title: 'Agent 完成' };
        node.type = 'agent';
        node.title = `${agentInfo.icon} ${agentInfo.title}`;
        node.description = `${agentInfo.title}（第 ${data.round} 轮）`;
        node.status = 'success';
        node.meta = data;
        break;

      case 'gate_complete':
        if (data.decision === 'advance') {
          node.type = 'gate';
          node.title = `✅ ${data.gate} 审核放行`;
          node.description = `${data.gate} 审核通过，评分 ${data.eval_score}`;
          node.status = 'success';
        } else if (data.decision === 'rework') {
          node.type = 'gate';
          node.title = `🔁 ${data.gate} 打回`;
          node.description = data.reason;
          node.status = 'warning';
        } else {
          node.type = 'gate';
          node.title = `⛔ ${data.gate} 升级`;
          node.description = data.reason;
          node.status = 'error';
        }
        node.meta = data;
        break;

      case 'rework_trigger':
        node.type = 'rework';
        node.title = `↩ 重新执行 ${data.rework_target_agent}`;
        node.description = data.reason;
        node.status = 'warning';
        node.meta = data;
        break;

      case 'round_update':
        node.type = 'round';
        node.title = `第 ${data.round} / ${data.max_rounds} 轮`;
        node.description = `轮次更新`;
        node.status = 'info';
        node.meta = data;
        break;

      case 'tool_error':
        node.type = 'tool_error';
        node.title = `⚠️ ${data.agent} 工具异常`;
        node.description = `${data.tool}: ${data.error}`;
        node.status = 'error';
        node.meta = data;
        break;

      case 'task_done':
        node.type = 'done';
        node.title = '🎉 研报生成完成';
        node.description = '任务已成功完成';
        node.status = 'success';
        node.meta = data;
        break;

      case 'task_escalated':
        node.type = 'escalated';
        node.title = '⛔ 任务已升级';
        node.description = `需要人工复核（${data.reason}）`;
        node.status = 'error';
        node.meta = data;
        break;
    }

    return node;
  },
};
```

---

## 6. Pinia Store 设计

### 6.1 任务 Store（`src/stores/task.ts`）— 核心 Store

```typescript
import { defineStore } from 'pinia';
import type {
  TaskResponse,
  TaskStatus,
  UserTask,
  GateReview,
  EngineEvent,
} from '@/types/api';
import type { TimelineNode, TaskTrackView } from '@/types/ui';
import type { WSEvent } from '@/types/engine';

export const useTaskStore = defineStore('task', {
  state: () => ({
    // 当前任务
    currentTask: null as TaskResponse | null,
    // 任务 ID
    taskId: '',
    // 任务主题
    topic: '',
    // 状态
    status: 'running' as TaskStatus,
    // 轮次
    round: 1,
    max_rounds: 2,
    // 时间线节点
    timelineNodes: [] as TimelineNode[],
    // 原始事件列表（调试模式）
    rawEvents: [] as WSEvent[],
    // 错误信息
    error: '',
    // 加载状态
    loading: false,
  }),

  getters: {
    // 获取当前 Agent
    currentAgent: (state) => {
      if (!state.currentTask) return null;
      // 从路由状态推断当前 Agent
      return null; // 简化
    },
    // 获取最后 Gate
    lastGate: (state) => {
      return state.currentTask?.routing_state.last_gate || null;
    },
    // 获取 Gate 审核历史
    gateHistory: (state) => {
      return state.currentTask?.routing_state.gate_review_history || [];
    },
    // 获取引擎事件
    engineEvents: (state) => {
      return state.currentTask?.routing_state.engine_events || [];
    },
    // 是否终态
    isTerminal: (state) => {
      return ['done', 'escalated', 'aborted'].includes(state.status);
    },
    // 是否需要人工复核
    needsReview: (state) => {
      return state.status === 'escalated';
    },
  },

  actions: {
    // 设置任务
    setTask(task: TaskResponse) {
      this.currentTask = task;
      this.taskId = task.task_id;
      this.status = task.status;
      this.topic = task.user_task.topic;
      this.round = task.routing_state.round;
      this.max_rounds = task.routing_state.max_rounds;
    },

    // 清空任务
    clear() {
      this.$reset();
    },

    // 设置错误
    setError(msg: string) {
      this.error = msg;
    },

    // 添加时间线节点
    addTimelineNode(node: TimelineNode) {
      this.timelineNodes.push(node);
    },

    // 添加原始事件
    addRawEvent(event: WSEvent) {
      this.rawEvents.push(event);
    },

    // --- WebSocket 事件处理 ---
    handleAgentComplete(data: AgentCompleteData) {
      this.round = data.round;
    },

    handleGateComplete(data: GateCompleteData) {
      // 更新 Gate 审核历史
      if (this.currentTask) {
        this.currentTask.routing_state.gate_review_history.push({
          decision: data.decision,
          reason: data.reason,
          eval_score: data.eval_score,
          problem_points: data.problem_points,
          gate: data.gate,
          round: data.round,
          timestamp: new Date().toISOString(),
        });
      }
    },

    handleReworkTrigger(data: ReworkTriggerData) {
      if (this.currentTask) {
        this.currentTask.routing_state.rework_target_agent = data.rework_target_agent;
        this.currentTask.routing_state.rework_reason = data.reason;
      }
    },

    handleRoundUpdate(data: RoundUpdateData) {
      this.round = data.round;
      this.max_rounds = data.max_rounds;
    },

    handleToolError(data: ToolErrorData) {
      // 添加工具状态
      if (this.currentTask) {
        this.currentTask.tool_status.push({
          agent: data.agent,
          tool: data.tool,
          ok: false,
          error: data.error,
        });
      }
    },

    handleTaskDone(data: TaskDoneData) {
      this.status = 'done';
      if (this.currentTask) {
        this.currentTask.status = 'done';
        this.currentTask.routing_state.status = 'done';
        this.currentTask.report_markdown = data.report_markdown;
      }
    },

    handleTaskEscalated(data: TaskEscalatedData) {
      this.status = 'escalated';
      if (this.currentTask) {
        this.currentTask.status = 'escalated';
        this.currentTask.routing_state.status = 'escalated';
        this.currentTask.escalate_reason = data.reason;
      }
    },
  },
});
```

### 6.2 模板 Store（`src/stores/template.ts`）

```typescript
import { defineStore } from 'pinia';
import type { TemplateInfo } from '@/types/ui';

export const useTemplateStore = defineStore('template', {
  state: () => ({
    // 模板列表（静态配置，从 templates/manifest.yaml 加载）
    templates: [] as TemplateInfo[],
    // 选中的模板
    selected: null as TemplateInfo | null,
  }),

  getters: {
    availableTemplates: (state) => {
      // 默认只显示 unified_shell 模式的模板
      return state.templates.filter(t => t.ui_mode === 'unified_shell');
    },
  },

  actions: {
    // 加载模板（实际从后端 API 或静态文件加载）
    async loadTemplates() {
      // v1 阶段模板为静态配置
      this.templates = [
        {
          id: 'research-report',
          name: '研报',
          description: '调研→分析→撰稿三角色协作，带三道审核闸',
          agents: ['Researcher', 'Analyst', 'Writer'],
          gates: ['GateA', 'GateB', 'GateC'],
          ui_mode: 'unified_shell',
        },
      ];
    },

    selectTemplate(templateId: string) {
      const template = this.templates.find(t => t.id === templateId);
      this.selected = template || null;
    },
  },
});
```

### 6.3 WebSocket Store（`src/stores/websocket.ts`）

```typescript
import { defineStore } from 'pinia';

export const useWebsocketStore = defineStore('websocket', {
  state: () => ({
    // 连接状态
    connections: new Map<string, { isConnected: boolean; close: () => void }>(),
  }),

  actions: {
    // 关闭所有连接
    closeAll() {
      this.connections.forEach((conn) => conn.close());
      this.connections.clear();
    },
  },
});
```

### 6.4 设置 Store（`src/stores/settings.ts`）

> ⚠️ 使用 setup-style store（setup 语法），因为 option-store 的 state 顶层不能直接调用 `useStorage`。
> 调试模式持久化使用 `pinia-plugin-persistedstate` 插件（M7-1 安装），而非在 state 内手动调用 useStorage。

```typescript
import { defineStore } from 'pinia';
import { ref } from 'vue';

export const useSettingsStore = defineStore('settings', () => {
  // 调试模式开关（由 pinia-plugin-persistedstate 持久化到 localStorage）
  const debugMode = ref(false);

  const toggle = () => {
    debugMode.value = !debugMode.value;
  };

  return {
    debugMode,
    toggle,
  };
});
```

> **持久化配置（M7-1 在 main.ts 中注册）**：
> ```typescript
> import { createPinia } from 'pinia';
> import piniaPluginPersistedstate from 'pinia-plugin-persistedstate';
>
> const pinia = createPinia();
> pinia.use(piniaPluginPersistedstate);
> ```
> 并在 settings store 中添加 `persist: true` 选项（setup-style store 通过 defineStore 第二个参数传入）。

---

## 7. 核心页面设计

### 7.1 模板选择页（`views/TemplateSelect.vue`）

```vue
<template>
  <div class="template-select">
    <PageHeader title="选择任务模板" />

    <el-row :gutter="20" class="template-grid">
      <el-col :span="8" v-for="template in availableTemplates" :key="template.id">
        <el-card class="template-card" @click="selectTemplate(template)">
          <template #header>
            <div class="template-header">
              <h3>{{ template.name }}</h3>
              <el-tag v-if="template.ui_mode === 'custom_component'" type="warning">自定义</el-tag>
            </div>
          </template>
          <p class="template-desc">{{ template.description }}</p>
          <div class="template-meta">
            <span class="meta-item">
              <el-icon><user /></el-icon>
              Agent: {{ template.agents.length }}
            </span>
            <span class="meta-item">
              <el-icon><lock /></el-icon>
              Gate: {{ template.gates.length }}
            </span>
          </div>
          <el-button type="primary" class="start-btn">开始</el-button>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useTemplateStore } from '@/stores/template';

const router = useRouter();
const templateStore = useTemplateStore();

const availableTemplates = templateStore.availableTemplates;

onMounted(async () => {
  await templateStore.loadTemplates();
});

const selectTemplate = (template: TemplateInfo) => {
  templateStore.selectTemplate(template.id);
  router.push({ name: 'TaskSubmit' });
};
</script>
```

### 7.2 任务提交页（`views/TaskSubmit.vue`）

```vue
<template>
  <div class="task-submit">
    <PageHeader title="提交研究任务" />

    <el-card class="form-card">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
        <el-form-item label="研究主题" prop="topic">
          <el-input v-model="form.topic" placeholder="请输入研究主题" />
        </el-form-item>

        <el-form-item label="研究范围" prop="scope">
          <el-input
            v-model="scopeInput"
            type="textarea"
            :rows="4"
            placeholder="每行一个研究维度，如：市场规模、竞争格局、技术趋势"
          />
        </el-form-item>

        <el-form-item label="输出格式" prop="output_format_spec">
          <el-select v-model="form.output_format_spec" placeholder="选择输出格式">
            <el-option label="Markdown 报告" value="markdown" />
            <el-option label="表格对比" value="table" />
            <el-option label="JSON 数据" value="json" />
          </el-select>
        </el-form-item>

        <el-form-item label="约束条件" prop="constraints">
          <el-input
            v-model="constraintsInput"
            type="textarea"
            :rows="3"
            placeholder="每行一个约束，如：仅限2024年后数据、需引用3篇以上"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" size="large" @click="submit" :loading="submitting">
            提交任务
          </el-button>
          <el-button @click="router.back()">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useTemplateStore } from '@/stores/template';
import { useTaskStore } from '@/stores/task';
import { taskService } from '@/services/taskService';

const router = useRouter();
const templateStore = useTemplateStore();
const taskStore = useTaskStore();

const form = ref({
  topic: '',
  scope: [] as string[],
  output_format_spec: 'markdown',
  constraints: [] as string[],
});

const scopeInput = ref('');
const constraintsInput = ref('');
const submitting = ref(false);

// 输入转数组
watch(scopeInput, (val) => {
  form.value.scope = val.split('\n').filter(s => s.trim());
});

watch(constraintsInput, (val) => {
  form.value.constraints = val.split('\n').filter(s => s.trim());
});

const rules = {
  topic: [{ required: true, message: '请输入研究主题', trigger: 'blur' }],
  scope: [{ required: true, message: '请填写研究范围', trigger: 'blur' }],
  output_format_spec: [{ required: true, message: '请选择输出格式', trigger: 'change' }],
};

const submit = async () => {
  try {
    submitting.value = true;
    const response = await taskService.createTask({ user_task: form.value });
    // 跳转到运行追踪页
    router.push({ name: 'TaskTracking', params: { taskId: response.task_id } });
  } catch (error) {
    console.error('任务创建失败:', error);
  } finally {
    submitting.value = false;
  }
};
</script>
```

### 7.3 运行追踪页（`views/TaskTracking.vue`）— 核心页面

```vue
<template>
  <div class="task-tracking">
    <!-- 顶部信息栏 -->
    <div class="track-header">
      <h2>{{ topic || '任务追踪' }}</h2>
      <div class="track-meta">
        <StatusBadge :status="status" />
        <span class="round-info">第 {{ round }} / {{ max_rounds }} 轮</span>
        <el-switch
          v-model="debugMode"
          inactive-color="#f1f5f9"
          active-color="#13c2c2"
          inactive-icon="moon"
          active-icon="sun"
          @change="toggleDebug"
        />
        <span class="debug-label">调试模式</span>
      </div>
    </div>

    <!-- 时间线 -->
    <el-card class="timeline-card">
      <Timeline :nodes="timelineNodes" @node-click="handleNodeClick" />
    </el-card>

    <!-- 引用链状态 -->
    <el-card v-if="showCitationChain" class="citation-card">
      <template #header>引用链状态</template>
      <div class="citation-chain">
        <div class="citation-step">
          <span class="step-label">检索</span>
          <span class="step-count">{{ retrievalCount }} 条</span>
        </div>
        <el-icon class="arrow">→</el-icon>
        <div class="citation-step">
          <span class="step-label">分析</span>
          <span class="step-count">{{ conclusionCount }} 条</span>
        </div>
        <el-icon class="arrow">→</el-icon>
        <div class="citation-step">
          <span class="step-label">撰稿</span>
          <span class="step-count">{{ segmentCount }} 段</span>
        </div>
      </div>
    </el-card>

    <!-- 终态处理 -->
    <el-dialog v-model="showTerminal" :title="terminalTitle" :close-on-click-modal="false">
      <p v-if="status === 'done'">✅ 任务已完成，可查看最终研报。</p>
      <p v-else-if="status === 'escalated'">
        ⛔ 任务已升级：{{ escalateReason }}
        <br>
        请前往人工复核页处理。
      </p>
      <template #footer>
        <el-button v-if="status === 'escalated'" type="primary" @click="goReview">
          去复核
        </el-button>
        <el-button v-if="status === 'done'" type="primary" @click="goResult">
          查看研报
        </el-button>
        <el-button @click="$router.back()">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useTaskStore } from '@/stores/task';
import { useSettingsStore } from '@/stores/settings';
import { taskService } from '@/services/taskService';
import { wsService } from '@/services/wsService';
import Timeline from '@/components/timeline/Timeline.vue';
import StatusBadge from '@/components/common/StatusBadge.vue';

const route = useRoute();
const router = useRouter();
const taskStore = useTaskStore();
const settingsStore = useSettingsStore();

const taskId = computed(() => route.params.taskId as string);
const debugMode = computed(() => settingsStore.debugMode);

// 从 store 派生
const topic = computed(() => taskStore.topic);
const status = computed(() => taskStore.status);
const round = computed(() => taskStore.round);
const max_rounds = computed(() => taskStore.max_rounds);
const timelineNodes = computed(() => taskStore.timelineNodes);
const escalateReason = computed(() => taskStore.currentTask?.escalate_reason);

// 引用链统计
const retrievalCount = computed(() => taskStore.currentTask?.retrieval_records.length || 0);
const conclusionCount = computed(() => taskStore.currentTask?.analysis_conclusions.length || 0);
const segmentCount = computed(() => taskStore.currentTask?.draft_segments.length || 0);
const showCitationChain = computed(() => retrievalCount.value > 0 || conclusionCount.value > 0);

// 终态弹窗
const showTerminal = computed(() => taskStore.isTerminal);
const terminalTitle = computed(() => {
  return status.value === 'done' ? '任务完成' : '需要人工复核';
});

// ==================== WebSocket 连接管理 ====================

// taskId → WebSocket 连接缓存，防止路由来回切换重复创建多个 WebSocket 实例，避免时间线重复节点
const wsConnections = new Map<string, ReturnType<typeof wsService.connect>>();

// 获取或创建当前任务的 WebSocket 连接
const getOrCreateWsConnection = (tid: string) => {
  let conn = wsConnections.get(tid);
  if (!conn || !conn.isConnected.value) {
    conn = wsService.connect(tid);
    wsConnections.set(tid, conn);
  }
  return conn;
};

// 序列号兜底同步：v1 预留 sequence_id 扩展字段，检测到跳变时触发全量快照拉取
let lastSequenceId: number | null = null;
const checkSequenceGap = (sequenceId: number | undefined) => {
  if (sequenceId === undefined) return; // v1 后端未实现 sequence_id
  if (lastSequenceId !== null && sequenceId !== lastSequenceId + 1) {
    console.warn(`⚠️ WebSocket 序列号跳变: ${lastSequenceId} -> ${sequenceId}，触发全量快照同步`);
    syncFullSnapshot();
  }
  lastSequenceId = sequenceId;
};

// 全量快照兜底同步：mount / ws 重连 / 序列号跳变时调用，拉取完整 task 快照修复状态
const syncFullSnapshot = async () => {
  try {
    const task = await taskService.getTask(taskId.value);
    taskStore.setTask(task);
    console.log('✅ 全量快照同步完成');
  } catch (error) {
    console.error('❌ 全量快照同步失败:', error);
  }
};

onMounted(async () => {
  // 1. 拉取完整任务状态（初始快照兜底）
  try {
    const task = await taskService.getTask(taskId.value);
    taskStore.setTask(task);
  } catch (error) {
    console.error('获取任务失败:', error);
    return;
  }

  // 2. 建立/复用 WebSocket 连接（taskId 连接缓存，防止重复创建）
  const wsConn = getOrCreateWsConnection(taskId.value);

  // 3. 监听连接断开事件，自动触发全量快照兜底
  watch(
    () => wsConn.isConnected.value,
    (connected) => {
      if (!connected) {
        console.warn('⚠️ WebSocket 已断开，等待重连后同步快照');
      } else {
        console.log('🔌 WebSocket 已重连，触发全量快照同步');
        syncFullSnapshot();
      }
    }
  );
});

onUnmounted(() => {
  // 路由切换时不关闭连接（连接缓存复用），仅在页面彻底销毁时可选择清理
  // 如需清理：wsConnections.get(taskId.value)?.close();
});

const toggleDebug = () => {
  settingsStore.toggle();
};

const handleNodeClick = (node: TimelineNode) => {
  // 节点展开详情
  console.log('节点点击:', node);
};

const goReview = () => {
  router.push({ name: 'TaskReview', params: { taskId: taskId.value } });
};

const goResult = () => {
  router.push({ name: 'TaskResult', params: { taskId: taskId.value } });
};
</script>
```

---

## 8. 时间线组件设计

### 7.4 结果/输出页（`views/TaskResult.vue`）

> ⚠️ **Markdown 渲染安全约束**：TaskResult 页面渲染研报 Markdown 内容时，**必须使用 DOMPurify 做 XSS 清洗**。
> 引擎产出的 report_markdown 可能包含 `<script>`、`onerror=` 等恶意 HTML 属性，直接渲染会导致 XSS 攻击。
> - 依赖：`npm install dompurify @types/dompurify`
> - 使用方式：`const cleanHtml = DOMPurify.sanitize(marked.parse(markdownContent))`
> - 标记为 **高危安全红线**，编码实现必须包含此步骤，不可跳过。

```vue
<template>
  <div class="task-result">
    <PageHeader title="研报结果" />

    <!-- 任务信息 -->
    <el-card class="result-meta">
      <div class="meta-row">
        <span class="meta-label">主题：</span>
        <span class="meta-value">{{ topic }}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">状态：</span>
        <StatusBadge :status="status" />
      </div>
      <div class="meta-row">
        <span class="meta-label">完成时间：</span>
        <span class="meta-value">{{ formatTime(updatedAt) }}</span>
      </div>
    </el-card>

    <!-- 研报内容（DOMPurify XSS 清洗后渲染） -->
    <el-card class="result-content">
      <template #header>研报内容</template>
      <div class="markdown-body" v-html="cleanReportHtml" />
    </el-card>

    <!-- 评审留痕（折叠区） -->
    <el-card class="result-review">
      <template #header>
        <el-collapse v-model="activeNames">
          <el-collapse-item name="1">评审留痕（{{ gateHistory.length }} 条）</el-collapse-item>
        </el-collapse>
      </template>
      <el-timeline>
        <el-timeline-item
          v-for="(review, index) in gateHistory"
          :key="index"
          :type="review.decision === 'advance' ? 'success' : review.decision === 'rework' ? 'warning' : 'danger'"
          :title="`${review.gate} - ${review.decision}`"
        >
          <p>{{ review.reason }}</p>
          <p>评分：{{ review.eval_score }}</p>
          <p v-if="review.problem_points.length">问题点：{{ review.problem_points.join('、') }}</p>
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <!-- 审计包下载 -->
    <el-card class="result-audit">
      <template #header>审计归档</template>
      <el-button type="primary" @click="downloadAudit" :loading="downloading">
        下载审计包 (ZIP)
      </el-button>
      <p class="audit-note">包含：研报 + Gate 审核历史 + 历史版本 + 原始任务 + 引擎事件</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { useTaskStore } from '@/stores/task';
import { taskService } from '@/services/taskService';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import StatusBadge from '@/components/common/StatusBadge.vue';

const route = useRoute();
const taskStore = useTaskStore();

const taskId = computed(() => route.params.taskId as string);
const topic = computed(() => taskStore.currentTask?.user_task.topic || '');
const status = computed(() => taskStore.currentTask?.status || 'running');
const updatedAt = computed(() => taskStore.currentTask?.updated_at || '');
const reportMarkdown = computed(() => taskStore.currentTask?.report_markdown || '');
const gateHistory = computed(() => taskStore.currentTask?.routing_state.gate_review_history || []);

const activeNames = ref(['1']);
const downloading = ref(false);

// DOMPurify XSS 清洗 + Markdown 转 HTML
const cleanReportHtml = computed(() => {
  if (!reportMarkdown.value) return '';
  // 先用 marked 将 Markdown 转 HTML
  const rawHtml = marked.parse(reportMarkdown.value);
  // 再用 DOMPurify 清洗 XSS
  const cleanHtml = DOMPurify.sanitize(rawHtml, {
    FORCED_ATTR: ['href', 'src', 'alt', 'title'],
    ALLOWED_TAGS: [
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'p', 'br', 'hr',
      'strong', 'em', 'u', 's', 'code', 'pre',
      'blockquote', 'ul', 'ol', 'li',
      'table', 'thead', 'tbody', 'tr', 'th', 'td',
      'a', 'img',
    ],
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class'],
  });
  return cleanHtml;
});

const formatTime = (iso: string) => {
  return new Date(iso).toLocaleString('zh-CN');
};

onMounted(async () => {
  try {
    const task = await taskService.getTask(taskId.value);
    taskStore.setTask(task);
  } catch (error) {
    console.error('获取任务失败:', error);
  }
});

const downloadAudit = async () => {
  try {
    downloading.value = true;
    const blob = await taskService.downloadAudit(taskId.value);

    // 异常处理：后端业务错误可能被包装在 Blob 内（如错误页面 HTML）
    // 检查 Blob 类型和大小，如果是非预期的则报错
    if (blob.size === 0) {
      throw new Error('审计包为空，请检查服务端状态');
    }
    if (blob.type === 'text/html') {
      // 可能是后端返回的错误页面而非 ZIP
      const text = await blob.text();
      if (text.includes('error') || text.includes('Error')) {
        throw new Error('审计包下载失败：服务端返回错误信息');
      }
    }

    // 触发下载
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit_${taskId.value}_${new Date().toISOString().slice(0, 10)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error('审计包下载失败:', error);
    alert('审计包下载失败，请检查网络连接或服务端状态');
  } finally {
    downloading.value = false;
  }
};
</script>
```

---

## 8. 时间线组件设计

### 8.1 Timeline.vue（容器）

> ⚠️ **大数据量性能预留**：多次 rework 会产生大量 timelineNodes，v1 原型阶段使用 `v-for` 全量渲染，但必须预留虚拟滚动占位。
> 当 `nodes.length > 100` 时，应切换为虚拟滚动组件（如 `vue-virtual-scroller` 或 Element Plus 的 `el-virtual-list`），
> 避免 DOM 节点过多导致页面卡顿。M7-3 实现时需预留 `useVirtualScroll` composable 占位。

```vue
<template>
  <div class="timeline">
    <div class="timeline-line"></div>
    <!-- v1 使用全量渲染，但预留虚拟滚动接口 -->
    <div v-if="nodes.length <= VIRTUAL_THRESHOLD" class="timeline-nodes">
      <TimelineNode
        v-for="(node, index) in nodes"
        :key="node.id"
        :node="node"
        :index="index"
        @click="$emit('node-click', node)"
      />
    </div>
    <!-- 大数据量时切换虚拟滚动（M7-3 预留占位） -->
    <el-virtual-list
      v-else
      :data-key="'id'"
      :items="nodes"
      :item-size="80"
      overscan="10"
      class="timeline-virtual"
    >
      <template #default="{ item: node }">
        <TimelineNode :node="node" @click="$emit('node-click', node)" />
      </template>
    </el-virtual-list>
  </div>
</template>

<script setup lang="ts">
import { defineProps, defineEmits } from 'vue';
import type { TimelineNode } from '@/types/ui';
import TimelineNode from './TimelineNode.vue';

// 虚拟滚动阈值：超过此节点数切换虚拟滚动，避免 DOM 卡顿
const VIRTUAL_THRESHOLD = 100;

defineProps<{
  nodes: TimelineNode[];
}>();

defineEmits(['node-click']);
</script>

<style scoped>
.timeline {
  position: relative;
  padding: 20px 0;
}
.timeline-line {
  position: absolute;
  left: 30px;
  top: 20px;
  bottom: 20px;
  width: 2px;
  background: var(--el-border-color);
}
.timeline-nodes {
  position: relative;
  margin-left: 60px;
}
</style>
```

### 8.2 TimelineNode.vue（节点）

```vue
<template>
  <div
    class="timeline-node"
    :class="`node-${node.type} status-${node.status}`"
    @click="$emit('click')"
  >
    <div class="node-dot"></div>
    <div class="node-content">
      <div class="node-header">
        <span class="node-title">{{ node.title }}</span>
        <span class="node-time">{{ formatTime(node.timestamp) }}</span>
      </div>
      <div class="node-desc">{{ node.description }}</div>
      <!-- 调试模式：显示原始事件 -->
      <div v-if="debugMode && node.rawEvent" class="node-raw">
        <pre>{{ formatRaw(node.rawEvent) }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { defineProps, defineEmits, computed } from 'vue';
import { useSettingsStore } from '@/stores/settings';
import type { TimelineNode } from '@/types/ui';
import type { WSEvent } from '@/types/engine';

const settingsStore = useSettingsStore();
const debugMode = computed(() => settingsStore.debugMode);

const props = defineProps<{
  node: TimelineNode;
}>();

defineEmits(['click']);

const formatTime = (iso: string) => {
  return new Date(iso).toLocaleString('zh-CN');
};

const formatRaw = (event: WSEvent) => {
  return JSON.stringify(event, null, 2);
};
</script>

<style scoped>
.timeline-node {
  position: relative;
  padding: 16px 0;
  cursor: pointer;
  transition: opacity 0.2s;
}
.timeline-node:hover { opacity: 0.8; }
.node-dot {
  position: absolute;
  left: -34px;
  top: 20px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--el-border-color);
  border: 2px solid var(--el-color-primary);
}
.node-content {
  padding: 12px 16px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  border-left: 4px solid var(--el-color-primary);
}
.node-header { display: flex; justify-content: space-between; align-items: center; }
.node-title { font-weight: 600; font-size: 14px; }
.node-time { font-size: 12px; color: var(--el-text-color-placeholder); }
.node-desc { margin-top: 4px; font-size: 13px; color: var(--el-text-color-regular); }
.node-raw { margin-top: 8px; background: #1e293b; color: #e2e8f0; padding: 8px; border-radius: 4px; font-size: 11px; overflow-x: auto; }

/* 状态色 */
.status-success .node-dot { background: var(--el-color-success); border-color: var(--el-color-success); }
.status-success .node-content { border-left-color: var(--el-color-success); }

.status-warning .node-dot { background: var(--el-color-warning); border-color: var(--el-color-warning); }
.status-warning .node-content { border-left-color: var(--el-color-warning); }

.status-error .node-dot { background: var(--el-color-danger); border-color: var(--el-color-danger); }
.status-error .node-content { border-left-color: var(--el-color-danger); }

.status-info .node-dot { background: var(--el-color-info); border-color: var(--el-color-info); }
.status-info .node-content { border-left-color: var(--el-color-info); }
</style>
```

---

## 9. 路由配置

```typescript
import { createRouter, createWebHistory } from 'vue-router';

const routes = [
  {
    path: '/',
    redirect: '/templates',
  },
  {
    path: '/templates',
    name: 'TemplateSelect',
    component: () => import('@/views/TemplateSelect.vue'),
    meta: { title: '模板选择' },
  },
  {
    path: '/submit',
    name: 'TaskSubmit',
    component: () => import('@/views/TaskSubmit.vue'),
    meta: { title: '提交任务' },
  },
  {
    path: '/task/:taskId',
    name: 'TaskTracking',
    component: () => import('@/views/TaskTracking.vue'),
    meta: { title: '运行追踪' },
  },
  {
    path: '/task/:taskId/result',
    name: 'TaskResult',
    component: () => import('@/views/TaskResult.vue'),
    meta: { title: '结果查看' },
  },
  {
    path: '/task/:taskId/review',
    name: 'TaskReview',
    component: () => import('@/views/TaskReview.vue'),
    meta: { title: '人工复核' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/templates',
  },
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

// 全局守卫：页面标题
router.afterEach((to) => {
  document.title = `${to.meta.title || ''} - Report Agent Team`;
});

export default router;
```

---

## 10. 环境变量配置

```
# .env 开发环境
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_BASE_URL=ws://localhost:8000/api/v1

# .env.production 生产环境
VITE_API_BASE_URL=https://api.report-agent-team.example.com/api/v1
VITE_WS_BASE_URL=wss://api.report-agent-team.example.com/api/v1
```

---

## 11. 实现里程碑（M7 分阶段）

| 阶段 | 范围 | 预估工时 | 依赖 |
|------|------|----------|------|
| **M7-1** | 项目脚手架 + 类型定义 + API/WS 服务层 | 1 天 | M6 API 已就绪 |
| **M7-2** | 模板选择页 + 任务提交页 | 1 天 | M7-1 |
| **M7-3** | 运行追踪页 + 时间线组件（核心） | 2 天 | M7-1 |
| **M7-4** | 结果/研报页 + Markdown 渲染插件 | 1 天 | M7-3 |
| **M7-5** | 人工复核页 + 审计包下载 | 0.5 天 | M7-3 |
| **M7-6** | 集成测试 + 联调 | 1 天 | M7-1~M7-5 |

**总预估**：约 5.5 个工作日

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| WebSocket 断连 | 实时状态更新丢失 | 使用 useWebSocket 自动重连 + 轮询兜底 |
| API 返回数据与类型定义不一致 | 运行时错误 | 添加运行时校验（zod 或 io-ts） |
| 大数据量时间线渲染卡顿 | 用户体验差 | 虚拟滚动 + 分页加载 |
| Markdown 渲染 XSS | 安全问题 | 使用 DOMPurify 清洗 |
| CORS 跨域 | 请求失败 | FastAPI 已配置 CORS * |

---

## 13. 设计纪律

1. **类型先行**：所有 API 交互必须先定义 TypeScript 类型，禁止 any
2. **状态同源**：UI 展示字段必须能在 UIDESIGN §8 对照表找到来源
3. **读写分离**：UI 只读引擎状态；只回写 user_task 提交和人工复核操作
4. **先设计后编码**：本文件评审通过前不落地任何 .vue/.ts 代码
5. **组件拆分**：页面级组件不超过 300 行，逻辑抽到 composables
6. **错误友好**：所有 API 调用必须有 try/catch + 用户友好提示

---

## 14. 开放问题

1. **认证机制**：当前 API 无认证，生产环境是否需要 JWT/OAuth？
2. **模板动态加载**：v1 静态模板，v2 是否需要从后端动态加载模板 manifest？
3. **多语言**：是否需要 i18n 国际化支持？
4. **移动端**：是否需要响应式适配移动端？

---

> 【草稿代码说明】
> 本文档内所有 Vue、TypeScript 代码片段为结构示例草稿，**不能直接复制用于生产编码，存在已知缺陷，编码实现必须修复以下问题：**
> 1. WebSocket仅做增量事件推送，不保证历史事件重放；mount/重连后必须调用`GET /tasks/{taskId}`拉取完整快照做状态兜底同步；增加taskId连接缓存，防止重复创建ws连接。
> 2. Pinia option‑store禁止state顶层直接调用`useStorage`；调试模式持久化改用pinia-plugin-persistedstate。
> 3. 生产代码禁止any，全部使用文档定义的强TypeScript类型。
> 4. TaskResult页面Markdown渲染必须使用DOMPurify做XSS清洗。
> 5. 时间线组件预留虚拟滚动，应对大量rework产生大量节点DOM性能问题。
> 6. axios拦截器禁止直接调用pinia store，错误向上抛出，由组件层捕获更新状态。
> 7. TS不存在`int`类型，全部改为`number`。
> 8. 业务权限提醒：v1原型debugMode仅前端localStorage开关；未来版本后端接口需要鉴权控制是否返回`rawEvents`、`prior_versions`调试字段，不可仅依赖前端开关。
> 9. watch等vue组合式API需要显式import。

---

**本文件评审通过后，启动 M7-1 前端脚手架搭建。**