# DESIGN_PLATFORM_FUNCTIONS · 研报 Agent 平台功能设计

> 元信息：起草 2026-09-08（boss 给 6 张 Accio Work 参考图，明确"我需要的是里面的功能"）
> 状态：待评审（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议），全 PASS 才动代码
> 关联：[DESIGN_SETTINGS_CONSOLE.md](./DESIGN_SETTINGS_CONSOLE.md)（配置层）、[UIDESIGN.md](./UIDESIGN.md)、[DESIGN.md](./DESIGN.md)、[API_SPEC.md](./API_SPEC.md)

---

## 0. 立约与来源

### 0.1 触发（2026-09-08）
boss 给 6 张 **Accio Work** 界面截图，初判为视觉参考，经澄清**实际要的是里面的功能体系**：

> "你注意看图，我需要的是里面的功能"

### 0.2 方向校正
- ❌ 初判（错）：把 UI 做成 Accio 那种视觉风格
- ✅ 正解（对）：把 Accio 那套**平台级功能**（Agent 市场 / 插件市场 / 技能库 / 消息渠道 / 对话入口）落地到研报系统，**按研报场景替换领域内容**

### 0.3 与 Settings Console 的分工
| 文档 | 定位 | 覆盖 |
|---|---|---|
| `DESIGN_SETTINGS_CONSOLE.md` | **配置层** | 让**已有能力**可配置（模型映射 / Agent prompt / Gate 审核 / 任务模板 / Agent 数量 / 输出格式） |
| **`DESIGN_PLATFORM_FUNCTIONS.md`（本文件）** | **扩展层** | 让**新能力**可插拔（Agent 市场 / 数据源插件 / 研报技能 / 推送渠道 / 对话入口） |

两者共用同一套视觉风格（§6），通过 UIDESIGN 页面树整合。

---

## 1. Accio 功能提取（6 图实证）

| 图 | 页面 | 提供的功能 | 关键元素 |
|---|---|---|---|
| 图1 | 主对话 | 对话式入口 + 快捷任务 + Agent 切换 + 任务历史 | 输入框、5 快捷操作、右上"切换智能体"、侧边任务历史 |
| 图2 | 消息渠道 | 多渠道接入 + 授权管理 | Tabs（渠道配置/授权管理）、6 渠道卡片（钉钉/微信/飞书/企微/Telegram/Discord）、状态+统计+设置机器人 |
| 图3 | 应用权限 | 第三方服务授权连接 | Tabs（插件/技能/应用权限）、卡片（LinkedIn/Shopify/Alibaba/TikTok/GitHub）、未连接+连接 |
| 图4 | 插件市场 | 插件生态（浏览/搜索/筛选/安装） | Tabs（市场/我的）、搜索+筛选、卡片（采购工具箱/生意助手/CRM/建站等）、安装按钮 |
| 图5 | 技能库 | 能力分类 + 添加 | 搜索+筛选、分组（按海岛岛岛6/市场调研19/产品设计4）、卡片+`+` |
| 图6 | 智能体 | 多 Agent 管理 + 对话 | Tabs（公开/个人）、6 列卡片网格、头像+名称+标签+描述+对话按钮 |

---

## 2. 研报场景映射（核心）

### 2.1 映射总表

