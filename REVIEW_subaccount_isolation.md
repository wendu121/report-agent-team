<!-- reviewed-by: independent-subagent -->

# REVIEW_subaccount_isolation.md — 子账号模型/密钥隔离 · 独立审议（gate ③）

- **审议者**：general-purpose-29（独立审议子代理，运行于 CodeBuddy Code / Hy3 环境）。本代理与主实施者无关联，所有结论均自行重跑/自行证伪得出，未采信 VERIFICATION.md 的任何结论。
- **审议时间**：2026-09-19 19:50（容器时钟 UTC 11:50；北京时间 UTC+8 19:50）
- **被审里程碑**：「取消子账号继承主账号 new-api 网关，子账号自配模型与密钥」（DESIGN_subaccount_model_isolation.md）
- **审议方法**：只读源码 + 在运行中的 `report-api` 容器内独立重跑 `verify_subaccount_isolation.py` + 自行构造对抗性用例（T1–T4），逐条证伪 REVIEW.md §1 清单。未修改任何应用源码、未修改 VERIFICATION.md、未 git commit/push；唯一写入文件为本报告。

---

## 结论

**PASS_WITH_NOTES**。

- **BLOCKING 项（按「假隔离 / 假配置 / 数据泄漏 / 用户拿到假结果」定义）：0 条。** 核心隔离保证经独立重跑验证成立——子账号在「有上下文」与「引擎子进程」两条路径下都不会触达主账号 new-api 网关；无 token 读取账号作用域接口被 401 拦截；未配置模型的子账号被诚实拦下而非静默借用主账号网关。
- **HIGH-1（原 `stores/template.ts:18` 裸 fetch → `/templates` 401 回归）已闭环。** team-lead 在 gate ③ 出结论后单独修复（仅改 `web/src/stores/template.ts` 两处：新增 `import { authFetch } from '@/api/client'` 并将第 18 行裸 `fetch` 改为 `authFetch`），我作为独立审议者**事后复核**确认修复正确（见文末「§附录 · 修复复核」）。剩余唯一未验证项是「已重建的前端 bundle / 浏览器真机点击」——受「不重建/重启容器」约束，我无法实证，但源码与类型检查层面证据充分（见附录）。
- （原报告此处曾把该项列为「建议交付前必修 HIGH」；修复复核后降级为已闭环，仅留上述 bundle 级未验证标注。）

---

## 逐条回应 REVIEW.md §1 补审清单

### A. 隔离是否真的无可绕过（最高优先）

**A·`_has_tenant_context()` 异常分支（REVIEW.md §1 A 第 1 点）**
- 结论：**不成立（清单假设与代码不符）**。清单称「异常分支返回 `False`（=允许 env 回落）」，但当前代码：
  ```python
  # orchestrator.py:972-975
  try:
      return tenancy.current_account_id(allow_none=True) is not None
  except Exception:  # noqa: BLE001
      return True
  ```
  异常分支返回的是 `True`（=按「有账号」处理 → 诚实返回空端点），是**安全方向**，不会静默回落主账号网关。清单的担忧在当前代码下不成立。
- 我已实测引擎子进程「无 `account_id`」路径（T3）：`load_model_interfaces_and_endpoints()` 在 `LEGACY_GLOBAL=False` 下因 `config_path → current_account_id(allow_none=False)` 直接抛 `TenancyError`，**根本走不到「伪造 new-api」分支**（见 T3 输出）。即删除 `RAT_LEGACY_GLOBAL=1` 后，无上下文的任务**失败 loud** 而非借用主账号网关。
- 证据：`orchestrator.py:957-975`、`tenancy.py:195-212`、`tenancy.py:223-229`；T3 输出：`EXCEPTION (no-context load): TenancyError 当前线程没有租户上下文……`。

**A·除 6 个 public_router 接口外是否还有无鉴权账号作用域读接口（REVIEW.md §1 A 第 2 点）**
- 结论：**成立（已修复）+ 1 处额外未鉴权项（任务事件 WS，非模型/密钥泄漏，单列）**。
- 6 个 `public_router` 端点均已加 `Depends(_tenant_required)`（实测无 token 全部 401，见下）：
  - `server/admin.py:1090-1091 (/templates)`、`1302-1303 (/agents-library)`、`1628-1629 (/plugins)`、`1813-1814 (/models)`、`2130-2131 (/skills)`、`2356-2357 (/channels)`，每个签名均带 `_acc: Account = Depends(_tenant_required)`。
  - `server/admin.py:881` 定义 `public_router = APIRouter()`；`900` 定义 `_tenant_required`；`main.py:112` 挂载为 `/api/v1`。
