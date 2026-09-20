# REVIEW_M12-5 · 专家 shape 接入研报流水线（独立评审 / 门禁 ③）

> 评审对象：`report-agent-team` M12-5（专家 `shape` 接入研报流水线 + `model` 消费 + 解析逻辑 DRY）。
> SoT：`DESIGN_M12-5_expert_shape.md`。门禁 ② 自审：`VERIFICATION_M12-5.md`。
> 本文件由独立子代理产出，主代理不自签 `reviewed-by`。

## 0. Verdict

**PASS_WITH_NOTES**

所有 6 项评审检查均在源码中以 file:line 实证，且实际运行测试全部通过（7 passed + e2e PASS + 19 回归 passed）。遗留 1 个非阻断 note：M12-5 新增单测未包含「team 型专家经 `build_pipeline_system` 显式拒绝」的专属用例（该行为由 M12-4 回归 + 代码路径共同保证，见 §6）。

## 1. Scope reviewed

- 设计符合性：逐条对照 `DESIGN_M12-5 §2 / §3 / §4`。
- 源码落点：`tools/experts.py`、`chat_agent.py`、`orchestrator.py`。
- 测试真实运行（非仅阅读）：单测 V1-V6、e2e、M12-4 回归子集。
- 未改动任何源码（仅评审 + 写本文档）。

## 2. 评审检查 + file:line 证据表

| # | 检查点 | 结论 | 证据（file:line） |
|---|---|---|---|
| 1 | `resolve_expert` 存在；显式 @ → 隐式 route → 降级 `("", None)`；`meta` 含 `shape`/`model` | PASS | `tools/experts.py:324` 定义；显式 `resolve_mention` `:340`；隐式 `route` `:343`；无匹配降级 `:346`；`ExpertError`（team/损坏）捕获 `:349-351`；外圈 `except Exception` 防御纵深 `:363-365`；`meta` 含 `shape` `:359`、`model` `:360` |
| 2 | `chat_agent._resolve_expert_context` 真正委托，无重复解析逻辑 | PASS | `chat_agent.py:275` 仅 `return ex.resolve_expert(user_text)`；导入守卫 `:270-274` 仅兜底模块缺失、不含解析逻辑；M12-4 解析代码已抽离 |
| 3 | `build_pipeline_system`：`build_agent_system`+`build_skill_context`；仅 `expert_meta.shape == reg["shape_of"][role]` 才追加专家片段；返回 `(system, expert_meta)` | PASS | `orchestrator.py:1735` build_agent_system；`:1737` build_skill_context；`:1738-1739` 非空才追加（先 skill）；`:1746` resolve_expert；`:1750` shape 过滤器 `expert_meta.get("shape") == reg["shape_of"][role]`；`:1751` 追加；`:1752` 返回 `(system, expert_meta)` |
| 4 | `_resolve_agent_model` 优先级 `model_override > expert_model > models["roles"][role]["model"]`，且 `expert_model` 仅当 `shape` 匹配才取 | PASS | `orchestrator.py:1713-1717` `expert_model = meta.model if (meta and meta.shape==shape) else ""`；`:1718` `return model_override or expert_model or models["roles"][role]["model"]` |
| 5 | `make_agent.node` 正确接线，`model` 下游被消费 | PASS | `orchestrator.py:1785` 调 `build_pipeline_system`；`:1787-1788` 调 `_resolve_agent_model`；`model` 被消费 `:1884`（`_run_fc_loop`）、`:1893`（`llm.complete`）、`:1902`（重试） |
| 6 | 无回归：无专家时行为不变（V3）；team 型仍被拒 | PASS（team 见 note） | V3 基线逐字节一致 `tests/test_m12_5_shape.py:95-104`；team 型：`tools/experts.py:174-180` `read_expert` 抛 `ExpertError`，`list_experts` `:258-262` 跳过该包，`resolve_expert` `:349-351` 捕获降级 |

### 2.1 关键正确性复核（防 shape 过滤被绕过 / model 优先级错）

