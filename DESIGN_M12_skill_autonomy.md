# DESIGN_M12 · Skill 自主获取与专家团（Skill Autonomy & Expert Corps）

> 状态：**草案 v2，待 boss 评审**。按铁律「先出设计文档，再写代码」，评审通过前不落任何实现代码。
> SoT 关系：本文是 M12 的单一事实源，与 `DESIGN.md v2.4`（引擎）、`DESIGN_M11-2_reflection.md`（反思/进化）并列，不覆盖它们。

## 0. 决策输入（boss）

| 决策项 | 选择 | 含义 |
|---|---|---|
| 自治程度 | **分级自治** | L0 提示词类自动装配；L1/L2 生成方案 + 试跑，**一键审批**才落盘 |
| 首批形态 | **SKILL.md 风格** | WorkBuddy/Claude 风格：一个 md（+可选脚本），格式规整、风险可控 |
| 专家团 | **仿 WorkBuddy 专家包体系构建** | 专家 = 领域人设 + 方法论/工作流 + 知识 + 绑定技能/工具；支持从仓库/网页**转化**生成 |
| Skill 来源 | **不限 GitHub，网上任意 URL** | 直链 md / GitHub 仓库 / 普通网页均可；不同类型走不同解析器与闸门 |

原始诉求：「我给她一个 github 上的 skill，她会自己按自己的架构去安装；比如我接你的专家团，她能自己学习接上。」
补充（v2）：「专家团仿照 WorkBuddy 构建；skill 也不一定是 github 上的，网上的可以。」

---

## 1. 目标 / 非目标

### 目标
- **G1 自主装配**：给定任意 URL，agent 自动完成「获取 → 理解 → 适配到自身架构 → 验证 → 生效」，无需人手改配置。
- **G2 分级自治 + 可回滚**：不同风险等级走不同闸门；任何落盘可一键回滚且留痕。
- **G3 专家团（仿 WorkBuddy）**：专家包体系——领域人设 + 方法论 + 绑定技能/工具/模型；可被显式 @ 或按意图自动路由；支持从 URL/仓库/本地项目**转化**生成专家包。

### 非目标（明确边界）
- **N1**：**不允许 agent 自主修改引擎自身代码**（boss 铁律）。"自主"止步于**生成装配方案 + 审批后落盘配置/技能/专家包**。
- **N2**：**不执行未审批的外部代码**。首批不做脚本执行（L2 见 §3.2）。
- **N3**：**不重建 Agent 体系**。专家是**上层封装**，复用现有 `agents_library.yaml` 的 shape 执行，不新增第二套角色引擎（避免重复造轮子）。
- **N4**：不改 `DESIGN.md v2.4` 已冻结的引擎语义。

---

## 2. 架构：五个标准扩展位

「按自己的架构去安装」= 自动判断外来产物属于哪一类，装配到对应扩展位。

| 扩展位 | 载体（已 bind mount，热加载） | 装配动作 | 风险 |
|---|---|---|---|
| **Agent 角色** | `config/agents_library.yaml` | 新增角色定义（shape/prompt/model） | 中 |
| **技能指令** | `skills/<id>.md` + `config/skills.yaml` | 落 md + 注册表条目 | **低（L0）** |
| **数据源** | `config/plugins.yaml` + Provider | 新增源（keyless 可零代码） | 中 |
| **外部工具** | `config/mcp_servers.yaml` / 声明式 HTTP 工具 | 新增 server 或 HTTP 工具条目 | 中（L1） |
| **专家包（新增）** | `experts/<name>/{plugin.json, agents/*.md}`（WorkBuddy 原生格式）+ `config/experts.yaml`（注册表） | 包体原样落盘 + 注册表记一行映射 | 中（L1） |

可复用既有机制：热加载（改完下个任务/对话生效）、M11-2 的「草稿 → `accept_lesson()` → 生效」半自动进化范式（`server/admin.py:2226/2241`）、ruamel round-trip 回写（保注释，缺失 fail loud）。

---

## 3. 来源解析与分级闸门