- 交叉比对 `APIRouter(` 与 `tenancy.config_path(`（全仓 `server/`）：`api.py:30` 的 `router = APIRouter(dependencies=[Depends(get_current_account)])` 全局鉴权；`admin.py` 主路由（`router = APIRouter()` line 92）全部内部调 `_require_admin(x_admin_token)`（约 50 处），按 Bearer 设上下文。除这 6 个外，未见其它无鉴权账号作用域读接口。
- 额外发现：`server/websocket.py:14` `router = APIRouter()` 无鉴权依赖，`/tasks/{task_id}/stream`（line 185）仅凭 `task_id` 订阅。它**不读取任何账号配置**（仅按 task_id 中转引擎事件），故不构成模型/密钥泄漏；但属「按 task_id 即可订阅他人任务事件流」的越权信息面，超出本轮范围，建议另立议题处理。
- 证据：grep `APIRouter(` / `config_path(` / `tenant_path(` 全仓结果（见审议记录）；`server/websocket.py:14,185`；HTTP 实测：无 token `/templates|/skills|/agents-library|/channels` 均 → 401，带 token → 200。

**A·删除 `RAT_LEGACY_GLOBAL=1` 是否打断真实链路（REVIEW.md §1 A 第 3 点）**
- 结论：**部分成立**——研报任务全链路本身未受影响（主账号 / 已配 provider 的子账号均按预期工作），但暴露 1 个**前端回归**（template.ts，见上文 HIGH 项）与 1 个**行为变更**（无 account_id 的遗留任务现在失败 loud）。
- 容器实测 `RAT_LEGACY_GLOBAL` env = `None`、`LEGACY_GLOBAL = False`（已从删除后的 `.env` 生效，且运行容器确实如此，非仅工作树）。
  - 证据：`docker compose exec api python -c "import os; from server import tenancy; print(os.getenv('RAT_LEGACY_GLOBAL'), tenancy.LEGACY_GLOBAL)"` → `None / False`。
- 登录/注册（`auth.py` 独立 `APIRouter(prefix='/auth')`，不依赖 tenancy 上下文）→ 不受波及（代码层面合理推断；未做 HTTP 登录重放，因无 boss 凭据，标注为「未做 HTTP 重放」）。
- 主账号研报/对话链路回归：verify A5/B2 显示主账号仍持 `new-api` 端点（items=122），VERIFICATION §2.3 实跑 wendy `/chat` 返回真实回复（HTTP 200）。我独立重跑得到相同结论。
- 能力市场各页：Channels/Plugins/Agents/Skills 前端均改用 `authFetch`（实测带 token 200）；**唯 `/templates` 经 `template.ts` 裸 fetch 失败**（见 HIGH 项）。
- 推送渠道 `/channels`：前端 `Channels.vue:194` 用 `authFetch` → 带 token，正常。

**A·`_write_neutral_seed_files()` 覆盖判定（REVIEW.md §1 A 第 4 点）**
- 结论：**部分成立（构造时序可复现，但实际可达性低）**。
- 代码逻辑（`tenancy.py:131-156`）：仅当目标文件「不存在」或「与主账号原样副本逐字节相同」时才覆盖；若子账号文件 ≠ 主账号当前文件 → 视为「用户改过」→ 跳过（保守不动）。
- 我构造了时序（T2）：把子账号 `models.yaml` 写成「主账号旧文件 + 一行漂移注释」（即 polluted 且 ≠ 主账号当前），再调 `_write_neutral_seed_files` → **确实未净化**（仍含 `new-api`），且该子账号在上下文中加载到 `['new-api']` 端点。证明该保守判定在「隔离构造」下会漏净化。
  - 证据：T2 输出 `AFTER _write_neutral_seed_files -> still contains new-api: True`；`child endpoints loaded: ['new-api'] default: new-api`。
