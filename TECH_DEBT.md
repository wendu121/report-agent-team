# 技术债清单 · report-agent-team

> 记录已识别、尚未彻底解决、或采用「可接受折中」方式暂存的技术债。每条含：位置、现状、影响、后续处置。
> 已闭环的债（如 F6 判别联合重构）不列入此表，仅在对应 REVIEW/VERIFICATION 留痕。

## TD-001 · Element Plus 全量引入导致 vendor 块体积偏大

- **登记时间**：2026-09-06
- **触发改动**：`web/vite.config.ts` 已加 `manualChunks` 按 vendor 拆分（vue / element-plus / markdown / utils），并把 `chunkSizeWarningLimit` 调到 1100 静音警告。
- **现状（可接受折中）**：
  - 应用主块 `index.js` 已压到 **27.16 kB（gzip 9.76 kB）**；
  - `element-plus` vendor 块 **1,088.92 kB（gzip 341.64 kB）** —— 这是全量 `import 'element-plus'` 的库固有体积，非配置缺陷；
  - vendor 已独立成块：应用代码改动不再使 element-plus 缓存失效，浏览器可并行下载。
- **影响**：首屏需下载 ~1MB element-plus（gzip 342kB）。对管理后台类内网应用可接受；公网/弱网场景偏重。
- **后续处置（待评估）**：评估 **Element Plus 按需引入**（`unplugin-auto-import` + `unplugin-vue-components`，仅打包用到的组件），预计可把 element-plus 块降到 ~200–400 kB。
  - ⚠️ 前置风险：需逐个模板核对已用组件、有回归风险，且会改动业务代码 → **必须重跑 edict-gate 三道门禁**后方可入库。
  - 触发条件：仅在首屏体积成为实际瓶颈、或 boss 明确批准时才启动；当前 M7 前端已冻结（见下方「冻结声明」），不主动推进。

## 冻结声明（2026-09-06 boss 裁定）

- **M7 前端全部 PASS，前端侧工作冻结**：不再新增任何前端代码/配置改动（含本清单 TD-001 的按需引入优化），除非 boss 显式解冻或触发新需求。
- **例外出口**：真实引擎全链路跑通所需的「端到端用例回归」属后端/集成验证，不视为前端改动；其引发的必要前端适配另议。

## TD-002 · 真实引擎未推送中间 WS 事件（仅收尾事件）

- **登记时间**：2026-09-06（引擎端到端回归实测发现）
- **现状（真实缺口，非回归失败）**：
  - server 真实引擎路径（`server/api.py` `start_engine_background` → `server/engine_runner.py` 调 `orchestrator.run_report`）当前**仅广播收尾事件**：`connection_established` + `task_done` / `task_escalated`。
  - **不推送**中间事件 `agent_complete` / `gate_complete` / `rework_trigger` / `round_update` / `tool_error`。
  - 根因：orchestrator `run_report` 不接受事件回调；server 的 `engine_client.emit_engine_event` / `websocket.emit_*` 已就位但**未被引擎运行过程调用**。
- **影响**：前端 M7 `TimelineNode` / 进度条在**真实跑通**时只收尾填充，无法展示 agent/gate 实时流转；但契约形状（`engine.ts` 判别联合、API_SPEC §3.2 平铺 payload）完全兼容——只是事件流稀疏。
- **后续处置（待设计，非当前做）**：
  - 属**新功能**（中间事件流式化），按 boss「先设计后编码」纪律，**主代理不擅自动**；
  - 设计要点：给 `run_report` 加 `on_event` 回调钩子 → `engine_runner` 在 agent/gate/rework/round/tool 节点触发时调 `emit_engine_event` → `websocket.broadcast`；需对齐 `server/websocket.py` 既有 8 事件模型；
  - 触发条件：boss 批准后走「设计评审 → 编码 → edict-gate 三道门禁 → commit」。
- **检索降级备注**：环境无 `TAVILY_API_KEY`，`build_tools()` 自动降级 MockProvider（占位检索，不冒充真实），仅影响研报内容真实性，不影响事件链路与契约。

## TD-003 · orchestrator 发射状态与 server `RoutingState` 模型契约 mismatch（后端阻断 bug）

