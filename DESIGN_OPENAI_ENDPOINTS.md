# DESIGN_OPENAI_ENDPOINTS.md · 通用 OpenAI 兼容端点 + 模型接口解耦

> 目标：让 `report-agent-team` 的 LLM 调用层像 WorkBuddy 一样，可自由接入任意 OpenAI 兼容服务（base_url + api_key + model_id），不再被单一 new-api 端点锁死。

---

## 1. 现状与痛点

### 1.1 现状
- `model_mapping.yaml`（你截图的 UI 页面）是**可配置**的：角色（Researcher/Analyst/Writer）和审核闸（GateA/B/C）的默认模型、基座可改，改完下一个任务生效。
- LLM 调用层 `NewApiLLMClient` 本身已经是 OpenAI SDK 兼容实现（`openai.OpenAI(base_url=..., api_key=...)`）。
- ChatEntry 模型下拉已通过 `GET /models` 从 new-api `/v1/models` 拉取 100+ 真实模型。

### 1.2 真正卡脖子的地方
`engine_runner.py` 把 LLM 客户端**硬编码**为单一端点：

```python
llm_base_url = os.getenv("NEWAPI_BASE_URL", "http://localhost:3000/v1")
llm_api_key  = os.getenv("NEWAPI_API_KEY", "sk-no-key")
llm = NewApiLLMClient(llm_base_url, llm_api_key)
```

后果：
1. 无论用户在下拉里选什么模型，最终请求都发到同一个 `base_url`。
2. 无法像 WorkBuddy 一样为某个模型单独指定 `base_url` 和 `api_key`。
3. 如果某个模型只在另一个 OpenAI 兼容服务（如 SiliconFlow、DeepSeek 官方、自部署 vLLM）上，系统接不进来。

### 1.3 对 boss 问题的直接回答
> "模型映射是固定的吗？"

**`model_mapping.yaml` 不是固定的**，页面可直接编辑。但它只解决"角色默认用什么模型"，不解决"这个模型走哪个端点、用什么 key"。后者才是本次要解的。

---

## 2. 目标

1. `config/models.yaml` 同时定义 **interfaces（模型接口/选项）** 和 **endpoints（OpenAI 兼容端点）**。
2. 每个 interface 指向 `(endpoint_id, model_id)`，或保留 `kind=auto` 沿用 `model_mapping.yaml`。
3. `NewApiLLMClient` 按所选 interface 动态选择端点，缓存 OpenAI 客户端。
4. 不破坏现有 `model_mapping.yaml`：旧配置继续生效（旧模型名视为 default endpoint + raw model_id）。
5. `GET /models` 返回的列表带 `endpoint_id`，前端下拉可展示端点来源。

---

## 3. 配置结构

### 3.1 config/models.yaml（改造后）

```yaml
# 端点：任意 OpenAI 兼容服务
endpoints:
  - id: new-api
    name: new-api 网关
    base_url: ${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}
    api_key: ${NEWAPI_API_KEY:-}
    default: true

  - id: siliconflow
    name: SiliconFlow
    base_url: ${SILICONFLOW_BASE_URL:-https://api.siliconflow.cn/v1}
    api_key: ${SILICONFLOW_API_KEY:-}

  - id: deepseek-official
    name: DeepSeek 官方
    base_url: ${DEEPSEEK_BASE_URL:-https://api.deepseek.com/v1}
    api_key: ${DEEPSEEK_API_KEY:-}

# 接口：前端下拉里的可选项
interfaces:
  - id: auto
    name: 自动
    kind: auto
    description: 沿用 model_mapping.yaml 的默认映射
    default: true

  # 通用接口（universal aliases）
  - id: auto-chat
    name: 通用对话
    kind: universal
    endpoint: new-api
    model_id: auto-chat

  - id: auto-reasoning
    name: 深度推理
    kind: universal
    endpoint: new-api
    model_id: auto-reasoning

  # 具体模型：可绑定任意端点
  - id: LongCat-2.0
    name: LongCat-2.0
    kind: concrete
    endpoint: new-api
    model_id: LongCat-2.0

  - id: intern-s2
    name: Intern-S2
    kind: concrete
    endpoint: siliconflow
    model_id: moonshotai/kimi-k3-free   # 示例：不同端点不同真实模型名

# 网关动态拉取仍保留：new-api /v1/models 返回的模型自动归入 default endpoint
gateway:
  fetch_from_gateway: true
  url: ${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}
  api_key: ${NEWAPI_API_KEY:-}
  timeout: 10
  cache_seconds: 60
```

