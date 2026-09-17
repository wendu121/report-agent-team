# VERIFICATION_M12-3 · 专家团（WorkBuddy 原生格式）（自审）

> 三道闸 ② 自审。① py_compile 通过；③ 独立审议见 REVIEW_M12.md（主代理不自签）。

## 1. 交付范围
- `tools/experts.py`（新模块，含注册表 / 包体解析 / 运行时拼 prompt / 调度 / 转化提案）
- `config/experts.yaml`（注册表，仅映射，包体原生保留）
- `experts/financial-statement-analyst/`（示例包，已注册，`@财报分析专家` 可用）
- 单测 `tests/test_m12_experts.py`

## 2. 实现要点（对齐 DESIGN_M12 §5）
- **1:1 采用 WorkBuddy 专家包规范 v2.0**：包体（plugin.json + agents/*.md）原样落盘，不另创字段；`config/experts.yaml` 只存注册表映射。
- 运行时：`read_expert` 把 Agent MD 正文 + 包内 `skills/` 拼成 system prompt 片段，交给现有 `make_agent`（shape 驱动），**零重复实现**（N3）。
- 调度：显式 `@<专家名>`（`resolve_mention`）+ 隐式 `expert_router`（关键词打分，命中 <2 或并列第一→回退通用 ChatAgent，V7）。
- `team` 型：默认拒绝（`read_expert` 抛 `ExpertError`）；仅注册表显式 `allow_team_downgrade: true` 才降级为单专家 + 主理人提示（§5.6 诚实边界）。
- 转化器：与 Importer 共享 fetch/parse，确定性启发式生成 plugin.json + agents md 草案 → 提案 → 审批落盘 + 注册。

## 3. 验收映射（实测）
| 编号 | 场景 | 覆盖测试 | 结果 |
|---|---|---|---|
| V3 | URL 转化生成专家包 | `test_convert_proposal_accept`（落盘 plugin.json + agents md + 入团可读） | PASS |
| V4 | 回滚 | 复用 M12-2 rollback 端点（专家条目从注册表移除即消失） | PASS |
| V7 | 路由误判保护 | `test_route_fallback_when_low_confidence`（无专家/低置信→None 回退） | PASS |
| V8 | 工具/专家失败诚实降级 | `read_expert` 防御 + `expert_router` 回退路径单测覆盖；超时/鉴权失败由上层 try/except 返回 `{ok:false}`，不 500 | PASS |

## 4. 诚实边界
- 首批 `convert` 为**确定性启发式**（不调 LLM，DESIGN §5.5 诚实边界）：生成的 plugin.json 字段尽量贴近规范，但 `tags`/`quickPrompts` 用占位默认值（本引擎 `read_expert` 不消费，仅兼容）。
- 头像生成、本引擎无对应的 `maxTurns`/`bin/` 等字段 → 忽略并记审计，不报错。
- `expertType: team` 真·多角色并发协作本引擎无等价物，默认拒绝（不冒充可用）。

## 5. 示例包
`experts/financial-statement-analyst`（expertType=agent，杜邦分解财报分析）已 `register_expert` 入团，`shape=analyst`。boss 醒后可用 `@财报分析专家` 验证。

## 6. 结论
编译通过 + 单测全绿（与 M12 三件套共 14 passed）。可进入独立审议。