- **为何实际可达性低**：真实 `ensure_account_layout(seed_from=main)` 在调 `_write_neutral_seed_files` **之前**会 `_copy_tree_filtered(seed_root, root)` 先把主账号**当前**配置整份覆盖到子账号（`tenancy.py:283-291`）。因此重新 approve 时，子文件被重置为「=主账号当前」→ 与 `seed_root` 相等 → 被中性化。即「漂移 pollut+重审批」的漏净化路径在真实调用链里不会发生（复制步骤已先对齐）。真正的存量污染由 `purge_inherited_gateway.py` 负责（见下），其按 `id=="new-api"` 判定，能抓到该类污染。
- 结论：函数本身的保守性是真实存在的设计弱点（不宜作为「净化」保障），但被「复制-再比较」与 purge 脚本双重覆盖，不构成可直达的隔离泄漏。列为 LOW（防御性建议：让 `_write_neutral_seed_files` 也识别「端点指向 NEWAPI_* env 展开」这类本质判据，而非仅字节比较）。

**A·`purge_inherited_gateway.py` 污染特征完备性（REVIEW.md §1 A 第 5 点）**
- 结论：**部分成立（存在特征盲区）**。
- 判定仅按：`id == "new-api"`（`_models_polluted`，`scripts/purge_inherited_gateway.py:130-140`）；模型名白名单 `{"LongCat-2.0","llama-3.3-70b","glm-4.7"}`；base 白名单 `{"custom","cloudflare","zhipu","google"}`（`_mapping_polluted`，line 143-173）。
- 我构造 T4：子账号 `models.yaml` 含端点 `id: my-gateway`（**非 new-api**）但 `base_url: ${NEWAPI_BASE_URL}`、`api_key: ${NEWAPI_API_KEY}`（即指向主账号网关）→ `--dry-run` 扫描 7 个租户，**未报告该子账号**（CHILD REPORTED AS POLLUTED BY PURGE? False）。
  - 证据：T4 输出 `CHILD REPORTED AS POLLUTED BY PURGE? False`；purge 仅报告 wendy1 的 model_mapping/custom_providers 与遗留测试账号。
- 影响评估：当前唯一活体遗留子账号 `wendy1` 的 `models.yaml` 已被净化（T4 输出中 wendy1 仅 model_mapping/custom_providers 命中，models.yaml 未命中）→ 当前数据无此盲区实例。但作为「一次性修复脚本」，若将来主账号把网关端点改名（如 `my-gateway`）且存在彼时播种的遗留子账号，则漏洗。
- 结论：**LOW–MEDIUM（防御性）**。建议把判据改为本质判据——端点 `api_key`/`base_url` 展开后等于 `NEWAPI_*` env，或端点 id 非 `custom:*` 且非用户显式声明者。当前不强阻断（无现网实例）。

### B. 诚实性与错误路径

**B·`400 + 字符串 detail` 与 `[object Object]` 共用缺陷（REVIEW.md §1 B 第 1 点）**
- 结论：**部分成立（本轮 400 修对了，但对象 detail 的共用缺陷仍在其它端点）**。
- `/chat` 的 `ModelNotConfiguredError` 已正确用**字符串** detail（`server/api.py:2061-2071`），故子账号未配置时前端拿到可执行指引，不会变 `[object Object]`。实测 B4 → `HTTP 400, detail='当前账号尚未配置模型 API：…'`。
- 但 `CHAT_AGENT_FAILED` 的 500 仍返回**对象** detail（`server/api.py:2073-2080`：`{"error","message","traceback"}`）。前端 `ApiError` 构造（`web/src/api/errors.ts:9-17`）为 `super(body.detail || ...)`——当 `body.detail` 是对象 → `String({...})` = `"[object Object]"`。即凡是返回对象 detail 的端点，在经 `e.message` 消费的通用路径上会显示 `[object Object]`。
  - 缓解：ChatEntry.vue:690-692 直接取 `detail?.message`，故聊天错误路径实际显示真实文案，**未被 [object Object] 吞**。但 task/plugins 等用通用 `e.message` 的路径在对象 detail 下会显示 `[object Object]`。
- 结论：**LOW（UX/诚实展示）**。本轮已修核心 400 路径；建议把 `errors.ts` 改成「detail 为对象时优先 `detail.message` 或 JSON.stringify」，以彻底消除 `[object Object]`。

