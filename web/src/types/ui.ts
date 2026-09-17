// UI 层视图类型（对齐 UIDESIGN §5 + M7_FRONTEND_DESIGN §4.3）
import type { TaskStatus, AgentRole, GateName } from './api';
import type { WSEvent } from './engine';

// 时间线节点（UI 层聚合后的节点）
export interface TimelineNode {
  id: string; // 唯一 key（event_type + timestamp + 序号）
  type: 'agent' | 'gate' | 'rework' | 'round' | 'tool_error' | 'done' | 'escalated';
  timestamp: string;
  title: string; // 用户叙事的标题
  description: string; // 用户叙事的描述
  status: 'success' | 'warning' | 'error' | 'info'; // 节点状态色
  rawEvent?: WSEvent; // 原始事件（调试模式展示）
  meta: Record<string, unknown>; // 附加元数据（用于展开详情）；原设计文档为 any，已修正为 unknown
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

// 智能体市场条目（M9-1 · config/agents_library.yaml）
// 注意：id/gate 为 string 而非 AgentRole/GateName 联合类型——自定义 Agent 会打破内置联合，
// 强行收窄会让新增角色在类型层就报错（等于把扩展能力锁死）。
export interface AgentLibItem {
  id: string;
  name: string;
  avatar: string;
  tags: string[];
  description: string;
  shape: 'researcher' | 'analyst' | 'writer';
  tool: string;
  output_key: string;
  gate: string;
  visibility: 'public' | 'private';
  builtin: boolean;
}

// 数据源插件（M9-2，M10-P5 扩展字段）
export interface PluginItem {
  id: string;
  name: string;
  icon: string;
  category: string;
  description: string;
  auth_type: 'none' | 'api_key' | 'oauth';
  provider: string;
  enabled: boolean;
  status: 'connected' | 'disconnected' | 'coming_soon' | 'credential_saved';
  builtin: boolean;
  // M10-P5：真实官网 / 获取密钥教程（仅展示，不进引擎逻辑）
  doc_url?: string;
  key_guide_url?: string;
  key_env?: string;
}

// 研报技能库（M9-3 · config/skills.yaml）
// prompt_file 字段与 config/skills.yaml / DESIGN_PLATFORM_FUNCTIONS.md §3.3 统一（NOTE-2 字段漂移修正）
export interface SkillItem {
  id: string;
  name: string;
  icon: string;
  category: string;
  description: string;
  target_roles: string[];
  prompt_file: string;
  enabled: boolean;
  installed: boolean;
  builtin: boolean;
  status: 'active' | 'inactive';
}

// 研报推送渠道（M9-4 · config/channels.yaml）
// endpoint 仅管理视图使用（公开 GET /channels 不返回 endpoint，防泄露）；status 派生自 enabled+endpoint
export interface ChannelItem {
  id: string;
  name: string;
  icon: string;
  category: string;
  description: string;
  channel_type: 'webhook' | 'mock' | 'coming_soon';
  strategy: string[];
  endpoint: string;
  enabled: boolean;
  installed: boolean;
  builtin: boolean;
  status: 'connected' | 'disconnected' | 'coming_soon';
}

// 模板信息
export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  agents: AgentRole[];
  gates: GateName[];
  ui_mode: 'unified_shell' | 'custom_component';
  output_format: string[];
}
