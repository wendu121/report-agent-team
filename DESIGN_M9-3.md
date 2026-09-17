# DESIGN_M9-3 · 研报技能库（Research Skills Market）

> 关联：[DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.3（数据源插件市场的姊妹功能）
> 里程碑：M9 扩展层第 3 站（M9-1 智能体市场 ✅ / M9-2 数据源插件 ✅ / **M9-3 研报技能库** / M9-4 推送渠道 / M9-5 对话入口）
> 门禁：edict-gate 三道闸（① py_compile ② VERIFICATION_M9-3 自审 ③ REVIEW_M9-3 独立审议），每里程碑单独跑
> 纪律：先出设计文档不写代码；UI 必须是 controller 非 viewer；禁过度工程；本地 commit 完成 ≠ push

---

## 0. 范围裁定（与 boss 立约一致）

### 0.1 领域替换（抄架构、换内容）
`DESIGN_PLATFORM_FUNCTIONS.md` §3.3 已明确：Accio 的电商技能库（店铺诊断/选品/营销话术）对研报无意义，必须替换为**研报分析框架技能**：
- ✅ 财务分析（三表 / 杜邦分解 / 现金流质量）
- ✅ SWOT 分析
- ✅ 竞品对比
- ✅ 数据图表生成（终稿图表规格）
- ✅ 引用格式规范
直接照搬电商技能 = 方向性错误（与 M9-2 同源约束）。

### 0.2 真·控制器效应（核心，不是装饰卡片）
技能不是"市场里好看的卡片"。启用/禁用一个技能 **真实改变引擎拼给对应 Agent 的 prompt**：
- 启用 `swot` → Analyst 的 system prompt 被拼入 SWOT 框架片段 → 其分析结论被要求按 S/W/O/T 结构组织；
- 禁用 `swot` → 该片段**不再出现**于任何 Agent prompt → 报告结构随之变化。
这是配置驱动系统行为的真控制器（与 M9-2 数据源"启用→检索输入变"同源逻辑），可被离线实证（见 §9 E2/E6）。

### 0.3 不做清单（防过度工程）
- ❌ **per-task 技能选择 UI**（任务提交时勾选技能）：保持与 M9-2 数据源一致的**全局启用**模型（enabled 在 `skills.yaml`，影响所有任务），不引入任务 schema 改动。
- ❌ **Monaco 编辑器片段插入**（§3.3 提及的"在 Agent prompt 编辑器中插入"）：属 M8-2 编辑器增强，超出 M9-3，留待后续。
- ❌ **技能↔数据源硬依赖校验**（如"财务分析技能要求 financial 源就绪"）：技能是 prompt 软增强，不引入跨模块机器校验。
- ❌ RBAC / 技能版本管理 / 技能市场评分。
- ❌ 输出格式渲染（pptx/docx/pdf，已在 M8 TD-006 推后，非 M9-3 范围）。

---

## 1. 现状实证（代码事实，非印象）

### 1.1 Agent prompt 组装（orchestrator.py）
- `build_agent_system(role, reg)`（:331）：读 `agents/{role}.md` + 追加输出格式硬约束，返回 system prompt。
- `make_agent` 工厂（:623）内 `node(state)`：`:642 system = build_agent_system(role, reg)`；`:708 user = build_agent_user(...) + extra_context`（extra_context 按 shape 注入上游 state：researcher←search_results / analyst←retrieval_records / writer←analysis_conclusions）。
- **注入点**：在 `:642` 之后、LLM 调用（:711）之前，对 `role ∈ target_roles` 的启用技能，把 `skills/<id>.md` 片段拼入 `system`。热加载：`make_agent` 每次 `run_report` 重建（同 `load_agent_registry` 热加载），故 `build_skill_context` 内 `load_skills()` 每次重读磁盘即可零重启生效。

### 1.2 配置 bind mount 现状（docker-compose.yml api volumes）
已挂载：`./config`、`./.audit`、`./agents`、`./gates`、`./templates`、`./.secrets`。
**M9-3 须新增**：`./skills:/app/skills`（技能 md 片段与 agents/gates 同源，须 bind mount 否则 UI/Admin 写的自定义技能一重建容器即丢——M8 TD-008 同源教训）。

### 1.3 admin 复合写范式（server/admin.py，已有可复用）
`_load_plugins` / `_load_plugins_rt`（ruamel round-trip 保注释）/ `_atomic_write` / `_write_audit` / 失败回滚 均已在 M9-2 `plugins` 端点验证。M9-3 `skills` 端点**镜像**该范式，差异：技能复合写 = `skills.yaml` + `skills/<id>.md`（与 M9-1 agents-library 写 agent md + gate md + mapping 同源），无密钥管理（技能不需要 secrets）。

### 1.4 前端市场范式（web/src/views/Plugins.vue）
M9-2 已建 `/plugins` 市场页（市场/已安装 tabs + 卡片状态 + 新增向导 + 连接弹窗 + 侧边栏入口 + 路由）。M9-3 `Skills.vue` **镜像**其结构，去掉"连接密钥"弹窗（技能无密钥），保留启用/停用/删除/新增。

---

## 2. Goals / Non-Goals

### 2.1 Goals
- G1：技能 SoT = `config/skills.yaml` + `skills/<id>.md`，内置 5 个研报技能（见 §0.1）。
- G2：**真·控制器**——启用/禁用技能真实改变对应 Agent 的 system prompt（按 `target_roles` 作用域），离线可实证。
- G3：admin CRUD（`/admin/skills` 复合写 yaml+md，ruamel+原子+审计+回滚）+ 公开 `GET /skills`。
- G4：前端 `/skills` 市场（启用/停用/删除/新增），侧边栏入口。
- G5：热加载（零重启）、向后兼容（无 skills.yaml → 无注入）、诚实边界（见 §10）。

### 2.2 Non-Goals（= §0.3 不做清单）
N1 per-task 技能选择 / N2 Monaco 插入 / N3 技能↔数据源硬依赖 / N4 RBAC/版本/评分 / N5 输出格式渲染。

---

## 3. 架构

### 3.1 技能注册表 SoT（`config/skills.yaml`）
```yaml
# 研报技能库 SoT（M9-3）。status 由引擎按 enabled+片段存在实况重算，此处初值仅展示默认。
skills:
  - id: financial_analysis
    name: 财务分析
    icon: /skills/financial_analysis.svg
    description: 三表分析、杜邦分解、现金流质量评估
    category: 财务与估值
    target_roles: [analyst]          # 仅注入 Analyst（真控制器作用域）
    prompt_file: skills/financial_analysis.md
    enabled: true
    installed: true
    builtin: true
    status: active                    # active | inactive（无 enabled 且无片段时为 inactive）
  - id: swot
    name: SWOT 分析
    icon: /skills/swot.svg
    description: 优势/劣势/机会/威胁结构化研判
    category: 战略研判
    target_roles: [analyst]
    prompt_file: skills/swot.md
    enabled: true
    installed: true
    builtin: true
    status: active
  - id: competitor_compare
    name: 竞品对比
    icon: /skills/competitor_compare.svg
    description: 多维竞品对标与差距分析
    category: 行业研究
    target_roles: [analyst]
    prompt_file: skills/competitor_compare.md
    enabled: true
    installed: true
    builtin: true
    status: active
  - id: chart_generation
    name: 数据图表生成
    icon: /skills/chart_generation.svg
    description: 终稿图表规格（mermaid/表格），提升可读性
    category: 表达增强
    target_roles: [writer]
    prompt_file: skills/chart_generation.md
    enabled: true
    installed: true
    builtin: true
    status: active
  - id: citation_format
    name: 引用格式规范
    icon: /skills/citation_format.svg
    description: 统一引用标注与来源可追溯
    category: 表达增强
    target_roles: [writer]
    prompt_file: skills/citation_format.md
    enabled: true
    installed: true
    builtin: true
    status: active
```

### 3.2 技能抽象层（`tools/skills.py`，新建）
```python
class SkillError(Exception): ...

def load_skills() -> List[Dict[str, Any]]:
    """读 config/skills.yaml（每次调用现读磁盘 → 热加载，零重启）。
    文件缺失返回 []（向后兼容）。"""

def _recompute_status(spec: dict) -> str:
    """enabled 且有 prompt_file 且文件存在 → active，否则 inactive。"""

def build_skill_context(role: str, skills: Optional[List[dict]] = None) -> str:
    """返回应注入 role 的 system prompt 片段拼接串（可能为空）。
    过滤规则（顺序）：
      1. spec.enabled == True
      2. spec.installed == True（或 builtin）
      3. role ∈ spec.target_roles
      4. prompt_file 存在且非空（否则跳过该技能，防御不崩溃）
    每个命中技能：读 skills/<id>.md，前后加分隔标记：
      '\n【已启用技能：<name>】\n' + fragment + '\n【技能结束】\n'
    多命中按 yaml 顺序拼接。"""
```

- 片段加载复用 `_load_md` 同款机制（`BASE / prompt_file`）；若 `prompt_file` 路径不含 `.md` 则在末尾补（与 agents 一致）。
- `build_skill_context` 默认 `skills=None` 时内部调 `load_skills()`，确保调用点极简、热加载同频。

### 3.3 orchestrator.py 注入适配（make_agent 节点）
在 `:642 system = build_agent_system(role, reg)` 之后追加（仅一行调用 + 拼接）：
```python
from tools.skills import build_skill_context
skill_ctx = build_skill_context(role)
if skill_ctx:
    system = system + "\n\n" + skill_ctx
```
- 不改动 `build_agent_system` 签名（保持 gates/其他调用点不变），仅在节点内拼装，改动面最小。
- `role` 来自 `make_agent(role, ...)` 闭包，天然携带当前 Agent 角色，作用域判定即用 `target_roles`。

### 3.4 诚实边界落地（prompt 软增强，不谎称机器强制）
- 技能片段是**分析框架提示**，与 Agent 自身 `agents/<role>.md` 同性质——LLM 是否遵循取决于 prompt，引擎**不机器强制**报告必含某结构。
- 引擎可实证的是**接线真实**：启用技能 → 片段确实拼入对应 role 的 system prompt（见 §9 E2/E6）；禁用 → 不拼。这是诚实的控制器边界，不在 VERIFICATION 声称"技能保证报告结构"。
- 片段缺失 → `build_skill_context` 跳过（不 raise、不崩溃），admin 启用前校验 `prompt_file` 存在（§4.3）。

---

## 4. admin API（`server/admin.py`，镜像 M9-2 plugins 范式）

### 4.1 常量
```python
SKILLS_PATH = CONFIG_DIR / "skills.yaml"
SKILLS_AUDIT_DIR = BASE / ".audit" / "skills"
SKILLS_DIR = BASE / "skills"          # md 片段目录（bind mount）
SKILL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
VALID_ROLES = ["researcher", "analyst", "writer"]
```

### 4.2 读
- `GET /skills`（公开，`@public_router`）：返回 `items`（含 id/name/category/description/icon/enabled/installed/builtin/target_roles/status）+ `meta`（registry 路径、hot_reload 说明）。
- `GET /admin/skills`（需 admin token）：同上 + `valid_roles`。

### 4.3 复合写（POST / PUT / DELETE）
- **POST `/admin/skills`**（新增自定义技能）：校验 `id` 合法 + 不存在 + `target_roles` ⊆ VALID_ROLES；**复合写** = `skills.yaml`（ruamel round-trip 追加 entry，`enabled=True, builtin=False`——实现默认启用，因新增即写非空片段且启用守卫保证非空，新增即生效；与 M9-1 agents-library 默认 `enabled=False` 需显式启用的差异见下）**+** `skills/<id>.md`（写入 body.prompt 片段内容，空则拒绝并回滚）；失败回滚 yaml + 删 md（若已写）+ 审计 `create_rejected`/`create_rollback`。
  > 实现决策（实施审议 NOTE-S1 对齐）：M9-1 agents-library 新增 Agent 默认 `enabled=False`（需显式启用），但 M9-3 技能新增向导**必填片段**（空则 400 拒绝），故默认 `enabled=True` 使「新增即生效」更符合 UX，且启用守卫（md 非空）已兜底。
- **PUT `/admin/skills/{sid}`**（控制器入口）：`enabled` 启停 / `status` / `description` / `category` / `name` / `icon` / `target_roles`。**启用守卫**：`enabled=True` 且 `prompt_file` 对应 md 不存在 → 400 拒绝（防启用空技能，保证控制器真有效）。`builtin` 技能允许改 `enabled`/`target_roles`/`description`，不允许改 `prompt_file` 指向（防破坏内置）。
- **DELETE `/admin/skills/{sid}`**：复合删 = `skills.yaml` 移除 entry **+** 删 `skills/<id>.md`（仅自定义，builtin 拒删或软禁用？设计裁定：builtin 技能**不允许物理删除**，仅允许 `enabled=False` 停用；自定义才允许 DELETE。与 M9-1 builtin Agent 不可删同源）。失败回滚 + 审计。
- 全部走 `_atomic_write` + `_write_audit(subdir="skills", ...)` + 失败全回滚（复用 M9-2 范式）。

### 4.4 审计
`.audit/skills/` 下写 create/update/delete/enable/disable/rejected/rollback 事件（operator + src_path + item 快照），与 M9-1/M9-2 同源。

---

## 5. 前端（web/src/views/Skills.vue，镜像 Plugins.vue）

- 路由 `/skills`（`router/index.ts` 加 `SkillsMarket`），侧边栏「技能库」入口（`layouts/DefaultLayout.vue`，图标复用现有）。
- 页面：市场/已安装 tabs + 搜索 + 分类筛选（财务与估值/战略研判/行业研究/表达增强）+ 卡片（图标+名称+描述+状态标签+启用/停用/删除）+ 新增向导（填 id/name/category/target_roles + 片段文本框）。
- 调用：`GET /skills`（公开列表）+ `GET/POST/PUT/DELETE /admin/skills`（带 `X-Admin-Token`）。
- 删除自定义技能走二次确认；builtin 技能不显示删除按钮（仅启用/停用）。
- 视觉沿用 PLATFORM_FUNCTIONS §6 色板（白底大留白 + 绿 `#10B981` 强调），与 M9-1/M9-2 一致。

---

## 6. 数据流（启用 → 引擎）

```
UI 启用/禁用技能 (PUT /admin/skills/{id})
   └─ admin 写 skills.yaml (ruamel round-trip + 原子写 + 审计)
        └─ 下一个 run_report：
             make_agent 节点 → build_skill_context(role)
               ├ 读 skills.yaml（热加载，零重启）
               ├ 过滤 enabled + installed + role∈target_roles + 片段存在
               └ 拼入 system prompt（标记【已启用技能：<name>】...【技能结束】）
                    └─ llm.complete(system=含技能片段, ...) → Agent 按框架产出
```
禁用某技能 → `build_skill_context` 过滤掉 → system 不含该片段 → 报告结构随之变化（控制器真生效）。

---

## 7. 校验（门禁①）

- `python -m py_compile tools/skills.py tools/__init__.py orchestrator.py server/admin.py` → COMPILE_OK。
- 前端 `npm run build`（或 vite 类型检查）通过（与 M9-2 同标准）。

---

## 8. 热加载 / 原子写 / 审计（复用硬约束）

- 热加载：`build_skill_context` 内 `load_skills()` 每次 `run_report` 重读 `skills.yaml` → **零重启**生效（与 M9-2 `build_tools` 同频，无需 watchfiles）。
- 原子写：admin 复合写复用 `_atomic_write`（M8 确立），失败全回滚。
- 注释保留：`skills.yaml` 编辑走 ruamel round-trip（缺失 ruamel 则 fail loud 500，不降级 PyYAML——M8 硬约束）。
- 审计：`.audit/skills/`。
- bind mount：`./skills:/app/skills`（M8 TD-008 同源，防重建即丢）。

---

## 9. 验证计划（E1-E7，离线可实证，不依赖 new-api 配额）

验证脚本 `m9-3_verify.py`（仓库外 `C:\Users\sfkj\.workbuddy\`，可重跑）+ `m9-3_e2e.py`：

- **E1** 默认内置技能启用 → `build_skill_context("analyst")` 含 `financial_analysis`/`swot`/`competitor_compare` 片段文本（标记【已启用技能：...】）。
- **E2（控制器核心）** 禁用 `swot`（改 skills.yaml enabled=False）→ `build_skill_context("analyst")` **不再含** swot 片段；重新启用 → 重现。证明启停真改引擎 prompt。
- **E3** 自定义技能（POST /admin/skills + 写 md）→ `load_skills()` 接纳且 `build_skill_context("writer")` 含该片段。
- **E4（作用域）** `chart_generation` target_roles=[writer] → `build_skill_context("analyst")` **不含**，`build_skill_context("writer")` 含。证明 target_roles 真作用域。
- **E5（向后兼容）** 临时移走 `skills.yaml` → `load_skills()` 返回 [] → `build_skill_context` 返回 "" → 现有行为不变。
- **E6（e2e 真到达）** monkeypatch `StubLLMClient.complete` 捕获 `system`；`run_report` 跑通；断言 `analyst` 节点捕获的 `system` 含启用技能片段、`writer` 节点含 chart_generation；禁用 swot 后 analyst 节点 system 不再含 swot。
- **E7（多技能聚合）** 多个 analyst 技能同时启用 → `build_skill_context("analyst")` 按 yaml 顺序依次含全部片段。

---

## 10. 诚实边界（boss 立约，不掩饰）

- **R1 软增强不谎称强制**：技能是 prompt 框架提示，引擎实证"接线真实"（片段拼入目标 agent prompt），但**不声称**机器保证报告必含某结构；LLM 遵循度同 Agent 自身 prompt，不可控。
- **R2 无密钥不引 secrets**：技能不需要外部密钥，不引入 `.secrets` 管理（区别于 M9-2 数据源），简化且诚实。
- **R3 空技能防御**：片段缺失 → 引擎跳过（不崩溃、不冒充可用）；admin 启用前校验 `prompt_file` 存在。
- **R4 向后兼容**：无 `skills.yaml` → 无注入，现有行为不变。

---

## 11. 风险与不做清单
- 风险：技能效果依赖 LLM 遵循 prompt，stub 离线无法验证"报告质量变好"，只能验证"接线变"。已在 R1 诚实标注。
- 不做：见 §0.3 / §2.2。

---

## 12. 提交纪律
- 三道闸全 PASS 才 `git commit --only report-agent-team`（父仓含 wiki 等无关项，不卷入）。
- **无 boss 显式「推送」指令不 `git push`**。
- 交付文件：DESIGN_M9-3.md / VERIFICATION_M9-3.md / REVIEW_M9-3.md / config/skills.yaml / skills/*.md(5) / tools/skills.py / tools/__init__.py(适配) / orchestrator.py(注入) / server/admin.py(skills 端点) / web/src/views/Skills.vue + router + layout / docker-compose.yml(.skills mount)。
- 同步更新 `DESIGN_PLATFORM_FUNCTIONS.md` §3.3「已交付」交叉引用（随本提交入库）。
