# VERIFICATION_M10-P5 — 插件市场：消除「暂未推出」死胡同 + 六分类收口

> 门禁：① 校验（build/pytest/typecheck） → ② 自审（本文件） → ③ 独立子代理审议（REVIEW_M10-P5.md）
> 模式：edict-gate 全局强制（无 `.governance/`，git 变更即触发）
> 提交纪律：本地 `git commit`；**无 boss 显式「推送」指令不 push**（父仓 `E:\第二电脑` 无 remote 目标，本里程碑保持本地）

---

## 1. 用户诉求（原文要点）

1. 插件界面上的「暂未推出 / 即将推出」是死胡同——**如果需要 KEY，界面上必须给出真实填写 KEY 的入口**，不能只留「暂未推出」占位。
2. 为界面美观，插件按**六个分类**归置（原八类收敛）。
3. 需要 KEY 的插件，面板内放**填写 KEY 的链接**——**不要瞎编**（URL 必须真实可用）。
4. 检索拿 KEY 的教程放在对应插件面板上，方便老板自己手动去拿。

---

## 2. 变更范围

| 文件 | 类型 | 改动 |
|---|---|---|
| `DESIGN_M10-P5.md` | 新增 | 设计文档：`coming_soon` 语义澄清、诚实边界（不造假后端）、六分类映射、新增元数据字段、不做清单 |
| `config/plugins.yaml` | 改 | 19 个插件 → 6 分类（综合检索/学术文献/百科知识/财经行情/企业工商/电商数据）；为需 KEY 的插件补 `doc_url`/`key_guide_url`/`key_env`；`coming_soon` 插件写诚实说明 |
| `server/admin.py` | 改 | `_plugin_item()` 透传 `doc_url`/`key_guide_url`/`key_env` 三个可选字段到 `/api/v1/plugins` 响应 |
| `web/src/types/ui.ts` | 改 | `PluginItem` 接口新增 3 个可选字段 `doc_url?`/`key_guide_url?`/`key_env?` |
| `web/src/views/Plugins.vue` | 改 | 删「即将推出」死分支；新增 `pluginState()` 状态重解释；配置密钥弹窗增强（显示环境变量名 + 教程链接 + 诚实「后端未接入」提示）；面板外链（教程/官网）；CSS |

diff 规模：`config/plugins.yaml` +92、`server/admin.py` +5、`ui.ts` +6、`Plugins.vue` +117/−（净 +161/−59）。

---

## 3. 门禁 ① 校验结果（真实执行，非纸面）

| 检查项 | 命令 | 结果 |
|---|---|---|
| Python 编译 | `python -m py_compile server/admin.py` + `compileall server` | ✅ COMPILE OK |
| 测试套件 | `pytest tests/ -q`（managed python 3.13.14, pytest 9.1.1） | ✅ **79 passed**, 40 warnings（仅 `utcnow()` 弃用警告，非本次引入） |
| 前端类型检查 | `vue-tsc -b`（`npm run build` 前置） | ✅ 无类型错误 |
| 前端构建 | `vite build`（report-agent-team/web） | ✅ 1749 modules transformed，`dist/` 产出成功（11.64s） |

> 注：79 passed 较 M9-5 的 41 为更大范围套件（后续里程碑持续补充用例），本次 5 文件改动未引入任何失败。

---

## 4. 自审：逻辑正确性

### 4.1 `coming_soon` 死胡同已消除
- 旧逻辑：状态 `coming_soon` → 仅展示「即将推出」标签，无动作 → 死胡同。
- 新逻辑（`Plugins.vue` 模板 + `pluginState()`）：
  - `auth_type === 'api_key'` 的 `coming_soon` 插件：显示 `配置密钥` 按钮（走既有的 `POST /admin/plugins/{pid}/connect` → 写入 `.secrets/plugins.env` 的 `DS_<ID>_API_KEY`），并显示 `获取密钥教程 ↗` 外链。
  - `auth_type === 'oauth'` 的 `coming_soon` 插件：显示 `配置密钥` 按钮 + 诚实提示「需会话 Cookie，后端 Provider 待实现」。
  - 其余：状态标签 + 删除（非 builtin）。
