# DESIGN · 模型映射「可由 UI 控制 + 解析宽容且可见」

> 状态：待实施（2026-09-19）
> 触发：boss 子账号配 `deepseek-v4-flash-vision` 裸名 → 请求被打到 agnes → 503 `model_not_found` → 三道质量闸**静默降级放行**。
> 范围：**系统级**（UI + 引擎 + 子账号模板），**主账号必须同步适配、行为不得回退**。
> 档位：boss 批准「档 1 + 档 2」，**明确不做档 3**（未命中即报错）—— 见 §5 风险。

---

## 1. 实测证据（全部真机复现，非推断）

### 1.1 两个账号的解析结果对照

| 账号 | 默认端点 | `self._interfaces` key | 映射里的 model | 是否命中接口 | 实际打到 |
|---|---|---|---|---|---|
| 主账号 `9910ebbc` | **new-api** | `LongCat-2.0` / `auto*` / `custom:Intern-…`（共 7） | `LongCat-2.0` | ✅ HIT | new-api |
| 主账号 | | | `llama-3.3-70b` | ❌ miss | **回落 new-api** ✅ |
| 主账号 | | | `glm-4.7` | ❌ miss | **回落 new-api** ✅ |
| 子账号 `af28b675` | **custom:agent**（agnes） | `auto` / `custom:agent:agnes-3.0-flash` / `custom:discovery:deepseek-v4-flash-vision`（共 3） | `agnes-3.0-flash` | ❌ miss（裸名） | 回落 `custom:agent` → **侥幸正确** |
| 子账号 | | | `deepseek-v4-flash-vision` | ❌ miss（裸名） | 回落 `custom:agent` → **打错厂商 → 503** |

### 1.2 根因链（三层）

1. **接口 id 天生带前缀**：`orchestrator.py:1159` `interface_id = f"custom:{cp['id']}:{mid}"`。
2. **解析是精确查表**：`orchestrator.py:485` `iface = self._interfaces.get(model)` —— 无模糊、无消歧。
3. **未命中静默回落默认端点**：`orchestrator.py:494-496`；默认端点 = 第一个有 `base_url` 的端点（`:1179-1185`，`new-api` 优先）。

→ **同一份"裸名"写法，主账号对、子账号错**：主账号的默认端点恰是「什么都知道」的 new-api 网关；子账号没有网关，默认端点退化成它自己的第一个 provider（agnes）。

### 1.3 派生缺陷（本次一并修）

| # | 位置 | 问题 |
|---|---|---|
| D1 | `web/src/views/settings/Models.vue:47/52/71/80/104/107` | `base` / `model` 全是 `<el-input>` 手填框，placeholder 写着「如 custom」「如 LongCat-2.0」——**在诱导用户填裸名**。 |
| D2 | `orchestrator.py:494` | 未命中接口时**静默**回落，无日志、无告警 → 错误被藏起来（boss 这次就绕了大弯）。 |
| D3 | `Models.vue:88` + `server/admin.py:266-271` | 「异基座 ✅」绿勾**只比对 `base` 两个字符串**，完全不校验 model 是否真在该 provider 上 → 绿勾骗人。 |
| D4 | `server/tenancy.py` `NEUTRAL_MODEL_MAPPING_YAML` | 模板指引写「把 model 填成该 Provider 上的**真实模型 id**」→ **与解析器要求相反**，每个新子账号必踩。 |

---

## 2. 设计目标

| # | 目标 | 对应档 |
|---|---|---|
| G1 | 用户**无法**在 UI 上填出一个"解析不到正确厂商"的 model（下拉候选 = 真实接口 id） | 档 1 |
| G2 | 每条配置**实际会打到哪个端点**在 UI 上可见（消灭"侥幸"与"绿勾骗人"） | 档 1 |
| G3 | 裸名若在当前账号**唯一**可归属，则自动消歧；否则回落但**必须留 WARNING 日志** | 档 2 |
| G4 | 修正子账号模板指引 | 附带 |
| G5 | **主账号零回退**：现有 `model_mapping.yaml` 一字不改即可继续工作 | 硬约束 |