### 3.1 来源类型 → 解析器（v2 泛化）

| 来源 | 识别 | 解析器 | 默认闸门 |
|---|---|---|---|
| **直链 `.md`** | URL 以 `.md` 结尾 / content-type `text/markdown` | 直接取正文 + frontmatter | **L0**（纯指令则自动装配） |
| **GitHub 仓库** | host ∈ `github.com` | 定位 `SKILL.md`（仓库根或 `.workbuddy/skills/<n>/`） | **L0**（同上） |
| **普通网页** | 其它 HTTP(S) | 抓 HTML → **正文提取**（去 script/style/导航/广告）→ LLM 提炼为 SKILL.md 形态 | **L1**（含 LLM 提炼步骤，产物须审批） |
| **专家包 manifest** | 含 `expert.yaml`/`expert.json` 或 WorkBuddy 风格专家包 | 直接映射专家包字段 | **L1** |

正文提取要求：剥离 `<script>/<style>/<nav>/<footer>`，只保留主内容区；结果超 100KB 截断；提取失败诚实报错，不猜。

### 3.2 风险分级（保守优先）

| 等级 | 判定 | 装配目标 | 闸门 | 首批 |
|---|---|---|---|---|
| **L0 提示词型** | 纯指令文本，无脚本、无外部调用 | `skills/` + `config/skills.yaml` | 自动生效（schema + 渲染校验） | ✅ |
| **L1 声明式型** | 含 HTTP/OpenAPI/专家 API 声明，**无可执行代码**；或来自网页提炼 | 工具条目 / 数据源 / 专家包 | **dry-run → 一键审批** | ✅ |
| **L2 代码型** | 含 `scripts/`、`#!/`、`python `、`bash `、`allowed-tools` 等代码信号 | 需适配器 + 沙箱 | **首批直接拒绝**并诚实提示 | ❌ |

判定**保守**：任一代码信号命中即 L2 拒绝；无法确定一律按更高等级处理。

---

## 4. `tools/skill_importer.py`（新模块）

状态机：`FETCH → PARSE → CLASSIFY → ADAPT → VALIDATE → GATE → INSTALL → VERIFY`

```
import_skill(url) -> ImportResult
  FETCH    : 按 §3.1 选解析器；限大小（≤1MB）/超时（15s）；网络白名单
  PARSE    : 提取正文 + frontmatter（网页源走正文提取）
  CLASSIFY : L0 / L1 / L2（保守判定）
  ADAPT    : 产出 AssemblySpec（§4.1）—— **强 schema 校验，不接受自由文本**
  VALIDATE : L0 渲染检查；L1 dry-run 真实探测（超时/鉴权失败即判失败）
  GATE     : L0 → INSTALL；L1 → 落提案等审批；L2 → 拒绝
  INSTALL  : 先备份 → 写文件 → 写注册表（ruamel）
  VERIFY   : 回读校验（能被 build_skill_context / 工具装配 / 专家包加载读到）
```

### 4.1 AssemblySpec（强 schema）

```yaml
id: <kebab-case 唯一 id>
level: L0 | L1
name: <展示名>
description: <一句话>
target: skill | tool | datasource | expert     # 装到哪个扩展位
target_roles: [researcher, analyst, writer]     # 注入给谁
target_experts: [<expert_id>]                   # 挂到哪些专家（target=skill 时可选）
payload:
  # target=skill
  markdown: "<正文>"
  # target=tool（声明式，无代码）
  endpoint: https://...
  method: POST
  auth: {type: none|bearer|header, key_env: DS_XXX_API_KEY}
  request_template: {question: "{{query}}"}     # 仅白名单占位符
  response_map: {answer: "$.data.answer"}       # 仅受限 JSONPath
  # target=expert（见 §5）
  persona: "<人设 system prompt>"
  methodology: "<工作流步骤>"
  binds: {skills: [...], tools: [...], model: "<模型接口>"}
provenance: {source_url, source_type, fetched_at, sha256, license}
```

