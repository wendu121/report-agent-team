# VERIFICATION_M9-3 · 自审（设计 + 实施）

> 关联：[DESIGN_M9-3.md](./DESIGN_M9-3.md)（M9-3 研报技能库）
> 门禁：edict-gate 三道闸 ② 自审（主代理）；③ 独立审议见 [REVIEW_M9-3.md](./REVIEW_M9-3.md)
> 纪律：独立审议 `reviewed-by` 严禁主代理自签；全 PASS 才 commit

---

## 17. 设计自审（DESIGN_M9-3.md，编码前）

### 17.1 与上游文档一致性
- ✅ [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md) §3.3（研报技能库）**领域内容已替换**为研报分析框架技能（财务/SWOT/竞品/图表/引用），未照搬电商技能库（店铺诊断/选品/营销话术）—— §0.1 显式防错，与 M9-2 同源「抄架构换内容」约束。
- ✅ 复用 M9-1 / M9-2 范式（registry SoT + 复合写 + 市场 UI + 三道闸），但明确差异：技能是 **prompt 软增强**（非检索、非 Agent 流水线），不重复造轮子（§0.3/§2.2）。
- ✅ 复用 M8 硬约束（ruamel round-trip / 原子写 / 审计 / 热加载 / bind mount）与 M9-1/M9-2「复合写 + 失败回滚 + builtin 不可物理删」同源。
- ✅ 视觉沿用 PLATFORM_FUNCTIONS §6 色板，与 M9-1/M9-2 一致（§5）。

### 17.2 代码实证支撑（非凭印象）
- ✅ Agent prompt 组装（§1.1）：`build_agent_system(role, reg)`（:331）+ `make_agent` 节点 `:642 system = build_agent_system(role, reg)` + `:708 user = build_agent_user(...) + extra_context` —— 注入点 `:642` 后、`llm.complete`（:711）前拼技能片段，与 `Read` 实证一致（summary grep 印证 :331/:623/:642/:708）。
- ✅ bind mount 现状（§1.2）：docker-compose api volumes 已挂 config/.audit/agents/gates/templates/.secrets，**未挂 skills** → M9-3 须补 `./skills:/app/skills`（M8 TD-008 同源教训，防重建即丢）。
- ✅ admin 复合写范式（§1.3）：`_load_plugins`/`_atomic_write`/`_write_audit`/失败回滚已实测可用，M9-3 `skills` 端点镜像（差异：无密钥管理）。
- ✅ 前端市场范式（§1.4）：`Plugins.vue` 已建 `/plugins` 市场页，M9-3 `Skills.vue` 镜像（去连接密钥弹窗）。

### 17.3 诚实边界（boss 立约，不掩饰）
- ✅ R1 软增强不谎称强制：技能是 prompt 框架提示，引擎实证「接线真实」（片段拼入目标 agent prompt），**不声称**机器保证报告必含某结构（§3.4/§10 R1/§11 风险）。
- ✅ R2 无密钥不引 secrets：技能不需要外部密钥，不引入 `.secrets` 管理（区别于 M9-2 数据源），简化且诚实（§10 R2）。
- ✅ R3 空技能防御：片段缺失 → 引擎跳过（不崩溃、不冒充）；admin 启用前校验 `prompt_file` 存在（§3.4/§10 R3）。
- ✅ R4 向后兼容：无 `skills.yaml` → `load_skills()` 返回 [] → 无注入，现有行为不变（§3.2/§10 R4）。

### 17.4 控制器效应论证（核心：不是装饰卡片）
- ✅ G2：`build_skill_context(role)` 按 enabled+installed+role∈target_roles+片段存在 过滤 → 拼入 `system` → 启停某技能**真改引擎 prompt**（E2 实证：禁用 swot → analyst system 不再含 swot 片段；重新启用 → 重现）。
- ✅ target_roles 作用域真有效（E4：chart_generation target_roles=[writer] → analyst 不含、writer 含）。
- ✅ 此效应端到端可离线验证（stub LLM 捕获 system），不依赖 new-api 配额（§9 注）。

### 17.5 复合写 / 回滚论证
- ✅ POST/PUT/DELETE `/admin/skills` 走 `_atomic_write` + `_write_audit(subdir="skills")` + 失败全回滚（复用 M9-1/M9-2 范式，§4.3/§8）。
- ✅ 复合写 = `skills.yaml`（ruamel round-trip 追加）+ `skills/<id>.md`（写片段），与 M9-1 agents-library「写 agent md + gate md + mapping」同源；无密钥管理。
- ✅ 启用守卫：`enabled=True` 且 `prompt_file` 对应 md 不存在 → 400 拒绝（防启用空技能，保证控制器真有效，§4.3）。
- ✅ builtin 技能不允许物理删除（仅 `enabled=False` 停用），自定义才允许 DELETE —— 与 M9-1 builtin Agent 不可删同源（§4.3）。

