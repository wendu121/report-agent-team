# DESIGN_M11-2_reflection.md · M11-2 反思回写闭环（实现设计，v1 草案待 boss 评审）

- 日期：2026-09-13
- 上游 SoT：`DESIGN_M11_autonomous.md` §3（本文件是其**实现细化**，冲突处以本文件为准并逐条标注偏离原因）
- 前置：M11-1 `a1189c3`（kb 层）、M11-3 `ad4f25f`（D1-B function-calling）均已落地并容器内实测通过
- 状态：**仅设计，未写代码**（铁律：先过设计评审）

## 1. 问题与目标

现状（实测）：三道闸只判**本报告**，`escalate_reason` / rework 原因 / 工具失败等**证据随任务结束即丢弃**；引擎不从历史错误中长进。

M11-2 目标：任务结束时**捕获证据 → 生成结构化改进建议 → 人工 accept 后才回写配置**。
铁律不变：**`proposed` 永不自动 apply；引擎不因 reflection 改变行为，除非管理员显式 accept。**

## 2. 现状锚点（file:line，实测）

| 锚点 | 位置 | 说明 |
|---|---|---|
| `ReportState` 字段 | `orchestrator.py:78-92` | `routing_state`(含 escalate_reason/engine_events)、`gate_output`、`retrieval_records`、`tool_status` |
| 末闸/闸输出 | `orchestrator.py:89` `gate_output`；:1851 末闸 `END→kb_write` | 闸 decision/reason 在 `gate_output` |
| escalate 终态 | `orchestrator.py:1850` `g.add_edge("escalate", END)` | **绕过 kb_write** → 反思须同时接两条终态路径 |
| kb 落库节点 | `orchestrator.py:1768 node_kb_write`；:1834 注册；:1851 `kb_write→END` | kinds = `report_conclusion` / `retrieval_record` |
| kb 召回注入 | `orchestrator.py:1749 node_kb_retrieve`（无 kind 过滤） | → **reflection 落 kb 会污染 Researcher 可信来源集**（见 §5 决策 D2） |
| 闸→角色映射 | `orchestrator.py:867` `reg = {agents, gate_of, reviews}` | 复盘时定位"哪个 Agent 被哪道闸打回" |
| admin CRUD 范式 | `server/admin.py:1902-2024 /admin/skills`；`_yaml_rt()`:60、`_atomic_write()`:170、audit | 直接镜像（ruamel 保注释 + 原子写 + 审计） |
| 运行时写配置 | `config/*.yaml` 已 bind mount（`docker-compose.yml:67`） | 回写持久化成立 |

## 3. 数据模型：Reflection 条目

```yaml
# config/reflections.proposed.yaml（运行时生成；git-ignored）
reflections:
  - id: rfl-20260913-0001          # 日期+序号，幂等键
    created_at: 2026-09-13T00:40:00+08:00
    task_topic: "AI 芯片市场研报"
    status: proposed               # proposed | accepted | rejected
    kind: plugin_disable           # 见 §4 规则清单，一类一模板
    summary: "数据源 wikipedia 本任务 0 命中且 3 次超时"     # 一句话给 UI 列表
    evidence:                      # 证据链（可审计，全部来自真实 state）
      - "tool_status: wikipedia ok=false ×3 (timeout)"
      - "retrieval_records: wikipedia 命中 0 条"
    target:                        # 受影响配置的精确坐标
      file: plugins.yaml
      key: "sources.wikipedia.enabled"
    proposed_change:               # accept 时将执行的机械操作
      op: set_enabled              # 白名单见 §6
      value: false
```

## 4. 生成器：**确定性规则引擎**（v1，非 LLM）

**决策 D1（需 boss 拍板）**：v1 用确定性规则而非 LLM Reflector。
理由：①建议必须**可验证**（证据→建议一一对应，LLM 会编因果）；②零额外 token 成本；③绝不产生"建议改 prompt 第 3 段"这类幻觉级建议。LLM Reflector 留作 M11-2c（可选，独立评审后再上）。

v1 规则清单（每条产出 0..N 个 reflection，全部有 state 证据支撑）：

| kind | 触发条件（读 state） | proposed_change |
|---|---|---|
| `plugin_disable` | 某 enabled 源 `tool_status` 全 ok=false 或 0 条检索 | `plugins.yaml <id>.enabled=false` |
| `plugin_hint` | 某源命中率高但 credibility 均 low | 仅提示，无机械 op |
| `skill_gap` | 同一 rework 原因跨任务复现 ≥2（从历史 accepted/reflections 统计） | 建议 `skills.yaml` 增/启技能；**op 仅提示**，人工编辑 |
| `rework_hotspot` | 某 Agent 被 Gate 打回 ≥2 轮（`engine_events` 统计） | 仅提示 + 证据（"GateA ×2：编造 url"），人工去 Agent 配置台 |
| `escalate_review` | 任务以 escalate 终结 | 仅提示：escalate_reason + 涉事 Agent |