| Accio 功能 | 研报对应 | 价值 | ⚠️ 适配说明 |
|---|---|---|---|
| 智能体市场 | **研报 Agent 库** | ⭐⭐⭐⭐⭐ | 直接适用；Agent 换成研究员/分析师/撰稿人/审核员/财务专家 |
| 插件市场 | **数据源插件市场** | ⭐⭐⭐⭐⭐ | ⚠️ **必须替换**：Accio 的 Shopify/Alibaba/TikTok 是电商插件，对研报**无意义**；换为 Tavily/arXiv/雪球/巨潮/企查查 |
| 技能库 | **研报技能库** | ⭐⭐⭐⭐⭐ | 直接适用；技能内容换为财务分析/行业扫描/竞品对比/估值建模/风险识别 |
| 消息渠道 | **研报推送渠道** | ⭐⭐⭐⭐ | 直接适用；研报完成推送到钉钉/企微/飞书等 |
| 应用权限 | **数据源授权** | ⭐⭐⭐ | 直接适用；管理 Tavily Key / Wind 账号等 |
| 对话入口 | **对话式研报提交** | ⭐⭐⭐⭐ | 直接适用；"帮我写份 XX 行业研报" |
| 快捷操作 | **研报快捷模板** | ⭐⭐⭐⭐ | 直接适用；标准研报/竞品快评/行业扫描/财报解读 |
| 任务历史 | **研报任务历史** | ⭐⭐⭐ | 已有能力，需按新风格美化 |

### 2.2 关键诚实标注：不是"抄功能"

Accio 是**电商 AI 助手平台**，report-agent-team 是**研报多 Agent 协作系统**。两者领域不同：

| Accio 领域内容 | 研报领域内容（替换后） |
|---|---|
| Shopify / Alibaba / TikTok / 1688 | Tavily / arXiv / 雪球 / 巨潮资讯 / 企查查 |
| 采购工具箱 / 生意助手 / CRM / 建站 | 行业扫描 / 财报解读 / 竞品对比 / 估值建模 |
| 办公提效 / 设计 / 团购站搭建 | 标准研报 / 竞品快评 / 深度行业研究 |

**结论**：抄的是**平台架构**（市场 + 分类 + 安装 + 管理），换的是**领域内容**。若直接搬电商插件即为错误实现。

---

## 3. 功能架构（四大市场 + 一个入口）

### 3.1 智能体市场（Agent Library）
**对应**：图6 智能体页 + M8 G5（Agent 数量扩展）

**数据模型**（`config/agents_library.yaml`）
```yaml
agents:
  - id: researcher
    name: 研究员
    avatar: /avatars/researcher.png      # 圆形头像
    tags: [调研, 信息检索]
    description: 负责信息检索与事实核证，输出 retrieval_records
    role_key: Researcher                  # 关联 orchestrator 的 role
    tool: web_search
    gate: GateA
    visibility: public                    # public | private
    builtin: true                         # 内置不可删
```

**页面**：`/agents`
- Tabs：公开 / 个人 + 「全部智能体」下拉筛选
- 6 列卡片网格（头像 + 名称 + 标签 + 描述 + "对话"/"配置"按钮）
- 卡片点击 → Agent 详情/编辑（复用 M8-2 的 prompt 编辑器）

**能力**
- 浏览/搜索/筛选
- 新增 Agent（关联 tool + gate）→ 与 M8-5 的 `agents_registry.yaml` 打通
- 内置 Agent 只读，自定义 Agent 可编辑/删除

### 3.2 数据源插件市场（DataSource Plugins）
**对应**：图4 插件市场（**领域内容已替换**）
**✅ 已交付（M9-2，详见 [DESIGN_M9-2.md](./DESIGN_M9-2.md)）**：数据源插件是**真·控制器**——启停/连接状态经 `build_tools → build_search_tool` 真实驱动引擎检索输入（禁用某源 → 该源不进 `retrieval_records` → GateA 可能 escalate）；`coming_soon` 源引擎**永远跳过**，绝不冒充可用。SoT = `config/plugins.yaml`，密钥走 `.secrets/plugins.env`（不落 config）。

**数据模型**（`config/plugins.yaml`）
```yaml
plugins:
  - id: tavily
    name: Tavily 搜索
    icon: /icons/tavily.svg
    description: 实时网络检索，支持学术/新闻/财经源
    category: 数据源
    status: connected                     # connected | disconnected | coming_soon
    auth_type: api_key                    # api_key | oauth | none
    installed: true
```

