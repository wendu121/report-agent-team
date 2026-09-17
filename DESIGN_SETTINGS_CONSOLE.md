# DESIGN_SETTINGS_CONSOLE · M8 · 配置控制台

> 元信息：起草 2026-09-07（boss 立约"UI 必须是 controller，不能是 viewer"），落地方案
> 状态：待评审（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议），全 PASS 才动代码
> 关联：
> - **[DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md)**（扩展层：Agent 市场 / 数据源插件 / 研报技能 / 推送渠道 / 对话入口）
> - [UIDESIGN.md §6 模板驱动](../UIDESIGN.md)（当年设计愿景，本设计落地）、[DESIGN.md](../DESIGN.md)、[API_SPEC.md](../API_SPEC.md)
>
> **与 Platform Functions 的分工**：本文件是**配置层**（让已有能力可配置）；
> `DESIGN_PLATFORM_FUNCTIONS.md` 是**扩展层**（让新能力可插拔）。两者共用同一套视觉风格
> （见 Platform Functions §6），实施顺序：先 M8（本文件，配置扎实）→ 再 M9（平台功能扩展）。

---

## 0. 立约与现状

### 0.1 触发与立约（2026-09-07）
boss 在看 M7 完工的前端时质疑：当下一版「改造成本速查」表把「换模型 = ⭐ 分钟级」当卖点，但 `config/model_mapping.yaml` 在容器内文件系统里，**真要换模型得 SSH 进容器 + vi + docker restart**——所谓"分钟级"对 UI 用户根本不成立。

boss 立约：**UI 必须是 controller，不能是 viewer。** 所有决定系统行为的配置（6 类）必须在 UI 里直接操作，否则 UI 就是「带皮肤的 CLI」，没有意义。

原则已固化进项目长期记忆（MEMORY.md「UI 控制器原则」）。

### 0.2 当前架构缺陷（代码实证）
| 缺口 | 实测证据（`E:\第二电脑\report-agent-team`） |
|---|---|
| 模型映射需改 yaml | `config/model_mapping.yaml` 在容器内 `/app/config/`，浏览器访问不到 |
| Agent prompt 需改 md | `agents/{researcher,analyst,writer}.md` 同上 |
| Gate 审核需改 md | `gates/review.md` 同上 |
| 任务模板前端硬编码 | `web/src/stores/template.ts` 把"标准研报"写死在数组里 |
| Agent 数量代码硬编码 | `orchestrator.py:38-60` **5 个**字典（`PROD_KEY`/`TOOL`/`GATE_NAME`/`GATE_REVIEWS`/`GATE_AFTER`）硬编码 3 个 role 名 |
| 输出格式只支持 md | `tools/doc_export.py` 头部明写「Markdown 导出」；`outputs/` 实测 24 个 .md + 12 个 .json，零 pptx/docx/pdf |

### 0.3 设计目标（Goals / Non-Goals）

**必须做到（Goals）**
- G1：模型映射（roles/gates/base/model）UI 可改 + 校验 + 落盘
- G2：Agent prompt（`agents/*.md`）UI 可编辑 + 预览 + 落盘
- G3：Gate 审核标准（`gates/review.md`）UI 可编辑 + 预览 + 落盘
- G4：任务模板（`templates/*/manifest.yaml`）UI 可增删改 + 落盘
- G5：Agent 数量扩展（orchestrator 字典外置为 yaml）UI 可新增 + 落盘 + 受控重启
- G6：输出格式（Markdown / PDF / PPT / Docx）UI 可选 + 后端有对应渲染器
- G7：所有改动可审计（who/when/what/diff/why）
- G8：所有改动经 edict-gate 三道闸（设计文档本轮先行落地；后续代码改动逐步接）

**M8 不做（Non-Goals）**
- N1：可视化拖拽 LangGraph 节点编辑器（Path B，估时 2 月，过度工程）
- N2：多用户 / RBAC / SSO（单用户先跑通，接口预留 token 扩展位）
- N3：模板市场 / 导入导出（M9+ 再议）
- N4：输出格式 WYSIWYG 设计器（M9+）
- N5：Agent 实时执行监控（沿用 TaskRun 页）

---

## 1. 架构原则