**B·`ModelNotConfiguredError` 前置闸是否漏掉其它 LLM 路径（REVIEW.md §1 B 第 2 点）**
- 结论：**成立（已覆盖主路径）**。
- `chat_agent.py:442-446` 在 `step()` 入口用 `_llm_has_endpoints(self.llm)` 前置拦截；`_llm_has_endpoints`（`chat_agent.py:35-40`）对非 `NewApiLLMClient` 返回 `True`（不误判单测替身）。专家偏好模型、研报意图分支、propose_lesson 均经同一 `llm.complete`/`_resolve`。对于「有端点但 `chat.tool_model` 为空」的半配置态（见下 C 第 3 点），闸**有意不触发**（因为确有端点），走用户自有 provider，属设计预期。

**B·前端「未配置」判定与后端语义一致性（REVIEW.md §1 B 第 3 点）**
- 结论：**成立（一致，且由构造保证）**。
- 前端 `usableModels`（`web/src/views/ChatEntry.vue:469-470`）= `models.value.filter(m => !!m.endpoint_id && m.kind !== 'auto' && m.id !== 'auto')`；其数据来自 `authFetch('/api/v1/models')`（line 500），而该端点后端走同一 `load_model_interfaces_and_endpoints`，且自定义 provider 在 `enabled=False` 时已被 loader 排除（`orchestrator.py:1137`）。因此前端「已配置」集合与后端「有端点」集合同源派生，**不存在 provider enabled=false 导致的前端认为已配置/后端认为未配置的错位**。
- 证据：`orchestrator.py:1136-1138`（disabled 不进 endpoints）；`ChatEntry.vue:469-473`、`489`、`500`。

### C. 回归与副作用

**C·主账号全流程端到端（REVIEW.md §1 C）**
- 结论：**成立（独立重跑确认）**。主账号仍持 `new-api`（`verify` A5/B2：endpoints=['new-api','custom:Intern-S2-Preview-397B']，/models items=122）；主账号 `/chat` 返回真实回复（VERIFICATION §2.3 实测 HTTP 200）。无回退误伤。

**C·「异基座」硬约束对子账号（REVIEW.md §1 C 第 2 点）**
- 结论：**成立（有意且 UI 已说明）**。`admin.py:_validate_mapping` 未放宽；子账号需自备 ≥2 个不同来源 Provider 才能保存映射，UI（Models.vue）已明确提示「至少需要两个不同来源的 Provider」。属治理约束，非把子账号锁死到不可用（未配置时诚实红/禁发）。

**C·「有端点但 `chat.tool_model` 为空」路径（REVIEW.md §1 C 第 3 点，明确标注 B4 未覆盖）**
- 结论：**已补测，隔离成立，但存在轻微 UX 诚实缺口（非泄漏）**。
- 我构造 T1：子账号加自有 provider（端点非空）→ 留 `chat.tool_model: ""` → 发 `/chat`（不带 model / 带 `model=auto-chat`）。结果均 **HTTP 200**（非 400、非泄漏主账号网关）。
  - 证据：T1 输出 `body(model=None) -> HTTP 200`、`body(model='auto-chat') -> HTTP 200`。
- 行为链：`_resolve_chat_model`（`chat_agent.py:233-242`）在 `tool_model` 为空且前端未传具体模型时回退 `"auto-chat"` → `_resolve`（`orchestrator.py:471-497`）以 `default_endpoint_id`（=子账号**自己的** provider）承载 `real_model="auto-chat"` → 打到**子账号自有 provider**（`https://probe.example.invalid/v1`）。即**没有回落到主账号网关**，隔离保持。
- 诚实性缺口：因为确有端点，`ModelNotConfiguredError` 闸**有意不触发**；`auto-chat` 落到子账号自有 provider 后若其无此模型（多数 provider 没有 `auto-chat`），会被包成通用「脑子打结了」（`chat_agent.py` 末尾通用 except，设计预期「LLM 偶发故障」语义）。用户看到的是泛化错误而非「你还没在模型映射填 chat.tool_model」的精确指引——这是**轻微 UX 缺口，不是假隔离/假结果**。建议在 `step()` 入口增加「`chat.tool_model` 为空且前端未传模型」的精确前置提示（可选增强，非阻断）。

### D. 独立复现