---

## 3. 档 1 · UI 改为 controller（`Models.vue` + 新预检 API）

### 3.1 候选数据源（已存在，无需新造）

`GET /api/v1/models`（`server/admin.py:1813`，**账号作用域、需登录**）→ `items[]`：
```json
{ "id": "custom:discovery:deepseek-v4-flash-vision",
  "name": "deepseek-v4-flash-vision",
  "kind": "concrete", "source": "custom",
  "endpoint_id": "custom:discovery" }
```
- `id` → **`model` 的下拉值**
- `endpoint_id`（去重）→ **`base` 的下拉值**

实测候选规模：主账号 **122** 项（本地 2 + 网关 120，`gateway_ok=true`）；子账号 **3** 项（`gateway_ok=false`，诚实标注）。

### 3.2 控件改造

| 字段 | 改造 | 兼容措施 |
|---|---|---|
| `model` | `el-select` `filterable`，option = `id`，label = `name` + `（来源 · 端点）` | `allow-create` 兜底；主账号 122 项必须可搜索 |
| `base` | `el-select` `filterable`，option = `endpoint_id` 去重 | **`allow-create` + 现有值并入候选**——主账号的 `custom`/`cloudflare` 是**网关内渠道名**，不在 `endpoint_id` 里，必须保留 |

### 3.3 新增解析预检 API（消灭 D3，兑现 G2）

```
POST /api/v1/admin/models/preview
body: { roles: {...}, gates: {...}, eval: {...} }        # 与 PUT /admin/models 同构
resp: { items: [ { path: "roles.Researcher",
                   model: "LongCat-2.0",
                   endpoint_id: "new-api", real_model: "LongCat-2.0",
                   matched: true, fallback: false,
                   sibling_bases: [...],                  # 该模型在哪些端点也提供（歧义预警）
                   note: "" } ] }
```

**实现要求：复用 `NewApiLLMClient._resolve()`，禁止前端或 admin 重写解析规则**（单一事实源，避免规则漂移）。返回体**不含 api_key**。

### 3.4 状态列重做（诚实化）

| 情形 | 展示 |
|---|---|
| model 命中接口 | `✅ 命中接口 · 端点 custom:discovery` |
| model 未命中但回落可用 | `⚠️ 未命中接口 → 回落端点 new-api（可用，但依赖排序）` |
| 未命中且回落端点为空 | `❌ 无可用端点` |
| 闸与所审角色 base 相同 | `❌ 与 X 同基座`（保留现有字符串口径，见 §5） |

**异基座仍按 `base` 字符串比对**（`isViolation` 不变）——原因见 §5.1。

---

## 4. 档 2 · 引擎解析宽容且可见（`orchestrator.py::_resolve`）

```
输入 model
 ├─ 命中 interface（精确 key）            → 用其 endpoint（现状）
 ├─ 未命中 → 尝试「裸名唯一归属」
 │    ├─ 在 self._interfaces 中找 model_id == model 的项
 │    ├─ 恰好 1 个 → 采用它 + INFO 日志（记录自动消歧）
 │    ├─ 0 个     → 回落默认端点 + WARNING（含可用接口清单摘要）      ← 现状行为 + 新增日志
 │    └─ ≥2 个    → 回落默认端点 + WARNING（提示歧义，列出候选端点）
 └─ 默认端点也不存在 → 保持现状抛 LLMError（可执行指引）
```

**实现要点**
- 在 `load_model_interfaces_and_endpoints()` 后构建**裸名倒排索引** `{model_id: [iface, ...]}`，一次性 O(n)，避免每次解析 O(n) 扫描。
- 索引随 `_interfaces` 同步刷新（`NewApiLLMClient` 已有按账号缓存/失效机制）。
- 保持 `_resolve()` **返回值形态不变**（`Tuple[eid, real, cfg]`），不破坏既有调用点。

---