- **登记时间**：2026-09-06（引擎端到端回归 `e2e_real_run.py` 实测发现，任务 `babd02f1…` 跑 20 分 2 秒后 `status=escalated`）
- **现状（真实阻断缺陷，e2e 未 PASS 根因）**：
  - `server/api.py:281` 将引擎输出反序列化为 `task.routing_state = RoutingState(**routing)` 时，Pydantic 抛 **6 个 ValidationError**：
    - `gate_review_history.0.gate` / `.round` / `.timestamp` —— Field required；
    - `engine_events.0/1/2.timestamp` —— Field required。
  - 引擎本身正常：已产出 `gate_review_history`（`decision:advance`）与 `engine_events`（`agent_output_update` round 1/2）。失败**仅发生在「orchestrator 状态 → server Pydantic 模型」序列化边界**。
  - `escalated` 终态为**误标**——实为 schema 错误被兜底成 escalate，非内容质量升级。
- **契约错配点（精确）**：
  - `server/api.py` `GateReview` (`api.py:70-78`) 要求 `decision,reason,eval_score,problem_points,gate,round,timestamp` 全必填；`EngineEvent` (`api.py:56-67`) 要求 `event,reason,round,timestamp` 全必填（`timestamp` 必填）。
  - `orchestrator.py` 实际发射：`gate_review_history` 条目 `go` (`orchestrator.py:782-787`) **仅 `decision/reason/eval_score/problem_points`**，缺 `gate/round/timestamp`；`engine_events` 条目 (`orchestrator.py:749-751` 等) **缺 `timestamp`**。
- **影响**：真实引擎端到端**无法抵达 `task_done`**；前端 WS 仅收 `connection_established`，`routing_state` 为空、无 `report_markdown`。与 TD-002（中间事件不推）**正交**，但两者叠加使真实跑通链完全断在 API 边界。
- **决策（2026-09-06 boss 裁定：选 A）**：
  - **以 `server/api.py` 的 Pydantic 模型（GateReview / EngineEvent / RoutingState）为权威契约**；
  - orchestrator 侧补齐缺失字段，使发射状态满足 server 模型校验；
  - **不放宽 server 校验**（不改 `RoutingState(**routing)` 为 `model_validate`/`Optional`/`extra='ignore'`/`construct()`），防止后续继续产出残缺状态数据被静默接纳。
- **修复落点（已落地 `orchestrator.py`）**：
  - 顶部加 `from datetime import datetime` + 模块级 `_utcnow_iso()` helper（UTC ISO 时间戳，与 `api.py:289` 一致）；
  - 发射点 1 `agent_output_unusable` (`orchestrator.py:491-494`)：补 `round`(rs["round"]) + `timestamp`；
  - 发射点 2 `writer_citation_regenerated` (`orchestrator.py:686-687`)：补 `round` + `timestamp`；
  - 发射点 3 `gate_llm_unavailable_degraded_advance` (`orchestrator.py:762-765`)：补 `gate`(gate_name，原已有) + `round` + `timestamp`；
  - 发射点 4 `go` → `gate_review_history` (`orchestrator.py:800-803`)：补 `gate`(gate_name) + `round`(rs["round"]) + `timestamp`；
  - `py_compile` 通过；server 已重启加载新代码（旧进程已停，新 uvicorn :8000 健康）。
- **验证状态（2026-09-06 已实证闭环 ✅）**：e2e 第二轮（`7ea3cbf9…`）+ 第三轮（`0ac0b71f…`）`routing_state` **成功反序列化**，`gate_review_history` 带 `gate/round/timestamp`、`engine_events` 各条目带 `timestamp`（对比第一轮 6-error 崩溃，契约已对齐）。**TD-003 后端 bug 已修复并实证有效**，不再阻塞。
- **注**：e2e 仍 `escalated` 的根因已转移为 **glm-4.7 通道 429 配额**（见 TD-004），与 TD-003 正交。
- **入库前置**：TD-003 代码改动需待 e2e 全链 PASS（即 glm-4.7 配额问题解决后）一并走 独立子代理 REVIEW.md 迭代 5 → 三道闸全 PASS → `git add`/`commit`。

