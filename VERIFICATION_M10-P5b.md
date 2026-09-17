# VERIFICATION · M10-P5b 插件市场「oauth 凭证录入 + 打开官网」收口

> 门禁②自审文档。承接 M10-P5（`5ab38c4`）已消除「即将推出」死端，本补丁按 boss 最新三图指令补全三类真实缺口：
> ① oauth 类（雪球）也必须有真实「配置凭证」录入入口（Cookie/会话），且后端有落盘结果；
> ② 卡片须有「打开官网」按钮（用真实 doc_url）；
> ③ 新增数据源弹窗须补「官网地址 / 密钥教程地址 / 密钥变量名」三选填字段。
> 触发原话：「Cookie/会话凭证，那你就要有处理结果，要不然我怎么使用？官网跳转自动识别保存Cookie/会话凭证」+「预留官网地方，可以选填」。

## 1. 改动文件（实测 diff 已落地，未 commit）

| 文件 | 改动 |
|------|------|
| `server/admin.py` | `PluginPost` 增 `doc_url/key_guide_url/key_env`；`create_plugin` 非空时写入 `plugins.yaml` entry；`connect_plugin` 重写支持 `api_key`（→`DS_<ID>_API_KEY`）与 `oauth`（→`DS_<ID>_COOKIE`，body 取 `cookie`，拒空），oauth 不伪造 `connected`、返回 `credential_saved` |
| `web/src/views/Plugins.vue` | 动作区分支增 oauth「配置凭证」按钮；卡片链接 `doc_url` 与 `key_guide_url` 独立并存（「打开官网 ↗」+「获取密钥教程 ↗」）；新增弹窗补 3 字段；连接弹窗按 `auth_type` 分支（api_key→password / oauth→textarea 粘贴 Cookie）+ 「打开官网」提示；`onConnect` 按 `auth_type` 发 `api_key` 或 `cookie` |
| `web/src/types/ui.ts` | `PluginItem.status` 联合类型增 `'credential_saved'`（前端诚实显示 oauth 已存凭证） |

## 2. 门禁①校验结果（机器可验证）

| 项 | 命令 | 结果 |
|----|------|------|
| 后端语法 | `python -m py_compile server/admin.py` | ✅ OK |
| 后端单测 | `pytest tests/ -q` | ✅ **79 passed** |
| 前端类型 | `vue-tsc -b`（web/） | ✅ EXIT=0 |
| 前端构建 | `vite build`（web/） | ✅ 1749 modules，EXIT=0 |

## 3. 功能自审（逐条对齐 boss 指令）

### 3.1 oauth 也必须有「处理结果」（雪球）
- 卡片动作区：当 `p.auth_type === 'oauth'`（如 xueqiu）显示「配置凭证」按钮 → `openConnect(p)` → 连接弹窗。
- 连接弹窗对 oauth 渲染 `<el-input type="textarea">` 粘贴 Cookie，提示「F12 → Network → 复制 Cookie 值」。
- 提交 `onConnect`：oauth 发 `{ cookie: secret }` → 后端 `connect_plugin` 写入 `.secrets/plugins.env` 的 `DS_XUEQIU_COOKIE`，审计 `connect`/`cookie_set`。
- 返回 `status: credential_saved`，卡片 `pluginState` 显示「凭证已保存·待接入」（诚实：后端 Provider 待实现，不冒充 connected）。
- 持久化闭环：oauth 连接后 `connect_plugin` 同时把 `status: credential_saved` + `has_credential: true` 写回 `plugins.yaml`，`tools/data_sources._recompute_connected` 对 `coming_soon` 的 oauth 源若带 `has_credential` 标记即返回 `credential_saved`——因此**重载/重开页面后卡片仍显示「凭证已保存·待接入」**，非仅一次性 toast（解决独立审议 MINOR：原 `credential_saved` 分支为死代码）。
- ✅ 有真实录入入口 + 真实落盘（可 `cat .secrets/plugins.env` 验证）+ 卡片持久反馈，非死端。

### 3.2 「打开官网」按钮（doc_url）
- 卡片动作区：`v-if="p.doc_url"` 渲染 `<a ... target="_blank">打开官网 ↗</a>`（与「获取密钥教程 ↗」并存，互不覆盖）。
- 连接弹窗内也展示「打开官网登录拿凭证」链接，直接复用 `connectTarget.doc_url`。
- 数据中 xueqiu/cninfo/qcc/scholar/tavily 等均已配 `doc_url` 真实根域 → 用户点开即去对应官网登录/申请。
- ✅ boss「预留官网地方，可以选填」已满足：新增弹窗补 doc_url 字段；已有插件用已填 doc_url。

### 3.3 新增数据源弹窗补三选填字段
- 新增「官网地址 / 密钥教程地址 / 密钥变量名」三个 `el-input`，绑 `form.doc_url/key_guide_url/key_env`。
- `onCreate` 以 `{ ...form }` 发送 → 后端 `create_plugin` 非空时写入 `plugins.yaml`（ruamel round-trip 保注释）。
- 新建后「打开官网」「获取密钥教程」按钮即时出现，闭环。
- ✅ 满足「可以选填，要不然以后我怎么接入」。

## 4. 诚实边界声明（不可伪造）

- **「官网跳转自动识别保存 Cookie」在纯服务端 admin UI 下技术上不可行**：浏览器跨域安全 + 无用户态登录态代理，服务端无法代用户登录抓取 Cookie。本补丁交付的是诚实可行方案——「打开官网」按钮（用户自去登录）+「配置凭证」框（用户手动粘贴 Cookie 落盘）。不编造任何自动抓取/自动识别逻辑。
- `auth_type=none` 插件（cninfo/scholar 公开端点）：确实无需任何凭证，仅后端 Provider 待实现，UI 给「打开官网」+ 状态「后端未接入」，诚实不误导。
- 引擎 `provider=coming_soon` 跳过逻辑未动；配了密钥/Cookie 的 coming_soon 源仍被引擎跳过，直到后端接入——UI 已用 warn-note 明示。

## 5. 运行验收（门禁①之后，待部署实测）

- 待 `docker-compose up -d --build web api` 重建后，`curl /api/v1/plugins` 应返回含 `doc_url` 的 xueqiu/cninfo/qcc 等；
- 容器内 JS 应包含新文案「配置凭证」「打开官网」「保存凭证」；
- 浏览器 **Ctrl+F5 硬刷新**（vite 产物文件名变化但 boss 之前遇缓存，须硬刷）。

## 6. 结论

门禁①全绿，三图指令逐条闭环，诚实边界已显式声明。建议走门禁③独立审议后 commit（不 push，待 boss 显式指令）。

## 7. 独立审议 MINOR 闭环（post-review 补强）

- 独立子代理 `REVIEW_M10-P5b.md` 标记 1 项 MINOR：`credential_saved` UI 分支原为死代码（oauth 连接后 yaml 未回写状态、`_recompute_connected` 对 `coming_soon` 强制 `coming_soon`）。
- 本补丁已闭环：① `server/admin.py` `connect_plugin` oauth 分支写回 `status=credential_saved` + `has_credential=true`；② `tools/data_sources._recompute_connected` 对带 `has_credential` 的 oauth `coming_soon` 源返回 `credential_saved`。现已真实可达，卡片持久显示「凭证已保存·待接入」。属诚实表述（引擎仍跳过 coming_soon），不冒充可用。
- 该 MINOR 不影响 `REVIEW_M10-P5b.md` 的 `VERDICT: PASS_WITH_NOTES / MAJOR=0` 结论；本 § 为补强说明，非推翻独立审议。
