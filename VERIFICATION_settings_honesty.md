# VERIFICATION_settings_honesty.md · 设置页诚实化（档 1）自审

> 主代理自产物，供 gate ③ 独立审议核对。**不自签**。
> 设计依据：`DESIGN_settings_honesty.md`（K1/K2/K3）。
> 改动面：`web/src/views/settings/CustomProviders.vue`、`web/src/views/settings/Models.vue`（仅这两个文件）。

## §1 改动清单与实际校验

| # | 断言 | 验证方式 | 结果 |
|---|---|---|---|
| C1 | CustomProviders 统计改为「真正可用 = enabled && has_api_key」+ 独立「缺密钥」计数 | 浏览器读页：真正可用 0 / 缺密钥 1 / 已启用 0（此前是矛盾的"启用中 1 / 已配密钥 0"） | ✅ |
| C2 | 状态列三态，其中「启用·缺密钥（不可用）」分支真能渲染 | 临时把该 provider `enabled` 翻 true → 页面显示 **启用·缺密钥（不可用）** → 已改回 `false` 并复核 | ✅ |
| C3 | 新增弹窗未填密钥必须被阻断 | 填 ID+Base URL、留空密钥点「添加」→ 弹窗内报错"API Key 必填…"，**弹窗不关** | ✅ |
| C4 | 未造出垃圾 provider | 事后 `grep -c smoke-no-key custom_providers.yaml` = 0 | ✅ |
| C5 | api_key 输入框标记 required | `bsk observe`：`@e5 textbox "* API Key"` | ✅ |
| M1 | 「校验状态」瓦片在未命中时不再显示"通过" | 临时移除 `llama-3.3-70b` 接口 → 页面显示 **校验状态=需注意 / 3 条未命中接口 id** | ✅ |
| M2 | 未命中时点保存需二次确认 | 同上状态下点「保存」→ 弹出"高危配置确认"，正文列出 `gates.GateA/GateB/GateC` | ✅ |
| M3 | 取消 = 不落盘 | 点「取消」后 `.audit/models/` 文件数仍 10、最新条目仍 `2026-09-19T16-43-00-019254Z` | ✅ |
| M4 | 改回后恢复绿态 | 恢复接口 → 校验状态=通过 / 未命中 0 / 全部命中接口 id | ✅ |
| T1 | 类型检查 | `npx vue-tsc --noEmit` exit 0（无输出） | ✅ |
| B1 | 前端真机 bundle 已更新 | `docker compose build web` + `up -d web`（记忆铁律：改前端必须单独 build web，否则静默停在旧包） | ✅ |
| R1 | 心跳复核：三闸仍真可用 | `probe_model_quota.py 9910…`：三闸 `llama-3.3-70b` → OK（直接 json.loads），无"未命中"告警 | ✅ |

## §2 未做（明确边界，避免被误判为遗漏）

- **不做后端 PATCH / 启用开关 / 密钥回填**：那是真·控制器改造，涉及密钥写落盘的权限与审计，须单独立项（见设计 §2 K1）。因此本档**不能**救活已存在的"缺密钥 provider"，只能在 UI 如实标红 + tooltip 给出处理路径。
- **不做「未命中即报错」**：`DESIGN_model_mapping_uf.md` 档 3 明确否决（会废掉主账号历史裸名写法）。本档只做二次确认。
- **不改审计口径**：带未命中的保存仍记 `validation=pass`（真实 API 行为未变）。根治需给 `_write_audit` 加 `warnings` 字段 → 属后端契约变更，记账。
- **K3 `Login.vue` 未改**：复核 `Login.vue:86-94` 已按 `role==='main' && status==='active'` 分支，旧债描述失效，改它反而是破坏正确实现。

## §3 验证过程中的临时操作（已全部还原，供审议核对是否留脏）

1. `custom_providers.yaml`：`enabled false → true`（验 C2）→ 已由 `/tmp/cp.keep.yaml` 还原，`grep` 确认 `enabled: false`。
2. `models.yaml`：临时换回无 `llama-3.3-70b` 的旧版（验 M1/M2/M3）→ 已由 `/tmp/models.with_llama.yaml` 还原，`grep -c llama-3.3-70b` = 2。
3. 新增弹窗里填了 `smoke-no-key` / `https://api.smoke.invalid/v1`，**未提交成功**，磁盘无残留。
4. `.audit/models/` 多了两个我手动放的备份：`2026-09-20-manual-models.yaml.bak`、`2026-09-20-manual-custom_providers.yaml.bak`（非本次改动产生，是上一轮配置修复时的备份）。

