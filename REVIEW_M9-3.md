<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 10:12 GMT+8 · 独立审议第 20 轮（设计阶段） -->

## M9-3 设计独立审议（第 20 轮）

> 审议对象：`DESIGN_M9-3.md`（278 行，设计契约）
> 交叉参考：`VERIFICATION_M9-3.md` §17（主代理自审）、`DESIGN_PLATFORM_FUNCTIONS.md` §3.3
> 立场：独立子代理，未与主代理串通；主代理严禁自签 `reviewed-by`。
> 方法：逐项 Read 真实代码印证设计 §1 声称，而非只听主代理自审。

---

### 核查过程与逐项结论（A–F）

#### A. 领域替换正确性 ✅ PASS
- 设计 §0.1 / §3.1 五大内置技能 = 财务分析 / SWOT / 竞品对比 / 数据图表 / 引用格式，全部为研报分析框架，**无任何电商技能残留**（无店铺诊断 / 选品 / 营销话术）。
- 与 `DESIGN_PLATFORM_FUNCTIONS.md` §3.3「抄架构换内容」约束一致；且 §0.1 显式声明「照搬电商技能 = 方向性错误」，立场正确。
- 与 M9-2 同源逻辑（数据源换 Tavily/arXiv/雪球…）对称，领域替换到位。
- 小注（非缺陷）：上游 §3.3 草稿分组含「风险识别」，M9-3 §3.1 未单列风险识别技能、并入 SWOT/竞品，属合理收敛，不视为问题。

#### B. 真·控制器效应 ✅ PASS（代码实证成立）
- 注入点实测（`orchestrator.py`）：
  - `:331` `build_agent_system(role, reg)` 定义；`:642` `system = build_agent_system(role, reg)`；`:708` `user = build_agent_user(...) + extra_context`；`:712` `llm.complete(model, system, user, ...)`（try 在 :711）。
  - 设计 §3.3 拟在 `:642` 之后、`:712` 之前追加 `system = system + skill_ctx`，**位置真实有效**，且未改动 `build_agent_system` 签名（gates/其他调用点不受影响）。
- `make_agent` 由 `build_graph`（`:1056`）在每次 `run_report` 经 `:1068` 重建；节点 `node()` 每次执行都会调用 `build_skill_context(role)`，其内部 `load_skills()` 每次重读 `skills.yaml` → **热加载真成立**（零重启，与 `build_tools` 同频，实测 `tools/__init__.py:51` 即每次 `read_text`）。
- 启用/禁用直接改写 `skills.yaml` → 下一 `run_report` 的 `build_skill_context` 过滤结果变化 → 拼入的 `system` 片段随之增减。E2/E4 用 `StubLLMClient`（实测 `orchestrator.py:96` 存在、tests 已用）离线捕获 `system` 即可证，**不依赖 new-api 配额，可信**。
- 注入语义：技能片段追加在「输出格式硬约束」之后的 `system` 末尾，不破坏 JSON 硬约束，仅是框架提示（与 `agents/<role>.md` 同性质）——与 R1 诚实边界自洽。

#### C. 诚实边界 R1–R4 ✅ PASS
- R1：设计多处（§3.4 / §10 / VERIFICATION §17.3）只声称「接线真实」（片段拼入对应 role 的 system），**不声称**引擎机器强制报告必含某结构；LLM 遵循度不可控已在 §11 风险诚实标注。
- R2：技能无密钥，`server/admin.py` 技能端点设计明确不引 `.secrets`（区别于 M9-2 数据源），简化且诚实。
- R3：空技能防御双保险——`build_skill_context` 遇 `prompt_file` 缺失/空则跳过（不 raise、不崩溃）；admin 启用守卫 `enabled=True` 且 md 不存在 → 400 拒绝。
- R4：向后兼容——`load_skills()` 文件缺失返回 `[]` → `build_skill_context` 返回 `""` → 现有行为不变。