### 17.6 向后兼容
- ✅ `load_skills()` 文件缺失返回 [] → `build_skill_context` 返回 "" → 现有行为不变，防回归（§3.2/§10 R4）。
- ✅ 不改动 `build_agent_system` 签名（保持 gates/其他调用点不变），仅在 `make_agent` 节点内拼装，改动面最小（§3.3）。

### 17.7 风险与不做清单
- ✅ §0.3 N1-N5 / §2.2 N1-N5 / §10 R1-R4 / §11 明确排除过度工程项（per-task 技能选择 / Monaco 插入 / 技能↔数据源硬依赖 / RBAC·版本·评分 / 输出格式渲染）。
- ✅ 风险诚实标注：技能效果依赖 LLM 遵循 prompt，stub 离线只能验证「接线变」不能验证「报告质量变好」（§11）。

### 17.8 设计自审结论
设计内部自洽、与上游文档一致、代码事实有据（注入点 :642 后实证）、诚实边界清晰（软增强不谎称强制）、控制器效应可证（E2/E4 离线可验）、复合写与 M9-1/M9-2 同源安全。设计具备编码条件，提交独立审议（③）第 20 轮。

### 17.9 设计修订记录（回应独立审议第 20 轮）
独立审议结论 = **PASS_WITH_NOTES**，MAJOR=NONE（设计无需返修）。代码实证（注入点 `:642`→`:712`、bind mount 现状、`admin.py` 原子写/审计范式、`Plugins.vue` 前端范式、`StubLLMClient` 离线基础）均经独立子代理 Read 印证，主代理未凭印象编造。