> v1 只有 `plugin_disable` 产生**可机械执行**的 op；其余全部"仅提示"。宁可建议少而真，不可多而假。

## 5. 存储决策

**决策 D2（偏离总设计 §3.2，需 boss 拍板）**：reflection **不落 `kb.entries`**，只存 `config/reflections.proposed.yaml` + 审计日志。
原因（实测依据）：`node_kb_retrieve`（:1749）无 kind 过滤，召回结果作为 Researcher「唯一可信来源集合」注入（:1227 起）。reflection 是元建议（无 url、无检索语义），混入会污染来源集、稀释素材信噪比。若未来要检索历史反思，给 `kb_retrieve` 加 kind 过滤后再说（列入 §9 开放问题）。

`.gitignore` 增 `config/reflections.proposed.yaml`（运行时生成，不入库）。

## 6. 人工 accept 门禁（回写白名单，核心）

| op | 目标 | accept 行为 |
|---|---|---|
| `set_enabled` | `plugins.yaml <id>.enabled` | 校验 key 存在 → ruamel round-trip 改值 → `_atomic_write` → audit |
| （仅提示类，无 op） | — | accept 仅置 `status: accepted` 留痕（表示"已阅并采纳方向"），**不改任何 yaml**；prompt/技能内容修改永远走既有 Agent/技能配置台 |

**白名单之外一律拒绝（fail loud，HTTP 400）**：任何针对 `agents/*.md`、`gates/*.md` 的 op 永不进白名单。`reject` 仅置 status（证据留痕，不删除）。

## 7. 图接线改动（最小侵入）

```python
# orchestrator.py build_graph（:1832-1851）
g.add_node("reflect", node_reflect)          # 新增
g.add_edge("escalate", "reflect")            # 原 :1850 escalate→END
g.add_edge("kb_write", "reflect")            # 原 :1851 kb_write→END
g.add_edge("reflect", END)
```

`node_reflect`（orchestrator.py 新增，约 30 行）：读 state → 调 `tools/reflection.py::generate_reflection(state)` → **append 写入** `config/reflections.proposed.yaml`（文件锁 + 幂等 id）→ 失败降级 no-op（不阻断终态，同 kb_write 模式）。

## 8. API 契约（镜像 /admin/skills 范式：ruamel + 原子写 + audit）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/admin/reflections?status=` | 列表（默认全部，可按 status 过滤） |
| `POST` | `/api/v1/admin/reflections/{id}/accept` | 校验 op 白名单 → 回写/留痕 → 返回更新后条目 |
| `POST` | `/api/v1/admin/reflections/{id}/reject` | 置 rejected 留痕 |

鉴权：沿用 `X-Admin-Token`（同其他 admin 端点）。CORS 已放行（M8-1 已修）。

## 9. 前端（M11-2b，同一里程碑第二提交）

`web/src/views/` 新增「反思建议」面板 + 路由 + Pinia store：列表（status 徽章 / summary / evidence 展开）+ 接受/驳回按钮 + "仅提示类"显示为纯文本建议（无按钮，或按钮点击后提示"请到 XX 配置台人工修改"）。`WSEvent` 不动（反思非实时事件，刷新拉取即可）。

## 10. 测试计划（stub，零网络）

1. 构造含 `tool_status(ok=false)` / `escalate_reason` / 多轮 rework 的 state → `generate_reflection` 断言产出条目与证据链。
2. 无任何异常的干净 state → 断言产出 **0 条**（不造无据建议）。
3. accept 白名单：`set_enabled` 真改文件（tmp_path）+ 断言注释保留（ruamel）；伪造 op（改 prompt）→ 400。
4. 图接线：stub run_report 正常终态与 escalate 终态各跑一次 → 断言 `reflections.proposed.yaml` 各新增条目且任务正常 END。
5. 回归：现有 94 测试全绿（`kb_write→reflect→END` 改动不得破坏 M11-1）。

## 11. 三道闸计划

gate① `py_compile` + pytest（预计 94+8）+ `vue-tsc`/`vite build`（M11-2b）；gate② `VERIFICATION_M11-2.md`；gate③ **Intern-S2-Preview-397B** 独立审议（本轮实测其可信，且已验证"bundle 须含完整函数源码"防误报）。

## 12. 需 boss 拍板的 4 个决策

| # | 决策点 | 我的建议 |
|---|---|---|
| D1 | 生成器：确定性规则 vs LLM Reflector | **v1 确定性**（可验证/零成本/零幻觉）；LLM 版留 M11-2c |
| D2 | reflection 是否落 kb.entries（总设计 §3.2 说落） | **不落**（防污染 Researcher 来源集，理由见 §5） |
| D3 | accept 白名单范围 | **仅 `set_enabled` 可机械执行**；技能/prompt 全部"仅提示+人工" |
| D4 | 前端面板（M11-2b） | **做**（同里程碑第二提交；否则闭环不可见） |

确认后我按本文件实施（预计 orchestrator.py ~40 行 + tools/reflection.py 新文件 + admin.py ~120 行 + 前端一屏）。
