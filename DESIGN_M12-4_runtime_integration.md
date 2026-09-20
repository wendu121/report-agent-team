# DESIGN_M12-4 · 专家团与技能的运行时接入（Runtime Integration）

> 状态：**草案 v1，待独立评审**（先设计后实现，boss 铁律）。
> SoT 关系：本文是 M12-4 的单一事实源，服从 `DESIGN_M12_skill_autonomy.md`（M12 总纲），不覆盖它。
> 前置：M12-1（Importer）/ M12-2（L1 闸门 + API）/ M12-3（专家包）**代码已完成且 gate ③ PASS**。

---

## 0. 为什么要做这一档（现状盘点，实测非推断）

M12-1/2/3 交付后我做了运行时盘点，发现**三处「纸面完成、实际没接上」**：

| # | 现状（实测） | 性质 |
|---|---|---|
| **A** | `chat_agent.py` / `orchestrator.py` **均未 import `tools.experts`**（全仓 grep 仅 admin/tenancy/auth/测试命中）→ `read_expert`/`expert_router`/`resolve_mention` **零调用**，`@财报分析专家` 在对话里根本不生效 | **专家团未接入运行时** |
| **B** | `chat_agent.step()`（chat_agent.py:365）的 system 只拼 `CHAT_SYSTEM_PROMPT + 工具提示 + playbook + 知识召回 + 日期`，**不含 `build_skill_context`** → 导入的技能在研报流水线（orchestrator.py:1734）生效，在 `/chat` 读不到 | **V1 打折**：DESIGN_M12 §7 的 V1 明确要求「下一个 **/chat** 的 system prompt 实测能读到该技能」，而已有验收证据是 `build_skill_context("researcher")`，**不是 /chat** |
| **C** | `REVIEW_M12.md` 验收矩阵 V8 标 **partial**：「M12 未接入 orchestrator 运行时」 | 已知未闭环 |

补充盘点（非缺口，记录用）：前端 `/skills`、`/settings/experts` 已接路由（router/index.ts:37/60），UI 不缺。

因此 M12-4 的目标不是新增能力，而是**把已建好的东西真正接上并能实测**。

---

## 1. 目标 / 非目标

### 目标
- **G1**：`/chat` 支持专家团：显式 `@<专家名>` 必中；隐式按意图路由，**低置信回退通用 ChatAgent**（V7）。
- **G2**：`/chat` 能读到已启用/已导入的技能（补 V1 的 /chat 侧实证）。
- **G3**：任何专家包异常（缺 plugin.json / 空 md / `team` 型 / YAML 坏）**降级为「不注入」并留痕**，`/chat` 绝不 500（V8 闭环）。
- **G4**：消费注册表已有字段，不留「假配置」。

### 非目标（明确边界）
- **N1**：不改引擎流水线语义（不动 Router/Researcher/Analyst/Writer），专家的 `shape` 本档**不接入研报流水线**（见 §5 诚实边界）。
- **N2**：不新增第二套角色引擎（沿用 `DESIGN_M12 §5.1`：专家 = 上层封装）。
- **N3**：不改 `expertType: team` 的处置策略（仍默认拒绝，§5.6 待 boss 拍板的选项本档不动）。
- **N4**：不做专家头像生成、不改前端 UI（后端返回 `expert` 字段即可观测；UI 后续档再说）。

---

## 2. 设计

### 2.1 ChatAgent 接入专家（`chat_agent.py`）

在 `step()` 里、组装 system **之前**新增一个纯函数：

```python
def _resolve_expert_context(user_text: str) -> tuple[str, dict | None]:
    """显式 @ 优先，其次隐式路由；失败一律降级为 ('', None)。

    返回 (注入片段, 专家元信息)。**绝不抛异常** —— 专家是增强项，
    它的任何故障都不允许让 /chat 变 500（V8）。
    """
```

判定顺序（保守，宁可不注入也不误注入）：

1. `resolve_mention(user_text)` 命中 → 取该 id。**@ 了不存在的专家 → 不注入，且不报错**（用户可能只是在写邮箱/引用）。
2. 未命中 → `route(user_text)`（关键词打分，`best>=2` 且非并列第一才返回）→ 取 id。
3. 拿到 id → `get_expert(eid)` → `build_expert_system(expert)`。
4. 全过程 `except Exception` 兜底：`logger.warning` + `tools.experts._audit`（若可用）→ 返回 `("", None)`。

注入位置：`CHAT_SYSTEM_PROMPT` **之后**、工具提示之前 —— 人设应先于工具说明，避免工具列表淹没方法论。

返回体新增字段（可观测，前端可后续消费）：

```python
"expert": {"id": ..., "name": ..., "routed": True/False}   # 未命中专家时该字段为 None
```

`routed=False` 表示显式 @ 命中，`routed=True` 表示隐式路由命中。

### 2.2 `/chat` 注入技能（`chat_agent.py` + `tools/skills.py` 无改）

- 新增合法 role：**`chat`**（对话入口）。`tools/skill_importer.py:101`
  `VALID_ROLES = ("researcher", "analyst", "writer")` → 追加 `"chat"`。
