# DESIGN · 子账号模型 / 密钥隔离（取消对主账号 new-api 的共享）

> 状态：**待 boss 复核**（v1.0 · 2026-09-15）
> 触发：boss 指令「子账号需要自己配置模型，还有自己用自己的 api key，但是你现在用的是我一整套的
> new-api，这个你要取消，子账号就是用户自己配置模型」
> 关联：`DESIGN_account_hierarchy.md`（多租户 SoT）、`DESIGN_chat_model_picker.md`（自定义 provider）

---

## 1. 问题：子账号在偷用主账号的网关

实测（`tenants/af28b675-…` = wendy1 子账号）：

| 证据 | 值 |
|---|---|
| `tenants/<wendy1>/config/models.yaml` | 与主账号**逐字节同源**（3717B） |
| 其中 `endpoints[0]` | `id: new-api` · `base_url: ${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}` · `api_key: ${NEWAPI_API_KEY:-}` |
| 其中 `gateway.fetch_from_gateway` | `true`（子账号模型下拉会去拉主账号网关的真实模型列表） |
| `config/model_mapping.yaml` | `Researcher/Analyst/Writer → custom/LongCat-2.0`、`Gate* → cloudflare/llama-3.3-70b` |
| `config/custom_providers.yaml` | 主账号的 `Intern-S2-Preview-397B` provider 定义 |

`${NEWAPI_API_KEY}` 由**容器级环境变量**展开，与账号无关 → **子账号每一次 LLM 调用都打在主账号的
new-api 上、烧主账号的额度**。这与 `tenancy.py` 开篇写死的契约第 2 条
（「禁止静默回落全局根目录——静默回落 = 假隔离」）直接冲突。

### 三处泄漏点（按危害排序）

