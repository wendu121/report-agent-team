# DESIGN_M8-5 · 子集编排（任务模板真正驱动流水线）

> 元信息：起草 2026-09-08（M8-4 提交 `aaa009a` 后，下一步里程碑）
> 状态：**待 boss 评审**（edict-gate 三道闸：① 校验 ② 自审 ③ 独立审议，全 PASS 才动代码）
> 关联：
> - 承接 [DESIGN_SETTINGS_CONSOLE.md](./DESIGN_SETTINGS_CONSOLE.md) 的 **G5**（Agent 数量扩展）
> - 闭合 [VERIFICATION.md](./VERIFICATION.md) §13 设计偏差：G4 收敛配置层、编排消费=引擎硬编码 3+3 零回归、子集编排待 M8-5
> - 扩展层归属见 [DESIGN_PLATFORM_FUNCTIONS.md](./DESIGN_PLATFORM_FUNCTIONS.md)（M9 智能体市场 = 新增 Agent 类型）

---

## 0. 范围裁定（本设计最重要的一节）

### 0.1 真实痛点（不是想象的）
M8-4 把「任务模板」从前端死数组迁为后端可编辑资源，并让 `create_task` 把 `agents/gates` 落库为任务元数据。
**但引擎仍是硬编码 3+3**：`build_graph`（orchestrator.py:961）字面写死 `researcher/analyst/writer/gate_a/gate_b/gate_c` 六个节点，
`route_after_gate`（948-954）把 `GateA→analyst / GateB→writer / GateC→END` 写死。
→ M8-4 为防「假配置」被迫硬约束模板必须为完整 3+3。

**M8-5 的唯一真实目标**：让模板选定的子集**真的被执行**，消除「UI 选了子集但引擎跑全量」的假闭环。

### 0.2 与 G5 原文的偏差（主动 push back，请 boss 裁决）
`DESIGN_SETTINGS_CONSOLE.md` 的 G5 原文 =「orchestrator 字典外置为 yaml + UI 可新增 Agent + 受控重启」。
本报告**建议把 M8-5 收敛为子集编排，不碰 G5 的「新增角色」 half**：

| 维度 | 子集编排（本报告推荐 M8-5） | 原 G5 完整版（建议推回 M9） |
|---|---|---|
| 新增角色 | ❌ 不新增（仍 3 个已知角色） | ✅ UI 可新增第 4/N 个角色 |
| 字典外置 yaml | ❌ 不动（5 字典留代码，仅作 role→gate/tool 映射） | ✅ 外置 + UI 编辑 |
| 受控重启 | ❌ 不需要（图每次 `run_report` 现建，热加载） | ✅ 需重启 |
| 改动面 | `build_graph` + 3 路由函数 + 透传链路（5 处，纯机械） | 引擎 + 状态定义 + model_mapping + prompt 文件 + UI |
| 风险 | 低（向后兼容：缺省=全 3+3） | 高（跨状态/配置/UI 多处） |
| 归属 | M8 配置层 | M9 扩展层（智能体市场可插拔新 Agent 类型） |

**理由**：① M8-4 的痛点就是子集不执行，不是不能加角色；② 新增角色本质是可插拔新能力，属 M9 智能体市场；
③ 子集编排零重启、热加载、风险最小，符合 boss「精简克制、反感过度工程」原则。
**若 boss 选完整 G5，本报告作废，改走 DESIGN_PLATFORM_FUNCTIONS M9-1。**

---

## 1. 当前调用链（代码实证，非凭记忆）

```
api.create_task
  └─ start_engine_background (api.py:290)
       └─ engine_manager.launch_engine(task_id, user_task, max_rounds)   # engine_client.py:157
            └─ EngineProcess(task_id, user_task, max_rounds)              # :31 写 input_file={user_task,max_rounds}
                 └─ subprocess -m server.engine_runner <input_file>       # :71
                      └─ run_engine → run_report(user_task, llm, max_rounds, tools=None)  # engine_runner.py:54
                           └─ build_graph(llm, models, tools)             # orchestrator.py:961 硬编码 6 节点
```

**断层**：`agents/gates` 在 `TaskResponse`（M8-4 已存）但**未进入 input_file / run_report / build_graph** → 引擎永远跑全量。

---

## 2. 改动点（5 处，纯机械透传 + 引擎参数化）

