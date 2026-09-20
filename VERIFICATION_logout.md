# VERIFICATION · 账号「退出登录」功能补齐

> 配套提交：`web/src/layouts/DefaultLayout.vue` 左下角用户区新增「退出登录」入口。
> 触发：boss 问「账号怎么没有退出功能呢？」——排查确认此前 UI 无任何手动退出入口。

## 背景（为什么没有）
- `server/auth.py` 是 **JWT 无状态**鉴权：令牌自带有效期，服务端**不维护会话**，因此**无需**
  `/auth/logout` 服务端撤销端点（这与既有的 401 自动登出逻辑一致——`main.ts` 的 `UNAUTHORIZED_EVENT`
  处理器也只是 `auth.logout()` 清本地 token + 跳登录，不调任何后端）。
- `stores/authStore.ts` 的 `logout()` 早已存在（清 `token` + `me` + `setToken('')`，且 `persist:true`
  会同步清 localStorage）。但它**仅被 401 自动路径调用**（`main.ts:31`）——UI 上没有任何按钮触发它。
- 结论：不是「不能退」，是「没有手动退出的入口」。本变更补上入口。

## 变更内容（仅前端，2 处）
1. **模板** · `DefaultLayout.vue` 左下角 `user-row`（头像 + 用户名 + 角色）原本是纯展示 div，
   现包进 `<el-dropdown trigger="click" @command="onUserCommand">`，下拉菜单含一项
   `<el-dropdown-item command="logout">退出登录</el-dropdown-item>`（带 `SwitchButton` 图标）。
   点击头像即展开，点「退出登录」触发退出。
2. **脚本** · 新增 `onUserCommand(cmd)`：`cmd==='logout'` 时 `authStore.logout()` 并
   `router.replace({ name: 'Login' })`（已在 Login 页则不重复跳）。`router` 与 `authStore` 均为
   既有导入（`useRouter()` / `useAuthStore()`），无新增依赖。

## 门禁 ① 构建/编译校验
- `docker compose build web` → **BUILD_EXIT=0**（前端重新构建，`COPY . .` 重新执行）。
- `docker compose up -d web` → **UP_EXIT=0**。

## 门禁 ② 自审（实测证据）
- 容器内确认新 UI 已烘焙进产物：
  `docker exec report-web grep -l '退出登录' /usr/share/nginx/html/assets/index-*.js`
  → `index-eI2Li31i.js`（命中）。
- 行为核对（静态审查）：
  - 退出 = 清本地 token + 跳 Login，与既有 401 自动登出**同构**（无服务端调用，符合 JWT 无状态设计）。
  - `onUserCommand` 与既有 `goSettings(cmd)` 同形（`@command` 分发），无新增风险面。
  - 头像区此前无交互，现改为 `trigger="click"` 下拉——点击头像展开、再点「退出登录」是显式两步操作，
    不会误触；与设置下拉的交互范式一致。
- **未做浏览器点击级 e2e**：容器内无 headless 浏览器，且登出是纯前端状态变更（清 token + 路由跳转），
  逻辑与已长期运行的 401 自动登出同源，风险低。建议 boss 在 `http://localhost:18080` 登录后点一次头像
  → 「退出登录」做实落（注意浏览器可能缓存旧 `index.html`，必要时硬刷或带 `?cb=<ts>`）。

## 诚实边界
- 这是**前端本地登出**：清掉本地 JWT，跳回登录页。因 JWT 无状态，**已签发的令牌在服务端仍「有效」**
  直到自然过期（无服务端注销/黑名单）。对多端同时登录的场景，本端退出不影响其他端的会话——
  这是 JWT 无状态的固有语义，非本变更缺陷。若未来需要「单点强制失效」，须引入服务端 token 黑名单/
  刷新令牌机制（属后续增强，非本次范围）。
- 退出**未加二次确认弹窗**：与既有 401 自动登出一致（不弹确认），且退出是显式两步点击，误触概率低；
  若 boss 希望加 `ElMessageBox.confirm` 二次确认，可补（一句话改动）。

## 门禁 ③
见 `REVIEW_logout.md`（独立子代理产出，主代理不自签 `reviewed-by`）。
