# DESIGN_MODELS_GATEWAY.md · 模型下拉对接 new-api /v1/models

## 1. 现状问题（诚实边界）

当前 `GET /api/v1/models` 只返回 `config/models.yaml` 里硬编码的 6 个接口：

- `auto`（不覆盖）
- `auto-chat` / `auto-reasoning` / `auto-fast` / `auto-vision`（new-api universal alias）
- `LongCat-2.0`（唯一具体模型）

用户反馈："只有一个模型"——观感上除了 LongCat-2.0 外全是"通用接口"，没有真实模型可选。这与 WorkBuddy 直接列出所有可用模型的体验相差甚远。

根因：
1. 后端没有调用 new-api 的 `/v1/models` 拉取真实模型列表。
2. 前端下拉只消费 `config/models.yaml` 的静态接口。
3. 校验层 `api.py:create_task` 只认 `config/models.yaml` 的 `id` 集合。

## 2. 目标

让 ChatEntry 的"模型接口"下拉**真正自由选择** new-api 网关里所有可用模型，同时保留"自动 / 通用接口"快捷选项。

非目标：
- 不取代 `model_mapping.yaml` 的默认角色映射（`auto` 仍走它）。
- 不改变 Gate 异基座隔离（Gate 仍走 `model_mapping.yaml`）。
- 不要求用户必须手动维护 `config/models.yaml` 才能看到新模型。

## 3. 方案概述

```
config/models.yaml  ┐
.env (NEWAPI_*)     ├→  server/admin.py:_load_model_interfaces()
                      本地 interfaces + 网关 /v1/models 拉取 → 合并 → GET /api/v1/models
                                                  ↓
                              前端 ChatEntry.vue model select（分组）
                                                  ↓
                              payload.model → api.py 校验 → engine_runner → run_report → make_agent(model_override)
```

## 4. 配置结构（config/models.yaml）

```yaml
# config/models.yaml · 可选模型接口（ChatEntry 模型接口下拉）

# 网关：从环境变量读取，支持 ${ENV:-default} 占位符，实现"配置改一处，容器内外自适应"。
gateway:
  url: "${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}"
  api_key: "${NEWAPI_API_KEY:-}"
  fetch_from_gateway: true   # false = 只返回本地 interfaces（旧行为，逃生口）
  timeout: 10                 # 拉取 /v1/models 超时（秒）
  cache_seconds: 60           # 网关模型列表内存缓存（避免每次刷新都请求网关）

# 本地固定接口：用于覆盖 / 置顶 / 分组标签。
# kind: auto | universal | concrete | gateway
interfaces:
  - id: auto
    name: 自动
    description: 由 model_mapping.yaml 决定（推荐）
    kind: auto
    default: true

  # --- 通用接口（universal alias，由 new-api 网关解析） ---
  - id: auto-chat
    name: 通用对话
    description: new-api 自动路由常规对话模型
    kind: universal
  - id: auto-reasoning
    name: 深度推理
    description: new-api 自动路由强推理模型
    kind: universal
  - id: auto-fast
    name: 快速响应
    description: new-api 自动路由轻量快速模型
    kind: universal
  - id: auto-vision
    name: 多模态
    description: new-api 自动路由视觉/多模态模型
    kind: universal

  # --- 推荐具体模型（concrete，本地置顶展示） ---
  - id: LongCat-2.0
    name: LongCat-2.0
    description: 本地自定义基座主力模型
    kind: concrete
```

说明：
- `gateway.url` 使用 `${NEWAPI_BASE_URL:-...}` 占位符，Python 加载时替换。容器内默认 `http://host.docker.internal:3000/v1`，宿主机开发可设 `http://localhost:3000/v1`。
- `gateway.api_key` 使用 `${NEWAPI_API_KEY:-...}`，与 `.env` 复用同一密钥，**不硬编码在 yaml**。
- `cache_seconds` 避免高并发下频繁请求 new-api `/v1/models`。

## 5. 后端拉取与合并逻辑

### 5.1 环境变量替换

新增辅助函数 `_expand_env_vars(value: str) -> str`，支持 `${ENV}` 与 `${ENV:-default}`。

### 5.2 `_load_model_interfaces()` 行为

1. 读取 `config/models.yaml`。
2. 解析 `gateway` 段，展开环境变量。
3. 加载本地 `interfaces` 列表，给每个条目加 `source: local`。
4. 若 `fetch_from_gateway=true` 且 `url` 非空：
   - 检查内存缓存（进程级 dict，key=`url`，TTL=`cache_seconds`）。命中且未过期 → 直接返回缓存。
   - 否则发送 `GET {url}/models`，Header `Authorization: Bearer {api_key}`（OpenAI 兼容格式）。
   - 解析响应：`{ "data": [ { "id": "...", "object": "model" }, ... ] }`。
   - 对每个网关模型：
     - 若 `id` 已在本地 interfaces 中 → **本地优先**（保留本地 kind/名称/描述，不重复）。
     - 否则生成条目：`{ id, name: id, description: "new-api 可用模型", kind: "gateway", source: "gateway" }`。
   - 写入内存缓存，附 `fetched_at`。
5. 返回合并后的列表。

### 5.3 失败处理