**D·独立重跑 `verify_subaccount_isolation.py`（REVIEW.md §1 D）**
- 已执行：`docker compose exec -T -e PYTHONPATH=/app api python /app/scripts/verify_subaccount_isolation.py` → **13/13 通过，exit 0**。
- 我逐条审计了脚本断言性质：
  - `B1-B4`（`scripts/verify_subaccount_isolation.py:153-174` `_http_json` 用 `urllib.request` 真实发请求、读响应；`:379-419`）是**真 HTTP 断言**，非进程内自证。B1 子账号 /models items=1 泄漏项=[]；B2 主账号 items=122 含 new-api；B3 无 token→401；B4 未配置子账号 /chat→400 + 可执行指引。
  - A2b（`:232-253`）用 `subprocess` + `RAT_ACCOUNT_ID` + `bind_account` 真起子进程验证引擎路径，非进程内自证。
  - 非恒真断言：`_scan_for_gateway_traces`（`:121-138`）先剥 YAML 注释再匹配，避免中性模板里的说明性注释（「不共享主账号的 new-api 网关」）被误判为命中。B0（`:339-360`）在 B4 前显式归零探针 provider，规避了 VERIFICATION 自陈的「用例未归零导致 B4 误判 200」陷阱。
- 结论：**脚本可信，无需推翻**。

**D·审议所用模型（REVIEW.md §1 D 第 2 点）**
- 我以 general-purpose-29 独立子代理身份审议，未使用 `auto-fast`/`auto-reasoning` 等弱模型；全程基于 file:line 证据与独立重跑，未出现「无证据 PASS」或「编造行号」。

---

## 我独立重跑的结果（原始输出摘要）

**1) `verify_subaccount_isolation.py`（容器内，exit 0，13/13）**
```
NEWAPI_API_KEY 是否在容器环境中已设置：True
NEWAPI_BASE_URL = 'http://172.29.0.1:3000'
临时子账号：_iso_probe_0d728b7c (...)
✅ A1 子账号 config/ 不含主账号网关痕迹 — 已扫描 12 个文件，0 命中
✅ A1b 中性模板已实际落盘
✅ A2 子账号 endpoints 为空且无默认端点 — endpoints=0 个, default_endpoint_id=None
✅ A2b 引擎子进程路径同样得到空端点 — SUBPROC endpoints=0 default=None
✅ A3 未配置时抛 LLMError 且文案含可执行指引 — 「…请到「设置 → 自定义 API」添加你自己的 Provider…」
✅ A4a 自加 Provider 后端点=自己的 base_url/key — endpoint={'id':'custom:probe-provider','base_url':'https://probe.example.invalid/v1','api_key':'sk-probe-own-key-1234'}
✅ A4b 模型解析命中子账号自己的端点 — endpoint_id='custom:probe-provider' real_model='probe-model'
✅ A5 回归：主账号仍持有 new-api 端点 — 主账号=wendy endpoints=['new-api','custom:Intern-S2-Preview-397B']
✅ B0 探针 Provider 已清除
--- B 系列：真实 HTTP 链路 ---
✅ B1 HTTP：子账号 /models 不含主账号网关模型 — HTTP 200，items=1，泄漏项=[]
✅ B2 回归 HTTP：主账号 /models 仍含 new-api 网关模型 — HTTP 200，items=122
✅ B3 无 token 调 /models 返回 401（不再泄漏） — HTTP 401
✅ B4 HTTP：未配置模型的子账号对话被拦住且文案可执行 — HTTP 400，detail='当前账号尚未配置模型 API：请到「设置 → 自定义 API」添加你自己的 Provider（base_url + API Key），再选择模型。子账号不共享主账号的 new-api 网关。'
结果：13/13 通过
```