| 原则 | 含义 |
|---|---|
| **单一事实源** | 配置文件（yaml/md）仍是 SoT，UI 编辑 = 经后端 API 回写文件，不引入第二个事实源 |
| **热加载优先** | yaml/md 改完 → 后端监听变更 → 重读内存缓存，无需重启容器（除 G5 角色扩展外） |
| **校验前置** | UI 表单提交前 + 后端落盘前双重校验；任何写操作都经「dry-run 解析 → 序列化 → 校验 → 真正写」四步 |
| **审计可追溯** | 每次改动生成 `.audit/<时间戳>-<file>.{json,bak}`，含 before/after diff + 操作者 + 校验结果 |
| **降级路径** | 配置错误 → 加载失败 → 后端 500 + UI 错误提示 + 自动回滚到上次成功版本 |
| **接口预留** | 所有 API 路径与数据结构设计时考虑多用户扩展（M9+ 接入不破坏契约） |

---

## 2. UI 结构

```
/settings                              # 设置首页（4 模块入口卡片）
  /settings/models                     # 模型映射表（role/gate → base/model）
  /settings/agents                     # Agent 角色列表
    /settings/agents/[role]            # 单 Agent 编辑器（prompt + 预览）
  /settings/gates                      # Gate 审核列表
    /settings/gates/[gate]             # 单 Gate 编辑器
  /settings/templates                  # 任务模板列表
    /settings/templates/[name]         # 单模板编辑器
  /settings/templates/new              # 新增模板向导
  /settings/audit                      # 全局审计日志（只读浏览 + 回滚入口）
```

### 2.1 设置首页（/settings）
- 4 张入口卡片：Models / Agents / Gates / Templates
- 每卡显示：当前条目数、最近修改时间、校验状态（✅ 健康 / ⚠️ 有问题）
- 顶部「系统状态」条：所有配置最后校验时间、上次热加载时间、API 容器运行时间
- 右上角「查看审计日志」跳 /settings/audit

### 2.2 Models 页（/settings/models）
**视图**：表格，行 = 一条 role/gate 配置，列 = role/gate / base / model / temperature / max_tokens / 关联 reviews
**操作**
- 新增：弹窗表单（role 名唯一、base 必填、model 必填）
- 编辑：行内或弹窗
- 删除：**禁用**（必须保留至少一个 base 用于每个 role/gate，否则引擎崩）
**实时校验提示**
- Gate.base ≠ reviews 指向的 role.base（**硬约束**，违反高亮红框 + 保存按钮禁用）
- base 必须在 new-api 已注册模型白名单内（启动时拉一次缓存）
**落盘**：写回 `config/model_mapping.yaml`（原子化 + 备份 + 触发热加载）

### 2.3 Agents 页（/settings/agents）
**列表**：动态读取 `agents/*.md` 文件名（M8 初版仅 3 个：Researcher/Analyst/Writer；G5 落地后动态）
**单 Agent 详情页（/settings/agents/[role]）**
- 顶部元数据栏（role、关联 tool、关联 gate）—— 只读
- 中部：prompt 编辑器（**Monaco**，markdown 高亮，分屏预览，左编辑右渲染）
- 底部按钮：保存 / 取消 / 历史版本（点开抽屉读 `.audit/agents/`）
- 校验：md 必须可解析；建议性检查（包含「目标/工具/禁止」关键字——基于 gates/review.md 的审核结构）
**G5 范围内新增**：列表右上「新增 Agent」按钮 → 弹窗（role 名 / tool 名 / 关联 gate）→ 写 `agents/<role>.md` 模板 + **触发 api 容器受控重启一次**（orchestrator 字典加载新角色）

### 2.4 Gates 页（/settings/gates）
完全复用 Agents 页结构（80% 共用组件），对应 `gates/review.md`。

### 2.5 Templates 页（/settings/templates）
**列表**：当前所有模板（M8 初版 1 个：标准研报；可新增）
**单模板编辑器（/settings/templates/[name]）**
- 字段：name / description / agents（多选）/ gates（多选）/ output_format（多选 checkbox）/ ui_mode（unified_shell 或 custom_component）
- agents / gates 选项动态从 /settings/agents 和 /settings/gates 拉
- output_format 选项动态从后端渲染器列表拉（Markdown 始终存在；PDF/PPT/Docx 由 G6 提供）
**新增向导（/settings/templates/new）**：与编辑器同表单，预填默认值
**删除**：保留至少 1 个
**落盘**：写回 `templates/<name>/manifest.yaml`
**前端联动**：template.ts 改造为从 `/api/v1/templates` 动态拉取（**M7 时代前端硬编码彻底解除**）