**页面**：`/plugins`
- Tabs：市场 / 我的
- 搜索 + 分类筛选（数据源 / 财报 / 专利 / 新闻 / 社交）
- 卡片网格（图标 + 名称 + 描述 + "安装"/"已安装"）
- 点击"连接" → 授权弹窗（填 API Key 或 OAuth）

**内置插件清单（研报向）**
| 插件 | 用途 | 授权方式 |
|---|---|---|
| Tavily | 实时网络检索 | API Key |
| arXiv | 学术论文 | 无需 |
| 雪球 | A股/港股行情与讨论 | Cookie 或免授权 |
| 巨潮资讯 | 上市公司公告/财报 | 无需 |
| 企查查 | 工商信息/股权穿透 | API Key |
| Google Scholar | 学术引用 | 无需 |

### 3.3 研报技能库（Research Skills）✅ M9-3 已交付
**对应**：图5 技能库

> 实现见 [DESIGN_M9-3.md](./DESIGN_M9-3.md)：技能 = prompt 框架软增强，启用后注入对应 Agent 的 system prompt（target_roles 作用域），真·控制器（非装饰卡片）。字段统一用 `prompt_file`（与 config/skills.yaml 一致）。

**数据模型**（`config/skills.yaml`）
```yaml
skills:
  - id: financial_analysis
    name: 财务分析
    icon: /skills/financial.svg
    description: 三表分析、杜邦分解、现金流质量评估
    category: 财务与估值
    prompt_file: skills/financial_analysis.md   # 技能 prompt 片段（字段名与 config/skills.yaml / DESIGN_M9-3 统一）
    installed: true
```

**页面**：`/skills`
- 搜索 + 筛选
- 分组展示（财务与估值 X / 行业研究 Y / 竞品对比 Z / 风险识别 W）
- 卡片（图标 + 名称 + 描述 + `+` 添加）

**与 Settings Console 的关系**：技能本质是**可复用的 prompt 片段**，安装后可在 Agent prompt 编辑器中插入（M8-2 的 Monaco 编辑器支持片段插入）。

### 3.4 研报推送渠道（Delivery Channels）✅ M9-4 已交付
**对应**：图2 消息渠道（**研报场景新增价值**）

> 实现见 [DESIGN_M9-4.md](./DESIGN_M9-4.md)：推送 = 真·控制器（启用+端点已填 → 研报完成/升级真实外发 webhook/mock；coming_soon 永不发）。SoT = `config/channels.yaml`，无片段文件、无新 bind mount（channels 配置已在 M8 `./config` bind mount 内）。

**数据模型**（`config/channels.yaml`）
```yaml
channels:
  - id: dingtalk
    name: 钉钉
    icon: /channels/dingtalk.svg
    description: 研报完成后推送到钉钉群机器人
    status: disconnected                  # connected | disconnected
    stats: {users: 0, groups: 0, pending: 0}
    config:
      webhook: ""
      strategy: on_complete               # on_complete | on_gate_fail | manual
      pair_mode: group                    # group | single
```

**页面**：`/channels`
- Tabs：渠道配置 / 授权管理
- 6 渠道卡片（钉钉/微信/飞书/企业微信/Telegram/Discord）
- 每卡：状态标签 + 统计（用户/群聊/待处理）+ 策略配置 + "设置机器人"

**推送触发点**
- 任务完成（`status=done`）→ 推研报摘要 + 下载链接
- Gate 拦截（`escalated`）→ 推告警
- 手动推送

### 3.5 对话式入口（Conversational Entry）✅ M9-5 已交付
**对应**：图1 主对话页

> 实现见 [DESIGN_M9-5.md](./DESIGN_M9-5.md)：对话入口 = 真·控制器（结构化产出 user_task 并调 `POST /tasks` 真实触发引擎；4 快捷模板真改 scope/constraints；内联智能体选择 + 市场「对话」闭环预置编排子集）。纯前端 + 复用既有 API，后端零改动。

