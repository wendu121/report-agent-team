<!-- reviewed-by: independent-subagent (gate-③) -->

# 独立复核（Gate ③）— `report-agent-team`

**VERDICT: PASS_WITH_NOTES**

所有 5 个文件的改动均已逐行核验，Python 语法编译通过（exit 0），核心缺陷修复逻辑成立。下方为逐项证据与残留风险。

---

## 一、后端：`server/admin.py`

### (1a) `_models_meta` 无限递归热修 — ✅ 已修复
- `admin.py:1656` 定义访问器 `_models_meta()`，内部通过 `_MODELS_META.get(key)` 读取缓存（`admin.py:1658`）。
- `admin.py:1668` 现为 `_MODELS_META[key] = m`（直接写真实缓存 dict），**不再**是 `_models_meta()[key] = m`。确认递归已消除。
- 残留用法 `admin.py:1722-1809` 的 `_models_meta()["gateway_ok"] = ...` 等调用**正确**：访问器返回 `_MODELS_META[key]` 的同一 dict 引用，再对其设键，不会重新触发函数，无递归风险。

### (1b) ContextVar + Bearer 回退 — ✅ 正确
- `admin.py:32` `import contextvars`；`admin.py:40` `from fastapi import APIRouter, Header, HTTPException, Request`（`Request` 已导入，类型标注可用）。
- `admin.py:154-156` 模块级 `_current_request: ContextVar[Optional[Request]] = contextvars.ContextVar("rat_current_request", default=None)`，声明于 `_require_admin`（`admin.py:159`）之前，满足顺序要求。
- `admin.py:177` `req = request if request is not None else _current_request.get(None)`；`admin.py:178` `auth_header = req.headers.get("authorization") if req is not None else None`。手动调用 `request=None` 时正确回退到 ContextVar 读取 Bearer。

## 二、后端：`server/main.py`（中间件注入）
- `main.py:11` `from starlette.middleware.base import BaseHTTPMiddleware`。
- `main.py:76-84` 类 `_RequestContextMiddleware(BaseHTTPMiddleware)`，`dispatch` 内 `from .admin import _current_request`（`main.py:78`）、`_current_request.set(request)`（`main.py:80`）、`finally: _current_request.reset(ctx_token)`（`main.py:84`）。
- `main.py:87` `app.add_middleware(_RequestContextMiddleware)` 已注册。
- `main.py:9` `from fastapi import FastAPI, Request`（`Request` 已导入，标注可用）。
- **核心修复正确性**：Starlette `BaseHTTPMiddleware` 在 `dispatch` 内用 `contextvars.copy_context()` 运行，并经由同一 context 调用 `call_next` 下游（含端点），故 `dispatch` 中 `set` 的值对端点内 `_require_admin` 可见。`requirements.txt:11` `fastapi>=0.104` 拉起 starlette>=0.27（anyio 任务组，上下文正确传播），该行为成立。✅

## 三、前端：`web/src/api/client.ts`
- `client.ts:24-25` 导出 `UNAUTHORIZED_EVENT` 与 `FORBIDDEN_EVENT`。
- `client.ts:53` `const raw = (error.response?.data ?? {}) as Record<string, any>;`（因 `ErrorResponse` 无 `detail` 字段，故转 `Record<string,any>` 取原始响应体，逻辑合理）。
- `client.ts:54-59` 401 分支：清 token + `dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))`（保持旧行为）。
- `client.ts:60-64` 403 新增分支：`const msg = typeof raw.detail === 'string' ? raw.detail : (typeof raw.message === 'string' ? raw.message : '')`，`dispatchEvent(new CustomEvent(FORBIDDEN_EVENT, { detail: msg }))`。无 TS 类型错误，逻辑成立。✅

## 四、前端：`web/src/main.ts`
- `main.ts:10` `useAuthStore`；`main.ts:11` `UNAUTHORIZED_EVENT, FORBIDDEN_EVENT` 均从 `./api/client` 导入（与 client.ts 导出一致）。
- `main.ts:29-33` UNAUTHORIZED → `auth.logout()` + `router.replace({ name:'Login', query:{ redirect: router.currentRoute.value.fullPath } })`。
- `main.ts:36-38` FORBIDDEN → `const detail = (e as CustomEvent).detail; ElMessage.warning(typeof detail === 'string' && detail ? detail : '无权限访问该资源')`，与 client.ts 的 `detail` 载荷对齐。✅

## 五、前端：`web/src/views/Login.vue`
- `Login.vue:86` `const acc = await auth.register(...)`；`Login.vue:88` 分支 `acc.role==='main' && acc.status==='active'` → 文案「您是本系统的主账号，已自动激活，请用相同账号密码登录」；`Login.vue:92` 否则「等待主账号审批」。
- 返回契约匹配：`authStore.ts:44-46` `register` 返回 `Promise<AccountInfo>`（`role: data.role, status: data.status`）；后端 `auth.py:196 register` 与 `auth.py:185-186` 返回 `{role: acc.role, status: acc.status}`；`auth.py:212,214` 首个账号为 `role="main", status="active"`。故主账号分支**可命中**，非死代码。✅
- `Login.vue:47-49` 提示文案已如实说明「首个注册账号自动成为主账号并立即生效；其余为待审批」。✅

## 六、回归 / 残留风险核查
- **调用点回归**：`admin.py` 中 ~50 处 `_require_admin(x_admin_token)`（`x_admin_token` 仅作 `Header` 注入）现全部通过 ContextVar 回退读取 Bearer，无需 `request` 入参。未发现有调用点依赖 `request` 非 None（如 `req.headers` 之外的 request 属性），回退路径完整。
- **WebSocket 不受影响**：`main.py:91` `ws_router` 单独挂载；`BaseHTTPMiddleware` 仅包裹 HTTP scope，不进入 websocket 处理，admin 鉴权与 WS 互不干扰。
- **并发陷阱（需注意，非阻断）**：`contextvars` 为任务级隔离，uvicorn 默认 asyncio 下每请求独立任务，配合 `finally reset`，无跨请求串号。唯一边界：若某端点用 `asyncio.create_task()` / `BackgroundTasks` 把工作派发到**新任务**，新任务不继承该 context，ContextVar 将不可见——但 `_require_admin` 在端点内同步调用，早于任何任务派发，不受影响。
- **版本依赖（已满足）**：ContextVar 在 BaseHTTPMiddleware dispatch→端点间可见性依赖 Starlette≥0.14 的上下文传播修复；当前 `fastapi>=0.104` ⇒ starlette≥0.27，已满足。若未来将 Starlette 锁到极旧版本，该修复会静默失效（admin 端点重新 401）——属监控项，非当前缺陷。

## 结论
所有自报改动均经独立文件核验属实，Python 三文件 `py_compile` 通过（exit 0），核心 admin Bearer 缺陷通过「ContextVar + 每请求中间件注入」得到正确修复，前端三处改动内部一致且类型安全。判定 **PASS_WITH_NOTES**：实现正确，仅建议留意上述 Starlette 版本依赖与 contextvars 任务边界两项非阻断事项。
