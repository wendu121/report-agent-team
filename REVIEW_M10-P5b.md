# 独立门禁③ 审议 · M10-P5b（oauth 凭证录入 + 打开官网 收口）

> 本文件由独立审议子代理（门禁③）产出。仅读取代码并撰写本审议，**未修改任何源文件**。
> 审议对象：承接已提交里程碑 `M10-P5`（`5ab38c4`）的工作树未提交补丁（diff 已落盘，未 commit）。
> 对照物：`VERIFICATION_M10-P5b.md`（门禁②自审）、`server/admin.py`、`web/src/views/Plugins.vue`、`web/src/types/ui.ts`、`config/plugins.yaml`。

---

## 审议结论

`VERDICT: PASS_WITH_NOTES`

补丁三类缺口（oauth 真实凭证录入入口、卡片「打开官网」按钮、新增弹窗官网/教程/变量名三字段）均已落实，且诚实边界（不伪造「自动识别保存 Cookie」）处理正确。存在 2 处需记录的 MINOR 风险（其中一处导致自审文档 §3.1 的 UI 状态声称不成立），但不阻断交付。

---

## 10 项检查对照表

| # | 检查项 | 结果 | 验证证据（行号 + 引文） |
|---|--------|------|--------------------------|
| 1 | `connect_plugin` 凭证路由 | **PASS** | (a) `admin.py:1771-1776` `if auth_type == "api_key": value = (body.get("api_key")...); env_key = f"DS_{pid.upper()}_API_KEY"`；(b) `:1777-1782` `elif auth_type == "oauth": value = (body.get("cookie")...); env_key = f"DS_{pid.upper()}_COOKIE"`，空则 `raise 400 "cookie / 会话凭证不能为空"`；(c) `:1783-1785` `else: raise 400 "该插件无需凭证"`；(d) `:1804-1819` 仅 `auth_type == "api_key" and target.get("enabled")` 才写 `status=connected`，oauth 分支不写 yaml、返回 `status: "credential_saved"` |
| 2 | `create_plugin` 三字段持久化 | **PASS** | `admin.py:1649-1654` `if body.doc_url: entry["doc_url"]=...; if body.key_guide_url: ...; if body.key_env: ...`（仅非空写入）；写入对象 `entry` 为 `CommentedMap`（`:1642`），经 `ldata.setdefault("plugins",[]).append(entry)` → `_dump_commented`（`:1655-1656`）完成 ruamel 圆回写 |
| 3 | 动作区分支 oauth 在 catch-all 之前 | **PASS** | `Plugins.vue:63-66` `v-else-if="p.auth_type === 'oauth'"` 渲染「配置凭证」按钮 `@click="openConnect(p)"`，位于 `v-else`（`:69`）之前；catch-all 不再吞掉 oauth |
| 4 | 卡片链接 doc_url / key_guide_url 独立并存 | **PASS** | `Plugins.vue:76-77` `v-if="p.key_guide_url"`「获取密钥教程 ↗」与 `v-if="p.doc_url"`「打开官网 ↗」为两条独立 `v-if`，可同时显示 |
| 5 | 新增弹窗三字段 | **PASS** | `Plugins.vue:115-125` 三个 `el-input` 分别 `v-model="form.doc_url"` / `form.key_guide_url` / `form.key_env` |
| 6 | 连接弹窗按 auth_type 分支 + 打开官网提示 | **PASS** | `Plugins.vue:143-149` api_key→`type="password"` `v-model="secret"`；`:151-157` oauth→`type="textarea"` `v-model="secret"`；`:137-139` `v-if="connectTarget?.doc_url"` 展示「打开官网登录拿凭证」链接复用 `connectTarget.doc_url` |
| 7 | `onConnect` 荷载 + 禁用逻辑 | **PASS** | `Plugins.vue:330-332` `target.auth_type==='oauth' ? {cookie: secret.value} : {api_key: secret.value}`；`:334` `POST .../admin/plugins/${target.id}/connect`；保存按钮 `:167` `:disabled="!secret"` |
| 8 | `ui.ts` 联合类型 + `pluginState` 处理 | **PASS*** | `ui.ts:57` status 联合含 `'credential_saved'`；`Plugins.vue:229-231` `if (p.status === 'credential_saved') return {...'凭证已保存·待接入'...}` 提前返回，不落入误分支。\*见下方 MINOR：该分支对当前内置 oauth 源实际不可达 |
| 9 | 诚实性：无伪造「自动识别/保存 Cookie」 | **PASS** | 连接弹窗 `Plugins.vue:135`「请先点下方「打开官网」登录，再从浏览器开发者工具复制 Cookie 粘贴」；`:156` 占位「F12 → Network → 复制 Cookie 值」；全仓无自动抓取/注入 Cookie 逻辑。`VERIFICATION §4.1` 已显式声明该能力在服务端 admin UI 下技术不可行 |
| 10 | 构建证据合理性（沙箱无法重跑，做一致性核查） | **PASS** | `grep apiKey` 于 `Plugins.vue` **无匹配** → 旧 `apiKey` 全量重命名为 `secret` 完成；无遗留 `v-else-if` 隐藏 doc_url；`vue-tsc`/`vite`/`pytest`/`py_compile` 声明与代码内部一致（无法重跑，按声明接受但代码自洽） |