**2) 对抗性构造用例 T1–T4（独立脚本，容器内运行）**
```
T1 [有端点 + 空 tool_model]
  tool_model line: ['  tool_model: ""']
  body(model=None) -> HTTP 200
  body(model='auto-chat') -> HTTP 200
  （均打到子账号自有 provider，未回落主账号网关；返回 200 为通用故障容错路径）

T3 [引擎子进程无 account_id]
  current_account_id(allow_none=True) = None
  EXCEPTION (no-context load): TenancyError 当前线程没有租户上下文——资源路径无法确定。
  （删除 RAT_LEGACY_GLOBAL 后，无上下文任务失败 loud，不再静默借用主账号网关）

T2 [_write_neutral_seed_files 漂移污染时序]
  main models.yaml has new-api endpoint: True
  AFTER _write_neutral_seed_files -> still contains new-api: True
  AFTER still polluted: True
  child endpoints loaded: ['new-api'] default: new-api
  （函数保守跳过成立；但真实 ensure_account_layout 先复制再比较，实际可达性低）

T4 [purge 对改名网关端点]
  created child with renamed gateway endpoint my-gateway
  PURGE OUTPUT 扫描 7 租户：wendy 跳过；wendy1 model_mapping/custom_providers 命中；遗留测试账号命中
  CHILD REPORTED AS POLLUTED BY PURGE? False   ← 改名 id 漏洗（特征盲区）
```

**3) HTTP 鉴权面实测**
```
NO-TOKEN /templates  -> 401 {"detail":"未登录"}
NO-TOKEN /skills     -> 401
NO-TOKEN /agents-library -> 401
NO-TOKEN /channels   -> 401
WENDY1-TOKEN /templates -> 200 {"items":[{"id":"standard_research",...}]}
```
（证实 6 接口已鉴权；同时暴露 `template.ts` 裸 fetch 在无 token 时必 401。）

---

## 我推翻或修正了实施者（VERIFICATION.md / DESIGN）的说法

1. **推翻 REVIEW.md §1 A 第 1 点的假设**：清单称「`_has_tenant_context()` 异常分支返回 `False`（允许回落主账号 NEWAPI_*）」。实际代码（`orchestrator.py:975`）返回 `True`（安全方向）。该具体担忧在当前代码下**不成立**。→ 我从「待证伪风险」改为「已证实安全」。

2. **修正 VERIFICATION §2.2 / DESIGN D6 的「前端已随 D6 改造」表述**：DESIGN D6 与 VERIFICATION 变更范围称前端裸 `fetch` 已改走 `authFetch`/`api`，但**只列了 ChatEntry 的 /models、/plugins 与 Models.vue 的 /models**，遗漏了 `/templates`（被 `stores/template.ts:18` 裸 `fetch` 调用）。实测该端点现已 401，导致研报提交页模板列表对所有账号加载失败。这是实施者自审**未覆盖**的真实回归，我将其列为 HIGH 必修项。

3. **补充 VERIFICATION 自陈的「B4 只覆盖完全无端点」缺口**：我独立构造并验证了「有端点但 `chat.tool_model` 空」路径（T1），确认隔离仍成立（落到子账号自有 provider，非主账号网关），但存在「auto-chat 落到自有 provider 后泛化报错」的轻微 UX 缺口。该路径未被原 B4 覆盖，实施者已自陈此缺口，我补充了实证。

4. **对 A4 时序漏洞（REVIEW.md §1 A 第 4 点）的定调修正**：实施者在 VERIFICATION 风险栏将其列为「未完整覆盖」。我构造复现确认函数保守性确实存在（T2），但进一步证明在真实 `ensure_account_layout` 调用链中「先复制主账号当前配置再比较」使漏净化路径不可达，实际风险远低于清单字面暗示；真正的存量净化由 purge 脚本（按 `id=="new-api"`）兜底。故从「潜在泄漏」下调为「LOW 防御性建议」。

---

## 我无法验证 / 未验证的部分（如实标注）

1. **登录/注册 HTTP 重放**：未用 boss 真实凭据重放 `/auth/login`、`/auth/register`。依据代码（`auth.py` 独立路由、不依赖 tenancy 上下文）合理推断不受影响，但**未做 HTTP 实证**。
2. **浏览器真机点击**：未启动浏览器手点「研报提交页模板下拉」「停止/编辑」等交互；`template.ts` 的 401 影响是从代码 + HTTP 端点实测推断（端点 401 + 调用方不带 token），**未做浏览器视觉确认**。注意：另一审议者（gate ③ 并行）可能已用浏览器验证，结论以双方交叉为准。
3. **完整研报引擎端到端（新建任务→引擎子进程→WS→结果页）**：仅验证了「主账号 /chat 真实回复」与「引擎子进程上下文绑定（A2b/T3）」与「无 account_id 任务失败 loud」三块；未提交一个完整研报任务并等其落库全流程（VERIFICATION §R2-5 的 `e2e_report_flow.py wendy` 属第二轮里程碑，本轮未重跑）。
4. **`wendy`/`wendy1` 真实账号不动**：按约束，未触碰两账号配置；所有构造用例均用临时一次性子账号（`_iso_*`），并在审议末尾清理（遗留账户 `d455d4f5-…`/租户目录已删除，复核剩余 `_iso_` 账户数 = 0）。
5. **并发污染排查**：并行审议者可能改动共享 DB/会话；我的测试均在隔离临时账号上进行，且 T1 失败的首跑已在末尾清理，未观察到相互影响；若重跑时出现偶发异常，建议隔离单跑（规则已遵守，未见异常）。

