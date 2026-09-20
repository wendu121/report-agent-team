# REVIEW_settings_honesty.md · 设置页「假配置」诚实化（档 1）独立审议

> ✅ **状态：第 2 轮（rev2）已补审完成 · 结论 `PASS_WITH_NOTES`（2 项 NOTE，无 BLOCKING）**
> 补审方：gate3-settings-honesty-r2（独立子代理，与实施主代理无关，未采信实施者任何结论）。
> rev2 证据见本文末尾「§rev2 增量复审」；上文 §1–§3 为 rev1 历史段落（**只覆盖 MAJOR 修复前代码**），
> rev2 的结论以其为准，历史段落保留不改写。
> rev1 曾 BLOCKED（429 配额耗尽）的状态描述作废，以 rev2 为准。

> 审议方：gate3-settings-honesty（独立，主代理不自签）。
> 审议对象：`web/src/views/settings/CustomProviders.vue`、`web/src/views/settings/Models.vue`（仅这两个文件）。
> 依据：`DESIGN_settings_honesty.md`（K1/K2/K3）、`VERIFICATION_settings_honesty.md`（带怀疑核验）、`DESIGN_model_mapping_uf.md`、`DESIGN_gate_degradation_visibility.md`。
> 方法：对照 `git diff HEAD` 两文件 + 逐行读码 + 复跑 `vue-tsc --noEmit`。未跑浏览器。

---

## §1 结论

**PASS_WITH_NOTES**（需修 2 项 MAJOR、2 项 MINOR 后再合并）

诚实化核心机制实现正确、未引入后端变更、类型检查通过；但有两处「仍把高危/未知显示成绿灯」的残留，与项目宪法「禁止假配置」直接冲突，须修。

### 分级清单（附 file:line 与证据）

- **MAJOR-1｜顶部「校验状态」横幅未纳入未命中**
  - 位置：`Models.vue:37-45`（`<el-alert v-if="errors.length" …>` / `<el-alert v-else …>校验通过 · Gate 与所审 Agent 均异基座`）
  - 证据：该 alert 的 `v-if/v-else` 只判 `errors.length`，与 `unmatchedCount` 无关。K2 仅改了 `StatStrip` 瓦片（`Models.vue:326-344`）与 `onSave`，**没动这个最显眼的绿色横幅**。结果：当存在未命中高危条目且 `errors.length===0` 时，页面**同时**挂着绿色「校验通过」横幅（37-45）与「需注意」瓦片（326-344）/info 提示（24-34）。设计 §1 P2 要说消除的「通过 + 高危并存、互相打架的牌子」**未完全消除**。
  - 修法：该 alert 应复用 `stats` 的同套口径——`errors>0 → danger「校验未通过」`；`unmatchedCount>0 → warning「校验通过（N 条未命中需注意）」`；否则 `brand「通过」`。

- **MAJOR-2｜preview 失败时谎称「全部命中」**
  - 位置：`Models.vue:415-418`（refreshPreview 的 `catch { pvMap.value = {} }`）+ `Models.vue:329-344`（瓦片绿态）
  - 证据：预览接口一旦抛错，`pvMap` 被清空 → `unmatchedCount=0`。此时若无硬错误，瓦片渲染 `校验状态='通过'`、`未命中接口='0 / 全部命中接口 id'`（tone muted）。即**在「没有数据」时谎称「全部命中接口 id」**。
  - 与自审冲突：自审 §4 风险点 2 称「未伪造通过」——但 UI 字面渲染绿色「通过」/「全部命中接口 id」，该断言**不成立**。
  - 修法：增加 `previewReady`/`previewFailed` 标志；预览未成功返回时该瓦片显示「未预检 / 未知」而非「通过」/「全部命中」。`onSave` 在 preview 失败时的确认旁路属设计已知残留（§2 K2），但**绿态显示是新引入的不诚实**，须先修。