\* 第 8 项代码本身正确；其「不可达」问题归属于下方风险 1（MINOR）。

---

## MAJOR count: 0

---

## 真实风险（诚实 MINOR，按设计可接受）

**风险 1（MINOR · 自审文档声称不成立，但功能正确）——`credential_saved` UI 状态对当前内置 oauth 源不可达。**
- 事实链：`connect_plugin` 对 oauth **不写** `plugins.yaml` 的 status（仅 api_key+enabled 写，见 `admin.py:1804-1819`）。其返回的 `status: "credential_saved"` 仅存在于 HTTP 响应；前端 `onConnect` 在 `:345-346` 调用 `load()` 重新拉取 `/plugins`，state 由 `_plugin_item` → `load_data_sources` 重算。
- `tools/data_sources.py:933` `_recompute_connected` 对 `provider == "coming_soon"` **强制返回 `"coming_soon"`**（早于 auth_type 分支）。xueqiu 的 `provider=coming_soon`（`plugins.yaml:150`），故重拉后状态恒为 `coming_soon`，`pluginState` 走 `Plugins.vue:236-238` 显示「后端未接入·需凭证」，**不会**显示「凭证已保存·待接入」。
- 因此 `VERIFICATION_M10-P5b.md §3.1` 所述「卡片 pluginState 显示『凭证已保存·待接入』」**不成立**；`ui.ts:57` + `Plugins.vue:229-231` 的 `credential_saved` 分支对现有 oauth 源为**死代码**。
- 影响评估：功能层面 boss 的核心诉求（真实录入 + 真实落盘结果 + 打开官网）**已满足**——Cookie 真实写入 `.secrets/plugins.env`（可 `cat` 验证），保存成功 toast 显示「凭证已保存」（`:344`）。卡片显示「后端未接入·需凭证」属诚实表述，未误导。故不升 MAJOR，但自审文档需修正该句。
- 建议（非阻断）：若要让卡片显式反映「已存凭证」，需在 `connect_plugin` 对 oauth 也把 `status` 写回 `plugins.yaml`（如置 `credential_saved`），并相应调整 `_recompute_connected` 对 oauth 的判定（当前 line 931/942 把 oauth 归为 `disconnected`）。当前未做，符合「coming_soon 不冒充 connected」的既有纪律。

**风险 2（MINOR · 按设计可接受）——`DS_<ID>_COOKIE` 写入后暂无消费方。**
- `connect_plugin` 写入 `DS_XUEQIU_COOKIE`（`admin.py:1781`）。但 `tools/data_sources.py:908` 与 `:920` 的 `load_secrets()` **仅读取 `DS_*_API_KEY`**（两处 `k.endswith("_API_KEY")`），不识别 `_COOKIE`；`_recompute_connected`（`:925-942`）与 `SearchTool`（`:1054` 仅读 `_API_KEY`）也无 `_COOKIE` 消费逻辑。
- 结论：oauth Cookie 当前为 **stored-but-not-yet-consumed**，与 `provider=coming_soon` 引擎跳过的设计一致（后端 Provider 待实现）。属预期内 MINOR，非缺陷；后端接入时再扩展 `load_secrets`/`_recompute_connected` 即可。

**其它一致性确认（非问题）：**
- 内置 oauth 源（xueqiu）动作区可达性：`Plugins.vue:43/49/57` 三分支均不满足 → 命中 `:63` oauth 分支，「配置凭证」按钮对 xueqiu 可达。✓
- `config/plugins.yaml` 中 xueqiu（`:154-155`）、cninfo（`:167`）、qcc（`:179-181`）、scholar（`:193`）均含真实 `doc_url`；xueqiu/qcc 含 `key_guide_url`，qcc 含 `key_env`。✓
- `PluginPost` 已含三字段（`admin.py:1596-1598`），`_plugin_item` 已回传 `doc_url/key_guide_url/key_env`（`admin.py:1408-1410`）。✓

---

<!-- reviewed-by: independent-subagent -->
