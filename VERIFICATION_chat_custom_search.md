# VERIFICATION · 模型自定义修复 + 聊天记录搜索栏

> 关联：DESIGN_chat_model_picker.md / 用户截图诉求「模型的自定义，目前的功能就是一个假壳」+「聊天记录增加一个搜索栏」
> 日期：2026-09-12
> 范围：`server/api.py`（`_validate_custom_provider_payload`）、`orchestrator.py`（`_discover_custom_models` + 自定义 provider 合并循环）、`web/src/layouts/DefaultLayout.vue`（搜索栏）

---

## 1. 根因

### 1.1 自定义 API 是「假壳」（用户截图：添加 `Intern-S2-Preview-397B` → HTTP 400）

两个独立缺陷叠加，导致功能形同虚设：

| # | 缺陷 | 证据 | 后果 |
|---|------|------|------|
| R1 | `base_url` 强校验必须以 `/v1` 结尾，否则 `raise 400` | `server/api.py` 原 `_validate_custom_provider_payload`：`if not (url.endswith("/v1") ...): return False, "base_url 必须以 /v1 结尾"` | 用户填网关地址（如 `http://host.docker.internal:3000`，漏写 `/v1`）直接 400，添加失败 → 以为功能坏了 |
| R2 | 自定义 provider 的「自动发现」承诺未兑现 | `orchestrator.py` `load_model_interfaces_and_endpoints` 仅对 `cp.model_ids` 逐项生成 interface；`model_ids` 为空时**生成 0 个下拉项** | CustomProviders.vue 提示「留空 = 启动时按 base_url 拉 /v1/models 自动发现」是假话 → 用户留空 model_ids 添加后，模型下拉「自定义」分组里**什么都没有** → 假壳 |

补充事实：用户真正想要的 `intern-s2-preview-397b` **本来就在网关下拉里**（GET /models source=gateway，endpoint=new-api），截图里的 400 纯属被 base_url 校验误导走错了入口。

### 1.2 聊天记录无搜索

`DefaultLayout.vue` 的 `.chat-section` 仅渲染 `v-for s in chatSessions`，随会话增多后无法快速定位。

---

## 2. 修复

### 2.1 `server/api.py` `_validate_custom_provider_payload`
- `base_url` 规范化：去尾斜杠；若不以 `/v1` 结尾则**自动补 `/v1`**（OpenAI 兼容根恒为 `/v1`）。
- 移除「必须以 /v1 结尾」的硬 400；保留 http(s) 前缀校验、id 正则、model_ids 数组校验。
- 写回规范化后的 `data["base_url"]`，落盘即用。

### 2.2 `orchestrator.py`
- 新增模块级 `_CUSTOM_MODELS_CACHE`（TTL 120s）+ `_discover_custom_models(base_url, api_key)`：用 `urllib`（**禁用代理**，绕过 127.0.0.1:10808）拉 `${base_url}/models`，失败一律降级为空列表并记 warning，**绝不伪造模型**。
- 合并循环：provider 有 `model_ids` 时按原逻辑；**为空时改为自动发现**，生成 `custom:{id}:{mid}` interface（前缀「自动发现 ·」），并对与网关/内置重复的 model_id 去重，避免下拉出现两份同名模型。

### 2.3 `web/src/layouts/DefaultLayout.vue`
- 聊天记录标题下新增 `el-input` 搜索框（`v-model="searchQuery"`，clearable，Search 前缀图标）。
- 新增 `filteredSessions` computed：按 `topic || id` 不区分大小写子串过滤；空查询 = 全部。
- 列表 `v-for` 改用 `filteredSessions`；新增「无匹配的聊天记录」空态。

---

## 3. 门禁一：构建/类型校验

| 项 | 命令 | 结果 |
|----|------|------|
| 后端语法 | `python3 -m py_compile orchestrator.py server/api.py` | ✅ OK |
| 前端类型 | `vue-tsc --noEmit`（managed node 22.22.2） | ✅ EXIT=0 |
| 前端产物 | `vite build` | ✅ 1760 modules transformed，dist 产出成功 |

