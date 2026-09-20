# VERIFICATION_M12-5 · 专家 shape 接入研报流水线（自审 / 门禁 ②）

> SoT：`DESIGN_M12-5_expert_shape.md`（先设计后实现）。
> 治理：三道闸（① py_compile ② 本自审 ③ 独立子代理 `REVIEW_M12-5.md`，主代理不自签 `reviewed-by`）。
> 关联里程碑：M12-1/2/3 落地专家包解析 + 注册表 + 运行时接线；M12-4 接 chat 侧（已 commit `7e03f83`）。
> 本档接 **研报流水线侧**（orchestrator `make_agent`），两条路径共用 `tools.experts.resolve_expert` 单一入口。

## 1. 实现范围（对照 DESIGN_M12-5 §3 改动清单）

| 文件 | 改动 | 验证 |
|---|---|---|
| `tools/experts.py` | 新增 `resolve_expert(text) -> (ctx, meta)`（显式 @ → 隐式 route → 降级不冒泡）；新增模块级 `logger` | py_compile + 单测 |
| `chat_agent.py` | `_resolve_expert_context` 改为**委托** `tools.experts.resolve_expert`（删重复解析逻辑，DRY，行为不变） | 单测 R1-R4/V8 全过 |
| `orchestrator.py` | 新增 `_resolve_agent_model(...)`（模型优先级）+ `build_pipeline_system(role, reg, user_text)`；`make_agent.node` 改用其组装 system 并消费 `expert_meta["model"]`（shape 匹配才注入） | 单测 V1-V6 + e2e |
| `tests/test_m12_5_shape.py` | 新增 V1-V6 单测（一次性租户根隔离，不触网） | 7 passed |
| `scripts/verify_m12_5_e2e.py` | 一次性租户根 + 播种 1 个 analyst 专家 → 断言注入/隔离/模型消费（不触网） | RESULT: PASS |

## 2. 门禁 ① · py_compile

```
python -m py_compile tools/experts.py chat_agent.py orchestrator.py tests/test_m12_5_shape.py scripts/verify_m12_5_e2e.py
→ 退出码 0（无语法错误）
```

## 3. 门禁 ② · 自测证据

### 3.1 单测（一次性租户根隔离，不触网）
```
pytest -q tests/test_m12_5_shape.py
→ 8 passed in 1.65s
```
（初版 7 passed；门禁 ③ 议出非阻断 note（缺 team 型在流水线侧显式拒绝用例）后补 V7 → 8 passed。）

全量回归（确认 M12-4 委托无破坏）：
```
pytest -q
→ 286 passed, 61 warnings in 78.88s
```
（基线 278 → +8 新增；M12-4 的 `test_m12_runtime` / `test_m12_experts` / `test_m12_admin_api` 全部仍在 PASSED 集合内，R1-R4/V8 未回归。）

### 3.2 e2e（真实文件系统，不触网）
```
python scripts/verify_m12_5_e2e.py
→ [PASS] 专家 shape 接入研报流水线（V1/V4/V5）：Analyst 注入 + Researcher 隔离 + 模型消费 三验通过
→ RESULT: PASS
```

## 4. file:line 实证（核心改动落点）

- **单一解析入口**：`tools/experts.py` `resolve_expert`（位于 `route()` 之后，新增函数）。
  - 显式 @：`resolve_mention(text, experts)`，`routed=False`。
  - 隐式：`route(text, experts)`，`routed=True`（V7 ≥2 命中才选）。
  - 降级：`except ExpertError` + 末尾 `except Exception` 双保险 → `("", None)`，绝不冒泡（闭环 M12-4 V8）。
  - `meta = {"id","name","routed","shape","model"}`。
- **chat 委托**：`chat_agent.py` `_resolve_expert_context` 现仅 `return ex.resolve_expert(user_text)`（保留原签名/返回形）。
- **流水线接入**：`orchestrator.py` `build_pipeline_system`：
  - `system = build_agent_system(role, reg)` → 追加 `build_skill_context(role)`（顺序不变，先 skill）。
  - `user_text` 非空时 `resolve_expert` → **`expert_meta.get("shape") == reg["shape_of"][role]`** 才追加专家片段（shape 过滤器 = V1/V4 关键）。
  - 返回 `(system, expert_meta)`。
- **模型消费**：`_resolve_agent_model(model_override, expert_meta, shape, models, role)`：
  - `expert_model = meta.model if (meta and meta.shape==shape) else ""`
  - `return model_override or expert_model or models["roles"][role]["model"]`（用户级 > 专家 > role→model）。
  - `make_agent.node` 内：`model = _resolve_agent_model(model_override, expert_meta, reg["shape_of"][role], models, role)`。

## 5. 验收矩阵结论（DESIGN_M12-5 §4）

| 项 | 结果 | 依据 |
|---|---|---|
| V1 显式 @ + shape 过滤 | PASS | `test_v1_*`（Analyst 注入、Researcher 不注入） |
| V2 隐式 route | PASS | `test_v2_implicit_route`（routed=True） |
| V3 低置信无回归 | PASS | `test_v3_*`（meta=None，system == 基线逐字节） |
| V4 shape 错配不注入 | PASS | `test_v4_shape_mismatch_not_injected` |
| V5 模型消费 | PASS | `test_v5_model_priority` + `test_v5_expert_model_in_pipeline` |
| V6 包损坏降级 | PASS | `test_v6_broken_package_degrades`（meta=None，不抛） |

## 6. 诚实边界（与 DESIGN_M12-5 §5 一致，非推断）

- 专家**不改研报结构**：仅注入 persona 片段（与 skill 片段同级），产出 JSON schema 仍由 role `output_key` 约束。
- team 型专家：`read_expert` 抛 `ExpertError`（默认拒绝，见 M12 team 决策）→ `resolve_expert` 降级 `("", None)` → **永不进流水线**。
- 同 shape 多个专家：仅首个注入（确定性；无 fan-out，避免过度工程）。
- 前端未展示「本任务用的专家」：后端 `expert_meta` 已在 `build_pipeline_system` 返回，UI 后续档（本档不动 UI）。
- 无专家时 `build_pipeline_system` 行为与改造前逐字节一致（V3 守回归 + 285 全量回归佐证）。

## 7. 自审结论

门禁 ①（py_compile）通过；门禁 ②（自测）通过：286 全量 + e2e RESULT:PASS，M12-4 委托零回归。
独立子代理门禁 ③ 议出 `PASS_WITH_NOTES`；其非阻断 note ①（缺 team 型流水线侧显式拒绝用例）已由新增 **V7** 关闭（见 §3.1 / §5）。
**无未决阻断项**。
（本文件为自审，门禁 ③ 由独立子代理产出 `REVIEW_M12-5.md` 并自签 `reviewed-by`，主代理不代签。）
