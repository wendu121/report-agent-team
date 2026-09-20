# REVIEW · 账号「退出登录」功能（独立代码审查 · gate-③）

> 审查对象：`web/src/layouts/DefaultLayout.vue`（模板 + 脚本）新增的手动登出入口。
> 基准对照：`web/src/stores/authStore.ts` 的 `logout()`、 `web/src/main.ts` 的 401 自动登出逻辑、
> `VERIFICATION_logout.md` 的 gate-② 自审声明。
> 审查方式：**静态审查**（未运行构建、未做浏览器点击级 e2e——本机/容器内无 headless 浏览器，与 gate-② 一致）。

## Verdict: PASS_WITH_NOTES

变更在功能、结构、一致性上均正确，无 MAJOR 问题，风险与 gate-② 自评一致（微小、低危、镜像既有模式）。
存在 2 处 MINOR（均不阻塞），属诚实性/一致性说明层面的补充，而非代码缺陷。详见下方表格与建议。

## 分项审查表

| 区域 | 行号 | 严重度 | 结论 / 备注 |
|------|------|--------|------|
| `@command` 绑定正确 | `DefaultLayout.vue:115` | PASS | `<el-dropdown trigger="click" @command="onUserCommand">` 已正确接线；Element Plus 的 `command` 事件在 `el-dropdown-item` 被点击时触发，绑定无误。 |
| `command="logout"` 与判断一致 | `DefaultLayout.vue:125` ↔ `:324` | PASS | item 的 `command="logout"` 与 `if (cmd === 'logout')` 完全一致，无拼写/大小写错位。 |
| `onUserCommand` 逻辑 | `DefaultLayout.vue:323-330` | PASS | `cmd==='logout'` 时先 `authStore.logout()` 再 `router.replace({ name: 'Login' })`；`router.currentRoute.value.name` 是 Vue Router 4 下对当前路由 ref 的正确访问方式。 |
| 防重复跳转 / 死循环 | `DefaultLayout.vue:326` | PASS | 守卫 `router.currentRoute.value.name !== 'Login'` 存在且正确；已在 Login 页时不重复 replace，无重定向环。 |
| 既有渲染保留（user-row） | `DefaultLayout.vue:116-122` | PASS | 原纯展示 `<div class="user-row">`（头像 + 用户名 + 角色）作为 dropdown 触发插槽内容**完整保留**，avatar/name/role 仍渲染。 |
| CSS/结构未破坏 | `:630-660`、`:740` | PASS | `.user-row` 仍 `display:flex` 子元素，外层 `el-dropdown` 为块级包装，宽度撑满；响应式 `@media (max-width:860px)` 中 `.user-row` 居中等规则仍然命中。布局等价。 |
| `authStore` / `router` 已在作用域 | `:181`、`:186` | PASS | `const router = useRouter()` 与 `const authStore = useAuthStore()` 早已存在，无新增依赖、无未定义符号。 |
| `SwitchButton` 图标无需 import | `main.ts:22-25` | PASS | `main.ts` 通过 `for (... of Object.entries(ElementPlusIconsVue)) app.component(key, component)` **全局注册了所有图标**，`SwitchButton` 已为全局组件，模板 `<el-icon><SwitchButton/></el-icon>` 可用，无需在组件中 import。已核实。 |
| 与 401 自动登出一致性 | `main.ts:29-35` ↔ `:323-330` | PASS（含 MINOR） | 两者均为「清本地 token + 跳 Login、不调服务端」，与 JWT 无状态设计一致。差异见 M2。服务端撤销属后续增强，超出本次范围。 |
| 与 settings-dropdown 隔离 | `:137-156` | PASS | 新增的 user-row 下拉是**独立** `el-dropdown`（class `user-row-dropdown`），与既有 `settings-dropdown` 互不干扰，不会拦截彼此点击。 |
| 风险面 | 整体 | PASS | 头像区此前无交互，现为显式两步操作（点头像→点「退出登录」），无误触风险；无新增网络/状态面。 |
| gate-② 声明诚实度 | `VERIFICATION_logout.md` | PASS（含 MINOR） | 已如实声明：无浏览器点击级 e2e、登出为本地行为（JWT 服务端仍有效至过期）。见 M1。 |

## MAJOR / MINOR 与修复建议

### MAJOR
无。

### MINOR
- **M1（诚实性，非缺陷）** · `VERIFICATION_logout.md:29-30`
  `docker exec ... grep -l '退出登录' index-*.js` 命中只能证明**字符串已打包进产物**，并不等于「点击能触发登出」的功能性证据。doc 表述「容器内确认新 UI 已烘焙进产物」可由 grep 支撑，但措辞略有「打包即验证」之嫌。建议措辞改为：「构建产物含该入口字符串（打包证据），功能正确性仍需浏览器点验」——避免暗示 grep 已验证交互。不影响本变更正确性，仅补强文档严谨度。

- **M2（一致性，观察项，非缺陷）** · `DefaultLayout.vue:327` vs `main.ts:33`
  401 自动登出 `router.replace({ name: 'Login', query: { redirect: ...fullPath } })` 带了 `redirect` 回填；手动登出仅 `router.replace({ name: 'Login' })` 无 `redirect`。对「主动退出」这通常更符合预期（不应再回到受保护页），故**非 bug**；仅记录此有意差异，便于后续若想让登录页提供「回到原处」时保持一致。无需修改。

### INFO（可忽略，非问题）
- `:115` 新增 class `user-row-dropdown` 在 `<style scoped>` 中无对应规则——仅为语义钩子，无害；如追求整洁可移除或补样式。
- `:125` `el-dropdown-item` 上的 `divided` 在仅有单一项时会在项上方多一条分隔线，属纯视觉细节，不影响功能。

## Known limitations（已诚实披露）
- **前端本地登出**：仅清除本地 JWT（token + me + localStorage，`authStore.logout()`），跳转回 Login。因 JWT 无状态，**已签发的令牌在服务端仍有效至自然过期**；多端同时登录时本端退出不影响其他端会话——这是 JWT 固有语义，非本变更缺陷。
- **无二次确认弹窗**：与既有 401 自动登出一致（不弹确认），且为显式两步点击，误触概率低；若需 `ElMessageBox.confirm` 二次确认为「一句话改动」的可选增强。
- **未做浏览器点击级 e2e**：容器内无 headless 浏览器，登出是纯前端状态变更，逻辑与长期运行的 401 自动登出同源；建议人工在 `http://localhost:18080` 登录后点一次头像→「退出登录」做实落（注意浏览器可能缓存旧 `index.html`，必要时硬刷或带 `?cb=<ts>`）。
- **构建/编译未由本审查独立复跑**：gate-② 声明的 `BUILD_EXIT=0` / `UP_EXIT=0` 未被本审查另行验证，但产物含入口字符串且代码审阅未发现阻断性错误，与其结论相符。

<!-- reviewed-by: independent-subagent (gate-③) -->