**约束**：`request_template` 仅允许 `{{query}}`/`{{topic}}` 等白名单占位符；`response_map` 仅允许受限 JSONPath（禁 `..`、禁函数调用）。目的：从结构上杜绝 LLM 生成出可执行片段。

### 4.2 落盘、留痕与回滚

- **提案**：`.audit/skill_proposals/<id>.json`（bind mount，可追溯）
- **备份**：安装前整份备份到 `.audit/skill_backups/<timestamp>/`
- **回滚**：`POST /admin/skills/{id}/rollback`
- **审计**：import/install/rollback 全进 `.audit`

### 4.3 API（admin 鉴权）

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/admin/skills/import` | `{url}` → 返回提案或 L0 直接安装结果 |
| GET | `/admin/skills/proposals` | 待审批提案（含等级/dry-run 结论） |
| POST | `/admin/skills/proposals/{id}/accept` | 一键采纳 |
| DELETE | `/admin/skills/proposals/{id}` | 驳回 |
| POST | `/admin/skills/{id}/rollback` | 回滚 |
| GET | `/admin/experts` / POST `/admin/experts/{id}/toggle` | 专家团列表与启停 |

---

## 5. G3：专家团（仿 WorkBuddy 专家包）

### 5.1 定位：不重建角色引擎

**专家 = 上层封装**，不是第二套 Agent 体系：

```
专家包（config/experts.yaml）
  ├─ persona        人设 system prompt（领域专家口吻、边界）
  ├─ methodology    方法论/工作流（分步 SOP，注入 prompt）
  ├─ binds.skills   绑定的技能 id（来自 skills/，可含导入的 skill）
  ├─ binds.tools    允许调用的工具（web_search / data_proc / mcp:* / expert:*）
  ├─ binds.model    模型偏好（走 model_mapping.yaml 接口名）
  └─ shape          映射到的引擎执行形态（researcher/analyst/writer）
```

执行时展开为：`<persona> + <methodology> + <已启用技能上下文>`，交给现有 `make_agent`（shape 驱动）跑。**复用 M9-1 的 shape 机制，零重复实现**。

### 5.2 专家包格式：**直接采用 WorkBuddy 专家包规范 v2.0（1:1，不自创）**

规范来源（本机权威，已核对）：`workbuddy-builtin/skills/expert-manager/SKILL.md`
+ `references/plugin-json-spec.md` + `references/agent-md-spec.md`。

> **因此 WorkBuddy 生态里的专家包可原样导入、零转换** —— 这才是"她能自己学习接上"。

**存放**：`experts/<kebab-name>/`（bind mount 持久化），保持原生结构：

```
experts/legal-contract-reviewer/
├── plugin.json                              # 元数据 + 展示字段
├── agents/legal-contract-reviewer.md        # 人设 + 核心能力 + 工作流程 + 输出规范 + 注意事项
├── skills/<skill-name>/                     # 可选，随包自带技能
└── avatars/expert.png                       # 可选
```

**`plugin.json` 关键字段**（完整版见规范）：

| 字段 | 说明 |
|---|---|
| `name` / `version` / `description` | 唯一标识（kebab-case）/ 语义化版本 / 英文一句话 |
| `expertType` | `"agent"`（首批支持）/ `"team"`（限制见 §5.6） |
| `agentName` | 主 Agent 名 = `agents/` 下 MD 文件名（不含 `.md），须有业务语义 |
| `agents` / `skills` | 资源路径数组 |
| `displayName` / `profession` / `displayDescription` | `{en,zh}`；`displayDescription` 中文 **40-50 字** |
| `categoryId` | 12 类之一（`01-ProductDesign` … `12-IndustryConsultant`） |
| `tags` / `quickPrompts` | 均**固定 3 个**；`quickPrompts[0]` 必须 == `defaultInitPrompt` |
| `avatar` / `plugin` | 头像相对路径 / 值与 `name` 一致 |

**Agent MD**（frontmatter **禁止声明 `tools`** —— 工具权限系统统一分配，沿用 WorkBuddy 铁律）：

