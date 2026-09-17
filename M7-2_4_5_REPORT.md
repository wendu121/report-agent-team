# M7-2 / M7-4 / M7-5 页面深化报告

> 日期：2026-09-05
> 范围：M7 前端剩余页面（M7-1 脚手架 + M7-3 时间线 + M7-6 联调已先期完成）
> 结论：**M7 全部 6 个里程碑（M7-1~M7-6）落地，typecheck 0 error、vite build 成功（1713 模块）**

## M7-2 模板选择 + 任务提交页

- 骨架在 M7-1 已功能完整，本次仅确认通过，无重写。
- `template.ts` 静态硬编码 `standard_research` 模板（v1 不接后端 manifest），`templates` 常驻非空，`TemplateSelect` 无需 `onMounted loadTemplates`。
- `TaskSubmit`：表单 + `watch` 按行解析 scope/constraints + `task.createTask` → 跳 `TaskTracking`，含未选模板告警、提交错误回显。

## M7-4 研报结果页（深化）

对标 M7_FRONTEND_DESIGN §7.4，在原有 DOMPurify XSS 清洗 + 审计包下载基础上**补齐富 UI**：
- 任务 meta 卡：主题 / 状态（StatusBadge）/ 完成时间（`formatTime`）/ 升级原因。
- 研报正文卡：marked 渲染后经 `DOMPurify.sanitize` 清洗（设计安全红线，不可省略），下载按钮移入卡头。
- 评审留痕卡：折叠 `routing_state.gate_review_history`，`el-timeline` 展示每道闸的 decision 标签（放行/打回/升级 → success/warning/error）、评分、reason、problem_points。
- 终态：`escalated` 显示「前往人工复核」、返回追踪按钮。

## M7-5 人工复核页（深化）

- 升级上下文卡：主题 / 升级原因 / 最近闸（`routing_state.last_gate`）。
- 决策卡：`confirm` / `retry` / `abort` 单选；**retry 时显示重跑目标 Agent 下拉**（Researcher/Analyst/Writer），对应 `ReviewAction.rework_target_agent` 字段（缺省 Writer）。
- 提交：调用 `submitReview` → 跳回 `TaskTracking`。

## 遵守的纪律（对齐已审议清单）

- 强类型、无 any；`GateDecision` / `AgentRole` / `ReviewAction` 全部引用 `@/types`。
- Markdown 渲染必过 DOMPurify（安全红线）。
- 拦截器不调 store，错误由组件层 `taskStore.setError` 捕获回显。

## 验证

- `vue-tsc --noEmit`：**0 error**
- `vite build`：**成功，1713 模块**（仅 chunk>500kB 体积告警，非错误；后续可 manualChunks 优化，不在本次范围）
- M7-6 联调探针（tests/integration_m76.py 28/28）仍有效：本次仅改 UI 渲染，REST/WS 契约未动。