- **MINOR-1｜scope：Models.vue 夹带超出设计的重构**
  - 证据：`git diff HEAD -- web/src/views/settings/Models.vue` 远超出 `DESIGN_settings_honesty.md §3` 的两行清单，额外含整套 preview/select 重构：`base`/`model` 由 `el-input` 改 `el-select`、`pvMap`/`refreshPreview`/`loadAvailableModels`/`modelGroups`/`baseOptions`、`noModels` 指引、`异基座`列、`实际端点`列、`buildPayload` 抽出等。
  - 判断：这些大概率属并发的 model_mapping_uf / settings-console 轮次（同仓多代理未提交，无法从 git 干净归因）。**未触及后端**：`git diff HEAD` 仅这两 .vue 文件，无 `server/` 改动。建议 lead 核对来源；不影响本档诚实化逻辑正确性，但设计文档应补记。

- **MINOR-2｜自审计数证据在本 checkout 不可复现**
  - 证据：自审 §3.4 称 `.audit/models/` 有 `2026-09-20-manual-models.yaml.bak`、`2026-09-20-manual-custom_providers.yaml.bak` 两个手动备份；实际目录（`E:/第二电脑/report-agent-team/.audit/models`）只有 2026-09-08 的 `*-update/create_agent/delete_agent.yaml.bak` 共 8 个文件，**无**任何 2026-09-20 手动备份，最新条目是 2026-09-08 而非自审所称 2026-09-19。自审 M3 的「文件数仍 10」计数无法在此环境核对。
  - 缓解：M3 结论「取消=不落盘」由**代码路径**保证（`onSave` 在 `ElMessageBox.confirm` 的 `catch` 中 `return`，`api.put` 在其后，`Models.vue:496-514`），比计数更可靠，结论成立。主代理应订正 §3.4 描述，但不影响判定。

### 已核实正确的项（自审断言逐一核对）

| 自审 | 断言 | 独立核验 | 结果 |
|---|---|---|---|
| C1 | 统计按 `enabled && has_api_key` + 独立「缺密钥」 | `CustomProviders.vue:151-174` 源码确认 | ✅ |
| C2 | 三态「启用·缺密钥（不可用）」可渲染 | `CustomProviders.vue:44-67` 条件分支正确 | ✅ |
| C3 | 缺密钥阻断提交、弹窗不关 | `CustomProviders.vue:220-224` 早 return；仅成功路径 `dialog.show=false`(237) | ✅ |
| C4 | 未造出垃圾 provider | `onSubmit` 在缺密钥时 `return` 于 `addProvider` 之前(220-236) | ✅（代码保证） |
| C5 | API Key 标记 required | `CustomProviders.vue:97` `required` | ✅ |
| M1 | 瓦片未命中→「需注意」 | `Models.vue:326-338` 确认 | ✅（但顶部 alert 仍「通过」，见 MAJOR-1） |
| M2 | 未命中保存二次确认 | `Models.vue:496-514` 确认 | ✅ |
| M3 | 取消=不落盘 | 代码路径 `catch → return`(511-513)，`api.put` 其后 | ✅（代码保证；计数不可复现见 MINOR-2） |
| M4 | 改回恢复绿态 | `unmatchedCount=0` 时瓦片=`通过` | ✅ |
| T1 | `vue-tsc --noEmit` exit 0 | 独立复跑：**exit 0**（见 §3 命令） | ✅ |
| B1 | web 镜像已重建 | 超出静态审议范围（需 docker）；代码层已就绪 | ⚠️ 未验 |
| R1 | 心跳三闸 OK | 需后端/docker，超出静态审议 | ⚠️ 未验 |

- `has_api_key` 字段真实存在：`web/src/services/customProviderService.ts:10`，统计口径有效，非臆造。
- `ElMessageBox` 已导入：`Models.vue:256`，confirm 调用合法。
- 后端契约未变：`git diff HEAD` 仅两 .vue；preview 端点 `POST /admin/models/preview` 早已存在（被 `refreshPreview` 复用），本档未新增端点、未改 `server/api.py`/`server/admin.py`/`orchestrator.py`。
- 异基座口径未冲突：`isViolation` 比对 `base` 字符串（`Models.vue:350-353`），与 `DESIGN_model_mapping_uf.md §5.1`「比对 base 而非解析后端点」一致。
- K2 否决「未命中即报错」、采用二次确认，与 `DESIGN_model_mapping_uf.md` 档 3 一致；未改审计口径（仍 `validation=pass`，设计 §2 K2 已记账）。

