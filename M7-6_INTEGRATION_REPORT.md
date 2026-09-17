# M7-6 前后端联调报告

> 日期：2026-09-05
> 范围：M7 前端（web/）↔ M1–M6 已冻结后端契约（server/，API_SPEC.md v1.0）
> 结论：**全链路联调通过**，前端类型与后端真实 wire format 完全吻合。

## 验证架构

为避免「同事件循环内 httpx 争用」「跨进程回放触达不到真实 WS」「本地代理 env 污染直连」三类假象，采用三层正交验证：

1. **REST 真机契约**：独立子进程起真实 uvicorn（`server.main:app`），外部 httpx 客户端 `trust_env=False` 直连 → 排除代理 env（127.0.0.1:10808）干扰。
2. **WS 真实握手**：真实 `websockets.asyncio.client` 连后端（及经 vite 代理）→ 收真实 `connection_established`。
3. **WS 事件形态零编造**：直接实例化 server 自带 Event 模型并 `.dict()`——这正是 `emit_*` 广播的字节，逐字段比对 `web/src/types/engine.ts`，不改 server 码、不跨进程。

## 结果清单

### ① REST 契约（探针 28/28 全过 + 真机复现）

| 项 | 方法 | 期望 | 实测 | 结论 |
|---|---|---|---|---|
| 创建任务 | POST /api/v1/tasks | 201 + task_id | 201 + 13 字段 TaskResponse | ✅ |
| 查询任务 | GET /api/v1/tasks/{id} | 200 + 全字段 | 200 + 13 字段齐全 | ✅ |
| 查不存在任务 | GET /api/v1/tasks/{bad} | 404 TASK_NOT_FOUND | 404 | ✅ |
| 健康检查 | GET /health | 200 | 200 | ✅（注意：在根路径，不在 /api/v1 下）|

### ② WS 事件流（前端 engine.ts ↔ 后端 websocket.py）

`connection_established` + 7 类业务事件（agent_complete / gate_complete / rework_trigger / round_update / tool_error / task_done / task_escalated）全部验证：

- payload **平铺顶层**（非 `data` 包裹），与 M7-1/M7-3 已对齐的 engine.ts 一致；
- 必填平铺字段 `timestamp / status / audit_url`（task_done、task_escalated）均在 wire 中恒定存在；
- 经 vite 代理（`ws: true`）握手实测收到真实 `connection_established`。

### ③ Vite 代理（前端 dev 真机）

- 前端 dev server（5173）根页 200；
- 代理 POST /api/v1/tasks → **201**（与直连一致），GET /api/v1/tasks/{id} → 200；
- `/api/v1/health` 返回 404 属**预期**：后端 health 在根 `/health`，此 404 恰恰证明代理已转发到后端；
- WS `ws://127.0.0.1:5173/api/v1/tasks/{id}/stream` → 真实 connection_established。

## 关于「422」假象的说明（诚实记录）

早期一次探针里「经 vite 代理 POST 返回 422」并非代码缺陷，而是**测试夹具假象**：当时用 `npm.cmd run dev` 后台启动 vite，在 Git Bash 下 `.cmd` 孙进程拖垮进程组、vite 未就绪即被探测，导致请求未真正走代理。改用 `node web/node_modules/vite/bin/vite.js`（正确 cwd=web/）重测后，代理 POST 稳定返回 **201**。结论：**vite 代理配置正确，无需修改 vite.config.ts**。

## 契约漂移修复回顾（M7-1/M7-3 已落地，本次联调确认生效）

1. WS 路径：`/ws/{id}` → `/api/v1/tasks/{id}/stream`，vite 代理统一 `/api` 含 `ws: true`；
2. WS payload：由 `data` 包裹改为顶层平铺（engine.ts / timeline.ts / task.ts 全部对齐 API_SPEC §3.2）；
3. 依赖：pinia `^2.3.0` → `^3.0.1`（匹配 pinia-plugin-persistedstate@4 peer 依赖）。

## 遗留 / 下一步

- 真实引擎事件流（agent_complete 等中间事件）由 `orchestrator.py` 实时调用 `emit_*` 产生，依赖 new-api 网关真实 LLM 调用，**不在本次联调范围**（联调用 server 自带 Event 模型做无副作用回放验证形态）。
- 可选：M7-2/4/5 页面骨架已在 M7-1 落地，可继续深化；或起真实引擎做全链路跑通（需真实 LLM 网关配额）。
- 回归测试：`tests/integration_m76.py`（client-only，需先起 server，读 `M76_PORT` 环境变量）。

## 验证命令（可复现）

```bash
# 后端
python -m uvicorn server.main:app --app-dir . --host 127.0.0.1 --port 8000
# 前端（务必 cwd=web/）
cd web && node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
# 联调探针
M76_PORT=8000 python tests/integration_m76.py
```