## TD-004 · 模型映射 glm-4.7 通道 429 配额耗尽（当前 e2e 阻塞根因）

- **登记时间**：2026-09-06（e2e 第二/三轮定位）
- **现状（真实上游配额问题，非代码 bug）**：
  - `config/model_mapping.yaml` 中 `Analyst` / `Writer` / `GateA` 三角色均绑 `glm-4.7`（zhipu 通道）。
  - 直连网关硬验证：`LongCat-2.0` → 200、`glm-4.7` → **429「余额不足或无可用资源包」(code 1113)**、`llama-3.3-70b` → 200。
  - TD-003 schema 修复已实证生效（routing_state 反序列化成功），但引擎大半调用撞 glm-4.7 429 → Analyst 无产出 → 降级 escalate。
- **影响**：真实引擎端到端**无法抵达 `task_done`**；与 TD-002（中间事件不推）、TD-003（已修复）三者曾叠加阻断，现仅剩本项 + TD-002（TD-002 仅影响事件稀疏度，不阻断 task_done）。
- **决策（2026-09-06 boss 裁定：选 A · 已执行）**：
  - **`model_mapping.yaml` 改绑（仅配置变更，不动任何引擎业务代码）**：
    - `Analyst`: zhipu/glm-4.7 → **custom/LongCat-2.0**；
    - `GateA`: zhipu/glm-4.7 → **cloudflare/llama-3.3-70b**；
    - `Writer`: **保留 zhipu/glm-4.7**（若后续 e2e 仍撞 429，再切 LongCat-2.0——boss 预授权此备选）。
  - 注释中已明确标注风险：**llama-3.3-70b 历史实测 schema 输出不稳定**（不按 schema 出 draft_segments），GateA 依赖 `orchestrator.py` 的 `robust_json_load()` 容错兜底，非 100% 保证。
  - 异基座隔离硬约束复核通过：GateA(cloudflare)≠Researcher(custom)、GateB(cloudflare)≠Analyst(custom)、GateC(custom)≠Writer(zhipu)。
  - 引擎每任务独立子进程调 `run_report()` 内部重新读 YAML → **无需重启 server**，新任务自动生效。
- **待恢复备注**：**待 glm-4.7 通道恢复（充值/上游解除限流）可切回原映射**——只需改回本文件对应三处绑定，prompt 与代码不动。
- **当前状态（2026-09-06 已实证解决 ✅）**：e2e 第四轮（`d152e81a…`，3 分 20 秒）**429 彻底消失** —— GateA 真实 `advance`(eval_score 0.8)、Analyst 产出 3 条结构化结论（con-1/2/3）、`engine_events` 为空（零 `agent_output_unusable`）。模型改绑生效，**本项不再阻断 e2e**，降为观察项（等 glm-4.7 恢复切回原映射）。
- **e2e 新阻断**：第四轮 `escalated` 根因已转移为 **TD-005**（GateB 审核 prompt 未携带 retrieval_records）——已于 2026-09-06 修复并实证（见 TD-005）。
- **预授权执行（2026-09-06 · e2e 第五轮触发）**：第五轮证实 **Writer 仍撞 glm-4.7 429**（`engine_events` 两条 `agent_output_unusable`，`429 - 余额不足或无可用资源包 (code 1113)`），触发 boss 预授权「Writer 切 LongCat-2.0」，已执行：
  - `Writer`: zhipu/glm-4.7 → **custom/LongCat-2.0**；
  - **联动** `GateC`: custom/LongCat-2.0 → **cloudflare/llama-3.3-70b** —— 因 Writer 移入 custom 后，GateC 若仍为 custom 将**违反硬约束 1**（`gates.*.base != roles.<reviews>.base`，即 Gate 不得与所审 Agent 同基座）。llama 用于 Gate 有实测背书（GateA/GateB 均 advance 0.9）。
  - 校验：YAML 合法、**硬约束 ALL PASS**（GateA/B/C 均 cloudflare vs 三角色均 custom）、**glm-4.7 已全部移出**。引擎子进程每任务重读 YAML → 无需重启 server。