---

## §2 对「请重点核查」各项的逐条回答

1. **逻辑正确性**：诚实化逻辑本身正确，无分支写反、无响应式依赖漏。`unmatchedCount`（`315-317`）正确排除 `eval` 且按 `!v.matched` 计；`onSave` 先 `errors` 拦、再 `unmatchedCount` 确认、`catch` 取消即 `return`（511-513），确认路径才 `api.put`（518）。`buildPayload` 抽出后保存与预览共用，无结构漂移。三态与统计口径均对。

2. **是否引入新的假象**：
   - 已引入两处（MAJOR-1、MAJOR-2）：顶部绿色「校验通过」横幅对未命中高危配置仍亮；preview 失败时瓦片谎称「全部命中」。二者都属宪法禁止的「把不可用/未知显示成可用/通过」。
   - 未引入的：`has_api_key` 文案限用「已注入 .secrets」而非「可用/有效」，密钥列诚实（自审 §4 风险3 已自陈，成立）；CustomProviders 三态不会再把「启用+缺密钥」显示成「可用」。

3. **是否与既有设计冲突**：不冲突。`isViolation` 比对 base 字符串符合 model_mapping_uf §5.1；K2 的「二次确认而非报错」符合档3；未动 gate_degradation_visibility 相关口径。

4. **范围是否失控**：后端零改动（已证）。但 `Models.vue` 工作树含一大块设计 §3 未记载的 preview/select 重构（MINOR-1），来源疑似并发轮次，建议 lead 核对。不应作为本档 blocker，但设计文档应补。

5. **自审断言是否属实**：逐项核对见上表。C1–C5、M1–M4、T1 成立；B1/R1 超出静态范围。自审 §4 风险2「未伪造通过」**不成立**（MAJOR-2）；§3.4 备份文件描述与真实目录不符（MINOR-2）。`ElMessageBox` 导入、`enabled && has_api_key` 统计口径、`.audit/models/` 不落盘结论均经代码证实（后者计数法不可复现但不影响结论）。

---

## §3 待修项（不要替他改代码）

1. **MAJOR-1**：`Models.vue:37-45` 顶部 alert 纳入 `unmatchedCount`——`errors>0`→danger；`unmatchedCount>0`→warning「校验通过（N 条未命中需注意）」；否则 brand「通过」。消除与「需注意」瓦片打架的绿横幅。
2. **MAJOR-2**：`refreshPreview` 增加 `previewReady`/`previewFailed` 标志；预览失败时 `StatStrip` 该瓦片显示「未预检/未知」，不得显示「通过」/「全部命中接口 id」。
3. **MINOR-1**：核对 `Models.vue` 额外重构来源（model_mapping_uf / settings-console 轮次），并在对应设计文档补记；确认不属本档夹带。
4. **MINOR-2**：主代理订正 `VERIFICATION_settings_honesty.md §3.4` 的 `.audit/models/` 备份与计数描述，使其与本 checkout 一致。

> 修复 MAJOR-1、MAJOR-2 后，本档即可从 PASS_WITH_NOTES 转为 PASS。

---

## §3b 类型检查复跑命令与结果（独立核验 T1）

```
# 宿主（Windows Git Bash），PATH 修复后
export PATH="/c/Users/sfkj/.workbuddy/binaries/node/versions/22.22.2-3:/usr/bin:/bin:$PATH"
cd E:/第二电脑/report-agent-team/web
node_modules/.bin/vue-tsc --noEmit
# => 无输出，EXIT 0
```
独立复跑结果：**exit 0**，与自审 T1 一致。

---

## 补审待办（rev2）—— 429 恢复后必须审完才能 commit

> 以下内容由主代理（待审方）登记，**不是审议结论**。请补审者独立核验，勿采信本文件任何自述。

**待审对象**：`web/src/views/settings/Models.vue` 相对 rev1 的增量（约 4 处）。
`CustomProviders.vue` 自 rev1 后未再修改，可沿用已有结论。

1. **MAJOR-1 增量** —— 顶部 alert 由 `v-if errors / v-else 绿色成功` 改为四路互斥
   `v-if errors` → `v-else-if previewFailed` → `v-else-if unmatchedCount` → `v-else success`。
   重点：四个分支是否互斥且穷尽；未命中时页面是否**彻底不再出现 `el-alert--success`**。