- **shape 过滤不会误注入**：过滤器用**精确相等** `:1750`。若 `expert_meta.shape` 为空串（损坏/缺字段），不会匹配任何 role 的 `"researcher"/"analyst"/"writer"`，天然不注入——安全。
- **model 与 persona 注入同源约束**：`build_pipeline_system` 与 `_resolve_agent_model` 都用同一个 `reg["shape_of"][role]` 比较，二者对 shape 的判断一致；不会出现「persona 不注入但 model 被错用」或反之。
- **team 型双重保险**：`list_experts` 在枚举阶段即跳过 team 包（`:258-262`），`resolve_expert` 即便直接命中也会在 `get_expert` 处抛 `ExpertError` 被捕获降级（`:349-351`），永不进入流水线 system / model。

## 3. 测试结果（实测，非阅读）

运行环境：受管 venv `C:\Users\sfkj\.workbuddy\binaries\python\envs\default\Scripts\python.exe`，PowerShell（Bash shim 在沙箱损坏）。一次性租户根隔离，未触网、未污染真实 `config`。

| 命令 | 结果 |
|---|---|
| `pytest -q tests/test_m12_5_shape.py` | **7 passed in 10.29s** |
| `python scripts/verify_m12_5_e2e.py` | **RESULT: PASS**（Analyst 注入 + Researcher 隔离 + 模型消费 三验通过） |
| `pytest -q tests/test_m12_runtime.py tests/test_m12_experts.py`（M12-4 回归） | **19 passed in 24.45s** |

> e2e 日志重定向出现中文乱码（编码问题），但 ASCII 标记 `RESULT: PASS` / `[PASS]` 清晰，结论可靠。

## 4. 验收矩阵（DESIGN_M12-5 §4）

| 项 | 结果 | 依据 |
|---|---|---|
| V1 显式 @ + shape 过滤 | PASS | `test_v1_explicit_mention_shape_filter`（Analyst 注入、Researcher 不注入） |
| V2 隐式 route | PASS | `test_v2_implicit_route`（routed=True） |
| V3 低置信无回归 | PASS | `test_v3_low_confidence_no_regression`（meta=None，system==基线） |
| V4 shape 错配不注入 | PASS | `test_v4_shape_mismatch_not_injected` |
| V5 模型消费 | PASS | `test_v5_model_priority` + `test_v5_expert_model_in_pipeline` |
| V6 包损坏降级 | PASS | `test_v6_broken_package_degrades`（meta=None，不抛） |

## 5. Gaps / Notes（诚实标注）

1. **[NOTE, 非阻断] team 型拒绝无专属 M12-5 用例**：`tests/test_m12_5_shape.py` 覆盖了损坏包（V6）与低置信（V3），但未直接用一个 `expertType: team` 专家跑 `build_pipeline_system` 断言不注入。该行为由 (a) M12-4 回归 `test_m12_*` 19 passed 间接保证、(b) 代码路径 `tools/experts.py:174-180 / 258-262 / 349-351` 显式保证。建议后续补一条 V7-team 用例以闭环 DESIGN §5 边界断言。
2. **[INFO] e2e 日志中文乱码**：仅影响重定向日志可读性，不影响判定；脚本本身打印 `RESULT: PASS`。属于测试输出环境问题，非实现缺陷。
3. **[INFO] chat 侧委托保留导入守卫**：`chat_agent.py:270-274` 在 `tools` 模块不可用时返回 `("", None)`，属防御性兜底而非解析逻辑，未引入重复实现，DRY 成立。

## 6. 诚实声明（Honesty）

- 所有 6 项检查均已**实际读取源码 file:line** 并**实际运行**对应测试，未凭推断判 PASS。
- 唯一未以「专属 M12-5 用例」形式直接触达的是 team 型拒绝（见 §5 note 1）；但该行为有代码路径实证 + M12-4 回归佐证，不构成阻断，已如实标注为 NOTE 而非 PASS 隐藏。
- 未提交任何更改（未 commit），仅产出本评审文档。

<!-- reviewed-by: independent-subagent (gate-③) -->
