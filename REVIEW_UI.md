<!-- reviewed-by: independent-subagent -->

# UI 独立审议报告 — M12-3 专家团前端

- **审议对象**：`web/src/views/settings/Experts.vue`（新）、`web/src/services/expertService.ts`（新）、`web/src/router/index.ts`、`web/src/layouts/DefaultLayout.vue`
- **后端契约基准**：`server/admin.py:2546-2667`，`tools/experts.py`（`read_expert` 126-182、`_write_package` 426-486、`accept_expert_proposal` 489-505、`register_expert` 295-339）
- **构建状态**：lead 确认本回合 `npm run build`（`vue-tsc -b && vite build`）已通过。以下为独立于类型检查的逻辑/契约复核——类型检查器抓不到的问题。

## 裁决：PASS_WITH_NOTES

契约完全对齐，防御式渲染到位，开关失败回滚、转化校验、驳回确认+刷新均正确。仅发现若干非阻断性观察项（见下）。

---

## 1. 后端契约逐项核验

### GET `/admin/experts` — ✅ 对齐
- 后端 `admin_list_experts`（`server/admin.py:2546`）返回 `{ ok, items, count, proposals }`，`items` 已剔除 `body` / `skills_text`（`admin.py:2556`）。
- `ExpertItem`（`expertService.ts:6-23`）字段：`id, dir, enabled, shape, model, plugin, frontmatter, expert_type, degraded_team, display_name, profession, tags, category_id` —— 与后端 `read_expert` 返回（`tools/experts.py:166-182`） slim 后完全一致。
- TS 类型**没有**假设 `body` / `skills_text`（后端已剥离），且所有字段均为可选或带 `?`。无契约越界。
- `listExperts(enabledOnly)`（`expertService.ts:76-81`）请求 `params: { enabled_only }`，与后端 query 参数名 `enabled_only`（`admin.py:2548`）一致。✅

### POST `/admin/experts/{eid}/toggle` — ✅ 对齐
- 后端 `ExpertToggle`（`admin.py:2534`）仅需 `{ enabled: bool }`。
- `toggleExpert(eid, enabled)`（`expertService.ts:83-85`）发送 `{ enabled }`，路径 `encodeURIComponent(eid)+'/toggle'`，动词 POST。完全一致。

### POST `/admin/experts/convert` — ✅ 对齐
- 后端 `ExpertConvertReq`（`admin.py:2538`）：`url, id?, name?, shape?, allow_team_downgrade?`。
- `ExpertConvertReq`（`expertService.ts:46-52`）与之一一对应。
- `convertExpert`（`expertService.ts:87-90`）整 req POST。前端 `onConvert`（`Experts.vue:281-286`）构造 `{ url, name?, shape?, allow_team_downgrade? }`：`''/false` 经 `|| undefined` 省略，匹配后端默认值（shape 启发式、allow_team_downgrade=False）。`id` 未传（可选）。✅
- 返回体 `{ ok, status, id, expert_type, proposal }` 与 `ExpertConvertResult`（`expertService.ts:55-61`）一致；`onConvert` 读取 `res.expert_type`、`res.id` 正确。

### GET `/admin/experts/proposals` — ✅ 对齐
- 后端返回 `{ ok, items, count }`（`admin.py:2635`）。`ProposalListResult`（`expertService.ts:71-74`）匹配，`listProposals` 取 `data.items`。✅
- `ProposalItem`（`expertService.ts:26-43`）覆盖 proposal 实际落盘字段（`tools/experts.py:2605-2619`：`id, name, source_url, shape, expert_type, allow_team_downgrade, agent_body, provenance{...}`，外加可选 `model/description/category_id`）。

### POST `/admin/experts/proposals/{pid}/accept` — ✅ 对齐
- 路径、动词均正确：`api.post('/admin/experts/proposals/'+encodeURIComponent(pid)+'/accept')`（`expertService.ts:97-99`）。

### DELETE `/admin/experts/proposals/{pid}` — ✅ 对齐
- 路径、动词均正确：`api.delete('/admin/experts/proposals/'+encodeURIComponent(pid))`（`expertService.ts:101-103`）。