```yaml
---
name: legal-contract-reviewer        # 必须与文件名一致
description: English description for activation
displayName: {en: "...", zh: "..."}
profession:  {en: "...", zh: "..."}
maxTurns: 50
skills: [contract-checklist]         # 可选，预加载技能
---
```

正文固定五段：**角色描述 / 核心能力 / 工作流程 / 输出规范 / 注意事项**。

### 5.3 运行时映射（WorkBuddy 格式 → 本引擎）

| WorkBuddy 概念 | 本引擎落地 |
|---|---|
| Agent MD 正文（角色+能力+流程+输出规范） | 拼成 `system prompt` 注入 `make_agent` |
| `frontmatter.skills` / 包内 `skills/` | 走 `build_skill_context` 注入（与导入的 skill 天然打通） |
| `categoryId` | 分类浏览 + 路由召回特征（不做强约束） |
| `expertType: agent` | 映射 `shape`：`researcher`/`analyst`/`writer`（导入时 LLM 建议，**审批时可改**） |
| 工具权限 | **不由包声明**，统一系统分配（沿用铁律） |
| `config/experts.yaml` | 只存**注册表**：id / 目录 / enabled / shape / model / 来源溯源 |

即：**包体保持 WorkBuddy 原生格式，我们只在 `config/experts.yaml` 记一行映射** —— 既能直接吃 WorkBuddy 专家包，又不破坏「配置即控制器」立约。

### 5.4 调度（两种触发）

- **显式**：对话中 `@<专家名>` 或 UI 选专家 → ChatAgent 加载该专家包执行。
- **隐式（自动路由）**：轻量 `expert_router` —— 按 `categoryId`/`tags`/描述做候选召回（关键词 + 可选 embedding），再交 LLM 一次判别；**未达高置信度则回退通用 ChatAgent**（不强行套专家，避免误路由）。

### 5.5 转化器（复用 Importer，对齐 WorkBuddy「资料转化模式」）

WorkBuddy 的转化策略（角色描述→Agent MD、流程→工作流程章节、API 文档→skills/references、脚本→skills/scripts、模板→templates、多角色分工→Team 型）我们照搬映射规则，用同一套 Importer 实现：

`POST /admin/experts/convert {url}` → 抓取 → LLM 产出专家包草案（`plugin.json` + `agents/*.md`）→ **审批** → 落 `experts/<name>/` + 注册。

与 skill 导入共享 fetch/parse/classify/备份/回滚/审计全链路，**不另写一套**。

### 5.6 与 WorkBuddy 的差异（诚实边界，必须写明）

| 差异 | 说明 | 处置 |
|---|---|---|
| **`expertType: team` 的协作机制** | WorkBuddy 靠宿主 `TeamCreate`/`Agent` 工具做真·多角色并发协作；本引擎是固定 LangGraph 流水线（Router/Researcher/Analyst/Writer），**没有等价物** | **首批只支持 `agent` 型**；`team` 型导入时诚实拒绝并提示，或降级为"拆成多个独立专家 + 主理人 prompt"（需 boss 定） |
| **头像生成** | WorkBuddy 用 `ImageGen` 自动生成 | 首批：允许无头像（用文字首字占位），可选后续接 `ImageGen` |
| **`maxTurns` / `bin/` 可执行工具** | 本引擎无对应概念 | 忽略（不报错），仅在校验时提示"该字段被忽略" |

---

## 6. 安全边界（不可协商）

1. **网络白名单**：默认允许 GitHub 域；**其它域名须 boss 显式确认后加入白名单**（网页来源风险更高）。
2. **路径白名单**：只写 `skills/`、`config/skills.yaml`、`config/experts.yaml`、`config/expert_apis.yaml`、`.audit/`。**严禁**写 `tools/`、`orchestrator.py`、`server/`。
3. **禁止代码执行**：首批不执行任何来自外部 skill 的脚本（L2 拒绝）。
4. **强 schema + 白名单占位符/JSONPath**。
5. **先备份后写**，保证可回滚。
6. **幂等**：同 `source_url + sha256` 不重复落盘。
7. **留痕**：所有动作进 `.audit`。