- `build_skill_context("chat")` 注入到 system（位置：专家片段之后、工具提示之前）。
- 导入默认 `target_roles`（skill_importer.py:587）由 `["researcher"]` → **`["chat", "researcher"]`**。
  理由：DESIGN_M12 §7 V1 要求「下一个 /chat 能读到」，默认只给 researcher 等于该验收永远不达标；
  同时保留 researcher，不破坏研报链路。既有技能注册表不受影响（它们本来就只写 researcher）。
- 预算复用既有 `SKILL_CONTEXT_MAX` / `SKILL_FRAGMENT_MAX`，不新增配置项。

### 2.3 消费 `expert.model`（防假配置）

注册表条目已有 `model` 字段，UI 可填。运行时不消费 = **假配置**（boss 铁律）。
规则：**用户未显式指定模型时**，才用专家的 `model` 作为默认；用户显式指定则尊重用户。

```python
# chat_agent.py
model = _resolve_chat_model(user_model or expert_model)
```

### 2.4 补齐 MINOR（REVIEW_M12.md 残留关注点 1）

`tools/experts.py::register_expert`（:328）未对 `eid` 调 `_norm_id`，注册表 `id` 存原始字符串。
本档补 `_norm_id`，与 M2 字面要求一致（无越界写，属一致性修复）。

---

## 3. 不做的事（诚实边界，写进文档防后人误判）

| 项 | 现状 | 处置 |
|---|---|---|
| 专家 `shape` 字段 | 注册表可填，但研报流水线未接入 | **本档不消费**；在 `VERIFICATION_M12-4.md` 与文档注明「shape 供后续研报流水线档（M12-5）使用」，**不停在"填了但悄悄忽略"的状态** |
| `expertType: team` | 默认拒绝 | 维持 DESIGN_M12 §5.6，待 boss 在「直接拒绝 / 降级拆分」间拍板 |
| 专家头像 `avatar` | 包内可带，运行时不渲染 | 忽略并记审计（沿用 M12-3） |
| 前端展示「当前专家」 | 后端返回字段 | 本档不动 UI |

---

## 4. 改动清单（精确）

| 文件 | 改动 |
|---|---|
| `chat_agent.py` | 新增 `_resolve_expert_context()`；`step()` 注入专家片段 + 技能上下文（`chat`）；`model` 解析支持 expert.model；返回体加 `expert` 字段 |
| `tools/skill_importer.py` | `:101` `VALID_ROLES` 追加 `"chat"`；`:587` 默认 `target_roles` 改 `["chat", "researcher"]` |
| `tools/experts.py` | `register_expert` 的 `eid` 过 `_norm_id` |
| `tests/test_m12_runtime.py`（新） | §6 测试计划 |
| `scripts/verify_m12_e2e.py`（新） | 真实网络端到端（真 URL 导入 + 真网页提取 + `@专家`） |

---

## 5. 验收（补 DESIGN_M12 §7 的 V1 / V8，新增 R1-R6）

| # | 场景 | 期望 |
|---|---|---|
| **V1（补全）** | 导入 L0 技能后 **/chat** 的 system | **实测**含「【已启用技能：…】」（不是只断言文件存在 / 不是只测 researcher） |
| **V8（闭环）** | 专家包损坏 / team 型 / 注册表 YAML 坏 | `/chat` 正常返回，不注入专家，**不 500**，且留痕 |
| R1 | `@财报分析专家 xxx` | system 含该专家片段，`expert.routed=False` |
| R2 | `@不存在的专家` | 不注入、不报错，`expert=None` |
| R3 | 意图明确但无 @（如「杜邦分解怎么看」） | 高置信 → 注入，`expert.routed=True` |
| R4 | 模糊/并列命中 | **回退**通用 ChatAgent，不注入（V7） |
| R5 | 专家带 `model` 且用户未指定 | 用专家 model；用户显式指定时以用户为准 |
| R6 | 注册畸形专家 id（含 `..`/`/`） | 注册表存规范化 id（MINOR 闭环） |

---

## 6. 测试计划（不做纸面验收）

`tests/test_m12_runtime.py`（不触网、不打真 LLM）：
- FakeLLM 记录收到的 system；FakeBundle 注入（沿用既有 DI 契约，**不破坏** `_bundle_injected`）。
- 覆盖 R1–R6 + V1 + V8（含专家包 plugin.json 缺失、md 为空、`team` 型三种降级）。

`scripts/verify_m12_e2e.py`（真实网络，容器内跑）：
- 真 URL 导入（GitHub；宿主 raw 走 502 时自动降级 contents API，见项目坑 19）→ 断言 /chat system 可读。
- 真网页 L1：`extract_main_text` 抓真实页面 → 断言无 `<script>`/`<style>` 残留。
- 重复导入幂等（V6）。
- `@财报分析专家` → 注入成功。

---

## 7. 三道闸

① `py_compile` 全量 + 相关单测；② `VERIFICATION_M12-4.md` 自审；
③ **独立子代理** `REVIEW_M12-4.md`（主代理不自签 `reviewed-by`）。