#### D. 与 M9-1/M9-2 范式一致 ✅ PASS
- 复合写 + 原子写 + 审计：实测 `server/admin.py` 存在 `_atomic_write`（:166）、`_write_audit`（:172）、`_load_plugins_rt`（:1373，ruamel round-trip）；`PLUGINS_PATH`（:1355）及 plugins 端点已实测可用。M9-3 `skills` 端点**镜像**该范式（差异仅无密钥管理），可复用，未重复造轮子。
- builtin 不可物理删除（仅 `enabled=False`）与 M9-1 builtin Agent 不可删同源；失败回滚 + 审计与 M9-1/M9-2 同源。
- bind mount 新增 `./skills:/app/skills` 复用 M8 TD-008 同源教训（防重建即丢）。
- 注意事项见下方 NOTE-1（bind mount 与内置 md 文件 seeding 必须与 agents/gates/config 同处理）。

#### E. 不过度工程 ✅ PASS
- §0.3 / §2.2 排除项（N1–N5）明确：per-task 技能选择、Monaco 插入、技能↔数据源硬依赖、RBAC/版本/评分、输出格式渲染，均在 Non-Goals 中排除，设计正文未出现膨胀实现。
- 技能是 prompt 软增强而非可执行工具/检索源，未引入跨模块机器校验，改动面控制在 `make_agent` 节点内一行调用，符合「最小改动」纪律。

#### F. 代码实证可信度 ✅ PASS（主代理未凭印象编造）
- §1.1 注入点 `:642`/`:708`/`:711-712`、make_agent 每次 run_report 重建 —— **独立 Read 印证属实**。
- §1.2 bind mount 现状：实测 `docker-compose.yml` api volumes 已挂 `./config`、`./.audit`、`./agents`、`./gates`、`./templates`、`./.secrets`，**确未挂 ./skills** → 新增 `./skills:/app/skills` 成立。
- §1.3 admin 范式：`_atomic_write`/`_write_audit`/`_load_plugins_rt` 均实测存在。
- §1.4 前端：`web/src/views/Plugins.vue` 实测存在（市场/已安装 tabs + 搜索 + 分类筛选 + 卡片启用/停用/连接 + `router/index.ts:21` `/plugins` 路由），M9-3 `Skills.vue` 镜像成立。
- `StubLLMClient`（`orchestrator.py:96`）实测存在 → E6 e2e 离线可行。
- `parse_ok`：现有 `orchestrator.py`/`server/admin.py` 可解析（交叉验证无语法破坏风险）。

---

### 发现的问题

**MAJOR：NONE**（设计无阻塞级缺陷，核心控制器机制扎根真实引擎代码，可编码）

**MINOR / NOTES（须分级处理）：**

- **NOTE-1（编码前必须落实，否则控制器在 Docker 下静默失效）**：
  设计 §1.2 仅写「须新增 `./skills:/app/skills`」，但未显式点明 **bind mount 会遮罩镜像内 `/app/skills`**，故宿主 `./skills` 必须携带 5 个内置 md 文件、且 `config/skills.yaml` 必须存在，否则内置技能 `status` 全 `inactive`、片段永不注入——控制器静默失效（与 M8 的 agents/gates/config 同源陷阱）。
  落实清单（编码阶段必做、E2/E6 必验）：① `skills/*.md`(5) 与 `config/skills.yaml` 须**入库**（实测 `.gitignore` 未忽略 `skills/` 与 `config/`，可入库）；② Dockerfile `COPY . .` 已覆盖，但 bind mount 下以宿主为准，须确保交付/部署时宿主 `./skills` 非空；③ 在交付清单与部署说明显式标注，避免他人 `git clone` 后空目录挂载。此条非设计重做，但属「必须编码前确认」的硬约束延伸。

- **NOTE-2（文字一致性，编码顺带修）**：`DESIGN_PLATFORM_FUNCTIONS.md` §3.3 草稿用字段名 `prompt_template`，M9-3 §3.1 用 `prompt_file`。M9-3 §12 要求回写上游「已交付」交叉引用时，须将上游字段名统一为 `prompt_file`，避免两文档字段漂移。

- **NOTE-3（验证计划细化，非阻塞）**：E6 用 `StubLLMClient.complete` 捕获 `system`——实测 `StubLLMClient`（:96）存在且 tests 已用，可行；但 `m9-3_e2e.py` 需按 role 分别捕获各 Agent 节点的 `system`（analyst/writer 不同），设计 §9 E6 描述已含此意，实现时按 role 分桶断言即可。