| # | 文件 | 改动 | 向后兼容 |
|---|---|---|---|
| C1 | `server/api.py` | `start_engine_background` 调 `launch_engine(..., agents=task.agents, gates=task.gates)` | 新增 kw，旧调用无影响 |
| C2 | `server/engine_client.py` | `EngineProcess.__init__` + `launch_engine` 收 `agents/gates`，写进 `input_data["agents"/"gates"]` | 缺省 `[]` |
| C3 | `server/engine_runner.py` | `run_engine` 读 `input_data.get("agents",[])` / `("gates",[])`，传 `run_report(..., agents=..., gates=...)` | 缺省空→全量 |
| C4 | `orchestrator.py run_report` | 加 `agents=None, gates=None` 形参；缺省 `None`→全 3+3；透传 `build_graph(..., agents=agents)` | ✅ 完全兼容 |
| C5 | `orchestrator.py build_graph` | **核心**：按有序链动态建节点+边；3 个路由函数改为闭包（带 chain 状态） | 缺省链=原 6 节点 |

**前端**：零改动。`task.agents/gates` 已回传前端（M8-4）；模板页 agents/gates 多选即「选 K 个 agent→gate 对」。

---

## 3. build_graph 新算法（子集编排）

### 3.1 入参约定
- `agents`：有序角色列表，缺省 `["Researcher","Analyst","Writer"]`（保持原行为）。
- `gates` **不独立传**：由 `GATE_NAME[role]` 推导，保证 **gate↔agent 配对**（绝不允许孤儿闸）。
  → 模板里勾了 `Writer` 就必定带 `GateC`；勾了 `GateC` 却不勾 `Writer` 视为非法（M8-4 校验拦截）。

### 3.2 节点命名（去字面硬编码）
- agent 节点 = `role.lower()`（`researcher`/`analyst`/`writer`）
- gate 节点 = `gate_` + `role.lower()`（`gate_researcher`…）内部；`make_gate(GATE_NAME[role], ...)` 仍读真实闸名

### 3.3 边构建
```
START → agents[0].lower()
每 role:  role.lower()  ──ok──▶  gate_<role.lower()>
每 role:  gate_<role.lower()>  ──advance──▶  下一 role.lower() 或 END
                        ──rework──▶  router（再由 route_router 回 TARGET_NODE[rework_target_agent]）
                        ──escalate──▶  escalate
escalate → END
```

### 3.4 三个路由函数改为闭包（带 chain）
```python
def _make_route_after_gate(chain):
    def route(state):
        go = state.get("gate_output", {})
        d = go.get("decision")
        if d == "escalate": return "escalate"
        if d == "rework": return "router"
        last = state.get("routing_state", {}).get("last_gate")   # "GateA"
        role = GATE_REVIEWS.get(last)                            # "Researcher"
        if role is None or role not in chain: return "router"
        idx = chain.index(role)
        return chain[idx+1].lower() if idx+1 < len(chain) else END
    return route

def _make_route_after_agent(chain):
    def route(state):
        if state.get("agent_result") == "ok":
            role = state.get("routing_state", {}).get("_last_agent")
            return ("gate_" + role.lower()) if GATE_NAME.get(role) else "router"
        return "router"
    return route

def _make_route_router(chain):
    def route(state):
        rs = state.get("routing_state", {})
        go = state.get("gate_output")
        if go is None: return chain[0].lower()
        d = go.get("decision")
        if d == "escalate": return "escalate"
        if d == "advance":
            role = GATE_REVIEWS.get(rs.get("last_gate"))
            if role is None or role not in chain: return END
            idx = chain.index(role)
            return chain[idx+1].lower() if idx+1 < len(chain) else END
        if d == "rework":
            if rs.get("status") == "escalated": return "escalate"
            tgt = rs.get("rework_target_agent")
            return tgt.lower() if tgt in chain else "escalate"   # 子集外 rework → 诚实升级
        return "escalate"
    return route
```

### 3.5 build_graph 骨架
```python
def build_graph(llm, models, tools=None, agents=None):
    chain = agents or ["Researcher", "Analyst", "Writer"]
    g = StateGraph(ReportState)
    g.add_node("router", make_router())
    for role in chain:
        g.add_node(role.lower(), make_agent(role, llm, models, tools))
        g.add_node("gate_" + role.lower(), make_gate(GATE_NAME[role], llm, models))
    g.add_node("escalate", node_escalate)
    g.add_edge(START, chain[0].lower())
    g.add_conditional_edges("router", _make_route_router(chain),
        {**(r.lower(): r.lower() for r in chain), "escalate": "escalate", END: END})
    for role in chain:
        g.add_conditional_edges(role.lower(), _make_route_after_agent(chain),
            {"gate_" + role.lower(): "gate_" + role.lower(), "router": "router"})
        g.add_conditional_edges("gate_" + role.lower(), _make_route_after_gate(chain),
            {**(r.lower(): r.lower() for r in chain if chain.index(r) > chain.index(role)),
             "router": "router", "escalate": "escalate", END: END})
    g.add_edge("escalate", END)
    return g.compile()
```