2. **MAJOR-2 增量** —— 新增 `const previewFailed = ref(false)`（声明紧随 `pvMap`）；
   `refreshPreview()` 成功置 false / catch 置 true；瓦片改为四态
   `异常(danger) > 未知(warn) > 需注意(warn) > 通过(brand)`；未命中瓦片显示 `—`。
   重点：组合态是否漏判（errors 与 previewFailed 同时成立、`noModels` 分支是否正确复位、
   pvMap 非空但 previewFailed 未复位导致误显示"未知"）。
3. **自审 §5 的证据是否属实**：`VERIFICATION_settings_honesty.md §5` 声称用 XHR 注入 +
   SPA 重挂载验证了两种失败态，并已还原 `models.yaml`。请核对现有配置未被污染
   （应为 `gates.*.base=cloudflare / model=llama-3.3-70b`，且 `models.yaml` 含 `llama-3.3-70b` 接口）。
4. **范围是否失控**：本档相对 rev1 是否只动了这 4 处？有没有顺手改别的东西。

**禁止事项**：不要相信本文件 §1 的结论已覆盖修复后代码 —— 它没有。

---

## §rev2 增量复审（gate3-settings-honesty-r2，独立补审）

> 方法：逐行读 `Models.vue` / `CustomProviders.vue` 当前工作区源码 + `git diff HEAD` 范围核对
> + 主账号配置/YAML/secrets 实物核查 + 独立复跑 `vue-tsc --noEmit`。
> **未跑浏览器**：下列渲染类结论（四路 alert 实际 DOM、二次确认交互）均为**代码级论证 + 类型检查**，
> 非真机截图证据；自审 §5 所述 XHR 注入 + SPA 重挂载结果未独立复现，仅核对到「配置已还原」。

### 总结论：`PASS_WITH_NOTES`（2 项 NOTE，无 BLOCKING；MAJOR-1/MAJOR-2 修复成立）

### 必查项判定表