## §5 rev2：独立审议（gate3-settings-honesty）提出的修复与复核

审议结论初版为 PASS_WITH_NOTES，2 项 MAJOR 均指向「页面自称通过」，已修复如下。

### MAJOR-1 顶部绿色横幅与告警条打架

- 位置：`Models.vue:36-45`（原 `v-if="errors.length"` + **`v-else` 绿色成功**）。
- 问题：只要没有硬错误就挂绿色「校验通过」，未命中时页面同时存在绿色成功横幅 + 橙色未命中告警 + 瓦片「需注意」。
- 修法：改为四路互斥 `v-if / v-else-if(previewFailed) / v-else-if(unmatchedCount) / v-else`，
  其中未命中态文案为「需注意 · N 条未命中接口 id」，并说明异基座约束仍满足。
- 复核：移除 `llama-3.3-70b` 接口制造未命中后，页面 alert 列表**只有 info + warning 两条，
  不再有 success**：`[el-alert--info]…` / `[el-alert--warning] 需注意 · 3 条未命中接口 id …`。

### MAJOR-2 预检失败时页面谎称「通过 / 全部命中」

- 位置：`refreshPreview()` 的 catch 只清空 `pvMap`，未留失败标记。
- 修法：新增 `previewFailed` ref；成功置 false、异常置 true；瓦片改用四态
  `异常(danger) > 未知(warn) > 需注意(warn) > 通过(brand)`，未命中瓦片显示 `—`
  （hint "预检失败，暂无法判定"）；横幅同步加「校验未知 · 接口预检不可用」分支。
- 复核手法（可复用）：在页面注入 XHR patch（把 `/admin/models/preview` 请求改为触发 error），
  再用 `history.pushState + PopStateEvent` 做 SPA 路由重挂载触发 `load()` → `refreshPreview()`。
  实测显示：横幅 `[el-alert--warning] 校验未知 · 接口预检不可用`；瓦片 `校验状态=未知 /
  接口预检不可用，无法确认是否命中`、`未命中接口=— / 预检失败，暂无法判定`。
- **对初版自审 §4 风险 2 的更正**：我当时写「页面同时显示"—"，未伪造通过」——**不成立**，
  瓦片当时明确显示绿字「通过 / 全部命中接口 id」。是独立审议抓出来的，已改。

### MINOR 澄清（非本档夹带）

- Models.vue 工作树里确有 preview / el-select 重构（`git diff --stat` 358+/29-），但
  `git show HEAD:web/src/views/settings/Models.vue | grep -c pvMap` = **0** → 该重构是更早批次
  （`DESIGN_model_mapping_uf.md` 档 1+2，长期未 commit）的改动，本档只动了统计、横幅、
  `previewFailed` 与 `onSave` 确认四处。
- 自审 §3.4 提到的两个手动备份确实存在：`tenants/9910ebbc-.../.audit/models/
  2026-09-20-manual-{models,custom_providers}.yaml.bak`（已在 `ls` 中复核）。
  「取消=不落盘」另由代码路径保证，不依赖该计数。

### rev2 自测汇总

`npx vue-tsc --noEmit` exit 0；`docker compose build web` + `up -d web`；
上述两个 MAJOR 场景均浏览器实跑；实验所用的 `models.yaml` 临时替换已还原
（`grep -c llama-3.3-70b` = 2），页面回到「通过 / 0 未命中 / 全部命中接口 id」。

## §4 自评风险点（欢迎独立审议重点打）

1. **C3 把 api_key 变成必填是否过严？** 存在"本地 vLLM 免鉴权"的合法场景。证据是引擎 `NewApiLLMClient` 明确要求 key（实测 `缺少 api_key` 报错），且**无 UI 补救通道**——在"允许创建但永远用不了"和"拒绝创建"之间选了后者。若将来后端支持免鉴权端点，需放宽。
2. **M2 的确认是否可被绕过？** 只在"未命中 > 0"时触发；若 preview 接口挂了，`pvMap` 被清空 → `unmatchedCount` = 0 → 无确认。**这是已知的保守行为**（页面同时显示"—"），未伪造通过。
3. **`has_api_key` 的语义是否等于"密钥真的有效"**？不等于——它只说明 `.secrets/custom_providers.json` 里有该 provider 的 key 条目，不校验可用性。文案已限用"缺密钥"而非"无效密钥"。
