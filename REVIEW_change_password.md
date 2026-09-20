<!-- reviewed-by: independent-subagent (gate-③) -->
# Gate-③ 审查报告：当前账号自助改密

审查对象（3 个变更文件）：
- `web/src/stores/authStore.ts`
- `web/src/layouts/DefaultLayout.vue`
- `scripts/verify_change_password.py`

## 结论：PASS_WITH_NOTES

前端改动与描述一致，构建（vue-tsc + vite）、部署与后端验证（gate-② 脚本 PASS、OpenAPI 确认 `/api/v1/auth/change-password`）均通过，主流程可用。存在 1 个真实的小缺陷（错误提示文案提取路径错误）与若干低风险/低风险 UX/安全提示，均不阻断发布。

---

## 改动核对（与描述一致性）

| 项 | 位置 | 结论 |
|----|------|------|
| `changePassword` action | `authStore.ts:64-66` | 一致，`http.post('/auth/change-password', { new_password })` ✓ |
| `Key` / `ElMessage` 导入 | `DefaultLayout.vue:209-210` | 一致；`Key` 为合法具名导出（vue-tsc 构建通过即证明，缺导出会报 TS2305）|
| 头像下拉新增「修改密码」项 + `Key` 图标 | `DefaultLayout.vue:125-128` | 一致，绑定 `command="change-password"` ✓ |
| `el-dialog`（v-model `pwdVisible`、标题、取消/确定、destroy-on-close + `@closed`） | `DefaultLayout.vue:172-197` | 一致 ✓ |
| 两个密码框 + ≥8 位/一致性校验 | `DefaultLayout.vue:175-191, 370-377` | 一致 ✓ |
| `openChangePwd / submitPwd / onPwdClosed` | `DefaultLayout.vue:362-399` | 一致 ✓ |
| `onUserCommand` 处理 `change-password` → `openChangePwd` | `DefaultLayout.vue:407-409` | 一致 ✓ |
| `logout` 分支仍正确 | `DefaultLayout.vue:402-406` | 一致，未被破坏 ✓ |
| `ElMessageBox` 导入已移除 | 全文 grep 无匹配 | 确认不再被引用（无 TS6133 风险，构建已证明）|

---

## 问题清单

### MINOR（小缺陷，建议修复但不阻断）
1. **错误提示文案永远走兜底，服务端 detail 丢失**
   - `DefaultLayout.vue:390`：`ElMessage.error(e?.payload?.data?.detail || '修改失败')`
   - 实际抛出的异常是 `ApiError`（构造链：`client.ts:66` → `errors.ts:4-15`）。`ApiError` 只有 `status / code / details / message` 属性，**没有 `payload` 也没有 `data`**，因此 `e?.payload?.data?.detail` 恒为 `undefined`，任何错误都只显示「修改失败」，拿不到后端 `detail`（如「密码至少 8 位」等）。
   - 补充：响应拦截器把 FastAPI 的 `detail` 以 `Partial<ErrorResponse>`（`message/error/details`）转型后传入 `ApiError`，`ApiError` 的 `message` 取 `body.message || body.error`，故 `detail` 也未被带入 `e.message`（仅得到 `HTTP <status>`）。这是更早存在的客户端基础设施限制，非本次引入，但本次代码用了错误的属性路径，使失败体验更差。
   - 建议：`ElMessage.error(e?.message || '修改失败')`；若要显示真实 `detail`，需在 `client.ts` 拦截器里把 `raw.detail` 写入 `ApiError`（如 `details.detail` 或 message 优先）。

2. **`pwdError` 仅在「新密码」输入框 `@input` 时清空**
   - `DefaultLayout.vue:180`：`@input="pwdError = ''"` 只挂在 `newPwd` 上。
   - 当错误为「两次输入的密码不一致」时，用户只修改「确认新密码」框不会即时清除错误，需再次点击「确定」重校。纯 UX 瑕疵，无功能影响。

### NOTE（安全/UX 提示，设计为「重置语义」，可接受但需知悉）
3. **后端不校验旧密码**（`server/auth.py:~282`，非本次改动）
   - 端点仅 `Depends(get_current_account)` + 重算 `password_hash`，属重置语义。对本地单用户部署可接受；但意味着只要会话保持登录，任何人都能改密（共享机器上有锁死原主风险），且子账号可改自己的密码（符合预期）。
4. **强制重新登录仅清客户端 token，服务端 JWT 未失效**
   - `DefaultLayout.vue:384-388`：改密成功后 `authStore.logout()` + `router.replace({name:'Login'})` 只清本地 `rat_token`。旧 JWT 在服务端仍有效至过期；若同一账号在其它标签页已登录，会话不会立即失效。本地单用户场景影响极小，已用注释诚实说明。

### 已确认无问题（回归项）
- `onUserCommand` 的 `logout` 分支逻辑完整、未被改动破坏。
- 对话框可安全关闭：取消按钮 / 点遮罩 / 右上角 X 均触发 `pwdVisible=false` → `@closed="onPwdClosed"` 重置表单；改密仅在 `submitPwd` 成功时发起，关闭不产生副作用。
- `verify_change_password.py` 自写自清临时账号、断言旧拒新收，逻辑自洽；它验证的是改密核心机制（与端点同函数），真实路由存在性已由运行服务 OpenAPI 实测覆盖。

---

## 一句话总结
前端改密入口与对话框接线正确、构建与后端验证均通过，但失败提示用了错误的异常属性路径导致服务端错误信息永远丢失（小缺陷），旧密码不校验与强制重登为已知的重置语义取舍，整体可发布（PASS_WITH_NOTES）。
