<!-- reviewed-by: independent-subagent -->
# REVIEW_OPENAI_ENDPOINTS.md · 通用 OpenAI 兼容端点改造独立审议

> 项目：E:\第二电脑\report-agent-team（FastAPI + Vue3）
> 改动：让 LLM 调用层支持任意 OpenAI 兼容端点，每个模型接口(interface)绑定独立 endpoint(base_url+api_key+model_id)
> 审议角色：gate ③（独立审议子代理，决定性审议者）

---

## Summary

本次改造将 `NewApiLLMClient` 从硬编码单一端点 `(base_url, api_key)` 扩展为多端点模式：通过 `config/models.yaml` 声明 `endpoints` + `interfaces`，`complete()` 调用时按所选 model 动态解析到对应端点 + model_id。密钥通过 `${ENV:-default}` 占位符由 `.env` 注入，不进 yaml。`admin.py` 的本地 `_expand_env_vars` 已删除并迁移到 `orchestrator`（单一事实源）。`make_gate` 仍走 `model_mapping.yaml`（异基座隔离未破）。前端 `ChatEntry.vue` 下拉选项显示端点标签（`.mo-ep`），`ModelItem` 增加 `endpoint_id`/`model_id`。

---

## Verdict

**PASS_WITH_NOTES**

---

## MAJOR（阻塞项，无）

无。未发现端点解析错、密钥泄露进 yaml、调用点遗漏旧签名、Gate 隔离被破。

---

## MINOR（外观/小问题，非阻塞）

1. **DESIGN_OPENAI_ENDPOINTS.md 含旧签名示例（文档层，不影响运行时）**。该文件第 20-21 行和第 168-171 行展示了旧签名 `NewApiLLMClient(llm_base_url, llm_api_key)` 作为"现状"描述，第 240-241 行展示旧写法作为"替换前"。这些是设计文档中的历史/对照示例，非实际代码。真实代码已全部迁移到新签名。建议在文档中标注"以下为改造前/供对比"以免混淆。
2. **config/models.yaml 中 new-api endpoint 的 api_key 默认值为空字符串**（第 17 行 `api_key: "${NEWAPI_API_KEY:-}"`）。这导致 `_get_client('new-api')` 抛 `LLMError`（因为 api_key 为空）。这是预期的诚实行为（不静默用空 key 调错端点），但新用户若未在 `.env` 设置 `NEWAPI_API_KEY` 会直接看到错误。可在文档或 `.env.example` 中更醒目地提示需设置该变量。

---

## Gaps（无法验证，诚实标注）

1. **浏览器 UI 未实跑**：前端 `ChatEntry.vue` 的下拉选项 `.mo-ep` 端点标签渲染、分组逻辑自洽，但未在 `localhost:18080` 亲测真实浏览器渲染效果。代码层面（template + computed + style）已读，逻辑自洽。
2. **多端点真实 e2e 未跑**：当前 new-api 配额规避态，未接入第二个端点（如 SiliconFlow）并填 `.env` 后实测真实调用。解析逻辑已单测覆盖（`_resolve` 正确路由），但真实网络请求未跑。
3. **`npm run typecheck` / `npm run build` 退出码已确认 0，但前端运行时的 Vue 模板类型推断未逐行审查**（仅读了 ChatEntry.vue 关键片段）。

---

## 实读确认（真实 file:line）

### 1. env 展开单一事实源（orchestrator.py）
- `_expand_env_vars` 定义：`orchestrator.py:493-506`。单一事实源在此。
- `admin.py` 本地 `_expand_env_vars` **已删除**，仅剩注释说明迁移（`admin.py:1441-1442`）。grep 确认无残留定义（`admin.py` 中仅第 1441-1442 行注释提及）。

### 2. 多端点解析正确性（orchestrator.py）
- `load_model_interfaces_and_endpoints` 定义：`orchestrator.py:509-577`。返回 `(endpoints, interfaces, gw, default_endpoint_id)`。
- `_resolve(model)`：`orchestrator.py:258-277`。逻辑：
  - 命中 interface 且指定 endpoint → 返回 `(eid, real_model, cfg)`；若 `cfg is None` 抛 `LLMError`（不静默回落，`orchestrator.py:272-273`）。
  - 未命中 → 走 default endpoint + raw model_id；若无 default 抛 `LLMError`。