- **当前状态**：e2e 第六轮（后台 `lOKyNp`）验证中，目标 `task_done`。
- **入库前置**：TD-005 修复 → e2e PASS → 回填 VERIFICATION.md → 独立子代理 REVIEW.md 迭代 5 → 三道闸全 PASS → `git add`/`commit`。

## TD-005 · GateB 审核 prompt 未携带 `retrieval_records`（引擎缺陷 · e2e 当前唯一阻断）

- **登记时间**：2026-09-06（e2e 第四轮 `d152e81a…` 实测发现）
- **现状（真实引擎缺陷，非模型/配额/MOCK 问题）**：
  - GateB 连续两轮判 `rework`，理由均为「结论 `source_ids` 引用的 `rec-6/rec-7/rec-11/rec-12/rec-16/rec-17` 在 retrieval_records 中不存在」；第二轮 rework 理由原文 *"当前没有提供 retrieval_records 进行校验"*（LLM 自陈上下文缺失）。
  - 但 `final_state.retrieval_records` **实际含全部 6 条记录**（`tests/e2e_real_run_report.json:105-148`，id/url/title/snippet/credibility 齐全）—— 属**审核方看不到数据**而非数据缺失。
- **根因（精确定位 `orchestrator.py:364-382` `build_gate_user`）**：
  - 该函数组装给 Gate 的审核材料仅三项：①`Gate: {gate_name}` ②`待审 {role} 产出（结构化段 prod_key）` ③`user_task`；
  - **仅 `GateC` 额外附带 `report_markdown`**（M5 修复时补），**所有 Gate 均不附带 `retrieval_records`**；
  - 故 GateB 校验 Analyst 引用链（`source_ids`）时无记录 ID 全集可对照 → 臆断 ID 不存在 → 必然 rework → 两轮打满（`max_rounds=2`）→ `escalated`。
- **性质判定**：**prompt 构建层代码缺陷**。与模型无关（换任何模型都同样看不到数据）、与 MOCK 检索无关（记录真实存在，仅内容为占位）。属**缺陷修复**（同 TD-003 性质），非新功能。
- **影响**：真实引擎端到端**无法抵达 `task_done`**（在 GateB 必挂）。当前 e2e 唯一阻断项（TD-003 已修、TD-004 已绕、TD-002 仅致事件稀疏不阻断）。
- **设计文档（2026-09-06 出稿 · 待评审 ✅ 已交付）**：`TD-005_GATE_CONTEXT_DESIGN.md` —— boss 选 **B（先设计后编码）**。文档含：铁证（记录实际存在 + LLM 自陈缺失）、根因（Agent 生产侧有 `retrieval_records` 上下文 vs Gate 审核侧无，orchestrator.py:595-598 vs 364-382；GateB 代码硬校验 415-431 甚至自带"可用 id 集合"示范）、S/F/D 方案对比、token 估算、副作用与回滚、§7 验证计划、§8 **D1–D5 五项待评审决议**。**评审通过后方可编码**。
- **实现与实证（2026-09-06 ✅ 已闭环）**：按 boss 批复「按推荐实现」编码 —— `orchestrator.py:370` 常量 `GATE_CONTEXT_MAX_REC_DETAIL=200`、`373-391` 纯函数 `_fmt_retrieval_records`（空→`None`）、`413-417` `build_gate_user` GateB 分支。`py_compile` OK + 冒烟（6 条格式正确/空→None/260 条明细截断 200 且 id 全量）。
  - **e2e 第五轮（`8f3ad3e0…`）铁证**：`GateB` **advance（eval_score 0.9）**，理由 *"所有结论均有来源标注，且来源 id 均存在于检索记录中"* —— 对比第四轮"不存在"，引用链校验已正确。**TD-005 缺陷已修复并实证生效。**
  - 第五轮仍 escalate 的根因已转移为 **Writer 撞 glm-4.7 429**（非本项），已按 TD-004 预授权切换，见 TD-004。
