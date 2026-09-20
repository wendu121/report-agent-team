# VERIFICATION_M12-4 · 专家团与技能的运行时接入（自审）

> 设计依据：`DESIGN_M12-4_runtime_integration.md` v1（先设计后实现，boss 铁律）
> 三道闸：① 静态校验 ✅ ② 本文自审 ✅ ③ 独立审议见 `REVIEW_M12-4.md`（主代理不自签）

## 0. 为什么有这一档（实测盘点，非推断）

M12-1/2/3 代码齐备且 gate ③ PASS，但运行时盘点是**没接上**：

| 缺口 | 实测证据 | 性质 |
|---|---|---|
| A 专家团零调用 | `chat_agent.py` / `orchestrator.py` 全仓 grep **未 import `tools.experts`**（仅 admin/tenancy/auth/测试命中）→ `read_expert`/`expert_router`/`resolve_mention` 无人调用，`@财报分析专家` 在对话里不生效 | 纸面完成 |
| B /chat 读不到技能 | `chat_agent.py` 原 :365 的 system 只拼 `CHAT_SYSTEM_PROMPT + 工具提示 + playbook + 知识召回 + 日期`，**无 `build_skill_context`** | **V1 打折**：DESIGN_M12 §7 的 V1 要求「下一个 **/chat** 能读到该技能」，既有证据只验了 `build_skill_context("researcher")` |
| C | `REVIEW_M12.md` 验收矩阵 V8 标 partial「M12 未接入 orchestrator 运行时」 | 已知未闭环 |

非缺口（记录）：前端 `/skills`、`/settings/experts` 已接路由（router/index.ts:37/60）。

## 1. gate ① 静态校验
- `py_compile`：chat_agent.py / tools/experts.py / tools/skill_importer.py / tools/skills.py / server/api.py / server/admin.py / orchestrator.py → **exit 0**
- 前端本档无改动（不触发 vue-tsc）

## 2. 改动清单（精确）
| 文件 | 改动 |
|---|---|
| `chat_agent.py:261` | 新增 `_resolve_expert_context()`：显式 `@` 优先 → 隐式 `route()` → **任何异常降级为不注入**（绝不冒泡，V8） |
| `chat_agent.py:410-420` | `step()` 注入 专家片段 + 技能上下文（`build_skill_context("chat")`），位置在 `CHAT_SYSTEM_PROMPT` 之后、工具提示之前（人设先于工具说明） |
| `chat_agent.py:448` | `chat_model = _resolve_chat_model(model or expert.model or None)` —— 消费注册表 model 字段，避免「能填但不消费」的假配置；用户显式指定时以用户为准 |
| `chat_agent.py:496` | 返回体加 `"expert": {"id","name","routed","model"}`（未启用为 None），可观测 |
| `tools/skill_importer.py:104` | `VALID_ROLES` 追加 `"chat"`（无它则 V1 的 /chat 侧永不成立） |
| `tools/skill_importer.py:591` | 导入默认 `target_roles` → `["chat", "researcher"]` |
| `tools/experts.py:335` | `register_expert` 的 `eid` 过 `_norm_id`（闭环 `REVIEW_M12.md` 残留 MINOR-1） |

## 3. gate ② 实证

### 3.1 单测（12 条，全绿）
`tests/test_m12_runtime.py`：R1 显式 @ 注入 / R2 未知 @ 不注入 / R3 隐式高置信注入 /
R4 低置信回退（V7）/ V8 包损坏降级 / V8 team 型降级 / V8 `list_experts` 抛异常不冒泡 /
V1 技能进 /chat / 反向（仅 researcher 的技能不污染对话）/ R5 专家模型偏好 / R5 用户模型优先 / R6 id 规范化。

**全量：278 passed / 0 failed / 0 errors（exit=0）**（266 原有 + 12 新增）。

### 3.2 真实网络端到端 `scripts/verify_m12_e2e.py`（一次性租户根，不污染真实 config）
| 步 | 结果 |
|---|---|
| 真实 GitHub raw 导入 → 审批 → /chat 可读 | **PASS**：`status=pending_approval`（真 README 含外链/代码块 → 保守判 L1，设计如此）→ `accept` → installed → **ctx 5920 字符**，target_roles=['chat','researcher'] |
| 重复导入幂等（V6） | PASS：两次 id 一致 |
| 真实网页正文提取（V2） | **SKIP**：宿主侧 `github.com` HTML 不可达（同一时刻 raw.githubusercontent.com 通）→ 按脚本约定记 SKIP，**不计为 PASS**（诚实） |
| `@专家` 解析（R1） | PASS：`@financial-statement-analyst` → 1078 字符专家片段，`routed=False` |

### 3.3 容器侧
镜像已重建并 `up -d api`（build 10s，命中缓存层）。容器内 `_resolve_expert_context` 可用。
⚠️ 跑 e2e 当时**容器整体 HTTPS 出网握手超时**（对照组 open-meteo 同样超时，非 GitHub 专属），
故真实网络段改在宿主跑。**这正是项目坑 15 的教训：单次日志不可判定「网络不通」，必须带对照组复核。**

## 4. 诚实边界（不夸大）
- **专家 `shape` 字段仍未消费**：注册表可填，但研报流水线未接专家（本档 N1 明确不做）。
  不停在「填了却悄悄忽略」——在此如实标注，留给后续档。
- **`expertType: team`** 维持 DESIGN_M12 §5.6 默认拒绝，待 boss 在「直接拒绝 / 降级拆分」间拍板。
- **V2 真实网页提取未实测**（网络不可达，SKIP）；单测也未覆盖真实 HTML 提取路径（沿用 M12-1 状态）。
- 前端未展示「当前专家」（后端已返回字段，UI 后续档）。

## 5. 结论
gate ① ✅、gate ② ✅（278 全绿 + 真实网络 V1/V6/R1 通过）。提交独立子代理审议（gate ③）。
