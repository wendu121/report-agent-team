# DESIGN_settings_honesty.md · 设置页「假配置」诚实化（UI 层，档 1）

> 状态：草稿待独立审议（gate ③）。**先设计后写码**（项目治理铁律）。
> 范围：仅两个 Vue 视图，**不改后端契约、不新增端点、不动引擎解析逻辑**。
> 背景：2026-09-20 凌晨主账号 inspect 发现的两处 UI 失真 + 当晚修复的配置层问题。

---

## §1 问题（坐实的证据）

### P1 自定义 API 页：「启用」与「有密钥」是两个互不相干的开关

后端只有 `GET /admin/providers`（`server/api.py:1491`）、`POST`（1497）、`DELETE`（1558），
**没有 PATCH / 启用开关 / 密钥回填接口**。而引擎侧明确要求密钥——实测：

```
docker compose exec -T api python /app/scripts/probe_candidate_models.py
  [Intern-S2-Preview-397B]
    ❌ LLMError: 端点 'custom:Intern-S2-Preview-397B' 缺少 api_key，请在 .env 注入对应密钥
```

即「无密钥 + enabled:true」在本系统里是**必然失败且无从补救**的状态：
UI 显示「启用 / 下个任务可用」，调用一定炸。2026-09-20 已把主账号该 provider
数据层置 `enabled:false`，但**UI 仍允许再造一个同样的**（新增弹窗的密钥字段标注"可选"、
`enabled` 默认 true）。

主账号实测到的自相矛盾（修复前）：「已注册 1 / 启用中 1 / 已配密钥 0」。

### P2 模型映射页：「校验通过」与「N 条未命中（高危）」并存

- `Models.vue:361-382` 的 `errors`（前端硬校验）只管空值 / reviews 指向 / 异基座，**不含未命中**；
- `Models.vue:322-327` 的「校验状态」瓦片只看 `errors` → 未命中时仍显示「通过」；
- `Models.vue:479-498` 的 `onSave()` 只挡 `errors` → 未命中配置一路放行并写入审计 `validation=pass`。

2026-09-20 已通过**补注册 `llama-3.3-70b` 接口 id** 让主账号归零（如何从"错"变成"对"，
见 §4），但**机制仍在**：下次任何人选个未注册的裸名，页面照样一边标"通过"一边标"高危"。

---

## §2 决策（K1–K3）

### K1（采用）自定义 API：从源头杜绝 + 诚实三态显示 —— 不新增后端开关

| 选项 | 说明 | 取舍 |
|---|---|---|
| A 新增 PATCH 端点 + 启用开关 + 密钥回填表单 | 真·控制器，能救已有 provider | ❌ 超出本档；涉及密钥写落盘的权限/审计设计，须单独立项 |
| **B 前端预防 + 三态显示（采用）** | 不让造出「启用·缺密钥」，已有的如实标红 | ✅ 零后端改动、可逆、直击失真 |

采用 B，三项改动：

1. **新增弹窗**：未填 API Key → **禁止提交**（标为必填），错误文案说明
   「本系统引擎要求 api_key；后端当前无补密钥接口，创建后无法补救」。
   把 `hint` 的"可选"改成如实说明。
2. **表格状态列三态**：`禁用` / `启用` / **`启用·缺密钥（不可用）`**（danger），
   后者带 `title` 提示处理路径。
3. **顶部统计**：「启用中」按 **`enabled && has_api_key`**（真正可用）计数，
   另加「缺密钥」计数 —— 消除"启用中 1 / 已配密钥 0"的自相矛盾。

> 边界（诚实）：本档**不**给已存在的 provider 提供起死回生的能力（无后端接口），
> 只能在 UI 上如实标红并写明出路（注入密钥到 `.secrets/custom_providers.json`
> 或改 `custom_providers.yaml` 后重建）。

### K2（采用）模型映射：未命中 → 显式二次确认，而非静默放行 —— 不做「未命中即报错」