---

## 交付物小结

- **结论**：PASS_WITH_NOTES。
- **BLOCKING（假隔离/假配置/泄漏/假结果）**：0 条。
- **必修 HIGH（建议交付前修，非严格 BLOCKING）**：`stores/template.ts:18` 裸 `fetch('/api/v1/templates')` 未带 Bearer → 研报提交页模板列表 401（D6 前端改造遗漏 `/templates`）。修法：改为 `authFetch('/api/v1/templates')` 或经 `api.get`。
- **LOW/MEDIUM 防御性建议**：
  - `scripts/purge_inherited_gateway.py` 污染特征盲区（改名网关 id 漏洗）→ 改本质判据（端点展开后 == NEWAPI_* env）。
  - `web/src/api/errors.ts` 对象 detail 仍会 `[object Object]`（CHAT_AGENT_FAILED 等 500）→ detail 为对象时优先 `detail.message`/JSON.stringify。
  - `_write_neutral_seed_files` 字节比较保守性（仅 LOW，真实链路已被复制-比较 + purge 覆盖）。
- **核心隔离保证**：经独立重跑 13/13 + 对抗用例 T1–T4 验证成立，无假隔离/假配置/数据泄漏/假结果。

---

## §附录 · 修复复核（HIGH-1：`stores/template.ts` 裸 fetch 回归）

> 背景：本附录是 gate ③ 出结论**之后**，team-lead 单独修复该 HIGH 项，由我作为独立审议者**事后复核**的记录。复核不依赖 team-lead 自测结论，全部证据自行取得。

### 1. 改动核对（源码级）

team-lead 的改动仅一处文件两处：

- `web/src/stores/template.ts:5` 新增 `import { authFetch } from '@/api/client';`（写法与 `web/src/views/ChatEntry.vue:279` 一致，已在该文件验证可编译）。
- `web/src/stores/template.ts:21` 由 `await fetch(\`${API_BASE}/templates\`)` 改为 `await authFetch(\`${API_BASE}/templates\`)`，并加两行注释说明必须带 Bearer 的原因。

我独立打开 `web/src/stores/template.ts` 全文核对，确认：

1. **确实修掉了原问题**：旧代码裸 `fetch` 不带 `Authorization`，命中 D6 后鉴权化的 `/templates` → 后端 `_tenant_required` 返回 401 → 研报提交页模板列表加载失败。新代码走 `authFetch`，而 `authFetch`（`web/src/api/client.ts:39-45`）会从 `localStorage` 读取 token 并注入 `Authorization: Bearer <token>`，请求现在带凭证 → 后端返回 200。原「401 导致加载失败」的根因被消除。
2. **未引入新问题**：
   - **响应兼容性**：`authFetch` 返回原生 `Promise<Response>`，`template.ts:21-24` 仍用 `res.ok` / `res.json()` / `data.items` —— 与旧裸 `fetch` 的响应处理**逐字相同**，无行为断点。
   - **Content-Type 副作用**：`authFetch` 仅在 `!headers.has('Content-Type')` 时补 `application/json`（`client.ts:42`）。本调用是 GET、无 body，补 Content-Type 对 GET 无副作用（后端忽略）。无破坏。
   - **循环 import**：`authFetch` 定义在 `client.ts`，该文件仅 `import axios / ./errors / @/types`（`client.ts:4-6`），**不 import 任何 pinia store**（代码注释 client.ts:20-22 明确纪律）。因此 `template.ts`（pinia store）import `authFetch` **不构成循环依赖**。`authFetch` 已被 `ChatEntry.vue:279`、`TemplateEdit.vue`、`Templates.vue` 等多个文件使用且项目可编译，证明该引用路径稳定。
   - **其它调用点**：grep 全仓 `fetch(.../templates)`，除已修的 `template.ts:21` 外，其余 `/templates` 消费者（`TemplateEdit.vue:109/118/141`、`Templates.vue:71/94`）**本来就用 `authFetch`**。即原裸 fetch 仅此一处，已闭环，无遗漏调用点。