- **入库前置**：第六轮 e2e PASS → 回填 VERIFICATION.md → 独立子代理 REVIEW.md 迭代 5 → 三道闸全 PASS → `git add`/`commit`。
- **待决策（boss 拍板，主代理不擅自动引擎代码）** —— 已于 2026-09-06 批复，留档备查：
  - **(A) 直接修 `build_gate_user`**：在审核上下文附 `retrieval_records`（建议精简为 `id/title/credibility` 控 token），使 Gate 可校验引用链；改动面约 3–5 行，可选仅对含 `source_ids` 的 GateB 生效或全 Gate 生效。
  - **(B) 先出设计文档再修**：按「先设计后编码」纪律，先提交上下文改造设计（token 膨胀评估、对 GateA/GateC 副作用分析），评审后编码。
  - **(C) 暂不修**：长期挂账，e2e 保持 RED、不入库。
- **验证前置**：修复后须重跑 e2e 至 `task_done` → 回填 VERIFICATION.md 6.7 → 独立子代理 REVIEW.md 迭代 5 → 三道闸全 PASS → 才 `git add`/`commit`。门禁当前 **RED**，严禁强行 PASS。

## TD-006 · Docker 容器化改造（新增任务 · 2026-09-07）

- **登记时间**：2026-09-07（boss 指令启动，独立新任务）
- **目标范围**：
  - FastAPI 后端容器（`server/` 经 uvicorn 服务）
  - 前端 Nginx 静态容器（`web/` 多阶段构建 → `dist/` 由 Nginx 托管）
  - `docker-compose.yml` 完整编排（后端 + 前端 + 必要依赖服务）
  - 全部环境变量清单
  - 镜像体积评估
  - 风险点
- **纪律（boss 硬约束，不可绕过）**：
  - **先设计后编码**：先产出 `TD-006_DOCKER_DESIGN.md` → 评审门禁 PASS → 才允许编写 Dockerfile / compose 代码（含 `nginx.conf` / `.env.example` / `.dockerignore`）。
  - **不跳过评审、不跳过门禁、不直接输出成品 Docker 代码**。
  - 代码完成后执行 `docker build` 构建验证 → 完整走 edict-gate 三道门禁（校验 → 自审 VERIFICATION.md → 独立审议 REVIEW.md，严禁主代理自签 `reviewed-by`）→ 全 PASS 才 `git add`/`commit`。
  - **不破坏基线 `e4b01d3`**：本任务作为新增文件 / 新增提交叠加其上，不 revert / 改写已入库内容。
- **设计文档状态**：已产出（`TD-006_DOCKER_DESIGN.md`，12 节：现状核查/目标架构/后端/前端/compose/全量环境变量/镜像体积/风险 R1–R10/待评审决议 D1–D10/验证计划/门禁纪律/范围外）。
- **评审状态**：独立子代理 agent-e225b397 事实核查 **PASS**（10 条断言全与代码一致、无编造；2 处措辞瑕疵已修）；boss 批复「D1–D10 全部推荐默认」后已编码。
- **编码状态**：已完成并 `docker build` 验证通过（后端 `report-agent-team-api` / 前端 `report-agent-team-web` 镜像均 Successfully built；api `/health` 200、web 静态 200 + `/api` 反代链路通）。构建中暴露并修复 2 真实根因：`psycopg2-binary` 缺失（sync 驱动）、`init_database` 非幂等（DuplicateTable）。详见 VERIFICATION.md §7。
- **门禁状态**：edict-gate 三道闸进行中（① 校验 PASS ② 自审完成 ③ 独立审议待派）。全 PASS 后 commit（基线 `e4b01d3` 之上）。

## TD-007 · report-web 宿主端口迁移 8080 → 18080（2026-09-07）

- **登记时间**：2026-09-07（boss 指令：不做远程 git、本地迭代；`report-web` 迁到不冲突端口并做好记录）
- **触发原因**：TD-006 按 D9 推荐默认将 web 映射为 `8080:80`，与宿主既有容器 **`mailhub-web`**（邮件项目，同为 `8080:80`）**互斥**——二者只能起一个。此前为跑通集成验证临时 `docker stop mailhub-web`，属**环境冲突非代码缺陷**。
- **改动范围（仅 1 行映射 + 登记文档）**：
  - `docker-compose.yml:77`：`- "8080:80"` → `- "18080:80"`（附 D9 修订注释说明冲突原因）
  - 新增 `PORT_REGISTRY.md`：**宿主端口分配登记册**（本项目映射 + 其它项目已占端口 + 选端口规则）