### 2.6 全局审计页（/settings/audit）
- 时间倒序表格：ts / file / action / operator / before→after diff / 校验结果
- 每行右侧「回滚到此版本」按钮（先备份当前到 .audit 再回滚）
- 筛选器：file / action / 时间范围

---

## 3. 后端 API 表面（新增 `server/admin.py`）

### 3.1 模型映射
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/models` | 返回 yaml 解析后的 dict |
| PUT | `/api/v1/admin/models` | 整体替换（前端整表提交）→ 校验 → 写盘 → 热加载 |
| PATCH | `/api/v1/admin/models/{role_or_gate}` | 单条修改（前端行内编辑） |

### 3.2 Agent Prompt
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/agents` | 列出 `agents/*.md` |
| GET | `/api/v1/admin/agents/{role}` | 返回 md 内容（utf-8） |
| PUT | `/api/v1/admin/agents/{role}` | 校验 → 写盘 → 热加载（Agent prompt 当前每次 build_agent_system 实时读 md，无需重启） |
| GET | `/api/v1/admin/agents/{role}/history` | 历史版本列表 |
| POST | `/api/v1/admin/agents` | G5：新增 Agent（写 md 模板 + 触发 api 重启） |

### 3.3 Gate Review
完全同 Agent Prompt，路径前缀 `/api/v1/admin/gates/`