**页面**：`/`（改造现有首页）
- 中央输入框："需要一份什么研报？"
- 下方快捷模板：标准研报 / 竞品快评 / 行业扫描 / 财报解读
- 右上「切换智能体」→ 跳 `/agents` 选择当前对话使用的 Agent 组合
- 输入后 → 解析为 `user_task` → 跳任务提交确认 → 执行

**与现有流程的关系**：对话入口是**任务提交页的前置交互层**，最终仍产出符合 API_SPEC 的 `user_task`，不破坏既有契约。

---

## 4. 页面结构（研报版 Accio 六页）

```
# ── M7 既有（实测 router/index.ts，勿臆造）──
/templates              # 模板选择（当前首页重定向目标）
/submit                 # 提交任务
/tasks/:taskId          # 运行追踪
/tasks/:taskId/result   # 研报结果
/tasks/:taskId/review   # 人工复核

# ── M9 新增（本设计）──
/                       # 对话入口（图1，M9-5 ✅ 已交付）
/agents                 # 智能体（图6，M9-1）
  /agents/[id]          # Agent 详情/编辑（复用 M8-2 prompt 编辑器）
/plugins                # 数据源插件市场（图4，M9-2，领域已替换）
  /plugins/[id]         # 插件详情/授权
/skills                 # 研报技能库（图5，M9-3）
/channels               # 推送渠道（图2，M9-4 ✅ 已交付）

# ── M8 新增（配置控制台，见 DESIGN_SETTINGS_CONSOLE.md）──
/settings               # 配置控制台首页
  /settings/models
  /settings/agents
  /settings/gates
  /settings/templates
  /settings/audit
```

> **⚠️ 实测校正（2026-09-08，独立审议 agent-bb9b6455 纠出）**：本文件初版曾写 `/tasks`
> 与 `/run/[taskId]`，**实测均不存在**。现行路由以 `web/src/router/index.ts` 为准（见上）。
> 后续实施须以该文件为唯一事实源，不得凭印象写路由。

> **⚠️ 真实功能缺口（顺带发现，非本轮范围）**：Accio 图1 有「任务历史」侧边栏
> （可浏览历史任务列表），但 report-agent-team 当前**无任务列表页**——只有
> `/tasks/:taskId` 按 ID 直访，用户无法回看历史任务。此缺口建议纳入 M9-5
> （对话入口改造）一并解决，或单开 `/tasks` 列表页里程碑。

---

## 5. 与 Settings Console 的整合

| 能力 | Settings Console（配置层） | Platform Functions（扩展层） |
|---|---|---|
| Agent | 编辑已有 Agent 的 prompt / 模型 | 浏览 Agent 市场、新增自定义 Agent |
| 工具 | 配置 Agent 绑定哪个 tool | 从插件市场安装新数据源 |
| 技能 | （无） | 浏览/安装研报技能（prompt 片段） |
| 输出 | 配置输出格式（md/pdf/pptx/docx） | 配置推送渠道（研报完成后推到哪） |
| 入口 | （无） | 对话式提交 + 快捷模板 |

**数据流**：对话入口 → 选 Agent/技能 → 提任务 → 引擎执行（用插件取数）→ 出研报 → 推渠道

---

## 6. 视觉风格（Accio 提取，两文档共用）

### 6.1 设计定位
极简白底 + 大留白 + 绿色强调；类 Linear / Notion / ChatGPT 风格

### 6.2 色板
| 用途 | 色值 |
|---|---|
| 卡片背景 | `#FFFFFF` |
| 页面背景 | `#F9FAFB` |
| Hover 背景 | `#F3F4F6` |
| 主色（强调/按钮） | `#10B981` |
| 标题文字 | `#111827` |
| 副标题文字 | `#6B7280` |
| 次要文字 | `#9CA3AF` |
| 边框 | `#E5E7EB`（1px） |
| 状态-未关联 | `#9CA3AF`（灰） |
| 状态-即将推出 | `#F59E0B`（黄） |
| 状态-已连接 | `#10B981`（绿） |