### 3.2 环境变量

新增 optional 变量（`.env` 里无值不影响旧配置）：

```bash
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_API_KEY=sk-xxx
```

密钥仍走 `.env`，不进 yaml。

---

## 4. 数据流

```
ChatEntry.vue
    │ 选择 interface id (如 "intern-s2")
    ▼
POST /api/v1/tasks  payload.model = "intern-s2"
    ▼
api.py create_task
    │ 校验：valid_ids = _load_model_interfaces() 返回的所有 interface.id
    ▼
engine_client.launch_engine(model="intern-s2")
    ▼
engine_runner.py
    │ 1) 从 config/models.yaml 加载 endpoints + interfaces
    │ 2) 构造 NewApiLLMClient(endpoints, interfaces, default_endpoint="new-api")
    ▼
run_report(model="intern-s2")
    ▼
make_agent(model_override="intern-s2")
    │ model = "intern-s2"
    ▼
llm.complete("intern-s2", system, user)
    │ NewApiLLMClient 解析：interface["intern-s2"] -> endpoint=siliconflow, model_id=moonshotai/kimi-k3-free
    │ 取 endpoints["siliconflow"].base_url/api_key 构造 OpenAI 客户端
    ▼
OpenAI SDK -> https://api.siliconflow.cn/v1/chat/completions
```

关键点：
- `model_mapping.yaml` 里的角色默认模型（如 `LongCat-2.0`）可以是 interface id，也可以是 raw model_id。
- 如果是 interface id，走 interface 解析流程；如果是 raw model_id，走 default endpoint。
- Gate 仍用 `model_mapping.yaml`，不受用户所选 interface 影响（异基座隔离保留）。

---

## 5. 后端改造

### 5.1 NewApiLLMClient（orchestrator.py）

当前签名：

```python
class NewApiLLMClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, max_retries: int = 2, timeout: int = 120):
```

新签名：

```python
class NewApiLLMClient(LLMClient):
    """通用 OpenAI 兼容 LLM 客户端（支持多端点）。"""

    def __init__(
        self,
        endpoints: Optional[List[Dict[str, Any]]] = None,
        interfaces: Optional[List[Dict[str, Any]]] = None,
        default_endpoint_id: Optional[str] = None,
        max_retries: int = 2,
        timeout: int = 120,
    ):
        self._endpoints = {e["id"]: e for e in (endpoints or [])}
        self._interfaces = {i["id"]: i for i in (interfaces or [])}
        self._default_endpoint_id = default_endpoint_id
        self._clients: Dict[str, Any] = {}   # endpoint_id -> OpenAI client cache
        self._max_retries = max_retries
        self._timeout = timeout

    def _resolve(self, model: str) -> Tuple[str, str, Dict[str, Any]]:
        """返回 (endpoint_id, real_model_id, endpoint_config)。"""
        iface = self._interfaces.get(model)
        if iface and iface.get("endpoint"):
            eid = iface["endpoint"]
            real_model = iface.get("model_id") or model
            return eid, real_model, self._endpoints[eid]
        # 回退：model 视为 default endpoint 下的 raw model_id（向后兼容）
        eid = self._default_endpoint_id
        if eid and eid in self._endpoints:
            return eid, model, self._endpoints[eid]
        raise LLMError(f"无法解析模型 '{model}'：未找到对应接口或默认端点")

    def _get_client(self, endpoint_id: str):
        if endpoint_id not in self._clients:
            cfg = self._endpoints[endpoint_id]
            from openai import OpenAI
            self._clients[endpoint_id] = OpenAI(
                base_url=cfg["base_url"].rstrip("/"),
                api_key=cfg["api_key"],
                timeout=self._timeout,
            )
        return self._clients[endpoint_id]

    def complete(self, model: str, system: str, user: str, **kw) -> str:
        endpoint_id, real_model, _ = self._resolve(model)
        client = self._get_client(endpoint_id)
        # 后续 retry/backoff 逻辑不变，只把 client 替换掉
```

