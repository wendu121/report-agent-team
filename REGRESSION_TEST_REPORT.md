# 回归测试小结 · 引擎异常鲁棒性

> 项目：report-agent-team · 研报多 Agent 协作系统（引擎层）
> 日期：2026‑09‑05
> 关联里程碑：**M5 已闭环**（真网关端到端跑通 `status=done`）+ **B 方案最小异常回归用例集**
> 模式：**全部 mock**，不依赖外部 API（不注入 TAVILY_API_KEY / 不真打 new-api），排除外部不确定变量

---

## 0. 目的

M5 端到端实跑撞出 5 个真 bug（下游 Agent 未注入上游 state、GateC 评估错位、无请求超时挂死、ToolError 未导入、doc_export 类体错位），**无一被原有 25 个单测覆盖**。单测只验证"代码符合我预期"，验证不了"系统在真实混沌下会不会骗人"——而本系统核心价值主张正是**防编造 + 可审计**。

本用例集专补**零覆盖的高危异常路径**，使引擎侧运行时契约充分冻结，之后再谈 M6（HTTP/WebSocket 服务层 + Vue3 前端对接）。

---

## 1. 测试策略

- 用**可控假 LLM**（`LLMClient` 子类）注入确定性 JSON / 异常 / 牌局序列，不触真网关。
- 覆盖两类信号：
  - **正向**：系统在异常下应走的降级/escalate 路径正确发生；
  - **负向**：正常数据下不应误触发降级/重建（防止过防御）。
- 引入**变异测试（mutation testing）**验证用例非空转——故意植入 bug，确认对应用例断言失败；若用例仍"通过"则说明是走过场。

---

## 2. 用例清单（5 场景 · 11 函数 · 16 例）

| # | 场景 | 测试函数 | 类型 | 验证点 |
|---|---|---|---|---|
| ① | 闸 LLM 连续不可用 + 代码硬校验通过 → 降级放行 | `test_gate_llm_unavailable_degrades_to_advance_with_audit` | 正向 | `decision=advance` + `engine_events` 必含 `gate_llm_unavailable_degraded_advance`（带 `gate`/`round`） |
| ① | 同上，但代码硬校验判 rework → 不得降级 | `test_gate_llm_unavailable_does_not_degrade_when_code_says_rework` | 负向 | 代码 rework 优先，仍走 rework 路径 |
| ② | 检索全空 → GateA 强制 escalate | `test_empty_retrieval_escalates_despite_llm_advance` | 正向 | 即使 LLM 判 `advance`，代码 `escalate` 优先级压过，**不得放行** |
| ③ | `max_rounds` 耗尽 → escalate + 边界断言 | `test_max_rounds_exhausted_escalates_with_bounds` | 正向 | `status=escalated` + `round==max_rounds`（不越界、不多跑一轮） |
| ④ | 引用章节缺失 url / 裸 id / 伪造 url | `test_bad_citation_section_is_rebuilt_and_audited`（参数化 ×3） | 正向 | doc_export **确定性重建** + `engine_events` 含 `writer_citation_regenerated` |
| ④ | 引用章节已完整（id+url 齐全）→ 不重建 | `test_valid_citation_section_is_not_regenerated` | 负向 | `regen=False`，无多余事件 |
| ⑤ | 瞬断型 429 → 重试 | `test_transient_429_is_retried` | 正向 | 进入退避重试分支 |
| ⑤ | 配额耗尽型 429 → 不重试 | `test_quota_exhausted_429_is_not_retried` | 正向 | 立即放弃（`_is_non_retryable`），不烧额度 |
| ⑤ | 鉴权/模型不存在等非重试错误 → 立即中止 | `test_non_retryable_errors_abort_immediately` | 正向 | 多消息样本均不重试 |
| ⑤ | 瞬断错误耗尽重试 → 抛 LLMError | `test_transient_error_exhausts_retries_then_raises` | 正向 | 触顶后抛错交由编排器 rework/escalate |
| ⑤ | `_is_non_retryable` 辅助判定单测 | `test_is_non_retryable_helper` | 单元 | 配额/鉴权/模型缺失=不可重试；rate limit/timeout=可重试 |

文件：`tests/test_regression_edge.py`（新增 16 例）

---

## 3. 变异测试结论（用例非空转）

| 植入变异 | 受影响用例 | 结果 |
|---|---|---|
| 撤销 GateA 空检索 escalate | ② | ✅ 断言失败（正确抓到） |
| 关闭 doc_export 引用章节重建 | ④（×3） | ✅ 断言失败（正确抓到） |
| 降级放行不写 `engine_events` | ① | ✅ 断言失败（正确抓到） |
| `_is_non_retryable` 恒 False（配额也重试） | ⑤ | ✅ 断言失败（正确抓到） |
| `_is_non_retryable` 恒 True（瞬断也放弃） | ⑤ | ✅ 断言失败（正确抓到） |

**结论：5 组用例均经变异验证为有效断言，非走过场。**

---

## 4. 全量结果

```
pytest tests/  →  41 passed in 4.53s
（原有 25 单测 + 新增 16 异常回归，零失败）
```

---

## 5. 引擎运行时契约冻结声明

经 M5 实跑闭环 + 本回归集，以下运行时契约**已冻结**，可作为后续 M6 服务层/前端对接的稳定接口面：

1. **三道闸优先级铁律**：代码 `escalate` > 代码 `rework` > 代码 `advance`（硬校验通过）> LLM 判定。LLM 不得覆盖代码硬校验结论。
2. **降级放行契约**：闸 LLM 不可用 + 代码硬校验通过 ⇒ 放行，但**必须**在 `engine_events` 留 `gate_llm_unavailable_degraded_advance`，供调试视图审计；业务叙事视图过滤。
3. **空检索契约**：`retrieval_records` 为空 ⇒ GateA 代码 `escalate`，不可被 LLM advance 绕过。
4. **引用链契约**：`report_markdown` 引用章节须覆盖全部 `retrieval_records` 的 **id 与 url**；缺失/伪造由 `doc_export` 确定性重建，并留 `writer_citation_regenerated`。
5. **重试契约**：瞬断型（rate limit/timeout/掉线）重试并退避；配额耗尽/鉴权/模型不存在**不重试**，立即换通道。
6. **超时契约**：单次 LLM 调用 `timeout=120s`（默认），防止 flaky 上游无限挂死。
7. **引擎事件序列**：`engine_events[]` 共 3 种（`agent_output_unusable` / `writer_citation_regenerated` / `gate_llm_unavailable_degraded_advance`），定义见 `memory/schema.md §4.1`，UI 映射见 `UIDESIGN.md §8`。

> 冻结范围：**引擎层运行时行为 + 状态 schema**。锁版文档（DESIGN/UIDESIGN）契约正文未改，仅 append‑only 追加修订记录与字段映射。

---

## 6. 已知边界 / 待办

- **内容仍是 mock**：未注入 TAVILY_API_KEY，检索为占位数据（url `mock.local`）。真实调研内容需后续补 key。
- **M6 未启动**：引擎尚无 HTTP/WebSocket 服务层，前端对接冻结于 UIDESIGN 设计文档，待本契约稳定后开启。
- **可选增强（非阻塞）**：真实流量下补充「Gate 连续 rework 同一问题」的死循环护栏；多上游通道自动 failover（非单纯人工重映射 model_mapping.yaml）。