| # | 位置 | 机制 |
|---|---|---|
| **L1** | `server/auth.py:348` → `tenancy.ensure_account_layout(child_id, seed_from=main.id)` | 子账号首次激活时**整份拷贝**主账号 `config/`，含 `models.yaml` / `model_mapping.yaml` / `custom_providers.yaml`。`COPY_EXCLUDE` 只挡了 `.secrets`，配置文件本身照拷 → 密钥占位符虽未拷贝，但它展开的是**全局 env**，等于密钥跟着来了 |
| **L2** | `orchestrator.py:1146-1157` | `endpoints` 为空时**凭空造**一个 `new-api` 端点（取 `NEWAPI_BASE_URL` / `NEWAPI_API_KEY`），**不分账号** → 即使把子账号的 yaml 洗干净，它照样静默回落到主账号网关。这是最阴的一条：修了 L1 不改 L2，表现上「看起来隔离了」但实际没隔离 |
| **L3** | 已落盘的租户目录 | 4 个 tenant 目录已带污染配置；`af28b675`(wendy1) 是活的那个 |
| **L4** | `server/admin.py` 的 6 个 `public_router` 端点 + `.env` 的 `RAT_LEGACY_GLOBAL=1` | **真机复现的活体泄漏**（发现过程见下）。6 个端点（`/templates` `/agents-library` `/plugins` `/models` `/skills` `/channels`）挂在**无鉴权**路由上 → FastAPI 不会跑 `get_current_account` → `tenancy` 的 ContextVar 从未设置 → 资源根在 `RAT_LEGACY_GLOBAL=1` 下回落到仓库全局目录 = **主账号的 config/**。实测：用 wendy1 的浏览器调 `GET /api/v1/models` 返回主账号的模型清单（`auto-chat` / `LongCat-2.0` / new-api 网关动态模型 / 主账号 custom provider） |

### L4 的发现过程（方法论复盘）

A 系列断言（进程内 `set_current_account` 后调加载器）**全绿**，但真实浏览器里子账号的模型下拉
照样列着主账号的网关模型。差别在于：A 系列证明的是「**有上下文时**隔离成立」，证明不了
「真实 HTTP 链路**会带上**上下文」。

这就是本项目的老教训（见 `MEMORY.md` 坑 17）——**fake 会照 bug 写，反向锁定缺陷**：
只测进程内路径，等于自己给自己盖了个合规的章。故补 B 系列直接在 HTTP 上验。

---

## 2. 目标与非目标

**目标**
1. 子账号**永远不会**触达主账号的 new-api 端点与密钥（代码级硬保证，不靠约定）。
2. 子账号的模型 = **自己配的 Provider**（`base_url` + 自己的 `api_key`），在「设置 → 自定义 API」自助添加。
3. 没配 → **诚实报错**并给出可执行指引，**不降级、不静默回落**。
4. 主账号行为不变（它本来就是网关的主人）。

**非目标（本轮不做）**
- 主账号向子账号「下发」模型/额度（不在 boss 指令内）。
- 子账号模型计费/配额统计。
- 前端整体改版——只做「让隔离生效 + 让用户知道怎么配」的最小 UI。

---

## 3. 设计

### D1 · 播种净化（源头，`server/tenancy.py`）

子账号首次激活（approve）时，`models.yaml` / `model_mapping.yaml` / `custom_providers.yaml`
**不继承主账号**，改写入「子账号中性模板」：

```yaml
# models.yaml（子账号）
endpoints: []                 # 用户自己加
interfaces:
  - {id: auto, name: 自动, kind: auto, default: true}
gateway: {fetch_from_gateway: false}   # 不去拉任何网关的真实模型列表
```

```yaml
# model_mapping.yaml（子账号）—— 骨架保留、值留空
roles:  {Researcher: {base: "", model: ""}, Analyst: {…}, Writer: {…}}
gates:  {GateA: {reviews: Researcher, base: "", model: ""}, …}
eval:   {base: "", model: ""}
chat:   {tool_model: ""}
```

- 只写**骨架**不写值：让用户在「模型映射」页看到要填什么，而不是看到一份指向别人模型的假配置
  （铁律「禁假配置」）。空值会让 `/admin/models` 校验不通过 → 显式拦住，符合 fail-loud。
- 写入用 **`if not exists`**：重复 approve（停用→再启用走的是 `approve=False` 分支，本不会重播种）
  不会覆盖用户已填的配置。
- `agents_library.yaml` / `plugins.yaml` / `skills.yaml` / `templates` / `gates` **继续继承**——
  它们是能力定义不是凭据。`channels.yaml` 继承内容已核对：仅 webhook 类型占位，无真实 token。

### D2 · 加载器禁止伪造端点（`orchestrator.py`）

```
endpoints 为空时：
  无租户上下文（CLI / legacy 全局链路） → 保留旧行为（造 new-api，向后兼容脚本与单测）
  有租户上下文（真实账号）             → 返回 endpoints=[] 且 default_endpoint_id=None
```

即：**「这个账号一个端点都没有」是配置错误，必须报错，绝不用别人的端点顶上。**

### D3 · 可执行的错误信息（`orchestrator.NewApiLLMClient._resolve`）

端点集为空时抛：

> 当前账号尚未配置模型 API：请到「设置 → 自定义 API」添加你自己的 Provider（base_url + API Key），
> 再选择模型。子账号不共享主账号的 new-api 网关。

### D4 · 存量数据净化（`scripts/purge_inherited_gateway.py`）

对**非系统主账号**（`Account.is_system_main == False`）的租户目录做幂等清洗：
`models.yaml` 含 `new-api` 端点 → 换中性模板；`model_mapping.yaml` 的 model ∈ 主账号模型集
（`LongCat-2.0` / `llama-3.3-70b`）→ 换中性模板；`custom_providers.yaml` → `providers: []`。
支持 `--dry-run`；逐条打印 before→after 便于核对。

### D5 · 前端：让用户知道「要去配」

1. `ChatEntry.vue`
   - 模型下拉的**默认值不再是 `auto-chat`**（那是主账号网关的 universal alias，子账号没有）。
     改为：取列表里第一个带 `endpoint_id` 且 `kind != auto` 的项；一个都没有 → 空。
   - 一个都没有时显示 `el-alert` 警告 + 「去配置」跳 `/settings/custom-providers`，并**禁用发送**。
   - 已保存的 `localStorage` 选中项若已不在列表中（例如上一轮遗留的 `auto-chat`）→ 自动回落到上述规则。
2. `Models.vue`（模型映射）
   - 拉 `/api/v1/models`，若可用模型为空 → 顶部告警：「本账号尚未配置模型 API。请先到
     『自定义 API』添加 Provider；另注意 `gates.*.base` 必须 ≠ 所审角色的 `base`
     （防自审包庇）→ **至少需要两个不同来源的 Provider 才能通过校验**。」

### D6 · 账号作用域读接口必须鉴权（堵 L4）

`server/admin.py` 的 6 个 `public_router` 端点一律加 `Depends(_tenant_required)`
（内部即 `auth.get_current_account`：校验 Bearer → `set_current_account` → 返回账号）。
前端三个裸 `fetch('/api/v1/...')` 改走带 Bearer 的 `authFetch` / `api` 客户端
（`ChatEntry.vue` 的 `/models`、`/plugins`；`Models.vue` 的 `/models`）。

**并删除 `.env` 里的 `RAT_LEGACY_GLOBAL=1`**（代码注释里早就写明「Phase 2 前必须删除，
留着就是假隔离」）。删除后：没有租户上下文 → 显式 `TenancyError` / 401，而不是静默借用
主账号配置。`api.py:_current_aid()` 已有 `except TenancyError → 401` 的对应处置，语义一致。

> 影响面已核：`api.py` 的 `router = APIRouter(dependencies=[Depends(get_current_account)])`
> 说明 `/chat`、`/tasks` 等主链路**本来就带鉴权依赖**，上下文正常；`admin_router` 走
> `_require_admin`（内部按 Bearer `set_current_account`）；引擎子进程走 `RAT_ACCOUNT_ID`。
> 真正裸奔的只有这 6 个。

### D8 · 未配置模型必须显式报错（B4 暴露的第二层掩盖）

B4 首跑 **HTTP 200 + 一句正常回复**，而该子账号一个端点都没有。原因在
`chat_agent.py` 的 FC 循环外层：

```python
except Exception as e:            # ← 把"没配模型"和"模型偶发抽风"一视同仁
    return {"intent": "chat", "reply": f"抱歉，脑子打结了：{e!s}", ...}
```

于是"配置缺失"被伪装成"助手答不出来"，前端按正常助手气泡渲染 → 用户永远不知道该去配。
这与本项目铁律「跑不通就降级，绝不静默吞错」冲突。修法：

1. `chat_agent` 新增 `ModelNotConfiguredError`，并在 `step()` 入口前置判断
   （`_llm_has_endpoints()`：客户端无 `_endpoints` 键 = 非 NewApiLLMClient 的替身 → 不判定，
   保持单测可用）；未配置即抛，**不进入 fc-loop**。
2. `server/api.py` 的 `/chat` 单独捕获它 → `400` + **字符串** detail
   （传对象会被前端 `ApiError` 的 `String(body.detail)` 变成 `"[object Object]"`，把指引吃掉——
   这是踩过的坑，故 detail 必须是字符串）。
3. 前端 `ChatEntry.vue` 对该文案**不加**「服务出错」前缀——它本身就是可执行指引。

> 仍保留的诚实边界：用户自己 Provider 的偶发故障（429/超时）依旧走「脑子打结了」，
> 那是真的"模型答不出来"，不该冒充配置问题。

### D7 · 不变的约定

- `异基座`硬约束（`server/admin.py:230 _validate_mapping`）**不放松**。它是防自审包庇的治理规则，
  子账号同样适用；代价是子账号需自备 ≥2 个不同来源的 Provider —— 在 UI 里明说，不偷偷放宽。
- 主账号仍持 new-api 端点，行为零变化。

---

## 4. 判定标准（怎么算做完了）

| # | 断言 | 手段 |
|---|---|---|
| A1 | 新建/既有子账号的 `config/` 目录内**不出现** `new-api` / `NEWAPI_API_KEY` / `LongCat-2.0` 字样 | grep 租户目录 |
| A2 | 在子账号上下文下 `load_model_interfaces_and_endpoints()` 返回 `endpoints == []`、`default_endpoint_id is None`，**即使容器里 `NEWAPI_API_KEY` 有值** | 容器内脚本断言 |
| A3 | 子账号未配 Provider 时发一条聊天 → 报错文案为 D3 的可执行指引，且**没有对主账号网关发起任何 HTTP 请求** | 真实 `/chat` + 网关侧无新流水 |
| A4 | 子账号自加一个 Provider 后 → 模型下拉出现该 Provider 的模型 → 聊天走**子账号自己的 base_url/key** | 端到端真跑 |
| A5 | 主账号聊天/研报链路**不受影响**（回归） | 主账号真实对话 |
| A6 | `wendy1` 现有账号被 D4 净化后仍能登录、能进「自定义 API」页 | 真实浏览器 |

**B 系列（HTTP 链路，堵 L4 后新增——A 系列全绿也不足以判定通过）**

| # | 断言 | 手段 |
|---|---|---|
| B1 | 带子账号 token 调 `GET /api/v1/models` → **不含**主账号网关模型 | 真 HTTP |
| B2 | 带主账号 token 调 `GET /api/v1/models` → 仍含 new-api 网关模型（回归） | 真 HTTP |
| B3 | **不带** token 调 `GET /api/v1/models` → 401（旧行为是 200 + 泄漏） | 真 HTTP |
| B4 | 带子账号 token 发一条 `/chat` → 被拦住且文案是可执行的配置指引 | 真 HTTP |

---

## 5. 风险

| 风险 | 处置 |
|---|---|
| 引擎子进程若未 `bind_account` 就拿不到租户上下文 → 误判为「CLI 链路」而放开旧兜底 | D2 的判定要在**引擎真实子进程**里实测（A2 覆盖 api 进程；补一条引擎入口断言） |
| 存量子账号被净化后「打不了字了」，用户体感是坏了 | D3 文案 + D5 前端告警给明确指引；A6 用真实浏览器确认页面可到达 |
| 中性模板写空值 → `/admin/models` 一进去就红 | 这是**有意**的：未配置就该显式红。文案里说明「这是尚未配置，不是出错」 |
