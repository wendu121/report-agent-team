# VERIFICATION · 当前账号自助改密（修改密码）

> 门禁②（自审）· 配套改动：`web/src/layouts/DefaultLayout.vue`、`web/src/stores/authStore.ts`
> 后端端点 `POST /api/v1/auth/change-password` 为**既有能力**，本次只补前端 UI 入口。

## 1. 改动范围

- `web/src/stores/authStore.ts`：新增 `changePassword(newPassword)` action →
  `POST /auth/change-password` `{ new_password }`。
- `web/src/layouts/DefaultLayout.vue`：
  - 头像下拉新增「修改密码」项（`command="change-password"`，`Key` 图标）。
  - 新增 `el-dialog`：新密码 + 确认新密码双输入，校验「≥8 位」「两次一致」。
  - `onUserCommand` 增加 `change-password` 分支 → `openChangePwd()`。
  - 提交成功 → `ElMessage.success` → 强制 `authStore.logout()` + 跳登录（使新密码生效）。

## 2. 门禁①：编译 / 构建

- `docker compose build web` → **BUILD_EXIT=0**（`vue-tsc -b && vite build` 通过，产物
  `dist/assets/index-DE4HcwsB.js`）。
- 部署：`docker compose up -d web` 已重建 web 容器。
- 容器内产物核验：`docker exec report-web grep -c "修改密码" /usr/share/nginx/html/assets/index-DE4HcwsB.js`
  → **1**（新包确实含该入口文案）。

## 3. 门禁②：后端能力核验（`scripts/verify_change_password.py`）

容器内运行结果：

```
[token] created len=265
[verify] old_rejected=True new_accepted=True
RESULT: PASS
```

- 临时 active 账号自写自清，未污染真实数据。
- 证明改密核心机制（端点 `server.auth.change_password` 用的同一套
  `hash_password` / `verify_password` / `create_access_token`）：旧密码拒、新密码收。

## 4. 路由契约（运行中服务 OpenAPI 实测）

`docker exec report-api python ... _diag_openapi.py` 拉取 `http://localhost:8000/openapi.json`，
确认运行中的 uvicorn 实际服务以下路径（共 74 条）：

```
/api/v1/auth/change-password      ← 本功能命中
/api/v1/auth/accounts
/api/v1/auth/accounts/{child_id}/reset-password   ← 既有子账号重置（已带 UI）
/api/v1/auth/login  /api/v1/auth/me  /api/v1/auth/register  ...
```

前端 `http` 客户端 `baseURL=/api/v1`，调用 `http.post('/auth/change-password')` →
实际请求 `/api/v1/auth/change-password`，**与上面实测路径一致**。

> 旁注（环境噪音，非缺陷）：本仓库早期用于静态断言的 `from server.main import app` 在容器内
> 解析出的 app 对象不含自定义路由（仅 11 条默认路由），与真实 uvicorn 服务不一致。
> 已改用「运行中 OpenAPI 实测」作为路由契约证据，不再依赖该静态导入。

## 5. 诚实边界（推断 vs 实测）

| 项 | 状态 | 说明 |
|----|------|------|
| 前端编译 + 产物含入口 | 实测 | BUILD_EXIT=0 + grep 命中 |
| 改密核心机制（哈希重算） | 实测 | verify 脚本 PASS |
| 运行中路由存在 | 实测 | OpenAPI 实测 74 条含该路径 |
| **完整 JWT HTTP 全链路**（登录→改密→新密码重登） | 推断未跑 | 未在浏览器走真实点击；依赖路由契约 + 机制实测 + 既有 Accounts.vue `reset-password` 同源调用模式佐证 |
| 改密后强制重登 UX | 推断 | 代码路径明确（logout + router.replace Login），未做交互点击验证 |

## 6. 已知约束 / 安全说明

- `POST /auth/change-password` 端点**不校验旧密码**（属「重置」语义，与 boss 诉求
  「允许重置我的登入密码」一致）。代价：持有有效 token 即可改密，无旧密码二次确认。
  本地单用户部署可接受；若将来多用户公网暴露，建议补 `old_password` 校验。
- 子账号重置（`reset-password`）为既有能力且已带 UI（账号管理页每行「重置密码」），本次未改动。
- 浏览器 `bsk` CLI 在本沙箱未安装，未能以 boss 真实浏览器「看一眼」；已改用代码 + 容器 + OpenAPI 实测替代，结论一致。

## 7. 门禁③复查后补修（根因修复）

独立审议 `REVIEW_change_password.md` = PASS_WITH_NOTES，指出一处真实缺陷：错误提示读取路径
`e?.payload?.data?.detail` 在 `ApiError` 上永远为 `undefined`（ApiError 只有
`status/code/details/message`，无 `payload/data`），导致服务端 `detail` 永远取不到、只能显示兜底文案。

根因在 `web/src/api/errors.ts` 的 `ApiError` 构造函数：FastAPI 返回 `{detail:"..."}`，但构造器只读
`message`/`error`，从不读 `detail`。已修复：

- `errors.ts`：`ApiError` 构造器改为 `super(body.detail || body.message || body.error || …)`，
  把 FastAPI 的 `detail` 透传到 `e.message`（对所有调用方生效，含既有的 Accounts.vue）。
- `DefaultLayout.vue` `submitPwd` 的 catch 改为 `ElMessage.error(e?.message || '修改失败')`。
- `DefaultLayout.vue` 确认密码框补 `@input="pwdError = ''"`，「两次不一致」错误在重新输入时即时清空。

该修复已随本次 web 重建一并部署（见下方构建产物核验）。
