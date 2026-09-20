# 独立审议 · gate-③：登录页裸壳 / 侧边栏登录后显示

Verdict: **PASS_WITH_NOTES**

变更范围：`web/src/router/index.ts`、`web/src/App.vue`、`web/src/layouts/DefaultLayout.vue`
审议方式：逐文件读源码 + 独立 `docker build`（非冲突镜像标签 `rat-web-review-nc`，`--no-cache`）编译验证。未修改任何应用代码，未 `git commit`。

---

## 一、核心结论（是否真正解决 boss 诉求）

| 诉求 | 结论 | 证据 |
|------|------|------|
| 登录/注册页干净、无侧边栏 | ✅ 已解决 | `router/index.ts:31` `/login` 带 `meta:{ bare:true }`；`App.vue:6-9` 当 `isBare` 时只渲染裸 `<router-view>`，不挂载 `DefaultLayout` |
| 侧边栏仅在登录后出现 | ✅ 已解决 | 其余所有路由 `meta.bare` 均为 `undefined`，`isBare=false` → 走 `DefaultLayout` 分支 |
| 侧边栏用户区不再硬编码「管理员/本地部署」 | ✅ 已解决 | `DefaultLayout.vue:176-183` 改为从 `authStore.me` 派生 `displayName/roleLabel/avatarText` |

### 关键推理：`/login` 是否真的不渲染侧边栏
- `App.vue:18` `isBare = computed(() => route.meta.bare === true)`。
- 对 `/login`：`meta.bare === true` → `isBare` 为 `true` → `v-if` 分支（裸 `<router-view />`）渲染，`v-else` 分支（`<DefaultLayout>`）**不渲染**（`v-if`/`v-else` 互斥，二者不会同时存在于 DOM）。侧边栏（`DefaultLayout` 内的 `el-aside`）因此不挂载。✅
- 对所有其它路由：`meta.bare` 为 `undefined`（不等于 `true`）→ `isBare=false` → 走 `DefaultLayout` 分支，外壳照常渲染。✅
- 注册登录同页：boss 提到的「注册页」并非独立路由。经核 `Login.vue:14-17`，登录/注册是同一 `/login` 下的 `el-tabs` 两个 `name`（login/register），共享同一个裸路由，故注册态同样无侧边栏。✅

---

## 二、回归分析

1. **是否有其它本应裸壳的路由被漏标 bare？**
   否。路由表（`router/index.ts:26-66`）中除 `/login` 外，全部为登录后应用页（首页/智能体/能力市场/聊天/历史/设置等），都应带侧边栏，无需 `bare`。无漏标。

2. **`<router-view>` 在 `v-if`/`v-else` 上重复两份，对 Vue 3 有无问题？**
   - 不存在 `<keep-alive>` / `<transition>` 包裹（根 `App.vue` 模板即两分支直出），因此无“过渡/缓存需单一根”的陷阱。
   - 两个 `<router-view>` 永不共存：在 `bare` 态，裸路由视图挂载、内部 `DefaultLayout` 实例不存在；进入非 `bare` 态时裸视图卸载、`DefaultLayout`+其内 `<router-view>` 挂载。`DefaultLayout` 在 `v-else` 中处于模板固定位置，非 `bare` 路由间切换时 Vue 不重建它（`onMounted` 仅触发一次）。无组件身份错乱、无渲染空白。
   - **无任何路由渲染为空**：`/login` 有 `Login` 组件，其余各有对应 `component`。✅

3. **是否存在登录后卡在裸页 / 未登录却能进外壳？**
   - `router.beforeEach`（`router/index.ts:76-85`）：无 token 且目标非 `Login` → 跳 `Login`（bare）；有 token 且目标 `Login` → 跳 `/`。
   - 因此：未登录用户最终只落在 `/login`（bare），**永远进不了外壳**；已登录用户访问 `/login` 被重定向到 `/`（外壳态），**不会卡在裸页**。✅

---