### 3.4 Templates
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/templates` | 列出 `templates/*/manifest.yaml` |
| GET | `/api/v1/admin/templates/{name}` | 返回解析后 manifest |
| POST | `/api/v1/admin/templates` | 新增（校验 agents/gates/output_format 合法性） |
| PUT | `/api/v1/admin/templates/{name}` | 编辑 |
| DELETE | `/api/v1/admin/templates/{name}` | 删除（保留至少 1 个） |

### 3.5 系统级
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/status` | 健康状态（最后校验时间 / 错误数 / 上次热加载时间） |
| POST | `/api/v1/admin/reload` | 强制重读所有配置（异常恢复） |
| GET | `/api/v1/admin/audit?file=X&since=Y` | 审计日志查询 |
| POST | `/api/v1/admin/rollback/{audit_id}` | 回滚到指定版本（先备份当前） |
| GET | `/api/v1/admin/output_renderers` | 列出后端已实现的输出渲染器（Markdown / PDF / PPT / Docx 等） |

### 3.6 公共约定
- 所有 `/api/v1/admin/*` 须 Header `X-Admin-Token: <ADMIN_TOKEN>`（环境变量注入）
- 错误码统一：`400`（校验失败）/ `401`（鉴权失败）/ `404`（资源不存在）/ `409`（冲突，如 name 重名）/ `500`（写盘失败）

---

## 4. 文件回写与热加载

### 4.1 文件回写机制
- 后端 `admin.py` 用 `watchfiles` 库监听配置文件变更
- **原子化写盘**：先写临时文件 `<file>.tmp` → `os.replace()` 重命名（避免半写状态）
- **写前备份**：每次写入前把当前文件复制到 `.audit/<时间戳>-<file>.bak`
- **写后校验**：dry-run 解析 + 序列化（确保可读回） → 通过才返回 200，失败自动恢复备份

### 4.2 热加载实现
| 配置 | 当前实现 | M8 改造 |
|---|---|---|
| 模型映射 | 启动时读 yaml + 缓存 | 后端启动 + 监听文件变更（`watchfiles`）→ 触发 orchestrator 重读缓存 |
| Agent prompt | `build_agent_system(role)` 每次实时读 md（**当前已是热加载**） | 不变 |
| Gate review | 同 Agent prompt | 不变 |
| Templates | **前端硬编码** | 后端 templates API + 前端 template.ts 改动态拉取 |
| Agent 数量 | orchestrator 字典硬编码 | 提取为 `config/agents_registry.yaml`，orchestrator 启动时读；新增 Agent 角色后需 api 容器**受控重启一次** |

### 4.3 G5 难点诚实标注
- 当前 `orchestrator.py:38-60` **5 个**字典硬编码 role 名：`PROD_KEY`（产出字段）/ `TOOL`（默认工具）/ `GATE_NAME`（后接闸）/ `GATE_REVIEWS`（闸审角色）/ `GATE_AFTER`（成功后进入的闸节点）
- M8 提取为 `config/agents_registry.yaml`（key=role，value=output_key/tool/gate/reviews）
- orchestrator.py 改读 yaml → 新增 Agent 角色后**只需重启 api 容器一次**（约 5–10s）
- 不做 LangGraph 节点的动态注册（每个新增 role 仍是固定节点，但节点的「干什么」来自 yaml）—— 这是工程权衡，避免引入图动态化复杂度

---

## 5. 校验规则

### 5.1 模型映射（最严格，硬卡）
- 每个 role 必须有 base + model
- 每个 gate 必须有 base + model + reviews
- **硬约束**：∀ gate ∈ gates，gate.base ≠ reviews(role).base（防自审包庇）
- model 必须在 new-api 已注册白名单内（启动时拉一次缓存到 `config/model_whitelist.json`）

### 5.2 Agent Prompt
- md 文件必须可解析（无 YAML frontmatter 错误，若有则校验）
- **建议性检查**（非强制）：包含「目标 / 工具 / 禁止」关键字中至少 2 个（参考 gates/review.md 的审核维度）

### 5.3 Gate Review
- 同 Agent Prompt

### 5.4 Templates
- name 唯一（小写 + `-` 命名规范）
- agents / gates 字段值必须存在于当前 registry
- output_format 必须在后端 renderer 列表内（Markdown 永远存在）
- 至少保留 1 个模板

### 5.5 校验失败统一处理
- 后端返回 400 + 详细错误（含字段路径 + 期望值 + 实际值）
- 前端表单对应字段红框 + 顶部错误条 + 保存按钮禁用
- 写盘流程严格「dry-run → 校验 → 真写」，任何一步失败原子回滚

---

## 6. 审计日志

### 6.1 文件结构
```
.audit/
  models/
    2026-09-07T18-30-00-001Z-update-by-boss.json
      {"ts": "2026-09-07T18:30:00.001Z", "operator": "boss",
       "action": "update", "file": "config/model_mapping.yaml",
       "before": {...}, "after": {...}, "diff": "...",
       "validation": "pass"}
    2026-09-07T18-30-00-001Z-update-by-boss.yaml.bak     # 改动前完整快照
  agents/
  gates/
  templates/
  system/
```

### 6.2 审计 API
- `GET /api/v1/admin/audit?file=X&since=Y` → 列出改动
- `POST /api/v1/admin/rollback/{audit_id}` → 回滚（先把当前存为新 audit，再恢复）

### 6.3 与 edict-gate 联动
- 当前：手动运行 edict-gate（设计/自审/独立审议）
- 未来（M9+ 评估）：每个 audit 项自动触发 git commit + 三道闸（**先把现有自治跑稳再说**，避免引入新故障源）

---

## 7. 鉴权（M8 单用户先行，接口预留）

### 7.1 M8 范围内
- 单用户：admin token 通过环境变量 `ADMIN_TOKEN=xxx` 注入
- 前端设置入口放侧边栏底部「设置」图标（M7 时代只放了「模板选择 / 提交任务 / 运行追踪」，现在加设置）
- 所有 `/api/v1/admin/*` 请求 header `X-Admin-Token`
- token 仅作能力校验（不进 audit，operator 字段固定写 `"single_admin"`）

### 7.2 M9+ 接口预留
- API 路径不变，多用户接入时换为 session-based auth
- audit.operator 字段已字符串化，可扩展为 user_id
- RBAC 模型在 M8 数据结构层面预留（不实现）

---

## 8. 输出格式扩展（G6）

### 8.1 当前状态
- `tools/doc_export.py` 只做 Markdown
- `output_format_spec` 是自由字符串（`server/api.py:52`），传 "ppt" 仅进 LLM prompt，落盘仍是 .md

### 8.2 M8 改造
- 新增 `tools/renderers/` 包（包不是文件），每个格式一个 renderer：
  - `renderers/markdown.py`（沿用现有 doc_export 逻辑）
  - `renderers/pdf.py`（用 `weasyprint` 或 `pdfkit`，**依赖决策见 §11 风险**）
  - `renderers/pptx.py`（用 `python-pptx`，模板驱动）
  - `renderers/docx.py`（用 `python-docx`）
- 注册表 `tools/renderers/__init__.py`：`RENDERERS = {"markdown": MarkdownRenderer(), "pdf": PdfRenderer(), "pptx": PptxRenderer(), "docx": DocxRenderer()}`
- 后端新增 `GET /api/v1/admin/output_renderers` 返回已实现列表
- 前端 Templates 编辑器的 output_format 选项动态来自这个 API

### 8.3 渲染时机
- LLM 落盘仍按"按格式写文本"（Markdown 仍写 md）
- 落盘后由 renderer 转换：md → pdf / md → pptx / md → docx
- outputs/ 目录文件结构：`outputs/<task_id>/<artifact>.md` + `outputs/<task_id>/exports/<artifact>.pdf` 等

### 8.4 依赖决策（须评审）
| 方案 | 优势 | 劣势 |
|---|---|---|
| `weasyprint`（md→pdf） | 纯 Python，样式可 CSS | Windows 需装 GTK（依赖重） |
| `pdfkit` + wkhtmltopdf | 简单 | wkhtmltopdf 已停止维护 |
| `pandoc`（md→docx/pdf） | 学术标准，质量高 | 需系统装 pandoc 二进制 |
| `python-pptx`（md→pptx） | 纯 Python，无系统依赖 | 模板需手写 |

**M8 推荐**：Markdown（已有）+ PDF（pandoc）+ PPTX（python-pptx，结构化模板）+ Docx（pandoc）。理由：pandoc 是文档转换的事实标准，引入一个二进制依赖换 3 种格式支持，工程性价比最高。

---

## 9. 实现里程碑（M8 拆 5 步）

### M8-1：admin API 骨架 + Models 页（最小可走通闭环）
- 后端 `server/admin.py` 骨架（FastAPI Router + Token 校验中间件）
- GET/PUT model_mapping.yaml（用 watchfiles 监听变更 + 触发热加载）
- 前端 `/settings/models` 表格 + 行内编辑 + 实时校验
- **验收**：UI 改 Researcher's model → 实时生效 → 跑 e2e 确认切换到新模型 → edict-gate 三道闸全 PASS → commit

### M8-2：Agents 页 + prompt 编辑器
- 后端 GET/PUT agents/*.md（沿用 §4 写盘流程）
- 前端 `/settings/agents/[role]` 编辑器（Monaco + 分屏预览 + 历史版本抽屉）
- **验收**：UI 改 researcher.md → 新任务按新 prompt 执行 → e2e done → 三道闸 PASS → commit

### M8-3：Gates 页（复用 M8-2 80% 组件）
- 后端 GET/PUT gates/review.md
- 前端 `/settings/gates/[gate]` 编辑器
- **验收**：UI 严化 Gate 审核 → 跑任务验证 reject 行为 → 三道闸 PASS → commit

### M8-4：Templates 页 + 输出格式扩展（G4 + G6）
- 后端 `tools/renderers/` 包（md/pptx 先行；pdf/docx 评估）
- 后端 templates API（GET/POST/PUT/DELETE）
- 前端 `/settings/templates` + `/settings/templates/[name]` 编辑器
- 前端 `template.ts` 改动态拉取（**解除硬编码**）
- **验收**：UI 新增"竞品快评"模板 → 提交任务按新模板跑通 → 输出 pptx 真实下载 → 三道闸 PASS → commit

### M8-5：Agent 数量扩展（G5）
- `config/agents_registry.yaml` 提取（key=role，value=output_key/tool/gate/reviews）
- `orchestrator.py` 改读 yaml（字典外置）
- 前端 /settings/agents 新增 Agent 表单
- **验收**：UI 新增"Coder"角色（绑定 doc_export tool + GateD gate）→ api 容器受控重启一次 → 提交任务按新角色跑通 → 三道闸 PASS → commit

---

## 10. edict-gate 集成

### 10.1 本设计文档
- 本文档完成后：① 校验关（语法/MD linting）→ ② 自审关（写 `VERIFICATION.md §8` 含「本设计是否覆盖 6 类、是否诚实标注 G5 难点、是否破坏既有契约」）→ ③ 独立审议关（独立子代理审）→ 三道闸全 PASS 才进 commit

### 10.2 M8 后续每个子里程碑（M8-1～M8-5）
- 每个子里程碑完成后单独走三道闸（不一次性混审）
- 独立审议 reviewer 验证：实测 UI 操作 + e2e 跑通 + 落盘原子化 + 审计日志完整 + 热加载或受控重启到位

### 10.3 范围扩大原则
- 若 M8-1 ~ M8-5 任一步骤发现设计缺漏（如新增 G6 渲染器失败、orchestrator 改读 yaml 引入回归），**回退到本设计文档修订**，不绕过设计直接补代码

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| UI 编辑错配置导致引擎崩 | 校验前置（前端 + 后端）+ dry-run 解析 + 写盘前备份 |
| yaml/md 写入半中断 | 原子化（临时文件 + os.replace） |
| 热加载引入回归（orchestrator 字典改读 yaml） | M8-1 改造前后 e2e 真实回归；保留原字典硬编码路径作为 fallback |
| G5 新增 Agent 角色需重启 | 受控重启（docker compose restart api）≤ 10s，明确告知用户 |
| 多用户同时编辑冲突 | M8 单用户不考虑；M9 引入文件锁 + 乐观锁 |
| 输出渲染器（pandoc 等）引入系统依赖 | 容器内 apt-get 装；首次启动检测 + 友好报错 |
| audit 无限增长 | 按月份归档 `.audit/2026-09/` + 保留策略（M9+ 评估） |
| UI 编辑误触发真实 e2e 任务 | Models/Agents/Gates 编辑**不**触发自动 e2e；Templates 新增/删除提示「需手动提交任务验证」 |

---

## 12. 范围外（明确不做，存档备查）

- 拖拽 LangGraph 可视化编辑器（Path B）
- 模板市场 / 模板导入导出
- 多用户 / RBAC / SSO
- 输出格式 WYSIWYG 设计器
- Agent 实时执行监控（沿用 TaskRun 页）
- audit 自动触发 edict-gate（先手动跑稳）
- Agent prompt 版本对比（git diff 风格）

---

## 13. 与既有契约的兼容性

### 13.1 API 契约（API_SPEC.md）
- `/api/v1/admin/*` 是新增路径，不与既有 `/api/v1/tasks` `/api/v1/templates` 等冲突
- `GET /api/v1/templates`（前端模板选择页用）当前不存在；M8-4 改为后端返回（前端改造为动态拉取）
- WS 事件不变（admin 操作无 WS 推送需要）

### 13.2 前端类型契约
- AgentRole 联合类型 `'Researcher' | 'Analyst' | 'Writer'` 在 M8-5 后**动态扩展**
- 方案：AgentRole 改为 `string`（去除联合），保留运行时校验；或保留联合 + 由后端生成 .d.ts 注入（更严谨但工程重）
- **M8 倾向方案 1（string）**——理由：联合类型的价值在编译期检查，但 M8 后角色是动态的，编译期检查无意义；运行时校验（admin.py + 前端表单）已足够

### 13.3 既有前端硬编码解除
- `web/src/stores/template.ts` 的硬编码数组 → 改为从 `/api/v1/templates` 拉
- 侧边栏「模板选择 / 提交任务 / 运行追踪」→ 加「设置」入口（M8-1 后可见，但内容逐步解锁）

---

## 14. 评审修订记录

| 轮次 | 日期 | 状态 | 评审者 | 主要修订 |
|---|---|---|---|---|
| 草案 | 2026-09-07 | 待评审 | （主代理） | 初版 |
| 第 1 轮 | 待定 | 待自审 | 主代理 | VERIFICATION.md §8 |
| 第 1 轮 | 待定 | 待独立审议 | 独立子代理 | REVIEW.md 第 10 轮 |