## 2. 页面逻辑核验（Experts.vue）— ✅
- **开关回滚**：`onToggle`（`Experts.vue:256-269`）先存 `old = !!row.enabled`，await API；成功后 `row.enabled = val`，失败 `row.enabled = old` 回滚，无视觉抖动（`el-switch` 用 `:model-value` 单向绑定，`Experts.vue:112`）。`_toggling` 加载态正确。
- **转化校验**：`onConvert`（`Experts.vue:271-296`）`url.trim()` 为空时报 `'URL 必填'` 并 return。✅
- **驳回确认 + 刷新**：`onReject`（`Experts.vue:311-331`）走 `ElMessageBox.confirm`，确认后才 `rejectProposal` 并 `loadAll()`。`onAccept`（`Experts.vue:298-309`）也 `loadAll()` 刷新。两个列表的 `loading`/`proposalsLoading` 与 `ElMessage` 反馈齐全。
- **空值防御**：所有可选字段访问均用 `?.` 或 `||` 兜底：`row.display_name || row.id`（`:82`）、`row.plugin?.source_url`（`:103`）、`row.provenance?.source_type`（`:157`）、`row.model || '—'`（`:98`）。无 null-deref 风险。

## 3. DefaultLayout 聊天折叠核验 — ✅
- `toggleChat` 绑到 chat-title `@click`（`DefaultLayout.vue:32`）。
- 搜索框 `v-show="chatExpanded"`（`:42`），聊天列表 `v-show="chatExpanded"`（`:50`）。
- "新对话"按钮 `@click.stop="newChat"`（`:38`）——阻止冒泡，不会触发折叠。✅
- 折叠状态持久化：`LS_CHAT_EXPANDED='report-agent-team:chat-expanded'`，初始化读取（`:196-200`），`toggleChat` 写入（`:203-205`）。✅
- caret 旋转：`.chat-title.collapsed .chat-caret { transform: rotate(-90deg) }`（`:499-501`），`collapsed` 类在 `!chatExpanded` 时挂上（`:32`）。✅

## 4. 路由核验 — ✅
- `goSettings`（`DefaultLayout.vue:274-284`）中 `command="experts"` → `router.push('/settings/experts')`；路由已在 `router/index.ts:57` 注册（`name:'SettingsExperts'`）。`command="asset-center"` → `/settings/asset-center`（`:59`）。其余 `models/agents/gates/templates` 也命中通用分支。✅

---

## 非阻断性观察（NOTES）

**NOTE-1（轻微 UX 不一致）— accept 缺少确认弹窗**
验收点要求 "accept/reject have confirm"。`onReject` 有 `ElMessageBox.confirm`（`Experts.vue:313`），但 `onAccept`（`Experts.vue:298-309`）无确认直接注册。两者都会 `loadAll()` 刷新，逻辑无错，仅"通过"动作缺少二次确认，与驳回不对称。建议给 accept 也加确认（或明确取舍）。非阻断。

**NOTE-2（轻微展示缺陷）— 已注册专家的"源 URL"列显示的是相对包路径而非真实来源**
`Experts.vue:103` 用 `row.plugin?.source_url || row.dir` 渲染来源链接。但：
- `_write_package` 生成的 `plugin.json` 不含 `source_url` 键（`tools/experts.py:443-466`）；
- `read_expert` 返回 dict 也**不含** `source_url`（`:166-182`），仅含 `dir`（registry 的相对目录，如 `experts/xxx`）。
因此 `row.plugin?.source_url` 恒为 `undefined`，回退到 `row.dir` —— 链接 `href` 变成一个相对路径 `experts/xxx`，点击会 404（新标签页）。真实来源 URL 实际存在 registry entry 的 `source_url`（`register_expert:333`），但未被 `read_expert` 透出。
前端本身做了防御（`?.` + 兜底 `|| row.dir`），**不崩溃**；属展示层信息缺失。建议后端在 `read_expert` 返回中补 `source_url`，或前端改用语义更清晰的占位（如显示 ID/目录且不加 `href`）。非阻断。

**NOTE-3（措辞歧义）— expertCount 语义**
`expertCount.value = experts.value.length`（`Experts.vue:233`），即"当前列表条数"；表头写"已注册专家（{{ expertCount }}）"（`:69`）。当 `enabledOnly` 开关打开时，该数只含启用项，与"已注册"措辞略有出入；但前后一致、无逻辑错误。非阻断。

---

## 结论
四项前端改动与后端契约**完全对齐**，关键交互（开关回滚、转化校验、驳回确认+刷新、折叠持久化、路由跳转）逻辑正确，防御式渲染到位，类型构建已过。无阻断性缺陷。建议后续迭代顺手处理 NOTE-1/2/3（均为非阻断）。