| 审议项 | 级别 | 处置 |
|---|---|---|
| 注入点 `:642` 后拼 skill_ctx，热加载经 make_agent 每次重建 + load_skills 重读成立 | PASS | 设计已成立，编码直落 |
| 领域替换无电商残留 | PASS | 设计已成立 |
| 诚实边界 R1-R4 | PASS | 设计已成立 |
| 与 M9-1/M9-2 范式一致 | PASS | 编码镜像 |
| 不过度工程 | PASS | 设计已成立 |
| **NOTE-1 内置 skills/*.md(5)+skills.yaml 必须入库且宿主 ./skills 非空，否则 Docker 下控制器静默失效** | NOTE（编码前必做） | 编码阶段：①建 skills/*.md(5)+config/skills.yaml 并入库；②docker-compose 加 ./skills:/app/skills；③E2/E6 验内置技能真注入 |
| **NOTE-2 上游 §3.3 用 prompt_template、M9-3 用 prompt_file，回写时统一字段名** | NOTE（编码顺带修） | 编码阶段回写 DESIGN_PLATFORM_FUNCTIONS.md §3.3 统一为 prompt_file |
| NOTE-3 E6 按 role 分桶捕获 system | NOTE（非阻塞） | m9-3_e2e.py 按 analyst/writer 分桶断言 |
| NOTE-4 builtin md 经 bind mount 落宿主且被改 → 同源取舍，交付说明点明 | NOTE（已知取舍） | 审计/交付说明标注 |
| NOTE-5 GET /skills 不泄露 prompt 片段（仅元数据） | NOTE（正向确认） | 编码遵守 |

**设计无需返修**，按 NOTES 落实进入编码（三道闸 ① py_compile ② 实施自审 ③ 实施独立审议）。

---

## 18. 实施自审（编码后填，三道闸②后半）

> 编码完成、py_compile/build 全绿、E1-E7 实证后，主代理在此补实施自审（偏差/回滚实证/诚实边界落实验证），再交独立审议第 N 轮（实施）。

### 18.1 实现落点对照（设计 → 代码）
| 设计节 | 落点文件 | 实测状态 |
|---|---|---|
| §3.1 技能注册表 SoT | `config/skills.yaml`（5 内置技能：financial_analysis/swot/competitor_compare/chart_generation/citation_format，全 enabled=true/active/builtin） | ✅ 入库（NOTE-1 关键：宿主 ./skills 非空 + bind mount，否则容器内控制器静默失效） |
| §3.2 抽象层 | `tools/skills.py`：`SkillError`/`load_skills`/`_recompute_status`/`build_skill_context`；片段加载复用 `BASE/prompt_file` 同源 `_load_md` | ✅ py_compile 通过；E1-E7 实测 |
| §3.3 orchestrator 注入 | `orchestrator.py`：`:25` import `build_skill_context`；`:642` 后 `skill_ctx = build_skill_context(role)` 拼入 system（仅一行调用，改动面最小，不改 build_agent_system 签名） | ✅ E6 e2e 实测 Analyst/Writer 节点 system 含技能片段 |
| §4 admin API | `server/admin.py`：`GET /skills`（公开）+`GET/POST/PUT/DELETE /admin/skills`（ruamel round-trip + 原子写 + 审计 + 回滚 + 启用守卫 + builtin 不可删）；无密钥管理 | ✅ py_compile 通过 |
| §5 前端市场 | `web/src/views/Skills.vue`（市场/已安装 tab + 搜索 + 分类 + 卡片启用/停用/删除 + 新增向导含 target_roles 多选 + 片段文本框）+`router/index.ts` `/skills`+`layouts/DefaultLayout.vue`「技能库」入口（Collection 图标）+`types/ui.ts` `SkillItem` | ✅ 镜像 Plugins.vue，去连接密钥弹窗/加 target_roles |
| §8 热加载/bind mount | `docker-compose.yml` api volumes 加 `./skills:/app/skills`（M8 TD-008 同源） | ✅ |

### 18.2 控制器效应实证（E1-E7，离线可验，不依赖 new-api 配额）
- E1 默认内置技能启用 → analyst 含 financial/swot/competitor，writer 含 chart/citation（按 target_roles 作用域）：PASS
- E2（核心）禁用 swot → analyst system 不再含 swot；重新启用 → 重现：PASS（启停真改引擎 prompt）
- E3 自定义技能（POST 模拟写 yaml+md）→ writer 含：PASS
- E4 target_roles 作用域 → chart_generation 仅 writer 含、analyst 不含：PASS
- E5 向后兼容 → 无 skills.yaml → 空、现有行为不变：PASS
- E6 e2e 真实节点流 → StubLLMClient 捕获 Analyst/Writer system 含技能片段、禁用 swot 后 Analyst 不再含：PASS
- E7 多技能按 yaml 顺序聚合：PASS
- 验证脚本：`C:\Users\sfkj\.workbuddy\m9-3_verify.py`（E1-E5/E7）+ `m9-3_e2e.py`（E6），均带 skills.yaml 复原（try/finally），重跑稳定。

### 18.3 关键实测发现（诚实记录，非设计预判）
- **引擎 role 大小写鸿沟**：`build_graph` 用 `g.add_node(role.lower(), make_agent(role, ...))`——节点名 lower，但 `make_agent(role, ...)` 收到的 `role` 是**原始大小写**（registry key 为 `Analyst`/`Writer` 大写首字母）。`skills.yaml` 的 `target_roles` 用小写 `analyst`/`writer`。若 `build_skill_context` 直接 `role in targets` 会因大小写不匹配**完全不注入**（首轮 E6 FAIL 根因）。修复：`build_skill_context` 内 `role.lower()` 与 `target_roles` 逐元素 `.lower()` 归一比较（tools/skills.py :101-103）。此归一对 `tools.skills` 层 E1（直传小写）无副作用。已记入代码注释 + 本条，避免 M9-4/M9-5 重蹈。
- e2e 测试脚本初版用 `captured.get("analyst")` 小写 key 断言失败——实为测试脚本 key 大小写 bug，非引擎缺陷（诊断 `role='Analyst' has_skill=True` 证明引擎注入已成功）。修正 e2e 用大小写不敏感 `get_sys()`。

### 18.4 诚实边界落地（R1-R4）
- R1 软增强不谎称强制：`build_skill_context` 仅拼 prompt 框架片段，VERIFICATION 只声称「接线真实」（片段拼入对应 role system），不声称机器保证报告结构。stub 离线只能验证「接线变」不能验证「报告质量变好」（§11 风险已标注）。
- R2 无密钥不引 secrets：admin 技能端点无 `.secrets` 管理（区别于 M9-2），简化且诚实。
- R3 空技能防御：片段缺失 → `build_skill_context` 跳过（不 raise/不崩溃）；admin 启用守卫 `enabled=True` 且 md 缺失/空 → 400 拒绝（防启用空技能）。
- R4 向后兼容：无 `skills.yaml` → `load_skills()` 返回 [] → 无注入，现有行为不变（E5 实证）。

### 18.5 复合写 / 回滚实证
- POST/PUT/DELETE `/admin/skills` 走 `_atomic_write` + `_write_audit(subdir="skills")` + 失败全回滚（复用 M9-1/M9-2 范式）。
- 复合写 = `skills.yaml`（ruamel round-trip）+ `skills/<id>.md`（写片段），与 M9-1 agents-library 同源。
- builtin 技能不可物理删除（仅 `enabled=False` 停用），自定义才允许 DELETE（与 M9-1 同源）。
- 验证脚本临时改 skills.yaml 均经 try/finally 复原，交付文件未污染。

### 18.6 回归测试
- `pytest tests/test_orchestrator.py tests/test_regression_edge.py`：3 failed / 21 passed。
- 3 个失败 = `test_rework_loop_and_convergence` / `test_json_recover_by_retry` / `test_max_rounds_exhausted_escalates_with_bounds` —— **与 M9-2 基线完全相同**，经 `git stash` 在 M9-1 基线复现确认是 TD-009 预先存在债（rework/max_rounds/json 逻辑，M9-3 未触碰：`orchestrator.py` 仅改 skill 注入点 + import），非 M9-3 引入。守「不过度工程」纪律未越界修。

### 18.7 实施自审结论
设计→代码忠实落地，控制器效应 E1-E7 离线实证全 PASS，诚实边界 R1-R4 落实，复合写与 M9-1/M9-2 同源安全，向后兼容 E5 实证，回归仅 3 个预先存在债（TD-009，非本里程碑引入）。**独立审议（③）结论待第 21 轮**，预期 PASS_WITH_NOTES 不阻塞。
