<!-- reviewed-by: independent-subagent -->

# REVIEW_MODELS_GATEWAY.md · ChatEntry 模型下拉对接 new-api /v1/models 独立审议

> 审议者：独立代码审议子代理（gate ③）
> 审议时间：2026-09-10
> 关联设计：`DESIGN_MODELS_GATEWAY.md`
> 关联实现：`config/models.yaml` / `server/admin.py` / `web/src/views/ChatEntry.vue` / `server/api.py` / `orchestrator.py`

---

## Summary

本次改动将 ChatEntry 的"模型接口"下拉从硬编码 6 个接口升级为：从 `config/models.yaml` 读取本地 interfaces + 从 new-api 网关 `/v1/models` 动态拉取真实模型，合并后供用户自由选择。实现与设计文档基本一致。密钥通过环境变量注入，不落盘进 yaml；网关拉取失败时诚实降级到本地接口；Gate 异基座隔离未被破坏；api.py 校验自动覆盖网关模型。

**三道闸 ① 全部通过**（见下方实跑结果）。

---

## Verdict

**PASS_WITH_NOTES**

无 MAJOR（阻塞）问题。有 2 项 MINOR 和 3 项 GAP（无法验证项），均在下文如实标注。

---

## MAJOR（阻塞）

无。

---

## MINOR（外观/非阻塞）

### MINOR-1：_MODELS_META 模块级状态存在并发竞争

`server/admin.py:1429-1436` 定义模块级 `_MODELS_META` 字典，`_load_model_interfaces()`（admin.py:1497-1502）每次调用先重置再填充，`public_list_models`（admin.py:1568）随后读取。若两个并发请求同时调用 `_load_model_interfaces`，一个请求的 `_MODELS_META` 可能被另一个请求的中间状态覆盖，导致返回的 `meta.gateway_ok` 与实际本次拉取结果不一致。

**影响**：仅 meta 响应的诚实性，不影响数据流（下拉列表 `items` 本身正确返回）。属于外观级问题，非阻塞。

**建议**：将 meta 作为局部变量随返回值传递，而非模块级共享状态。或为每次调用生成唯一请求 id 做关联。当前 `_GATEWAY_CACHE_LOCK` 仅保护缓存读写，不保护 `_MODELS_META`。

### MINOR-2：网关模型 name=id，下拉友好名不足

`_fetch_gateway_models`（admin.py:1476-1479）生成的网关条目 `name: mid`（name 等于模型 id）。若 new-api 返回的 id 为技术标识符（如 `glm-4-flash`），下拉显示不够直观。设计文档 §10 已登记此优化点，非本次范围。

---

## Gaps（无法验证，如实声明）

### GAP-1：无法实跑浏览器验证下拉 UI

审议环境无浏览器，无法实际打开 ChatEntry 页面验证：
- `el-option-group` 四个分组是否正确渲染
- `filterable` 搜索是否可用
- 网关未加载时 Warning 图标 + tooltip 是否弹出

**核验方式**：通过 Read 逐行核对 Vue 模板与脚本（见下方实读确认），确认逻辑正确。但运行时行为未实测。

### GAP-2：无法实跑 LLM 验证选中网关模型真被调用

审议环境无 new-api 网关实例，无法验证：
- 用户选择网关模型（如 `glm-4-flash`）提交后，`model_override` 是否真透传给 `NewApiLLMClient.complete(model=...)`
- 网关是否真路由到对应上游模型

**核验方式**：通过 Read 确认数据流——`make_agent`（orchestrator.py:656）用 `model_override or models["roles"][role]["model"]`；`run_report`（orchestrator.py:1131）将 `model` 参数转为 `model_override`；`NewApiLLMClient.complete`（orchestrator.py:257）使用 `model` 参数调用 OpenAI SDK。数据流代码正确，但运行时未实测。

### GAP-3：无法验证 new-api 实际响应格式

设计文档假设 new-api `/v1/models` 返回 `{"object":"list","data":[{"id":...}],"success":true}`。`_fetch_gateway_models`（admin.py:1470-1473）解析 `payload.get("data")`。此格式假设未经实际请求验证。若 new-api 返回格式有差异（如 `data` 在外层或字段名不同），拉取将失败并降级。

---

## 实读确认（真实 file:line）

### 1. 环境变量展开正确性（admin.py:1441-1456）

正则 `r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"`（admin.py:1449）：
- `${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}` → group(1)=`NEWAPI_BASE_URL`, group(2)=`http://host.docker.internal:3000/v1` → `os.getenv("NEWAPI_BASE_URL", "http://host.docker.internal:3000/v1")`
- `${NEWAPI_API_KEY:-}` → group(1)=`NEWAPI_API_KEY`, group(2)=""（空串，非 None）→ `os.getenv("NEWAPI_API_KEY", "")`

**结论**：展开逻辑正确。`:-` 后为空时 default 为空串，`os.getenv` 返回 env 值或空串。密钥从 .env 注入，yaml 中仅存占位符，响应 meta 不返回 key（admin.py:1574 仅返回 `gateway_url`）。

### 2. 网关拉取正确性（admin.py:1459-1483）