## 5. 兼容性与风险（主账号零回退的证明）

### 5.1 ⚠️ 为什么**不能**把异基座校验改成"比解析后的端点"

主账号现状：`roles.*.base = custom`、`gates.*.base = cloudflare`，但**解析后的端点都是 `new-api`**（同一个网关）。
若把口径改成比端点 → 三道闸**全部**判定"同基座" → 保存被禁 → **主账号配置从此改不动**。故口径保持字符串比对，只在 UI 上**额外展示**解析端点（信息性、非阻断）。

### 5.2 主账号行为不变的逐条核对

| 主账号 model | 改动后解析 | 与现状对比 |
|---|---|---|
| `LongCat-2.0` | HIT 接口 → `new-api` | 不变 |
| `llama-3.3-70b` | 索引里无 `model_id == llama-3.3-70b` → 0 命中 → 回落 `new-api` | **不变**（仅多一条 WARNING 日志） |
| `glm-4.7` | 同上 | **不变** |
| `eval` / `chat.tool_model` = `LongCat-2.0` | HIT → `new-api` | 不变 |

> 备注：`self._interfaces` 只含**本地**接口（实测主账号 7 项），**不含**网关动态拉取的 120 项；`/models` 的 122 项是 admin 层单独 merge 的。故"唯一归属"不会把主账号的网关模型抢到别的端点。
> 即便将来网关模型被并入 interfaces，其 `endpoint` 亦为 `new-api`，解析结果仍然相同 → 无回退。

### 5.3 子账号行为变化

改配置后（本次已用真实 API 写入，注释经 ruamel round-trip 保留）：全部 6 项 + `eval` 均 HIT 接口，各归各家。裸名宽容是**兜底**，不替代正确配置。

---

## 6. 附带修复 · 子账号模板指引（D4）

`server/tenancy.py::NEUTRAL_MODEL_MAPPING_YAML` 的指引改为：

```yaml
#   2) 下面的 model 必须填「接口 id」，格式 custom:<provider id>:<模型 id>
#      例：custom:agent:agnes-3.0-flash
#      （只填裸模型名会因解析不到而被回落到默认端点，可能打到错误厂商并报 503）
#   3) base 填该 Provider 的 id（与 custom_providers.yaml 的 id 一致）
```

并在模板顶部给出**可直接照抄的两 provider 示例**，避免新账号再次踩坑。

---

## 7. 验收标准（全部须实测）

| # | 验收项 | 判定 |
|---|---|---|
| A1 | 主账号打开「模型映射」页，`model` 下拉含 `LongCat-2.0`/`llama-3.3-70b`，`base` 保留 `custom`/`cloudflare` 不丢值 | 页面实测 |
| A2 | 子账号打开同页，`model` 下拉只含本账号 2 个接口 id（**不含**主账号网关模型） | 页面实测 |
| A3 | 子账号从下拉选 `deepseek-v4-flash-vision` → 写入的是 `custom:discovery:deepseek-v4-flash-vision` | 保存后读盘 |
| A4 | 状态列对 `llama-3.3-70b`（主账号）显示"未命中 → 回落 new-api"，而非绿勾 | 页面实测 |
| A5 | 真实任务跑通：三道闸 `eval_score` 为**真分数**（非 `null`），`routing.status = done` | 引擎产物 JSON |
| A6 | 主账号真实任务不回归（解析结果与改动前逐条一致） | 引擎产物 JSON |
| A7 | `POST /admin/models/preview` 返回值不含任何密钥 | 响应体检查 |

---

## 8. 不做的事（明确划线）

- ❌ **档 3**：未命中即报错 —— 会当场废掉主账号的 `llama-3.3-70b`/`glm-4.7`（见 §5.2）。
- ❌ 不改 `GateReview.eval_score` 类型契约（另一条独立的修复线，与本设计解耦）。
- ❌ 不改任务持久化（`tasks` 表空转，独立议题）。
- ❌ 不引入新依赖、不改 `docker-compose.yml`。