### 2. 独立复跑两条 HTTP 路径（容器内，非 host curl）

因宿主沙箱禁止出网、且 `node`/docker 经 wsl 被安全策略拦截，我改为在**运行中的 `report-api` 容器内**（`docker compose exec`，未重建/重启）直接打内部端点（api 容器内监听 **8000**，host `18080` 是 web/nginx 反代）。用一次性临时子账号 mint token，验证后删除（无残留）：

```
$ docker compose exec -T -e PYTHONPATH=/app api python /app/_fixcheck.py
NO-TOKEN   -> (401, '{"detail":"未登录"}')      ← 后端鉴权化生效，无凭证被拒（设计预期）
TEMP ACC   _fixchk_da84cb21
WITH-TOKEN -> (200, '{"items":[],"total":0}')   ← 带 Bearer 的合法请求成功返回
CLEANUP account deleted: True                   ← 临时账号已清理，无泄漏
```

- **无 token → 401**：证实后端 `/templates` 确实鉴权化（D6 行为未回退），原回归的「前提」仍在；但这是后端正确行为，不是缺陷。
- **带 token → 200**：证实「带 Bearer 的合法请求」能正常拿到响应。前端修复后发出的正是此类请求 —— 因此提交页模板加载恢复。
- 临时账号已删除（`CLEANUP account deleted: True`），复核未触碰 wendy/wendy1。

### 3. 「undefined import 能否被 vue-tsc 拦住」的独立证据

team-lead 自测 `npm run typecheck`（= `vue-tsc --noEmit`）exit 0。我因沙箱拦截 `node`/wsl **无法在本会话重跑 vue-tsc**，但提供以下**独立于「typecheck 通过即解析成功」推论**的结构性证据：

1. `authFetch` 确为真实导出符号：`web/src/api/client.ts:39` 为 `export async function authFetch(input: string, init: RequestInit = {}): Promise<Response>`（grep 确认，非类型声明占位）。
2. 导入路径 `@/api/client` 是项目内已验证可解析的别名：同路径已被 `ChatEntry.vue:279`、`TemplateEdit.vue`、`Templates.vue` 以 `import { authFetch } from '@/api/client'` 引用且项目可编译，说明 `@`→`web/src` 别名与文件存在性无问题。
3. 命名导入 `{ authFetch }` 与导出名**逐字符一致**（大小写敏感）。TS 对「模块有导出但该具名成员不存在」会直接报 `has no exported member 'authFetch'`；对「模块路径无法解析」报 `Cannot find module`。二者均会在 `vue-tsc` 阶段失败。
4. 返回类型 `Promise<Response>` 与调用点 `res.ok`/`res.json()` 类型匹配 —— 若 `authFetch` 实际返回类型不符，调用点亦会报类型错。

综上，即便不重跑 typecheck，也可判定：该 import 若写错（路径错/名字错/未导出），项目类型检查必然失败；team-lead 的 typecheck exit 0 与该结构性证据互相印证，可信。

### 4. 复核结论

- **HIGH-1 已闭环**：源码改动正确消除了「裸 fetch → 401」根因，无新引入问题（响应兼容、无 CT 副作用、无循环依赖、无遗漏调用点），独立 HTTP 复跑印证「带 token 200 / 无 token 401」双路径符合预期。
- **唯一未验证项（如实标注，非缺陷）**：**已重建的前端 bundle / 浏览器真机点击**。受「不重建/重启任何容器」约束（另有一审议者并发使用同一环境），我无法实证「线上跑的就是这份修复后的 bundle」。该未验证项不影响「源码层修复正确」的结论，但交付前建议由有重建权限的一方在 CI/构建后做一次浏览器真机确认（研报提交页模板下拉可正常加载）。
- 我未修改任何应用源码，仅更新本报告（追加本附录 + 顶部结论标注 HIGH-1 已闭环）。