### 5.2 配置加载辅助函数（orchestrator.py）

新增 `load_model_interfaces_and_endpoints(path)`：
- 读 `config/models.yaml`
- 展开 `${ENV:-default}`
- 返回 `(endpoints, interfaces, gateway_cfg, default_endpoint_id)`

`_load_model_interfaces()`（admin.py）保持只返回 interfaces 列表（供 `GET /models` / `api.py` 校验），但内部复用同一加载逻辑。

### 5.3 engine_runner.py

替换：

```python
import os
llm_base_url = os.getenv("NEWAPI_BASE_URL", "http://localhost:3000/v1")
llm_api_key = os.getenv("NEWAPI_API_KEY", "sk-no-key")
llm = NewApiLLMClient(llm_base_url, llm_api_key)
```

为：

```python
from orchestrator import load_model_interfaces_and_endpoints

endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
llm = NewApiLLMClient(
    endpoints=endpoints,
    interfaces=interfaces,
    default_endpoint_id=default_endpoint_id,
)
```

### 5.4 api.py / admin.py

- `GET /models` 返回的 item 增加 `endpoint_id` 字段（前端展示用，不暴露 key）。
- `create_task` 校验逻辑不变：valid_ids 来自 interfaces 列表。
- 网关动态拉取的 `kind=gateway` 模型自动绑定 `endpoint_id=default_endpoint_id`，`model_id` 为其真实 id。

---

## 6. 前端改造

### 6.1 ChatEntry.vue

- 模型下拉选项增加端点标识（小标签或分组）。
- 可选：按 endpoint 分组展示（类似 WorkBuddy 的 provider 分组）。
- 搜索框保留（模型多时必需）。

### 6.2 types/api.ts

```typescript
export interface ModelItem {
  id: string;
  name: string;
  description?: string;
  kind: 'auto' | 'universal' | 'concrete' | 'gateway';
  default?: boolean;
  endpoint_id?: string;
  model_id?: string;
}
```

---

## 7. 向后兼容

- 旧 `config/models.yaml` 没有 `endpoints` 段时：自动构造一个 `default` endpoint，从 `gateway.url/api_key` 或 `NEWAPI_*` env 取值。
- 旧 `model_mapping.yaml` 里的模型名（如 `LongCat-2.0`）：如果它同时是 interface id，走 interface 解析；否则走 default endpoint + raw model_id。
- `StubLLMClient` 不受影响。

---

## 8. 风险与回退

| 风险 |  mitigation |
|------|------------|
| 配置解析失败导致引擎起不来 | `_expand_env_vars` 已验证；失败时抛出明确 LLMError，不静默吞错。 |
| 用户选了一个 endpoint 未配置或 key 为空的 interface | `complete()` 解析时检查 endpoint 存在且 api_key 非空，否则 LLMError 上升。 |
| 多端点缓存泄漏 | OpenAI client 按 endpoint_id 缓存，数量有限；进程重启即释放。 |
| 网关拉取模型与本地 interface id 冲突 | 本地优先；网关同名模型不重复。 |
| 需要回退 | 把 `config/models.yaml` 改回只保留一个 `new-api` endpoint，行为与现在一致。 |

---

## 9. 验收标准

1. `config/models.yaml` 可配置 ≥2 个 OpenAI 兼容端点。
2. 新增一个指向 SiliconFlow/DeepSeek 官方接口的 interface 后，不改动代码即可在 ChatEntry 选择并使用。
3. 旧 `model_mapping.yaml` 中的默认模型继续工作。
4. Gate 仍走 `model_mapping.yaml`，不受用户所选 interface 影响。
5. 三道闸全过：typecheck/build/py_compile + VERIFICATION + 独立审议。