- **端口选型依据**：实测探测候选端口，宿主已占用 `3000 / 2280-2285 / 5300 / 5401 / 28080 / 54320 / 8096 / 8125 / 8420 / 8424`；`18xxx` 区间与既有 8xxx / 2xxxx / 5xxxx 全不冲突，故选 **18080**。
- **无需改动之处（已实证）**：前端 `wsService.ts` 用 `location.host` 构建 WS URL、axios `baseURL` 为相对 `/api/v1`，**无后端地址硬编码** → 换端口前端自动适配；全仓 grep `8080` 仅命中注释 / 历史文档 / 新登记册，**无代码层硬编码**。
- **验证结果**：`report-web` 重建后 `0.0.0.0:18080->80` Up；`http://127.0.0.1:18080/` **200**、`/api/v1/tasks` 反代 **200**、SPA 深链 **200**；`mailhub-web` 已 `docker start` **复原**（8080 **200**），二者**同时在线**，冲突彻底解决。
- **后续规则**：改任何端口映射前先查 `PORT_REGISTRY.md` 并跑端口探测，改后回填该表；不再为跑通而随意停他人容器（临时停须复原）。
- **门禁**：edict-gate 三道闸（① 校验 ② 自审 VERIFICATION.md §7.10 ③ 独立审议 REVIEW.md 第 9 轮）全 PASS 才 commit；**不 push 远程**（boss 指令）。

## TD-008 · 配置文件持久化（config/agents/gates/templates 四类 bind mount 已全闭环）

- **登记时间**：2026-09-08（M8-1 开发中实测发现）
- **触发原因**：`Dockerfile` 是 `COPY . .`，而 `docker-compose.yml` 的 api 服务**只 mount 了 `./outputs`
  与 `./.engine_state`**——`config/` `agents/` `gates/` 三个配置目录**都在镜像层**。
  后果：M8 配置控制台在 UI 上改了配置，**容器一重建就回退到镜像默认值**，UI 改动等于白改，
  直接违背 boss 立约「UI 必须是 controller，不能是 viewer」。
- **已修（M8-1）**：`docker-compose.yml` api.volumes 追加 `./config:/app/config`。
  实测：PUT 改 `Analyst.model` → 宿主机 `config/model_mapping.yaml` 同步变化（持久化铁证）。
- **已修（M8-2）**：追加 `./agents:/app/agents`。
  实测：UI 写入标记行 → 宿主机 `agents/researcher.md` 同步出现；容器内
  `build_agent_system("Researcher")` 返回的 system prompt 含该标记（**引擎热加载铁证**）。
- **已修（M8-3）**：追加 `./gates:/app/gates`（`orchestrator.py` 真实读取 `gates/review.md`）。
- **已修（M8-4）**：追加 `./templates:/app/templates`（任务模板从前端死数组迁为后端可增删改资源，
  bind mount 生效 md5 已对拍；宿主改 manifest → 容器内同文件 md5 一致）。
- **现状（2026-09-08）**：四类配置目录 `config/agents/gates/templates` 全部 bind mount，
  UI 改动落盘到宿主机、容器重建不丢，TD-008 主体闭环。
- **为什么不在 M8-1 一次 mount 四个**：M8-1 范围是 Models 页，只用到 config；
  提前 mount 未被本里程碑验证的目录属范围外改动。已在 compose 注释与本节双处留痕防忘。
- **顺带教训**：`config/model_mapping.yaml` 含 5 次变更历史注释（33 行），
  配置回写**必须**用 `ruamel.yaml` round-trip（PyYAML 的 `safe_dump` 会吞掉全部注释）。
  `server/admin.py::_yaml_rt` 在 ruamel 缺失时 **fail loud**（返回 500 明确报错），
  **不静默降级为 PyYAML**——降级 = 静默破坏配置文件的历史记录。
- **相关待办（M8-4 独立审议 LOW 项）**：见 TD-009（admin 页未带 X-Admin-Token、delete 快照静默丢失、
  缺 catch、status 无鉴权、`_template_item` 并发 404），均非阻断，挂账待统一治理。

## TD-009 · M8-4 独立审议 LOW 项（非阻断，挂账待统一治理）