| # | 检查项 | 结论 | file:line 证据 |
|---|---|---|---|
| 1 | 四路 alert 互斥且穷尽；未命中时无 `el-alert--success` | ✅ PASS | `Models.vue:37-59` 线性 `v-if="errors.length"` → `v-else-if="previewFailed"` → `v-else-if="unmatchedCount"` → `v-else`(success)。同一 alert 槽位的线性 if/else-if 链：未命中态只命中第 3 路（warning「需注意」），success 分支需前三路全假。页面另一处 success 仅表格「异基座」列 `el-tag`(181)，非 `el-alert`。顶部 alert 区无任何 `type="success"` 残留 |
| 2 | `previewFailed` 组合态 | ✅ PASS | 优先级：alert 链 errors 先于 previewFailed（37 vs 45）正确——硬错误 > 预检失败。`previewFailed` 声明于 `:314`；`refreshPreview()` `noModels` 分支 `:440-444` 复位 `pvMap={}`+`previewFailed=false`（正确：noModels 时不发起请求，无「未验成」语义，应显示通过而非未知）；成功路径 `:449-450` pvMap 写入 + false；catch 路径 `:454-455` 清空 pvMap + true **原子绑定**——不存在「pvMap 非空且 previewFailed=true」的持久态（置 true 必伴随清空，置 false 必伴随新数据） |
| 3 | 自审 §5 证据 / 配置未污染 | ⚠️ PARTIAL | ① ✅ `model_mapping.yaml` GateA/B/C 均 `{base: cloudflare, model: llama-3.3-70b}`（实测 sed 55-77 行）；② ✅ `models.yaml:73-79` 含 `id: llama-3.3-70b`（concrete, endpoint: new-api, model_id: llama-3.3-70b）；③ ❌ **`custom_providers.yaml` 当前（mtime 09-20 10:17，全文 Read 复核）三条 provider 均 `enabled: true`**（qwen3.8-max / **Intern-S2-Preview-397B** / glm-5.3），与必查基准「Intern enabled: false」**不符**，且同 mtime 的 `.secrets/custom_providers.json` 已含三处 api_key（Intern 条目 HAS）→ 判定见 NOTE-2；④ ✅ 全仓 grep `smoke-no-key` 无命中（自审 §3.3 未提交成功属实）；⑤ ✅ 自审 §3.4 所称两个 manual 备份实存（`tenants/…/.audit/models/2026-09-20-manual-{models,custom_providers}.yaml.bak`），rev1 MINOR-2「不可复现」在本 checkout 已销——.audit 落在租户目录而非仓库根 `.audit/`，rev1 审的是根目录 |
| 4a | CustomProviders 统计口径 / 三态 / api_key 阻断 | ✅ PASS | 统计「真正可用 = enabled && has_api_key」+「缺密钥」独立计数（`CustomProviders.vue:151-174`，「已启用」项 hint 已注明「含缺密钥项」，四瓦片自洽）；三态标签含 `启用·缺密钥（不可用）`（49-65，danger tag + tooltip 给出注入路径）；`onSubmit` 缺 key 早 return 于 `addProvider` 之前（220-224）、弹窗不关（仅 237 成功路径 `dialog.show=false`）、文案「可选」已改（101-104，HEAD 原文 71 行确为「可选」→ 本批改）；`API Key` form-item `required`（97）。HEAD 基线对照：HEAD 统计仍为「启用中/已配密钥」旧口径（HEAD:117-118），确认口径改动属本批 |
| 4b | Models 保存遇未命中二次确认「取消不落盘」 | ✅ PASS | 保存路径唯一：`api.put('/admin/models')` 全仓仅 `Models.vue:556` 一处；`onSave` `:534-552` 未命中 >0 → `ElMessageBox.confirm`，取消/关闭落 `catch → return`（549-551），`api.put` 在其后（556）且与 confirm 同函数串行 await，无并行入口；保存按钮 `:236` `:disabled="!!errors.length || saving"` 仅拦硬错误；页面无 form/@submit/回车提交入口（无 `@keydown`）。代码路径保证取消不落盘。注：浏览器实测（点击确认框取消）未独立复跑，结论基于代码路径 |
| 4c | rev2 增量是否只动了 4 处 | ✅ PASS | `git diff HEAD` Models.vue 共 387 行变更，但**基线归因**：`git show HEAD:...Models.vue` 中 `pvMap`/`el-select`/`refreshPreview` 计数均为 0 → 整个 preview/select 重构都不在 HEAD，属未 commit 的早期批次（model_mapping_uf 档 1+2，rev1 MINOR-1 已记账）。本批增量 = ① alert 四路化（37-59）② `previewFailed` ref（314）③ 瓦片四态（345-378）④ `onSave` confirm（534-551）+ 注释。CustomProviders.vue 91 行变更全在统计/三态/hint/onSubmit，无夹带（grep 无新增 fetch/autoDiscover/PATCH 类改动） |
| 5 | 是否引入新假象 | ⚠️ NOTE-1 | 见下 |
| 6 | 范围是否失控 | ✅ PASS（含 NOTE-2 事实记录） | 本批 diff 仅两 .vue；`server/`、`orchestrator.py`、`TaskResult.vue` 等 M13 批次未提交改动已隔离，未混入本批判定；后端契约未变（`POST /admin/models/preview` 早已存在，`server/admin.py:431`，无新增端点） |

### NOTE 清单（非阻断）

- **NOTE-1｜「真正可用」措辞略强于其语义**（`CustomProviders.vue:156-159`）
  `has_api_key` 只证明 `.secrets/custom_providers.json` 有条目，不证明密钥有效。
  密钥列「已配置/缺」（36 行）与「缺密钥→调用必失败」tooltip（31 行）守住了边界，
  但顶部瓦片「**真正可用** · 已启用且有密钥」+ success 态在「密钥已配但失效/未注入」情形下
  会把「已配置」暗示成「可用」。建议降档为「已启用且已配置密钥」（hint 保持现样即可）。
  不影响 K1「统计口径 = enabled && has_api_key」的正确性，仅文案强度问题。