- `urllib.request.Request(f"{url.rstrip('/')}/models")`（admin.py:1464）
- `req.add_header("Authorization", f"Bearer {api_key}")`（admin.py:1467）
- `urllib.request.urlopen(req, timeout=timeout)`（admin.py:1468），使用配置的 timeout（默认 10s）
- `payload = json.loads(body)`（admin.py:1470），`payload.get("data")`（admin.py:1472）
- 逐条取 `entry.get("id")`，生成 `kind="gateway"`, `source="gateway"` 条目（admin.py:1476-1482）

**结论**：拉取逻辑正确，使用 urllib + timeout + Bearer header，解析 data 数组取 id。响应格式假设注释在 admin.py:1462。

### 3. 合并与降级（admin.py:1486-1558）

- 本地 interfaces 现读磁盘（admin.py:1504-1520），`source="local"`
- 逃生口：`fetch_from_gateway=false` 或 url 为空 → 直接返回本地（admin.py:1522-1524），`_MODELS_META["disabled"]=True`
- 本地优先：`if not any(o["id"] == it["id"] for o in out)`（admin.py:1541-1542, 1552-1553），网关 id 与本地重复时不重复
- 内存缓存：`_GATEWAY_CACHE`（admin.py:1426）+ `_GATEWAY_CACHE_LOCK`（admin.py:1427），TTL=`cache_seconds`（admin.py:1530），命中逻辑 admin.py:1534-1543
- 失败降级：`except Exception`（admin.py:1554）→ `_MODELS_META["gateway_ok"]=False`（admin.py:1555），`_MODELS_META["gateway_error"]`（admin.py:1556），`logger.warning`（admin.py:1557），返回本地列表 `out`

**结论**：合并、本地优先、缓存、降级逻辑均正确实现。诚实降级（失败时 gateway_ok=false 并返回本地）已实现。

### 4. 前端分组（ChatEntry.vue）

- 四个 computed 按 kind 过滤：autoModels（line 188）、universalModels（line 189）、concreteModels（line 190）、gatewayModels（line 191）
- `el-option-group` 四个分组：自动（line 91-96）、通用接口（line 97-102）、推荐模型（line 103-108）、网关模型（line 109-114）
- `filterable` 属性（line 89）
- `modelsMeta`（line 193），`gwWarnText`（line 194-198）区分 `gateway_disabled` 与 `gateway_error`
- Warning 图标 + tooltip（line 116-122），条件 `modelsMeta.gateway_ok === false`
- `kindLabel`（line 251-253）：universal→"通用"，concrete→"具体"，gateway→"网关"，auto→"自动"
- `fetchModels`（line 232-244）读取 `data.meta` 存入 `modelsMeta`

**结论**：前端分组、网关状态提示、搜索功能均正确实现。

### 5. Gate 异基座隔离未破（orchestrator.py）

- `make_gate`（orchestrator.py:833-834）：`gate_model = models["gates"][gate_name]["model"]`（orchestrator.py:842），**未使用 `model_override`**
- `make_agent`（orchestrator.py:625-626）：`model = model_override or models["roles"][role]["model"]`（orchestrator.py:656），**使用 `model_override`**
- 本次改动未触碰这两处。`run_report`（orchestrator.py:1112-1167）将 `model` 参数转为 `model_override`（orchestrator.py:1131），传给 `build_graph`（orchestrator.py:1136），最终传入 `make_agent`。

**结论**：Gate 仍走 model_mapping.yaml 异基座隔离；用户自选模型仅覆盖 Agent 调用。隔离未破。

### 6. api.py 校验（server/api.py:330-347）

- `valid_ids = {m["id"] for m in _load_model_interfaces()}`（api.py:334）
- `if req_model not in valid_ids` → 400 INVALID_MODEL（api.py:339-347）
- `_load_model_interfaces()` 已含网关模型（admin.py:1545-1553），故 valid_ids 自动包含网关模型 id
- `req_model == "auto"` 时跳过校验（api.py:330 `if req_model and req_model != "auto"`），"auto" 被允许

**结论**：校验逻辑正确，用户选 new-api 真实模型通过；未知 id 仍 400。"auto" 被允许。

---

## 实跑命令与退出码

```
cd "E:/第二电脑/report-agent-team/web" && npm run typecheck
EXIT=0

cd "E:/第二电脑/report-agent-team/web" && npm run build
EXIT=0  （vite 构建成功，1749 modules transformed）

cd "E:/第二电脑/report-agent-team" && python -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py
EXIT=0
```

三道闸 ① 全部通过。

---

## 结论

实现与设计文档一致，数据流完整：`config/models.yaml`（占位符）→ `_expand_env_vars` → `_load_model_interfaces`（本地 + 网关拉取 + 缓存 + 降级）→ `GET /models`（含 meta.gateway_ok）→ ChatEntry 分组下拉 → `payload.model` → `api.py create_task` 校验（valid_ids 含网关模型）→ `engine_runner` → `run_report` → `make_agent(model_override)`。

无 MAJOR 阻塞问题。2 项 MINOR（并发 meta 竞争、网关模型名友好度）和 3 项 GAP（浏览器 UI 未实测、LLM 透传未实测、new-api 响应格式未实测）已如实标注。

**裁决：PASS_WITH_NOTES**