- **登记时间**：2026-09-08（REVIEW.md 第 14 轮 L-1~L-5）
- **来源**：M8-4 任务模板页独立审议发现的 5 项 LOW 级问题，均非阻断，但属真实薄弱点，挂账防止遗漏。
- **L-1 · delete 快照 .bak 静默丢失**：`server/admin.py` delete 路径先 `rmtree` 再 `_write_audit`，
  而 `_write_audit` 的 `copy2` 包在 `except Exception: pass` → 源已不存在，静默跳过。
  修复方向：删除前先 `copy2` 到审计目录，或调整顺序（先审计后 rmtree）。数据未真丢（`before` 已在 JSON 内）。
- **L-2 · admin 页未带 X-Admin-Token**：`Templates.vue`/`TemplateEdit.vue` 的 fetch 裸奔（与 Models/Agents/Gates 同病，项目级既有债）。
  一旦生产配置 `ADMIN_TOKEN`，模板页整页 401。修复方向：抽 `adminFetch()` 统一注入 token。
- **L-3 · 缺 catch，网络异常静默**：`TemplateEdit.vue`/`Templates.vue` 部分 fetch 无 `catch` → 断网/502 时 unhandled rejection。
  修复方向：补 `catch` → `ElMessage.error`。
- **L-4 · `/admin/status` 无鉴权 + 耦合 mapping 解析**：无 `_require_admin`；`_load_commented()` 抛错即早退，
  使 `modules.templates`/`templates_mounted` 整段不可见。修复方向：模板/agents/gates 健康度与 mapping 解析解耦 + status 纳入鉴权。
- **L-5 · `_template_item` 并发 404 风险**：直接 `p.stat()`，与 `_template_names()` 间若被并发删除 → `FileNotFoundError` → 500。
  修复方向：包 `try/except FileNotFoundError` 跳过或 404。
- **INFO**：`ui_mode` 无枚举校验（§13.2.4 已声明不驱动分支，M9 实装前补枚举）。
- **状态**：挂账，不阻塞 M8-4 入库；建议 M8-5 或统一 admin 重构时一并处理。

## TD-010 · 侧边栏 `.sect` 分高依赖 `:first-of-type/:last-of-type` 顺序敏感

- **登记时间**：2026-09-16（UI 第三轮独立审议 `REVIEW_UI_R2.md §5` NOTE-4）
- **位置**：`web/src/layouts/DefaultLayout.vue:387-388`
  ```css
  .sect:not(.collapsed):first-of-type { flex: 3 1 90px; }
  .sect:not(.collapsed):last-of-type  { flex: 2 1 70px; }
  ```
- **现状（可接受折中）**：两条规则用**文档顺序**区分「聊天记录 / 历史记录」两块的高度权重。当前 `.app-aside` 内恰好只有这 2 个 `<section class="sect">`（行 39 / 行 81），其余兄弟是 `div.brand` / `ul.el-menu` / `div.user-zone`，故**当前行为正确**。
- **影响**：将来若在 `aside` 内新增第 3 个 `.sect`，`first-of-type` / `last-of-type` 会静默指到别的块 → 高度权重错分（不报错、不崩，仅布局走样）。属**隐式结构耦合**，且无任何断言保护。
- **为何本轮不修（2026-09-16 主代理决策）**：彻底修法需把顺序选择器换成显式语义 class（`.sect--chat` / `.sect--history`），**必须同时改模板 DOM**；而本轮该文件刚完成整体重写、且独立审议已就此出具 PASS，再改模板会扩大已过审的 diff 面。**有意识的取舍，非遗漏**；已提交审议方裁定（`REVIEW_UI_R2.md §6`）。
- **后续处置（待触发）**：下次因任何原因改动 `DefaultLayout.vue` 模板时**顺带**完成 —— 行 39 加 `sect--chat`、行 81 加 `sect--history`，387-388 改为 `.sect--chat:not(.collapsed){flex:3 1 90px}` / `.sect--history:not(.collapsed){flex:2 1 70px}`；同时可删掉 `@media(max-width:860px)` 里为绕过优先级而写的同形重复选择器（NOTE-2 的修复）。
- **状态**：挂账，不阻塞本轮 UI 改动入库。