- **NOTE-2｜范围外配置变更（10:17）：必查基准 ③「Intern enabled: false」当前不满足，且与本批无关**
  时间线（实物核查，租户目录未被 git 跟踪，仅能依 mtime + 备份推断）：
  1. 09-20 凌晨（DESIGN P1 修复）：`Intern-S2-Preview-397B` 置 `enabled: false`（文件曾含注释
     「2026-09-20 诚实化…待注入密钥后再置回 true」，本次复审核验 10:17 前的 grep 输出中该注释仍在，
     三条基准中 ①②③ 当时全部成立）；
  2. **10:17（本次补审进行期间）**：`custom_providers.yaml` 全文改写为三条 provider
     `qwen3.8-max` / `Intern-S2-Preview-397B` / `glm-5.3` 且**全部 `enabled: true`**（注释被移除，
     mtime 10:17）；同 mtime 的 `.secrets/custom_providers.json` 已有三条明文 api_key（Intern 条目 HAS）
     → 即「先注入密钥、再把 Intern 置回 true」的成对操作，语义上自洽（P1 的「假配置」被修复而非残留）；
  3. 09-19 备份 `2026-09-20-manual-custom_providers.yaml.bak` 仅 1 条 Intern（enabled: **true**）→
     「false」状态仅存在于 09-20 凌晨至 10:17 之间。
  判定：① 写入来源**无法归因本批**——本批 diff 仅两 .vue、无后端写路径；疑似并行轮次
     （如 task#25「修复自定义 API 管理」或主代理）完成密钥注入后回开。请 lead 追查来源并确认非误操作；
  ② 对本批结论的影响：必查基准 ③ 相对当前文件不满足，但偏差方向是「消除假配置」而非制造假配置，
     **不阻断本批 verdict**；③ `DESIGN_settings_honesty.md` P1「主账号缺密钥」「数据层置 enabled:false」
     的表述是凌晨时点事实，10:17 后已过期，建议 lead 在文档补记时点，避免后续审阅者误读。
  **该事项不属于本批改动的对错判定，仅如实记录，不计入本批阻断。**

### 类型检查（独立复跑）

```
export PATH="/usr/bin:/bin:/mingw64/bin:/c/Windows/System32:$PATH"
cd E:/第二电脑/report-agent-team/web && node_modules/.bin/vue-tsc --noEmit
# => 无输出，EXIT 0
```

### rev2 逐条必查项一句话回答

1. 四路 alert 互斥穷尽 ✅；未命中态全页无 `el-alert--success` ✅（success alert 仅存在于「errors=0 且 previewFailed=false 且 unmatchedCount=0」的健康态）。
2. 组合态无漏判：errors+previewFailed 并存时 alert 显示 errors（优先级正确）；noModels 分支复位正确；`previewFailed=true` 与 pvMap 非空不可能共现（置位与清空原子绑定于 catch）。
3. 自审 §5 证据：XHR 注入细节无法独立复现（未跑浏览器，已如实标注）；**配置还原核验为部分通过**——三闸 cloudflare/llama-3.3-70b ✅、models.yaml 含接口 ✅、smoke-no-key 无残留 ✅；但必查基准「Intern enabled:false」在 10:17 被并行操作改为 enabled:true（已成对注入密钥，方向为消除假配置，非本批所为），见 NOTE-2。
4. 全量复核：CustomProviders 三项（统计/三态/阻断）代码级成立；二次确认取消不落盘由代码路径保证（唯一保存入口 + catch return 先于 put）；无绕过路径（无其他保存入口/回车提交/并行写）。
5. 新假象：1 处文案强度问题（NOTE-1），无机制性不诚实。
6. 范围：本批 diff 干净（仅两 .vue，其中 Models.vue 的 pvMap 重构属早期批次、已 git 基线归因）；M13 批次改动已隔离；另有范围外 .secrets 事实记录（NOTE-2）。

### 终审

两项 MAJOR 修复**真实存在且实现正确**，rev1 PASS_WITH_NOTES 所附条件已满足；
新发现 2 项 NOTE（1 项文案建议 + 1 项范围外配置变更时点记录），均非阻断。
必查项 3 因 10:17 并行配置改写（NOTE-2）记 PARTIAL：其中「三闸 cloudflare/llama-3.3-70b」与
「models.yaml 含 llama-3.3-70b 接口」两项硬基准**当前仍成立**；「Intern enabled:false」在核验时点
已被改为 enabled:true（与密钥注入成对发生，方向为消除 P1 假配置而非制造，判定不阻断本批）。
**`PASS_WITH_NOTES`，可合并；NOTE-1 建议随本批顺手改，NOTE-2 由 lead 处置。**
补审 PASS 条件达成，rev1 所称「补审 PASS 前禁止 commit」的约束解除。