---

## 7. 验收标准（必须可实测，不认纸面）

| # | 场景 | 期望 |
|---|---|---|
| V1 | 导入直链/ GitHub 的纯指令 SKILL.md（L0） | 自动落盘，且下一个 /chat 的 system prompt **实测**能读到该技能（非仅"文件存在"） |
| V2 | 导入一个普通网页（L1） | 正文提取 → 提炼草案 → 审批 → 生效；不残留 HTML/脚本 |
| V3 | 从 URL 转化生成专家包 | 审批后入团，`@专家` 能按其人设+方法论作答 |
| V4 | 回滚 | 一键回滚后从注册表与 prompt 中消失，文件恢复 |
| V5 | 恶意/畸形输入（含脚本、超 1MB、非白名单域名、schema 不合法） | **拒绝**并给明确原因；零落盘、不污染现有配置 |
| V6 | 重复导入 | 幂等 |
| V7 | 专家路由误判保护 | 无高置信命中时回退通用 ChatAgent，不强行套专家 |
| V8 | 工具/专家 API 超时或鉴权失败 | 诚实降级 `{ok:false}`，/chat 不 500 |

---

## 8. 里程碑

- **M12-1 Importer 内核 + L0**：多来源解析器（直链 md / GitHub / 网页正文提取）、AssemblySpec schema、分级判定、备份/回滚/审计、单测覆盖 V1/V5/V6。
- **M12-2 L1 闸门 + API + UI**：提案落盘、dry-run、admin API、设置页「技能/专家」区、采纳/驳回、V2。
- **M12-3 专家团（WorkBuddy 原生格式）**：`experts/<name>/{plugin.json,agents/*.md}` 落盘与解析、
  `config/experts.yaml` 注册表、专家包装载（Agent MD 正文 + `frontmatter.skills` → `make_agent`）、
  `@专家` 显式调度、`expert_router` 自动路由与回退、转化器（照搬 WorkBuddy 资料转化映射）。
  **首批仅 `expertType: agent`**；`team` 型按 §5.6 处置。验收 V3/V4/V7/V8。

每档单独跑三道闸：① build/py_compile 校验 ② `VERIFICATION_M12-x.md` 自审 ③ **独立子代理** `REVIEW_M12-x.md`（主代理不自签）。

---

## 9. 风险与开放问题

| 项 | 说明 | 处置 |
|---|---|---|
| 容器出网 | `raw.githubusercontent.com` / 普通网页未实测（open-meteo 可达 ≠ 全网可达） | M12-1 第一步先探可达性；不可达则改宿主侧下载后挂载 |
| 网页正文提取质量 | 页面结构千差万别，易抓到导航/广告 | 先做保守提取 + 长度/噪声比校验；提取质量不达标则拒绝并提示人工粘贴 |
| 域名白名单运营 | 网上来源无边界，易失控 | 默认仅 GitHub；其它域名逐条显式确认入白名单，记入 `.audit` |
| 专家路由误判 | 自动路由可能选错专家 | 高置信才切专家，否则回退通用 ChatAgent（V7） |
| LLM 产出 AssemblySpec 幻觉 | 自由生成易产生非法值 | schema 强校验 + 白名单 + dry-run 三重拦截；失败即退回人工填写 |
| 与 M11-2 经验机制关系 | 两者都用「草稿→审批」 | 复用同一 UI/API 范式，但存储与审计分开，避免互相污染 |
| ~~需要 boss 提供 WorkBuddy 样例~~ | **已自行解决**：本机 `expert-manager` skill 即权威规范，§5.2 已 1:1 对齐 | — |
| **待 boss 定**：`expertType: team` 处置 | WorkBuddy 靠宿主 `TeamCreate/Agent` 做真·多角色协作，**本引擎无等价物** | 二选一：① 首批直接拒绝 team 型（保守、诚实）；② 降级为"拆成多个独立专家 + 主理人 prompt"（能覆盖更多生态包，但语义有损）。见 §5.6 |