### 3.6 prior_versions 动态初始化
`run_report` 的 `init["prior_versions"]` 当前写死 `{"researcher":[],"analyst":[],"writer":[]}`（:1016）。
改为 `{role.lower(): [] for role in (agents or FULL)}`，与 `make_agent` 写入键一致。

---

## 4. M8-4 硬约束的放松（让子集合法）

`server/admin.py::_validate_template` 当前：
```python
if sorted(ags) != sorted(KNOWN_AGENTS):   # 非全 3+3 → 400
    raise HTTPException(400, ...)
```
M8-5 改为：
- `ags` 必须是 `KNOWN_AGENTS` 的**非空子集**（空 → 400）；
- `gates` 必须 == `{GATE_NAME[r] for r in ags}`（配对校验，孤儿闸 → 400）；
- 顺序无关（引擎按 canonical 顺序跑）。
→ 模板多选 agents 即「选 K 个 agent→gate 对」，引擎真跑该子集。

---

## 5. 验证计划（三道闸证据）

| 项 | 方法 | 预期 |
|---|---|---|
| ① 校验 | `py_compile` orchestrator/api/engine_client/engine_runner；前端 typecheck（无改动应仍绿） | 0 错 |
| ② 自审 | VERIFICATION.md §14：设计偏差 + 4 类 stub 实测 | 见下 |
| ③ 独立审议 | REVIEW.md 第 15 轮（派独立子代理，严禁自签） | PASS/NOTES |
| **E1 全量回归** | stub-LLM `mode=pass`，不传 agents → 行为同 M8-4 | status=done，3 节点产出齐全 |
| **E2 单对** | agents=["Researcher"] → 跑 researcher→gate_researcher→END | 终态 done，`retrieval_records` 有值，`analysis/draft` 空 |
| **E3 中段子集** | agents=["Analyst","Writer"] → analyst→gate_b→writer→gate_c→END | draft_segments 有值，retrieval 空 |
| **E4 路径穿越式** | 模板传 agents=["Writer","GateC"] 配对 ✅ / agents=["GateC"] 无 Writer ❌ | 创建 400（孤儿闸） |
| **E5 rework 越界** | stub 让 GateA rework 到 Analyst，但子集仅 Researcher | 诚实 escalate（非静默跑全量） |

> stub-LLM 不触发真实 new-api 配额（当前 429 规避态），E1-E5 可离线跑通，消除「引擎执行需 LLM」的验证阻塞。

---

## 6. 诚实边界 / 风险

- **不新增角色**：不动 `ReportState` 定义、不动 `model_mapping.yaml`、不建新 prompt 文件、不碰 M8-2/3 的 agents/gates 编辑页。
- **受控重启不需要**：图每次 `run_report` 现建（沿用 orchestrator 天然热加载事实）。
- **rework 越界诚实失败**：子集外 rework 目标 → escalate（不静默降级为跑全量）。
- **`_last_agent` 字段**：`route_after_agent` 依赖 `routing_state._last_agent`，需确认 `make_agent` 在产出后写该字段（实测时 grep 确认，缺则补一行）。
- **M8-4 已落库的 agents/gates**：回到全量模板（standard_research）时 `agents=全 3+3`，行为与旧完全一致。

---

## 7. 不做（Non-Goals，防范围蔓延）
- N1：新增第 4/N 个 Agent 角色（推 M9 智能体市场）
- N2：字典外置 yaml + UI 编辑（推 M9-1）
- N3：可视化 LangGraph 节点编辑器（DESIGN_SETTINGS_CONSOLE N1，过度工程）
- N4：G6 输出格式渲染（PPT/Docx/PDF，推 M9）

---

## 8. 提交纪律
- 本地 `git commit --only report-agent-team`，**不 push**（boss 指令）。
- 改动集：orchestrator.py / server/api.py / server/engine_client.py / server/engine_runner.py / VERIFICATION.md / REVIEW.md（DESIGN_M8-5.md 同步入库）。
- 三道闸全 PASS 才 commit；独立审议 `reviewed-by` 严禁主代理自签。
