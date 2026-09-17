// M9-5 对话式入口：user_task 构建纯函数（唯一 payload 来源，真·控制器）
// 仅 import type，无运行时跨模块依赖 → 可被 Node --experimental-strip-types 真跑验证（E2/E4/E5/E6）。
import type { UserTask } from '@/types';

export interface ChatPreset {
  id: string;
  label: string;
  template_id?: string;
  scope: string[];
  constraints: string[];
  output_format_spec: string;
}

// 4 快捷模板（对齐平台 DESIGN_PLATFORM_FUNCTIONS.md §3.5）
export const CHAT_PRESETS: ChatPreset[] = [
  {
    id: 'standard',
    label: '标准研报',
    template_id: 'standard_research',
    scope: ['行业概况', '竞争格局', '关键风险', '核心结论'],
    constraints: ['数据须标注来源', '结论须可证伪'],
    output_format_spec: '结构化 Markdown：摘要 / 行业 / 竞争 / 风险 / 结论',
  },
  {
    id: 'competitor',
    label: '竞品快评',
    template_id: 'competitor_review',
    scope: ['竞品矩阵', '差异化', '份额'],
    constraints: ['对比须成对', '引用公开数据'],
    output_format_spec: '对比表 + 一句话结论',
  },
  {
    id: 'scan',
    label: '行业扫描',
    template_id: 'industry_scan',
    scope: ['产业链', '政策', '技术趋势'],
    constraints: ['覆盖最近 12 个月'],
    output_format_spec: '行业地图 + 趋势清单',
  },
  {
    id: 'earnings',
    label: '财报解读',
    template_id: 'earnings_read',
    scope: ['三表', '关键比率', '现金流'],
    constraints: ['比率须同比/环比', '附注异常须提示'],
    output_format_spec: '财务摘要 + 异常提示',
  },
];

// 无模板时的默认 scope（后端 api.py 要求 scope 非空，兜底满足契约）
export const DEFAULT_SCOPE = ['行业概况', '竞争格局', '关键风险'];

export interface BuiltTask {
  user_task: UserTask;
  template_id?: string;
  agents?: string[];
  // M9-5：本次任务的数据源 + 模型接口（ChatEntry 控制项透传）。
  plugins?: string[];
  model?: string;
  // 方案 A：独立「审核模型(Gate)」覆盖（auto/未传=沿用 model_mapping 异基座默认）。
  gate_model?: string;
}

// 唯一 payload 构建入口。组件与验证脚本共用，杜绝"假配置"。
export function buildUserTask(
  input: string,
  presetId?: string,
  agents?: string[],
  plugins?: string[],
  model?: string,
  gate_model?: string
): BuiltTask {
  const topic = (input || '').trim();
  if (!topic) {
    throw new Error('topic 不可为空'); // E4 守卫
  }
  const preset = CHAT_PRESETS.find((p) => p.id === presetId);
  const user_task: UserTask = {
    topic,
    // E5：scope 恒非空（preset 有则用 preset，否则 DEFAULT_SCOPE 兜底）
    scope: preset && preset.scope.length ? preset.scope : DEFAULT_SCOPE,
    constraints: preset ? preset.constraints : [],
    output_format_spec: preset ? preset.output_format_spec : '',
  };
  const out: BuiltTask = { user_task };
  if (preset && preset.template_id) out.template_id = preset.template_id;
  if (agents && agents.length) out.agents = agents; // E6 编排子集覆盖
  if (plugins && plugins.length) out.plugins = plugins; // 插件多选（真·控制器）
  if (model && model !== 'auto') out.model = model; // 模型接口（auto=沿用 model_mapping）
  if (gate_model && gate_model !== 'auto') out.gate_model = gate_model; // 方案 A：审核模型
  return out;
}

// 已知 Agent 角色（与 orchestrator 一致；自定义 Agent 由 /agents 市场管理）
export const KNOWN_AGENTS = ['Researcher', 'Analyst', 'Writer'];
