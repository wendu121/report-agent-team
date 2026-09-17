// 任务核心 store（setup-style）
// 设计清单第 1/2/5/6 项落地：WS 兜底同步 + 连接缓存 + 强类型事件处理
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { TaskResponse, WSEvent, TimelineNode, CreateTaskRequest } from '@/types';
import { getTask, createTask as apiCreateTask } from '@/services/taskService';
import { connect, closeWs } from '@/services/wsService';
import { wsEventToNode, buildNodesFromTask } from '@/utils/timeline';
import { ApiError } from '@/api/errors';

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskResponse | null>(null);
  const nodes = ref<TimelineNode[]>([]);
  const error = ref<string | null>(null);
  const loading = ref(false);

  const status = computed(() => currentTask.value?.status ?? null);
  const isTerminal = computed(
    () =>
      currentTask.value?.status === 'done' ||
      currentTask.value?.status === 'escalated' ||
      currentTask.value?.status === 'aborted'
  );

  // 供视图层派生的便捷字段（对齐 M7_FRONTEND_DESIGN §8 TaskTracking 示例）
  const topic = computed(() => currentTask.value?.user_task.topic ?? '');
  const round = computed(() => currentTask.value?.routing_state.round ?? 0);
  const maxRounds = computed(() => currentTask.value?.routing_state.max_rounds ?? 0);
  const timelineNodes = computed<TimelineNode[]>(() => nodes.value);

  // 全量快照应用（用于兜底同步重建时间线）
  function applySnapshot(task: TaskResponse): void {
    currentTask.value = task;
    nodes.value = buildNodesFromTask(
      task.routing_state.gate_review_history,
      task.routing_state.engine_events
    );
  }

  // 全量兜底同步（mount / WS 重连时调用 GET /tasks/{id}）
  async function syncFullSnapshot(taskId: string): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const task = await getTask(taskId);
      applySnapshot(task);
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : '获取任务失败';
    } finally {
      loading.value = false;
    }
  }

  // WS 增量事件处理（判别联合强类型，无 any、无 as 断言）
  function handleEvent(ev: WSEvent): void {
    const node = wsEventToNode(ev);
    nodes.value = [...nodes.value, node];

    if (!currentTask.value) return;
    const rt = currentTask.value.routing_state;
    switch (ev.event_type) {
      case 'round_update': {
        // API_SPEC §3.2 payload 顶层平铺：round / max_rounds（修复 F3：进度条实时推进）
        const { round, max_rounds } = ev;
        currentTask.value = {
          ...currentTask.value,
          routing_state: { ...rt, round, max_rounds },
        };
        break;
      }
      case 'task_done': {
        // API_SPEC §3.2 payload 顶层平铺（非 data 包裹）；判别联合收窄后 report_markdown 直读
        const md = ev.report_markdown;
        currentTask.value = {
          ...currentTask.value,
          status: 'done',
          report_markdown: md ?? currentTask.value.report_markdown,
        };
        break;
      }
      case 'task_escalated': {
        currentTask.value = { ...currentTask.value, status: 'escalated' };
        break;
      }
      default:
        break;
    }
  }

  // 启动追踪：先全量兜底，再订阅增量（连接缓存复用）
  function connectTracking(taskId: string): void {
    void syncFullSnapshot(taskId);
    connect(taskId, (ev) => handleEvent(ev));
  }

  function dispose(taskId: string): void {
    closeWs(taskId);
  }

  async function createTask(req: CreateTaskRequest): Promise<string> {
    const res = await apiCreateTask(req);
    return res.task_id;
  }

  function setError(msg: string): void {
    error.value = msg;
  }

  return {
    currentTask,
    nodes,
    error,
    loading,
    status,
    isTerminal,
    topic,
    round,
    maxRounds,
    timelineNodes,
    applySnapshot,
    syncFullSnapshot,
    handleEvent,
    connectTracking,
    dispose,
    createTask,
    setError,
  };
});
