# VERIFICATION · 登录页裸壳（侧边栏仅登录后显示）

> **gate-② 自审档**（不含 `reviewed-by` 标记；独立审议见 `REVIEW_login_bare_layout.md`）。
> boss 诉求（附截图）：「注册页面干净点，侧边栏取消，**登入以后才有这个功能**」。

## 一、根因

`web/src/App.vue` **无条件**把 `<router-view />` 套在 `DefaultLayout`（含侧边栏 `el-aside`）里，
于是 `/login`（登录/注册页）也渲染了整条侧边栏。

关键旁证：`Login.vue` 本身**早已**是「全屏居中的独立卡片」设计
（`.login-shell { min-height:100vh; display:flex; align-items:center; justify-content:center }`，
`.login-card { max-width:420px }`），只是从未有机会以该形态呈现——属于「外壳套错了」而非「登录页没设计」。

顺带发现：侧边栏用户区（截图左下角）把 **`管理员` / `本地部署` / 头像 `U` 全部硬编码**，
对任何账号都显示「管理员」——属明显失真，一并修正。

## 二、改动（3 文件）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `web/src/router/index.ts:31` | `/login` 路由加 `meta: { title: '登录', bare: true }`，显式标记为「无外壳裸页」 |
| 2 | `web/src/App.vue` | `<router-view v-if="isBare" />` + `<DefaultLayout v-else><router-view /></DefaultLayout>`；`isBare = computed(() => route.meta.bare === true)` |
| 3 | `web/src/layouts/DefaultLayout.vue:176-183` | 用户区改为从 `authStore.me` 派生：`displayName`（用户名/未登录）、`roleLabel`（主账号/子账号/本地部署）、`avatarText`（用户名首字母）；`onMounted` 增加 `if (!authStore.me) void authStore.fetchMe()` |

设计取舍：用路由 `meta.bare` 而非在 `App.vue` 里硬编码 `route.name === 'Login'`——
后续若出现其它公开页（如找回密码），只需补一个 meta，无需改外壳逻辑。

## 三、验证证据

### 门禁① 编译 / 构建

```
docker.exe compose build web   →   EXIT=0
  #13 RUN npm run build → vue-tsc -b && vite build
  ✓ 1786 modules transformed
  ✓ built in 10.07s
  产物 bundle: index-DxZ2upZy.js  →  index-B7bEkgKo.js（哈希变化 = 改动确已进包）
```

部署：`docker.exe compose up -d web` → `report-web Recreated / Started`（EXIT=0）。

### 门禁③ 独立审议

`REVIEW_login_bare_layout.md` → **PASS_WITH_NOTES**，标记 `<!-- reviewed-by: independent-subagent (gate-③) -->`。
审议方**独立重跑**了非冲突标签的 `--no-cache` 构建（`rat-web-review-nc`）→ exit 0、`1786 modules transformed`；
并逐条核对了裸壳分支互斥性、无漏标 `bare` 的路由、路由守卫交互、`fetchMe` 无死循环。
其残余说明（前端守卫非安全边界、stale `me` 边界、`roleLabel` 回退分支不可达）已如实收录于 §四。

### 运行时浏览器实测（真实 Chromium，bsk 驱动，`localhost:18080`）

| 步骤 | 期望 | 实测结果 |
|---|---|---|
| 直开 `/login` | 全屏居中登录卡，**无侧边栏** | ✅ VOM 仅含 heading/段落/登录·注册 tabs/账号·密码/登录按钮；**无** 新任务·智能体·能力市场·聊天记录·历史记录·设置·用户区 |
| 切到「注册」tab | 注册态同样无侧边栏 | ✅ `tabpanel "注册"` + 注册按钮 + 提示文案；**仍无任何侧边栏元素**（截图诉求核心） |
| 注册 `qa_shell_check` | 首注册即主账号 | ✅ 页面提示 `✓ 注册成功！您是本系统的主账号，已自动激活，请用相同账号密码登录。` |
| 登录后 | 侧边栏出现 | ✅ 侧边栏渲染：新任务 / 智能体 / 能力市场 / 聊天记录 / 历史记录 / 调试模式 / 设置 |
| 侧边栏用户区 | 显示真实账号 | ✅ `Q`（首字母头像）+ `qa_shell_check` + `主账号`（修复前恒为 `U` / `管理员` / `本地部署`） |

> 注：首次 `navigate` 命中浏览器旧缓存（nginx 的 `index.html` 被缓存 → 仍加载旧 JS 哈希），
> 表现为「侧边栏还在」。已核 `GET /` 返回的 `index.html` 实际引用新包 `index-B7bEkgKo.js`，
> 随即以带查询参数的 URL 强制取新 `index.html` → 新 UI 正确渲染。**代码/部署无误，属浏览器缓存现象**（见 §五）。

## 四、诚实边界（非缺陷，来自独立审议 + 自审）

1. **前端路由守卫不是安全边界**：`router/index.ts` 的 `beforeEach` 仅做前端跳转，真正的鉴权边界在后端
   `get_current_account`。本次只改了「外壳是否渲染」，未改动鉴权强度。
2. **`persist` 陈旧 `me` 边界**：若账号在后端被禁用/删除，localStorage 里的旧 `me` 会短暂显示为已登录，
   直到某次请求 401 才被 `main.ts` 的监听清掉并跳登录。属既有 `persist` 行为，**非本次引入**。
3. **`roleLabel` 的「本地部署」回退分支**：`AccountInfo.role` 类型仅 `'main' | 'sub'`，
   该分支实际不可达，属防御性兜底（无害）。
4. `bare` 目前仅 `/login` 使用；`/login` 内部「登录 / 注册」是同一路由的两个 tab（`Login.vue:14-17`），
   故注册态同样无侧边栏（即 boss 截图中那条侧边栏在注册态也已消失）。

## 五、部署/验证中发现的一个现象（非本次缺陷，但值得记一笔）

**现象**：`docker compose up -d web` 重建容器后，首次用浏览器 `navigate` 打开 `/login`，
页面上**侧边栏仍在**、且用户区仍是硬编码的 `U / 管理员 / 本地部署` —— 看起来像「改动没生效」。

**排查与结论**：
- `GET http://localhost:18080/` 实测返回 `200`，其 `index.html` 明确引用**新**包
  `/assets/index-B7bEkgKo.js`（= 本次构建产物哈希）→ **容器/镜像已是新代码**。
- 因此浏览器仍在跑**旧的 `index.html` + 旧 JS**：SPA 的 `index.html` 被浏览器缓存，
  即便 JS 文件名带内容哈希，只要 `index.html` 本身命中缓存，就会继续加载旧哈希的 JS。
- 用带查询参数的 URL（`/login?nocache=1`）强制重新取 `index.html` 后，新 UI 立即正确渲染。

**影响与建议**：boss 自己首次打开时也可能看到旧界面（一次硬刷新即可解决）。
若希望彻底避免，可考虑在 `web/nginx.conf` 对 `index.html` 加 `Cache-Control: no-cache`
（对 `assets/*` 保留长缓存——文件名已含内容哈希，天然可长缓存）。
本档**未改** `nginx.conf`（超出本次诉求范围），留作后续小改建议。

## 六、测试账号与临时文件清理

- 浏览器实测所注册的 `qa_shell_check`（main/active）**已从 DB 删除**，孤儿 tenant 目录已删；
  `SELECT count(*) FROM accounts` = **0**，`tenants/` 仅剩 `baseline-probe`。
  → **boss 的首个注册仍将成为主账号**（保持干净白纸）。
