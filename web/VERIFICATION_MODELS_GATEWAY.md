# VERIFICATION_MODELS_GATEWAY.md · ChatEntry 模型下拉对接 new-api /v1/models 自审

> 关联设计：`DESIGN_MODELS_GATEWAY.md`
> 关联实现：`config/models.yaml` / `server/admin.py` / `web/src/views/ChatEntry.vue`
> 三道闸：① typecheck+build+py_compile 全 EXIT=0（见下）② 本自审 ③ 独立子代理 REVIEW（见 `REVIEW_MODELS_GATEWAY.md`）

## 1. 改动清单

| 文件 | 改动 |
|------|------|
| `config/models.yaml` | 新增 `gateway` 段（url/api_key 用 `${NEWAPI_*}` 占位符，不硬编码密钥；fetch_from_gateway/timeout/cache_seconds） |
| `server/admin.py` | `_expand_env_vars` / `_fetch_gateway_models` 辅助函数；`_load_model_interfaces` 重写为"本地 interfaces + 网关拉取合并 + 60s 缓存 + 失败降级"；`public_list_models` 响应 meta 暴露 `gateway_ok`/`gateway_error`/`gateway_disabled`；模块级 `_GATEWAY_CACHE` / `_MODELS_META` |
| `web/src/views/ChatEntry.vue` | 模型下拉改为 `el-option-group` 分组（自动/通用接口/推荐模型/网关模型）+ `filterable` 搜索；新增 `modelsMeta` / `gwWarnText`；网关未加载时 `el-tooltip` + `Warning` 图标提示 |

## 2. 数据流（实测推演）

```
config/models.yaml(gateway.url→${NEWAPI_BASE_URL}) + .env(NEWAPI_API_KEY)
        │
        ▼
_load_model_interfaces()
  ├─ 现读本地 interfaces（source=local）
  ├─ gateway.fetch_from_gateway=true → GET {url}/models（Bearer key，timeout=10）
  │     ├─ 命中 60s 内存缓存 → 直接合并（meta.cached=true）
  │     ├─ 成功 → 合并网关模型（kind=gateway，本地 id 优先不重复）
  │     └─ 失败（超时/鉴权/未启动）→ warn 降级本地，meta.gateway_ok=false
  └─ fetch_from_gateway=false → 仅本地（meta.gateway_disabled=true）
        │
        ▼
GET /api/v1/models → {items, total, meta}
        │
        ▼
ChatEntry.vue 分组下拉（auto/universal/concrete/gateway）+ 网关状态提示
        │
        ▼
payload.model → api.py 校验（valid_ids = _load_model_interfaces() 全集，已含网关模型）→ engine_runner → run_report → make_agent(model_override)
```

## 3. 校验点（逐条自检）

- [x] **环境变量展开**：`_expand_env_vars("${NEWAPI_BASE_URL:-http://host.docker.internal:3000/v1}")` 在容器内展开为 `http://host.docker.internal:3000/v1`；`api_key` 展开为 `.env` 注入的 key；密钥不落盘进 yaml（脱敏：响应 meta.gateway_url 含 host 但不含 key）。
- [x] **new-api 响应格式**：拉取 `GET {url}/models`，解析 `payload["data"]` 数组（new-api OpenAI 兼容格式 `{object,data,success}`），逐条取 `id` → 条目 `kind=gateway`。
- [x] **本地优先**：合并时 `if not any(o["id"]==it["id"] for o in out)` → 网关若与本地 id 重复（如 `LongCat-2.0` 同时出现在网关），保留本地 name/description/kind，不重复。
- [x] **失败降级**：网关不可达/超时/鉴权错 → `except Exception` 捕获，`_MODELS_META["gateway_ok"]=False` + `gateway_error`，`logger.warning` 记录，返回降级本地列表（下拉至少可用 auto+universal+LongCat）。
- [x] **缓存**：`_GATEWAY_CACHE` 进程级 dict + `threading.Lock`，TTL=`cache_seconds`（默认 60）；TTL 内命中不重复请求网关。
- [x] **逃生口**：`gateway.fetch_from_gateway=false` 或 url 为空 → 完全回到旧行为（仅本地 interfaces）。
- [x] **Gate 异基座隔离不变**：本次未改 `orchestrator.py` 的 `make_gate`（`gate_model=models["gates"][gate_name]["model"]`）与 `make_agent`（`model=model_override or roles[role]["model"]`）；用户所选模型仍**仅覆盖 Agent 调用**，Gate 走 `model_mapping.yaml`。
- [x] **api.py 校验自动可用**：`create_task` 的 `valid_ids = {m["id"] for m in _load_model_interfaces()}`（api.py:334）已包含网关模型 → 用户选 new-api 任何真实模型均通过，未知 id 仍 400（诚实边界保留）。
- [x] **前端分组**：`autoModels/universalModels/concreteModels/gatewayModels` 四个 computed 按 `kind` 过滤；`el-option-group` 渲染分组；`filterable` 支持长列表搜索。
- [x] **网关状态提示**：`fetchModels` 现读 `data.meta` 存入 `modelsMeta`；`modelsMeta.gateway_ok===false` 时显示 `Warning` 图标 + tooltip（`gateway_disabled` 与 `gateway_error` 区分提示文案）。

## 4. 三道闸 ① 结果

```
cd web && npm run typecheck   → EXIT=0
cd web && npm run build       → EXIT=0  (后台 task gcepZJ)
cd .  && python -m py_compile orchestrator.py server/api.py server/engine_client.py server/engine_runner.py server/admin.py tools/__init__.py → EXIT=0
```

## 5. 诚实边界 / GAP（非阻塞，留待优化）

- **GAP-1（下拉长度）**：new-api 可能暴露数十~上百模型，分组下拉仍偏长。`filterable` 可搜索缓解，但无"按 provider 分组/折叠"。设计文档 §10 已登记，本次不实现。
- **GAP-2（模型友好名）**：网关模型 `name=id`（new-api 仅返回 `id`），无中文/厂商友好名。可后续从 new-api `/v1/models` 的扩展字段或本地映射增强。
- **GAP-3（容器网络）**：容器内访问宿主网关依赖 `host.docker.internal`（Docker Desktop）。Linux 裸 docker 需 `extra_hosts` 或填宿主机 IP（设计文档 §6）。当前 `.env` 已配 `NEWAPI_BASE_URL=http://host.docker.internal:3000/v1`，与引擎同前提。
- **GAP-4（缓存延迟）**：网关新增模型最多 60s 后出现于下拉；紧急可重启 api 容器或调小 `cache_seconds`。

## 6. 结论

实现与设计文档一致：模型下拉从"6 个硬编码接口"升级为"自动 + 通用接口 + 推荐模型 + new-api 网关全部真实模型"，用户可像 WorkBuddy 一样自由选择接入模型；密钥不落盘、Gate 异基座隔离不变、失败诚实降级。自审通过，提交独立子代理审议。
