// 路由配置（5 个页面，对齐 M7_FRONTEND_DESIGN §6）
import { createRouter, createWebHistory } from 'vue-router';
import TemplateSelect from '@/views/TemplateSelect.vue';
import TaskSubmit from '@/views/TaskSubmit.vue';
import TaskTracking from '@/views/TaskTracking.vue';
import TaskResult from '@/views/TaskResult.vue';
import TaskReview from '@/views/TaskReview.vue';
import SettingsModels from '@/views/settings/Models.vue';
import SettingsAgents from '@/views/settings/Agents.vue';
import SettingsGates from '@/views/settings/Gates.vue';
import SettingsTemplates from '@/views/settings/Templates.vue';
import SettingsTemplateEdit from '@/views/settings/TemplateEdit.vue';
import SettingsCustomProviders from '@/views/settings/CustomProviders.vue';
import SettingsMcpServers from '@/views/settings/McpServers.vue';
import SettingsExperts from '@/views/settings/Experts.vue';
import SettingsAsset from '@/views/settings/AssetCenter.vue';
import AgentsMarket from '@/views/Agents.vue';
import PluginsMarket from '@/views/Plugins.vue';
import SkillsMarket from '@/views/Skills.vue';
import ChannelsMarket from '@/views/Channels.vue';
import Market from '@/views/Market.vue';
import ChatEntry from '@/views/ChatEntry.vue';
import Login from '@/views/Login.vue';
import SettingsAccounts from '@/views/settings/Accounts.vue';

const routes = [
  // M9-5 对话式研报入口：中央输入 + 快捷模板 + 智能体组合（真调 createTask → POST /tasks → 引擎）
  { path: '/', name: 'ChatEntry', component: ChatEntry, meta: { title: '研报入口' } },
  // 聊天记录：每个会话一个窗口，URL 携带 sessionId（ChatEntry 同源组件，靠 watch 切会话）
  // meta.bare：登录/注册页不套应用外壳（无侧边栏）——侧边栏是登录后才有的功能
  { path: '/login', name: 'Login', component: Login, meta: { title: '登录', bare: true } },
  { path: '/chat/:sessionId', name: 'ChatSession', component: ChatEntry, meta: { title: '聊天' } },
  // M9-1 真·智能体市场：浏览/新增自定义 Agent（新增即真驱动流水线）
  { path: '/agents', name: 'AgentsMarket', component: AgentsMarket, meta: { title: '智能体市场' } },
  // M9-2 数据源插件市场：浏览/接入研报数据源（启用真驱动引擎检索）
  { path: '/plugins', name: 'PluginsMarket', component: PluginsMarket, meta: { title: '数据源插件' } },
  // M9-3 研报技能库：浏览/启用研报分析框架技能（启用真驱动对应 Agent prompt）
  { path: '/skills', name: 'SkillsMarket', component: SkillsMarket, meta: { title: '研报技能' } },
  // M9-4 研报推送渠道：浏览/配置/启停推送渠道（启用真驱动研报完成外发）
  { path: '/channels', name: 'ChannelsMarket', component: ChannelsMarket, meta: { title: '推送渠道' } },
  // Accio 统一能力市场：一页内 数据源/研报技能/推送渠道 三主 Tab（内嵌既有视图，复用不重写）
  { path: '/market', name: 'Market', component: Market, meta: { title: '能力市场' } },
  { path: '/templates', name: 'TemplateSelect', component: TemplateSelect, meta: { title: '选择模板' } },
  { path: '/submit', name: 'TaskSubmit', component: TaskSubmit, meta: { title: '提交任务' } },
  { path: '/tasks/:taskId', name: 'TaskTracking', component: TaskTracking, props: true, meta: { title: '运行追踪' } },
  { path: '/tasks/:taskId/result', name: 'TaskResult', component: TaskResult, props: true, meta: { title: '研报结果' } },
  { path: '/tasks/:taskId/review', name: 'TaskReview', component: TaskReview, props: true, meta: { title: '人工复核' } },
  // M8 配置控制台（UI 必须是 controller：模型映射 / Agent 提示词 / Gate 审核均在此直接操作）
  { path: '/settings', redirect: '/settings/models' },
  { path: '/settings/models', name: 'SettingsModels', component: SettingsModels, meta: { title: '模型映射' } },
  { path: '/settings/agents', name: 'SettingsAgents', component: SettingsAgents, meta: { title: 'Agent 提示词' } },
  { path: '/settings/gates', name: 'SettingsGates', component: SettingsGates, meta: { title: 'Gate 审核' } },
  { path: '/settings/gates/:gate', name: 'SettingsGate', component: SettingsGates, meta: { title: 'Gate 审核' } },
  { path: '/settings/templates', name: 'SettingsTemplates', component: SettingsTemplates, meta: { title: '任务模板' } },
  { path: '/settings/templates/new', name: 'SettingsTemplatesNew', component: SettingsTemplateEdit, meta: { title: '新增模板' } },
  { path: '/settings/templates/:name', name: 'SettingsTemplateEdit', component: SettingsTemplateEdit, meta: { title: '编辑模板' } },
  { path: '/settings/custom-providers', name: 'SettingsCustomProviders', component: SettingsCustomProviders, meta: { title: '自定义 API' } },
  // M11-3 MCP 消费桥：server 增删改即时落 config/mcp_servers.yaml（真·控制器）
  { path: '/settings/mcp-servers', name: 'SettingsMcpServers', component: SettingsMcpServers, meta: { title: 'MCP Server' } },
  // M12-3 专家团：列表 / 启停 / URL 转化提案 / 审批
  { path: '/settings/experts', name: 'SettingsExperts', component: SettingsExperts, meta: { title: '专家团' } },
  // M11-2 Asset Center（A1 合并面板）：反思建议 + 经验草稿，统一人工门禁入口
  { path: '/settings/asset-center', name: 'SettingsAsset', component: SettingsAsset, meta: { title: '经验 / 反思' } },
  // Phase 1 账号管理：仅主账号可见（启用/审批/停用/重置密码/删除子账号）
  { path: '/settings/accounts', name: 'SettingsAccounts', component: SettingsAccounts, meta: { title: '账号管理' } },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// ---- 登录守卫（Phase 1 · DESIGN_account_hierarchy.md §7）----
// 未登录且目标不是 /login → 跳登录页；已登录访问 /login → 回首页。
// 说明：仅做前端跳转，**不是**安全边界（真正的边界在后端 get_current_account）。
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('rat_token') || '';
  if (!token && to.name !== 'Login') {
    return next({ name: 'Login', query: { redirect: to.fullPath } });
  }
  if (token && to.name === 'Login') {
    return next({ path: '/' });
  }
  return next();
});

export default router;