- **关键**：密钥入口真实存在且真实写入 `.secrets`，不再是占位。后端 `build_search_tool` 对 `coming_soon` 的跳过逻辑**刻意未动**——这是诚实边界（见 §5），不是「假可用」，UI 已用 `warn-note` 明确告知「当前引擎仍会跳过」。

### 4.2 六分类收口
- 原 8 分类（数据源/学术/百科/行情/新闻/财报/工商/电商）→ 现 6 分类（综合检索/学术文献/百科知识/财经行情/企业工商/电商数据）。
- 映射：原「新闻」并入「综合检索」（gdelt 归入综合检索，带诚实 TLS 不稳定说明）、原「财报」并入「财经行情」（tencent_quote/xueqiu/cninfo）、原「数据源」泛化。
- `config/plugins.yaml` 每个插件均带 `category`，与 §2 映射一致；前端 `categories` 计算属性按 `category` 分组展示。

### 4.3 元数据透传链路完整
- YAML（`doc_url`/`key_guide_url`/`key_env`）→ `server/admin.py::_plugin_item()` 透传 → `/api/v1/plugins` JSON → `web/src/types/ui.ts::PluginItem` 类型 → `Plugins.vue` 渲染（外链 + 弹窗提示）。四层字段名一致，已 grep 确认无残留旧字段（`statusType`/`statusLabel` 已删除）。

### 4.4 配置密钥弹窗增强
- 显示 `key_env`（如 `DS_QCC_API_KEY`），让用户知道写入哪个变量。
- 显示 `key_guide_url`（教程），点击新标签页打开。
- `coming_soon` 时显示 `warn-note`：诚实说明后端未接入、密钥先预留、接入即生效。

---

## 5. 诚实边界（boss 铁律：「零 KEY 不伪造数据」）

- **未实现任何 fake backend**：qcc / xueqiu / cninfo / scholar 四个 `coming_soon` 插件**没有**新增伪造的 Provider。引擎 `build_search_tool` 仍按既定逻辑跳过它们——这是诚实的「不可用」声明，而非假装可用。
- **URL 全部真实**：`doc_url`/`key_guide_url` 仅用官方根域名（tavily.com、qcc.com / qcc.com/open、xueqiu.com、cninfo.com.cn、scholar.google.com、arxiv.org 等）。因本会话 WebSearch 被 `SENSITIVE_APPROVAL=TIMED_OUT` 拦截，未联网核验每个具体路径，但均取**官网根域/已知开放平台路径**，未编造深层子路径或参数。
- **教程链接 = 官网/开放平台入口**，老板可据此自行注册拿 KEY；未杜撰「一键获取」假流程。

---

## 6. 本里程碑「不做」清单（显式留痕）

- 不为 qcc/xueqiu/cninfo/scholar 写假 Provider（会违反零伪造原则）。
- 不改引擎 `build_search_tool` 的 `coming_soon` 跳过逻辑（保持诚实跳过）。
- 不联网编造密钥申请深层 URL（仅给官网/开放平台根域）。

---

## 7. 残留风险 / 待补

- **TD-010（建议，本次未做）**：为 `coming_soon` 的 api_key 插件实现真实 Provider（qcc 企业工商、xueqiu 行情），接入后即可经既有 connect 链路生效（密钥已预留到 `.secrets`）。属下一里程碑，非本次范围。
- **URL 深度未联网核验**：根域真实，个别开放平台深层路径（如 qcc.com/open 的具体申请页）建议老板首次点击时顺手确认；若有变动，改 `config/plugins.yaml` 一处即可，热加载。
- 前端 `vite build` 产物 `dist/` 已产出但未部署（容器构建走 `docker-compose up -d --build web`，非本次门禁范围）。

---

## 8. 门禁结论

- ① 校验：✅ 全绿（compile / 79 passed / typecheck / build）
- ② 自审：✅ 本文件完成，逻辑链路与诚实边界已确认
- ③ 独立审议：⏳ 待 REVIEW_M10-P5.md（独立子代理产出，主代理不自签 `reviewed-by`）

**自审结论：可进入门禁 ③ 独立审议。**