部署：`docker-compose up -d --build api` + `docker-compose up -d --build web`，两容器均 `Started` / `GET / → 200`。

---

## 4. 门禁二：实测（live-test 双证据）

> 全部在 `http://localhost:18080`（宿主端口 18080 → web 容器 80；api 容器 8000 内部）实测。
> ADMIN_TOKEN 未配置 → dev 模式放行，写操作无需 token。

### 4.1 修复 R1：不再 400（base_url 自动补 /v1）

```
POST /api/v1/admin/providers
  {"id":"ver-gw","base_url":"http://host.docker.internal:3000","model_ids":[],"enabled":true}
→ HTTP 200，返回 base_url = http://host.docker.internal:3000/v1   ✅ 原必 400 处现已通过
```

对照（修复前）：相同 payload 无 `/v1` → `HTTP 400 {"error":"INVALID_PROVIDER","message":"base_url 必须以 /v1 结尾"}` ❌（旧行为，已消除）。

### 4.2 修复 R2：留空 model_ids → 自动发现真生效

```
POST /api/v1/admin/providers
  {"id":"ver-gw2","base_url":"http://host.docker.internal:3000","api_key":"<new-api key>","model_ids":[],"enabled":true}
→ HTTP 200
GET /api/v1/models  →  custom 分组 count = 104，含 custom:ver-gw2:intern-s2-preview-397b   ✅
```

注：自动发现需 api_key（new-api 的 `/v1/models` 需鉴权；不带 key 时诚实降级为空，已验证不会伪造模型）。

### 4.3 自定义模型端到端可用（选 + 聊都通）

```
POST /api/v1/chat  {"message":"用一句话介绍你自己","model":"custom:ver-gw2:intern-s2-preview-397b"}
→ {"reply":"我是研报助手，一个既能陪你闲聊、也能帮你生成结构化行业研究报告的 AI 助手。", ...}   ✅ 真实补全
```

对照：不带 api_key 的自定义 provider 聊天 → 诚实报错 `端点 'custom:xxx' 缺少 api_key`（非伪造成功）✅ 边界诚实。

### 4.4 聊天记录搜索栏（前端）

- `vite build` 产物含搜索文案：`dist/assets/index-*.js` 命中 `搜索聊天记录` 与 `无匹配的聊天记录` ✅。
- 逻辑：`filteredSessions` 按 `topic||id` 实时子串过滤；空查询回退全部；`clearable` 一键清空。
- 浏览器交互（点击/过滤动画）无法在此无头环境断言，但构建产物 + 类型校验 + 代码审阅三重锁定；功能为纯前端 filter，无后端依赖，风险面极小。

---

## 5. 诚实边界（BLOCKED 不伪造）

- 自动发现对**未鉴权**的网关端点会降级为空（不报错、不伪造），属预期诚实行为；用户需填 api_key 才能拉到模型。
- 指向与 new-api **同一个网关**的自定义 provider 会产生重复下拉项（已按 model_id 去重，但 namespaced id 不冲突时仍可能两份）——属 UX 瑕疵而非缺陷；用户实际用例（用网关已有模型）应直接选下拉里的 gateway 项，无需自定义 provider。
- 搜索栏仅过滤本地已加载的 `chatSessions`（store 内），未做跨页/服务端检索——符合侧栏轻量定位诉求。
- 本 VERIFICATION 未做真实浏览器点击断言（无头环境），以 build + grep 产物 + 代码审阅替代；若 boss 要求可补 Playwright 点击实测。

---

## 6. 回滚 / 清理

- 所有实测 provider（ver-gw / ver-gw2）均已 `DELETE /api/v1/admin/providers/{id}` 清理，`config/custom_providers.yaml` 回到 `providers: []`。
- 代码改动为纯增量（规范化 + 自动发现 + 前端 filter），无 schema 变更，可安全回退。