- **NOTE-4（生命周期，已知取舍，非缺陷）**：builtin 技能 md 经 bind mount 落宿主且被入库，admin 改 builtin 的 `description`/`category`/`target_roles` 会改动入库文件——与 M8-2 agents/*.md 同源取舍，可接受，但建议在审计/交付说明中点明（与 M9-1/M9-2 builtin 编辑同源）。

- **NOTE-5（安全正向确认）**：`GET /skills` 公开路由仅返回元数据（id/name/category/description/icon/enabled/.../status），**不泄露 prompt 片段文本**，片段只进引擎 `system`。设计已隐含正确，记一笔正向确认。

---

### 最终结论

**PASS_WITH_NOTES**

设计扎根真实引擎代码（注入点 `:642`→`:712` 实测成立、热加载经 `make_agent` 每次 `run_report` 重建 + `load_skills` 每次重读成立）、领域替换正确无电商残留、真·控制器效应可离线实证、诚实边界 R1–R4 自洽、与 M9-1/M9-2 范式一致未过度工程、代码实证未被主代理编造。

**须在编码前/编码中落实的 NOTES：**
- **NOTE-1（关键）**：`skills/*.md`(5) + `config/skills.yaml` 必须入库且宿主 `./skills` 非空，否则 Docker 下控制器静默失效——E2/E6 必须验证内置技能真注入。
- NOTE-2：回写上游时统一字段名为 `prompt_file`。
- NOTE-3 / NOTE-4：验证脚本按 role 分桶、builtin md 生命周期说明。

**可后续跟进、不阻塞设计：** 无（NOTE-1 已列为编码前必做，非返修设计）。

**无需返修设计文档**——不建议打回重做，按 NOTES 落实即可进入编码（三道闸 ① py_compile ② 实施自审 ③ 实施独立审议）。

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 10:42 GMT+8 · 实施独立审议第 21 轮（独立子代理，主代理严禁自签） -->

## 实施独立审议（第 21 轮）

> 审议对象：M9-3 编码实现（tools/skills.py / orchestrator.py / server/admin.py / config/skills.yaml / skills/*.md(5) / docker-compose.yml / web 前端 / DESIGN_PLATFORM_FUNCTIONS.md §3.3）
> 立场：独立子代理，未与主代理串通；独立 Read 真实代码 + 交叉运行验证脚本（m9-3_verify.py / m9-3_e2e.py）+ `git diff` 核验改动面。
> 方法：以 DESIGN_M9-3.md 为权威契约，逐项比对实现；以 VERIFICATION §18 自审为对照但独立取证，不轻信自述。

---

### 核查过程与逐项结论（A–H + 回归基线）

#### A. 真·控制器效应 ✅ PASS（已独立实证，非装饰卡片）
- 注入点（`orchestrator.py`）：`:25` import `build_skill_context`；`:646-649` 在 `system = build_agent_system(role, reg)`（原 :642，行号因 import 微移为 :641）**之后**、`llm.complete`（:711 附近）**之前**拼入 `skill_ctx`。`build_agent_system` 签名未改，改动面最小，gates/其他调用点不受影响。
- `tools/skills.py:build_skill_context` 过滤顺序 = 设计 §3.2（enabled → installed/builtin → role∈target_roles → 片段存在非空），命中后拼 `【已启用技能：<name>】…【技能结束】`。
- **独立跑通验证脚本**：
  - `m9-3_verify.py`：E1（analyst 含 financial/swot/competitor、writer 不含）、E2（禁用 swot→analyst 不再含、重启用→重现）、E3（自定义 writer 含）、E4（chart_generation analyst 不含/writer 含）、E5（无 skills.yaml→空）、E7（按 yaml 顺序聚合）全部 `ALL_OK`。
  - `m9-3_e2e.py`（真实节点流，StubLLMClient 捕获 system）：E6 analyst 注入 financial/swot/competitor、writer 注入 chart_generation、writer 不注入 swot（作用域）、禁用 swot 后 analyst 不再含——`E2E_ALL_OK`。
- 结论：启停技能**真实改变**引擎拼给对应 Agent 的 system prompt，是真控制器，证据独立于主代理自述。

#### B. 诚实边界 R1–R4 ✅ PASS
- R1：VERIFICATION §18 仅声称「接线真实」（片段拼入对应 role system），**未**声称机器强制报告必含某结构；stub 离线只验证「接线变」，§11 风险已诚实标注。实现层 `build_skill_context` 仅拼 prompt 框架片段，与 `agents/<role>.md` 同性质，无机器强制。
- R2：admin 技能端点**无** `.secrets` 管理（`server/admin.py` skills 段无密钥读写），区别于 M9-2 数据源，简化且诚实。
- R3：空技能双保险——`build_skill_context` 遇 `prompt_file` 缺失/空则跳过（不 raise、不崩溃，`tools/skills.py:94-99`）；admin 启用守卫（PUT `enabled=True` 且 md 缺失/空 → 400 拒绝，`server/admin.py:1778-1785` + 内部复核 `:1796-1798`）。
- R4：向后兼容——`load_skills()` 文件缺失返回 `[]`（`tools/skills.py:39-40`）；E5 实证「无 skills.yaml → 空、现有行为不变」。

#### C. 设计→代码一致性 ✅ PASS
- **NOTE-1（关键）**：`skills/*.md`(5) 实存且非空（chart_generation 453B / citation_format 429B / competitor_compare 444B / financial_analysis 663B / swot 529B）；`config/skills.yaml` 实存；`docker-compose.yml:82` 已加 `./skills:/app/skills`。`git check-ignore skills/swot.md config/skills.yaml` 返回空（未被忽略），`git status` 显示二者将被跟踪入库 → 宿主 ./skills 非空、bind mount 下控制器不会静默失效。**NOTE-1 完全落实**。
- **NOTE-2**：`DESIGN_PLATFORM_FUNCTIONS.md:135` 「§3.3 研报技能库 ✅ M9-3 已交付」；`:138`/`:148` 统一用 `prompt_file`（无 `prompt_template` 漂移）；`types/ui.ts:62-63` 注释也标注与上游对齐。**NOTE-2 落实**。
- **lower 归一（G 项关键，诚实红线）**：`tools/skills.py:84` `role_l = role.lower()` + `:91` `targets = [t.lower() for t in ...]` + `:92` `role_l not in targets`。引擎节点流 `role='Analyst'/'Writer'`（大写首字母）经此归一提纯与 `target_roles` 小写匹配。E6 端到端（真实大写 role）注入成功即证明该归一**必要且生效**——若无此归一，注入在真实节点流会完全失效（首轮 E6 FAIL 根因已在自审记录，本审议独立跑 e2e 复现 PASS 佐证修复有效）。

#### D. 向后兼容 ✅ PASS
- 无 `skills.yaml` → `load_skills()` 返回 `[]` → `build_skill_context` 返回 `""` → `if skill_ctx:` 不成立，system 不变。E5 实证；现有行为零回归（改动仅追加分支，不改动既有 `build_agent_system` 结果）。

#### E. 不过度工程 ✅ PASS
- 实现仅含 `SkillError / load_skills / _recompute_status / _load_fragment / build_skill_context`，全在抽象层 + 节点内一行拼接。
- 未引入设计 §0.3/§2.2 排除项：无 per-task 技能选择 UI、无 Monaco 插入、无技能↔数据源硬依赖、无 RBAC/版本/评分、无输出格式渲染。前端为 controller（接真实 CRUD），非 viewer。

#### F. 复合写安全 ✅ PASS
- `server/admin.py` skills 端点复用 M9-1/M9-2 范式：`_load_skills_rt`（ruamel round-trip 保注释，缺失则建空骨架 `:1639-1647`）、`_atomic_write`、`_write_audit(subdir="skills")`。
- POST：校验 id 正则 + target_roles⊆VALID_ROLES + name/prompt 非空 + 不重名；失败 → `create_rejected` 审计 + 400；复合写 yaml + md；异常 → 回滚 yaml（before）+ 删 md + `create_rollback` 审计（`:1742-1755`）。
- PUT：启用守卫 + builtin 改 prompt_file→400 + 失败回滚 + `update_rollback` 审计。
- DELETE：builtin 拒删（400「内置技能不可删」）+ 复合删 yaml entry + md + 失败回滚 + `delete_rollback` 审计。
- 全部经独立 Read 印证，无密钥管理。

#### G. 关键实测发现核对（lower 归一）✅ PASS
- 独立确认 `tools/skills.py:84-92` 确有 `role.lower()` 与 `target_roles` 逐元素 `.lower()` 归一比较；且 E6 端到端用真实大写 role 跑通，证明该归一**已落地且必要**。此为诚实红线，已实现，无误。

#### H. 回归基线判断 ✅ 认可为预存债（TD-009）
- `git diff orchestrator.py`：`1 file changed, 7 insertions(+)`，仅 `:25` import + `:646-649` skill 注入（含注释）。**未触碰** rework 计数（`:643 rework_reason` 仅读取未改逻辑）、json 恢复（llm.complete 周边）、max_rounds 升级逻辑。
- 3 个失败测试：`test_rework_loop_and_convergence`(test_orchestrator.py:43)、`test_json_recover_by_retry`(test_orchestrator.py:83)、`test_max_rounds_exhausted_escalates_with_bounds`(test_regression_edge.py:166) —— 目标均属 rework/max_rounds/json，与 M9-3 注入点无因果链。
- 独立结论：改动面与失败测试目标正交，认可其为 M9-2 基线预存债（TD-009），**非 M9-3 引入，不阻塞 M9-3 闭环**。本审议未重跑完整 pytest（环境缺 langgraph 等依赖），但 `git diff` 改动面 + 测试目标交叉比对已足够独立判定；建议后续在 CI 修 TD-009，与 M9-3 解耦。

---

### 发现的问题

**MAJOR：NONE**（实现忠实落地设计、控制器真有效、诚实边界全守、向后兼容、复合写安全、回归非本里程碑引入；无真实缺陷需打回）

**MINOR / NOTE（分级处理）：**

- **NOTE-S1（非阻塞，可后续跟进）**：设计 §4.3 规定新增自定义技能默认 `enabled=False`，实现 `create_skill`（server/admin.py:1734）写入 `enabled=True`。功能上安全（POST 已校验非空白 prompt、控制器即时生效，属合理 UX），但偏离设计契约默认值。建议在 commit 前与主代理对齐：要么改回 `enabled=False` 以严格履约，要么在 DESIGN_M9-3 §4.3 补注「新增即启用」为有意设计。不阻塞闭环（无诚实/崩溃/回归风险）。
- **NOTE-S2（正向确认，非缺陷）**：PUT 端点未直接暴露可写 `status`（仅回写 `enabled/description/category/name/icon/target_roles`），`status` 由 `_recompute_status` 派生——比设计 §4.3「可改 status」更诚实（status 是派生量，不应手填），记一笔正向确认。
- **NOTE-S3（正向确认，非缺陷）**：`GET /skills`（public_router，无 token）仅返元数据（`_skill_item` 不含 prompt 片段文本），片段只进引擎 system —— 设计 NOTE-5 满足。

---

### 最终结论

**PASS_WITH_NOTES**

实施阶段实现忠实落地设计契约：真·控制器效应经独立跑通 E1-E7 + E6 端到端实证；诚实边界 R1-R4 全守；设计阶段 NOTE-1（内置 md + yaml 入库 + bind mount）、NOTE-2（上游 prompt_file 字段统一）均落实；lower 归一（G 项诚实红线）确已落地且经真实节点流验证；复合写 ruamel round-trip + 原子写 + 审计 + 回滚 + 启用守卫 + builtin 不可删全到位；向后兼容 E5 实证；无过度工程；3 个回归失败经 `git diff` 改动面对照确认为预存债（TD-009），与 M9-3 正交。MAJOR=NONE。

**NOTES 处置（按阻塞级别）：**
- **须在 commit 前确认/修（建议，非硬阻塞）**：NOTE-S1 —— 对齐新增技能默认 `enabled` 值（改为 False 履约，或改设计补注「新增即启用」）。此条为契约一致性，非缺陷，可商量；若主代理判断为有意设计则留 NOTE-S1 记录即可放行。
- **可后续跟进（不阻塞）**：NOTE-S2/S3 为设计比实现更优的正向确认，无需动作。
- **TD-009 回归债**：独立于 M9-3，建议 CI 后续修，不阻塞本里程碑 commit。

**主代理严禁自签 `reviewed-by`**；本审议由独立子代理落款，三道闸 ②③ 已完成，达到 commit 条件（仍须遵守「无 boss 显式推送指令不 git push」纪律）。
