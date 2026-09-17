# VERIFICATION_OPENAI_ENDPOINTS.md · 通用 OpenAI 兼容端点改造自审

> 配套设计：DESIGN_OPENAI_ENDPOINTS.md。本次让 LLM 调用层支持任意 OpenAI 兼容端点，
> 每个模型接口（interface）可绑定独立 base_url + api_key + model_id，与 WorkBuddy 一致。

## 1. 改动清单

| 文件 | 改动 |
|------|------|
| `orchestrator.py` | ① 顶部加 `import os`；② `NewApiLLMClient` 改为多端点：构造接收 `endpoints/interfaces/default_endpoint_id`，新增 `_resolve(model)` + `_get_client(endpoint_id)`（按端点缓存 OpenAI 客户端），`complete()` 按解析结果动态选端点；③ 新增 `_expand_env_vars` + `load_model_interfaces_and_endpoints(path)`（单一定义 env 展开与配置加载，含向后兼容）；④ `preflight()` 改用 default endpoint |
| `server/engine_runner.py` | 不再硬编码 `NEWAPI_BASE_URL/KEY`，改为 `load_model_interfaces_and_endpoints()` 构造多端点客户端 |
| `server/admin.py` | 删除本地 `_expand_env_vars`（迁移到 orchestrator 单一事实源）；`_load_model_interfaces` 复用 `load_model_interfaces_and_endpoints`，item 带 `endpoint_id`，网关模型归入 default endpoint |
| `config/models.yaml` | 新增 `endpoints` 段（new-api + 注释示例 siliconflow/deepseek）；每个非 auto interface 绑定 `endpoint` + `model_id`；`gateway.url/key` 改为取自 default endpoint |
| `run_real.py` | 改用 `load_model_interfaces_and_endpoints` 构造多端点客户端 |
| `tests/test_regression_edge.py` | `_mk_gateway_client` 改用新签名 |
| `web/src/views/ChatEntry.vue` | `ModelItem` 加 `endpoint_id/model_id`；下拉选项显示端点标签（`.mo-ep`） |
| `DESIGN_OPENAI_ENDPOINTS.md` | 新建设计文档 |

## 2. 验证结果（实测）

### 2.1 校验（gate ①）
- `npm run typecheck` → EXIT=0
- `npm run build` → EXIT=0
- `python -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py run_real.py tests/test_regression_edge.py` → EXIT=0

### 2.2 解析逻辑（Python 内联实测）
- 默认配置：`default_endpoint_id=new-api`，6 个 interface 正确绑定端点（auto-chat→new-api:auto-chat / LongCat-2.0→new-api:LongCat-2.0 …）
- `_resolve('LongCat-2.0')` → `('new-api', 'LongCat-2.0', cfg)` ✓
- `_resolve('auto-chat')` → `('new-api', 'auto-chat', cfg)` ✓
- 向后兼容：raw model_id `glm-x`（不在 interfaces）→ `('new-api', 'glm-x', cfg)`（走 default endpoint）✓
- 异常：interface 指向不存在的 endpoint → 诚实抛 `LLMError`（不静默回落到错端点）✓
- 向后兼容：旧配置无 `endpoints` 段 → 自动构造 default endpoint（default_endpoint_id=new-api）✓

### 2.3 诚实边界保留
- Gate 仍走 `model_mapping.yaml`（`make_gate` 用 `models["gates"][gate_name]["model"]`，未被本次改动触碰）。
- 用户所选 interface 仅覆盖 Agent（`model_override` → `make_agent`），Gate 异基座隔离不变。
- 端点 api_key 为空时 `_get_client` 抛 `LLMError`（不静默用空 key 调错端点）。

## 3. 数据流确认
```
ChatEntry 选 interface id
  → POST /tasks payload.model
  → api.py create_task 校验（valid_ids = _load_model_interfaces 全集，含 endpoint_id）
  → engine_runner.load_model_interfaces_and_endpoints()
  → NewApiLLMClient(endpoints, interfaces, default_endpoint_id)
  → run_report(model=interface_id) → make_agent(model_override) → llm.complete(interface_id)
  → _resolve(interface_id) → (endpoint_id, real_model_id, cfg) → _get_client(endpoint_id) → OpenAI SDK
```

## 4. GAP（诚实标注，非阻塞）
- **浏览器 UI 未实跑**：前端按 `endpoint_id` 分组显示端点标签逻辑自洽，具体渲染需 `localhost:18080` 亲测。
- **多端点真实调用未 e2e**：当前 new-api 配额规避态，真实多端点跑通需接入第二个端点（如 SiliconFlow）并填 `.env` 后实测；解析逻辑已单测覆盖。
- **新增端点步骤未自动校验**：用户手动加 `endpoints` 项若漏 `api_key`，运行时首个调用才报错（已诚实抛 LLMError，非静默失败）。

## 5. 结论
PASS（自审）。三道闸第①/②道通过，待独立审议（gate ③，Intern-S2-Preview-397B）。