- 网关拉取失败（超时 / 鉴权 / 未启动）：
  - 日志 `logger.warning` 记录失败原因。
  - 返回 **本地 interfaces**（保证下拉至少可用 auto + universal + LongCat）。
  - `GET /models` 响应 `meta.gateway_ok=false`、`meta.gateway_error=<原因>`，让前端可提示"网关模型未加载"。
- 无内存缓存且拉取失败：同fallback。

### 5.4 `GET /api/v1/models` 响应

```json
{
  "items": [
    { "id": "auto", "name": "自动", "kind": "auto", "default": true, "source": "local" },
    { "id": "auto-chat", "name": "通用对话", "kind": "universal", "source": "local" },
    { "id": "LongCat-2.0", "name": "LongCat-2.0", "kind": "concrete", "source": "local" },
    { "id": "glm-4-flash", "name": "glm-4-flash", "kind": "gateway", "source": "gateway" },
    { "id": "command-r-plus-08-2024", "name": "command-r-plus-08-2024", "kind": "gateway", "source": "gateway" }
  ],
  "total": 6,
  "meta": {
    "registry": "config/models.yaml",
    "gateway_url": "http://host.docker.internal:3000/v1",
    "gateway_ok": true,
    "fetched": 5,
    "cached": false,
    "hot_reload": "GET /models 每次读取，cache_seconds 内走内存缓存"
  }
}
```

注意：`meta.gateway_url` 脱敏，不返回 api_key。

## 6. Docker 网络与密钥

- 容器内访问宿主 new-api：`http://host.docker.internal:3000/v1`（Docker Desktop 已支持，Linux 需 `extra_hosts`）。
- 密钥复用 `.env` 的 `NEWAPI_API_KEY`（已注入 api 容器）。
- 无需改动 `docker-compose.yml`；`.env` 已包含所需变量。

## 7. 前端分组下拉

### 7.1 数据结构

`GET /models` 返回的 `items` 带 `kind`：

- `auto`：单独置顶
- `universal`：分组 "通用接口"
- `concrete`：分组 "推荐模型"
- `gateway`：分组 "网关模型"

### 7.2 UI 改动（ChatEntry.vue）

将当前单级下拉改为 `el-option-group`：

```vue
<el-option-group label="自动">
  <el-option label="自动" value="auto" />
</el-option-group>
<el-option-group label="通用接口">
  <el-option v-for="m in universalModels" :key="m.id" :label="m.name" :value="m.id" />
</el-option-group>
<el-option-group label="推荐模型">
  <el-option v-for="m in concreteModels" :key="m.id" :label="m.name" :value="m.id" />
</el-option-group>
<el-option-group label="网关模型">
  <el-option v-for="m in gatewayModels" :key="m.id" :label="m.name" :value="m.id" />
</el-option-group>
```

### 7.3 网关未加载提示

若 `meta.gateway_ok=false`，在 model select 右侧显示 `el-tooltip`：

> "new-api 网关模型未加载：{{ meta.gateway_error }}，仅显示本地接口。"

## 8. 后端校验（api.py:create_task）

当前校验：

```python
valid_ids = {m["id"] for m in _load_model_interfaces()}
if req_model not in valid_ids:
    raise 400 INVALID_MODEL
```

改动后：

```python
valid_ids = {m["id"] for m in _load_model_interfaces()}  # 已包含网关模型
if req_model not in valid_ids:
    raise 400 INVALID_MODEL
```

逻辑不变，但 `_load_model_interfaces()` 返回集合已扩大为"本地 + 网关"。

## 9. 引擎透传

`model_override` 直接透传给 `NewApiLLMClient.complete(model=...)`。new-api 网关负责解析：

- universal alias（`auto-chat` 等）→ 网关 abilities 路由表自动选择具体上游。
- 具体模型 ID（`LongCat-2.0`、`glm-4-flash` 等）→ 网关直接路由。

因此引擎侧无需改动。

## 10. 风险与回退

| 风险 | 缓解 |
|------|------|
| 网关不可达导致下拉为空/太少 | fallback 到本地 interfaces；`meta.gateway_ok=false` 提示 |
| 网关返回大量模型（>100）导致下拉过长 | 下拉支持搜索 / 虚拟滚动；可按 provider 分组（后续优化，本次不实现） |
| 用户选择网关模型后该模型被删/限流 | new-api 网关返回 `model_not_found` / 429，引擎已有重试与 escalate 处理 |
| api_key 泄露 | 不写入 yaml，复用 `.env`；响应不返回 key |
| 缓存导致网关新模型延迟出现 | `cache_seconds=60` 可接受；紧急刷新可重启 api 容器或调小缓存 |

回退开关：`config/models.yaml` 中 `gateway.fetch_from_gateway=false` → 完全回到旧行为。

## 11. 任务拆分

1. 改 `config/models.yaml`：新增 `gateway` 段，调整注释。
2. 改 `server/admin.py`：
   - `_expand_env_vars` 辅助函数。
   - `_load_model_interfaces` 拉取 + 合并 + 缓存。
   - `public_list_models` 返回 `meta.gateway_ok` 等。
3. 改 `server/api.py`：无需实质改动，`_load_model_interfaces()` 已扩展 valid_ids。
4. 改前端 `web/src/views/ChatEntry.vue`：模型下拉分组。
5. 改 `web/src/types/api.ts`：补充 model item 类型（如有必要）。
6. 跑三道闸：typecheck/build/py_compile、自审、独立审议。
7. Docker 重建 + 探活。