- `_get_client(endpoint_id)`：`orchestrator.py:279-293`。api_key 为空时抛 `LLMError`（`orchestrator.py:283-285`）。
- **实测验证**（从项目目录 `python -c` 内联）：
  - `load_model_interfaces_and_endpoints()` 返回 `endpoints=['new-api']`, `interfaces=['auto','auto-chat','auto-reasoning','auto-fast','auto-vision','LongCat-2.0']`, `default_endpoint_id='new-api'`。
  - `_resolve('LongCat-2.0')` → `('new-api', 'LongCat-2.0', cfg)` ✓
  - `_resolve('auto-chat')` → `('new-api', 'auto-chat', cfg)` ✓
  - `_resolve('glm-x')`（向后兼容 raw model_id）→ `('new-api', 'glm-x', cfg)` ✓
  - 构造指向不存在 endpoint 的 interface 并 `_resolve('bad')` → 正确抛 `LLMError: 模型接口 'bad' 指向的端点 'nonexistent' 未在 endpoints 中定义` ✓
  - `_get_client('new-api')`（api_key 为空）→ 正确抛 `LLMError: 端点 'new-api' 缺少 api_key` ✓
  - 旧配置（无 endpoints 段）→ 自动构造 default endpoint，`default_endpoint_id='new-api'` ✓

### 3. 密钥不落盘（config/models.yaml）
- `grep sk- config/models.yaml` → **无匹配**。密钥均用 `${ENV:-default}` 占位符（`models.yaml:16-17`, `models.yaml:22-23` 注释示例）。
- 端点 url/api_key 用 env 变量，真实 key 由 `.env` 注入。

### 4. 向后兼容
- 旧配置无 endpoints 段：`load_model_interfaces_and_endpoints` 第 565-575 行自动构造 default endpoint（取自 gateway 或 NEWAPI_* env）。实测验证通过。
- `make_gate` 仍用 `models["gates"][gate_name]["model"]`（`orchestrator.py:985`），未被本次改动触碰，Gate 异基座隔离保留。
- `make_agent` 的 `model_override` 行：`orchestrator.py:799`（`model = model_override or models["roles"][role]["model"]`），未被本次改动触碰。
- `api.py create_task` 校验：`api.py:330-347`，`valid_ids` 来自 `_load_model_interfaces()` 全集（含网关模型），`api.py:333-334`。

### 5. 前端（ChatEntry.vue）
- `ModelItem` 类型含 `endpoint_id` 和 `model_id`：`ChatEntry.vue:181-189`。
- 下拉选项对 non-auto 项显示 `.mo-ep` 端点标签：
  - universal 组：`ChatEntry.vue:97-103`（`<span v-if="m.endpoint_id" class="mo-ep">{{ m.endpoint_id }}</span>`）
  - concrete 组：`ChatEntry.vue:104-110`（同）
  - gateway 组：`ChatEntry.vue:111-117`（同）
- `.mo-ep` 样式：`ChatEntry.vue:479-487`。
- 分组逻辑：autoModels/universalModels/concreteModels/gatewayModels 按 `kind` 过滤（`ChatEntry.vue:193-196`），自洽。

### 6. 调用点全改（grep `NewApiLLMClient(`）
- 实际代码构造点（非文档）：
  - `engine_runner.py:52-56`：新签名 `NewApiLLMClient(endpoints=endpoints, interfaces=interfaces, default_endpoint_id=default_endpoint_id)` ✓
  - `run_real.py:61-67`：新签名（加 max_retries/timeout）✓
  - `tests/test_regression_edge.py:284-292`：`_mk_gateway_client` 新签名 ✓
- **无遗留旧位置参数 `(base_url, api_key)` 构造**。`grep` 结果中 `DESIGN_OPENAI_ENDPOINTS.md` 的旧签名示例为文档对照（非运行时代码），`VERIFICATION.md` 为引用。

---

## 实跑命令与退出码

1. `cd "E:/第二电脑/report-agent-team/web" && npm run typecheck` → **EXIT=0**
2. `cd "E:/第二电脑/report-agent-team/web" && npm run build` → **EXIT=0**（vite build 成功，产出 dist/）
3. `cd "E:/第二电脑/report-agent-team" && python -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py run_real.py tests/test_regression_edge.py` → **EXIT=0**

---

## 结论

改造设计合理、实现完整、向后兼容诚实。全部三个校验命令退出码均为 0。解析逻辑经内联 Python 实测覆盖正常/异常/向后兼容路径。无 MAJOR 阻塞项。建议关注 MINOR 中的文档清晰度与 `.env` 提示。

裁决：**PASS_WITH_NOTES**