| 选项 | 说明 | 取舍 |
|---|---|---|
| A 「未命中即报错」彻底阻断 | 最硬，配置从此不可能歪 | ❌ = `DESIGN_model_mapping_uf.md` **档 3 明确不做**：会当场废掉主账号历史裸名写法，裸名回落是既定设计行为 |
| B 后端 `_validate_mapping` 加未命中检查 | 拦在所有入口之前 | ❌ 等价于 A，且改审计口径（`validation=fail` 语义外扩），跨里程碑 |
| **C 前端强制二次确认 + 瓦片如实显示（采用）** | 未命中时保存前弹确认并列出条目；瓦片不再标"通过" | ✅ 不阻断、不改后端，但**不再让人无感知地存出高危配置** |

采用 C：

- 「校验状态」瓦片：未命中 > 0 → `value='需注意'`、`tone='warn'`、hint=`${n} 条未命中接口 id`（有真错误时仍为「异常」/danger 优先）。
- `onSave()`：未命中 > 0 → `ElMessageBox.confirm` 列出未命中条目与后果
  （「将回落默认端点，可能打到意料之外的模型」），**取消则不落盘**。
- 不动 `buildPayload()` / 不动后端。**不改审计口径**（仍是 save/passes）。

> 已知残留（本档不修，记账）：审计仍会把带未命中的保存记为 `validation=pass`。
> 要根治须改 `_write_audit` 语义（新增 `warnings` 字段），属后端契约变更。

### K3 `Login.vue` 注册文案 —— **经复核已修复，本档不动**

旧债描述「硬编码'等待主账号审批'，忽略 role=main/status=active」**已失效**：
`web/src/views/Login.vue:86-94` 已按 `acc.role === 'main' && acc.status === 'active'`
分支处理，第 48 行说明文案也与后端行为一致。**又一条过期记忆**（同批还有
「admin Bearer 全失效」已由 ContextVar 修复）。

---

## §3 实现清单（精确到现文件行号）

| 文件 | 位置 | 改动 |
|---|---|---|
| `web/src/views/settings/CustomProviders.vue` | 115-119 `stats` | 「启用中」按 `enabled && has_api_key`；新增「缺密钥」计数 |
| 同上 | 23-29 密钥列 | 补 `title` 说明 |
| 同上 | 33-39 状态列 | 三态：`禁用` / `启用` / `启用·缺密钥（不可用）`(danger) + `title` |
| 同上 | 69-72 密钥 hint | "可选" → 如实说明"本系统要求；无 UI 补密钥通道" |
| 同上 | 158-185 `onSubmit` | 未填密钥 → 阻断提交并给出原因 |
| `web/src/views/settings/Models.vue` | 322-327 校验瓦片 | 未命中时显示"需注意"而非"通过" |
| 同上 | 479-498 `onSave` | 未命中 → `ElMessageBox.confirm` 二次确认，取消不落盘 |

**不改**：`server/api.py`、`server/admin.py`、`orchestrator.py`、契约类型、审计结构。

---

## §4 为什么主账号现在是「0 未命中」（与本档的关系）

本档**不是**消除未命中的手段。主账号归零是因为 2026-09-20 在
`tenants/9910ebbc-.../config/models.yaml` 补注册了
`id: llama-3.3-70b (concrete, endpoint: new-api, model_id: llama-3.3-70b)`，
把「隐式回落」变成「显式命中」（路由行为不变）。P2 修的是**机制**：
让下一次再出现未命中时，页面不再"通过"与"高危"同时挂着。

---

## §5 验收计划（真实执行，不含浏览器不算过）

1. **类型**：`docker compose exec web npx vue-tsc --noEmit`（或宿主等价）→ exit 0。
2. **浏览器实测**（bsk，主账号临时 token）：
   - 自定义 API 页：Intern-S2-Preview-397B 行状态 = `启用·缺密钥（不可用）`；
     统计「启用中 0 / 缺密钥 1」。
   - 新增弹窗：Base URL 填 `https://example.invalid/v1`、**不填密钥** → 点「添加」被阻断并显示原因。
   - 模型映射页：临时把 GateC 的 model 改成未命中值 → 校验瓦片变「需注意」；
     点保存 → 弹二次确认 → **取消**（不落盘）；再改回 `llama-3.3-70b` 确认恢复绿态。
3. **回滚**：`git checkout -- <两个 vue>` + `docker compose build web && up -d web`。

> ⚠️ 改前端必须重建 **web** 镜像（`api`/`web` 独立镜像），否则浏览器里仍是旧 bundle 且不报错。