## 三、鉴权接线核对（`authStore.ts` / `client.ts` / `main.ts`）

- `me` 与 `fetchMe()` 均存在：`authStore.ts:18`（state.me）、`authStore.ts:48-57`（fetchMe）。✅
- `fetchMe` 行为符合声明：`authStore.ts:49` `if (!this.token) return;`；成功后 `this.me = data`。401 不在此处处理，而由响应拦截器统一处理（见下）。
- **401 是否广播未授权事件并登出（期望行为）？** 是。`client.ts:54-59`：401 时 `setToken('')` 清本地 token 并 `window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))`；`main.ts:29-35` 监听该事件 → `auth.logout()`（置 `me=null`、`token=''`）→ `router.replace({name:'Login'})`。拦截器与 `main.ts` 在同一事件同步链内顺序执行，故 `me` 与 `token` 均被清空，无残留旧账号。✅（符合 `client.ts:20-23` 约定的“拦截器不碰 store、由路由层响应”的纪律。）
- **`persist:true` 是否持久化 `me`？** 是。`authStore.ts:64` `persist:true`，经 `pinia-plugin-persistedstate`（`main.ts:17`）默认持久化整个 state，含 `me`。故刷新后外壳直接显示真实账号，无需重复 `fetchMe`。✅
- **`DefaultLayout.onMounted` 内 `fetchMe` 是否会死循环/请求风暴？** 否。`DefaultLayout.vue:233` `if (!authStore.me) void authStore.fetchMe();` 有前置守卫：仅在 `me` 为空时请求一次；`persist` 使第二次进入外壳时 `me` 已存在，不再请求；且 `onMounted` 每个外壳会话仅触发一次。无重复请求风暴。✅

---

## 四、编译证明（独立构建）

命令（与部署镜像标签隔离，不 clobber 主代理正在构建的 deploy 镜像）：
```
C:\Program Files\Docker\Docker\resources\bin\docker.exe build --no-cache -t rat-web-review-nc -f web/Dockerfile ./web
```
结果：**成功（exit 0，耗时 ~37s）**，关键输出（`build_review_nc.log`）：
- `#10 RUN npm ci || npm install` → `added 121 packages ... found 0 vulnerabilities`
- `#12 RUN npm run build` → `vue-tsc -b && vite build` → `vite v6.4.3 building for production...` → `1786 modules transformed` → `built in 13.64s`
- 即 `vue-tsc` 类型检查通过、`vite` 生产构建通过，本次改动（含 `App.vue` 模板、`DefaultLayout.vue` 计算属性、`router` meta）**类型与模板均合法**。

（首轮普通 `docker build -t rat-web-review` 因层缓存命中 4s 即出，已追加 `--no-cache` 重建以排除“缓存早于源码修改”的疑点，干净重建同样成功。）

---

## 五、残留说明 / 诚实边界（非缺陷）

1. **本次审议仅覆盖前端** `bare` 逻辑与编译。前端登录守卫（`router/index.ts`）本身声明“不是安全边界”，真正的鉴权边界在后端 `get_current_account` —— 不属本次改动范围，未予评估。
2. **持久化 `me` 的边界**：刷新后 `me` 来自 localStorage，若后端账号被禁用/删除，陈旧 `me` 仍会短暂显示，直到某次请求触发 401 才被 `main.ts` 清掉并跳登录。这是既有 `persist` 行为，非本次引入，记录为已知边界。
3. **`roleLabel` 回退值**：`DefaultLayout.vue:177-182` 当 `me.role` 既非 `main` 也非 `sub` 时回退为「本地部署」。当前 `AccountInfo.role` 类型仅含 `'main'|'sub'`（`authStore.ts:10`），该回退分支实际不可达，属防御性兜底，无害。
4. 未运行运行时浏览器实测（无可用 headless 浏览器环境），以上 UI 行为基于源码静态推理 + 编译通过；逻辑路径已逐条核对，置信度高。

---

<!-- reviewed-by: independent-subagent (gate-③) -->