### 6.3 组件规格
| 组件 | 规格 |
|---|---|
| 卡片 | 白底 + 圆角 8-12px + 1px `#E5E7EB` 边框 + 16-20px 内边距 |
| 状态标签 | 圆点 + 文字，胶囊形 |
| 按钮 | 主=绿填充、次=描边、动作=右上 `+`/`连接`/`安装` 文字链 |
| 头像 | 圆形 + 品牌色或渐变 |
| 搜索框 | 圆角 + 浅灰底 |

### 6.4 布局范式
- 左 sidebar 固定 240px（主导航 + 任务历史 + Agent 团队）
- 顶部窄 header（页面标题 + 一行描述 + Tabs）
- 主内容卡片网格（响应式 2/3/6 列）
- 底部用户信息条

---

## 7. 实施路线

| 里程碑 | 内容 | 依赖 |
|---|---|---|
| **M8**（已在设计） | Settings Console：6 类配置 UI 化 | 无 |
| **M9-1** | 智能体市场 `/agents` | M8-5（Agent 数量扩展） |
| **M9-2** | 数据源插件市场 `/plugins` | M9-1 | ✅ 已交付（commit 481aaf6，三道闸全闭环） |
| **M9-3** | 研报技能库 `/skills` | M8-2（prompt 编辑器） | ✅ 已交付（三道闸全闭环，见 DESIGN_M9-3.md） |
| **M9-4** | 推送渠道 `/channels` | 引擎任务完成钩子 | ✅ 已交付（三道闸全闭环，见 DESIGN_M9-4.md） |
| **M9-5** | 对话式入口 + 快捷模板 | M9-1~4 | ✅ 已交付（三道闸全闭环，见 DESIGN_M9-5.md） |

**顺序理由**：先 M8 把配置能力做扎实（已有能力可控），再 M9 做扩展生态（新能力可插）。避免能力扩展了但配置还靠改文件的尴尬。

---

## 8. 风险与诚实边界

| 风险 | 说明 | 缓解 |
|---|---|---|
| **领域内容误搬** | 若直接实现 Shopify/Alibaba 等电商插件即为错误 | §2.2 已明确替换为研报数据源；评审须核查 |
| **范围蔓延** | 平台化是长期工程，易失控 | 严格按 M9-1~M9-5 分步，每步单独走三道闸 |
| **与 LangGraph 编排冲突** | 现有引擎是固定 3 Agent 流水线，Agent 市场引入动态性 | M8-5 已设计 `agents_registry.yaml` 外置；M9-1 复用该机制 |
| **插件实际可用性** | 雪球/巨潮/企查查 等国内源反爬与授权不稳 | M9-2 实现时逐个实测，不可用的标 `coming_soon` 不假装可用 |
| **推送渠道合规** | 微信/企微机器人有频率与资质限制 | M9-4 实现时按官方能力设计，不承诺超额能力 |

---

## 9. 开放问题（待 boss 确认）

| # | 问题 | 影响 |
|---|---|---|
| Q1 | 研报数据源插件：优先哪几个？（Tavily 已有 MOCK，其余待定） | M9-2 范围 |
| Q2 | 推送渠道：是否真需要全部 6 个？（钉钉/企微优先？） | M9-4 范围 |
| Q3 | 对话入口：是否要真 LLM 解析意图，还是表单式引导？ | M9-5 复杂度 |
| Q4 | 技能库：技能是 prompt 片段还是可执行工具？ | M9-3 实现方式 |

> 上述 Q1–Q4 不阻塞本设计文档评审，实施到对应里程碑时再定。

---

## 10. 评审修订记录

| 轮次 | 日期 | 状态 | 评审者 | 主要修订 |
|---|---|---|---|---|
| 草案 | 2026-09-08 | 待评审 | （主代理） | 初版：Accio 功能提取 + 研报场景映射 |