---

## §rev3 措辞增量签认（独立子代理 gate3-settings-honesty-r3，2026-09-20）

> 方法：仅针对 rev2 之后新增的**纯文案增量**做增量签认。用 `git diff` + 文件 mtime 时序
> + 全仓 grep + 逐行读码确定增量边界，并独立复跑 `vue-tsc --noEmit`。
> 审议对象：`web/src/views/settings/CustomProviders.vue` 相对 rev2 签定时点（~10:17）的增量；
> `web/src/views/settings/Models.vue` **本次未再触碰**，rev2 结论对其继续有效。

### 增量边界判定（时序证据）

- `CustomProviders.vue` mtime = **2026-09-20 10:26:17**，晚于 rev2 签字时点（REVIEW 文件 §rev2 段落落盘 10:29 前、10:17 配置变更之后）；`Models.vue` mtime = **01:23:34**，远早于 rev2 时点，此后未再被修改（其 rev2 签定的 previewFailed/unmatchedCount 四路结构与四态瓦片现仍原样存在，`Models.vue:37-59, 314, 332, 345-378`）。
- rev2 必查项 4c 已签认：CustomProviders.vue 相对 HEAD 的全部改动（91 行）均属本批结构（统计/三态/hint/onSubmit），即 rev2 签字时这些**结构**已在、仅瓦片措辞为旧。因此 rev3 增量 = 仅下三处措辞变更，结构层面无任何新改动。
- 全仓 grep `真正可用`：**0 命中**（旧措辞已完全移除，无残留）。

### 三处增量逐项核验

| # | 位置 | 变更 | 结论 |
|---|---|---|---|
| 1 | `CustomProviders.vue:158` | 瓦片 label `真正可用` → `启用·已配密钥` | ✅ 纯文案；过滤条件 `i.enabled && i.has_api_key`（:159）纹丝未动 |
| 2 | `CustomProviders.vue:161` | hint `已启用且有密钥` → `密钥存在≠有效，以实际调用为准` | ✅ 纯文案；无任何代码影响 |
| 3 | `CustomProviders.vue:151-152` | 注释补诚实边界：has_api_key 只证 .secrets 有条目、不证有效，措辞不得暗示可用性已验证 | ✅ 纯注释；与 rev2 NOTE-1 的判定一致且自我约束 |

### 核验项回答

1. **逻辑/过滤/fetch 未动**：`stats` computed 的四项结构（:155-175）、`missingKey` 计算（:154）、
   `onSubmit` 的 api_key 必填阻断（:219-226）、三态 tag/tooltip（:44-67）、`load()` 的 fetch
   路径均与 rev2 签认时逐行一致；diff 中无任何 `filter` 条件、`fetch`/`api.*` 调用、
   `ref`/`computed` 依赖的新增或修改。✅
2. **Models.vue 未碰**：mtime 01:23:34 早于 rev2 时点；当前内容即 rev2 已签版本。✅
3. **类型检查**：独立复跑 `cd web && node_modules/.bin/vue-tsc --noEmit` → **无输出，exit 0**。✅
4. **新措辞守住诚实边界**：「启用·已配密钥」仅陈述「已配置」事实，不暗示密钥已验证可用；
   hint「密钥存在≠有效，以实际调用为准」**显式降档**了 rev2 NOTE-1 指出的暗示强度，
   无新增过度承诺；与密钥列「已配置/缺」+ tooltip（:27-38）口径自洽。✅

### 结论

**rev3 通过：NOTE-1 已在增量中修复，rev2 的 `PASS_WITH_NOTES` 结论与
`<!-- reviewed-by: independent-subagent -->` 标记对当前工作区继续有效。**
（NOTE-1 销除；NOTE-2 为范围外配置时点记录，归 lead 处置，本增量不影响。）

<!-- reviewed-by: independent-subagent -->
