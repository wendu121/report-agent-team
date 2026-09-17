# 独立评审 · 自定义 API 假壳修复 + 聊天记录搜索栏

**评审人**：独立 reviewer（不依赖 lead 结论，基于代码 + 实跑）
**项目**：`E:\第二电脑\report-agent-team`
**API**：`http://localhost:18080`（`ADMIN_TOKEN` 未设置，无需鉴权）
**结论**：**PASS**

---

## (a) 结论

两个特性均按预期实现并实跑通过：自定义 API 不再强依赖 `/v1` 后缀（自动规范化 + 自动发现模型），聊天记录搜索栏在 UI 与构建产物中均存在且行为正确。未发现会导致崩溃或回退的隐藏缺陷。

---

## (b) 实跑测试结果（均为本人用 curl 重新执行的真实观测值）

| # | 测试 | 期望 | 实际观测 | 结果 |
|---|------|------|----------|------|
| 1 | POST `/api/v1/admin/providers` `rv-gw`，`base_url=http://host.docker.internal:3000`，`model_ids=[]` | 200，`base_url` 规范化成 `...:3000/v1` | **HTTP 200**，body `{"id":"rv-gw","base_url":"http://host.docker.internal:3000/v1","model_ids":[],"enabled":true,"has_api_key":false}` | ✅ |
| 2 | POST `rv-gw2`，带 `api_key=sk-PzxN…`，`model_ids=[]` | 200 | **HTTP 200**，`has_api_key:true`，`base_url` 同样规范化为 `...:3000/v1` | ✅ |
| 2b | GET `/api/v1/models` | 自定义组含 `custom:rv-gw2:intern-s2-preview-397b`，count>0 | `total=214`，`custom:rv-gw2:*` 共 **104** 个，`custom:rv-gw2:intern-s2-preview-397b` 在列表中（`source=custom`,`endpoint=custom:rv-gw2`） | ✅ |
| 3 | POST `/api/v1/chat`，`model=custom:rv-gw2:intern-s2-preview-397b`，`message=hi` | 200 + 非空 `reply` 字符串 | **HTTP 200**，`reply` 存在、长度 126、`intent=chat`，内容 `"Hi there! I'm 研报助手, an AI assistant..."` | ✅ |
| 4 | `grep "搜索聊天记录" web/dist/assets/*.js` | 命中构建产物 | `web/dist/assets/index-BCsaSYZy.js` 命中；`无匹配的聊天记录` 亦命中 | ✅ |

> 测试结束后已 `DELETE rv-gw` 与 `rv-gw2`（`HTTP 200`），环境恢复干净状态。

---

## (c) 代码审阅发现

### 自定义 API 规范化（`server/api.py`）
- `_validate_custom_provider_payload`（L1065-1088）：
  - `url = data["base_url"].strip().rstrip("/")` 先去尾斜杠；
  - 已 `endswith("/v1")` 则保留，否则 `url + "/v1"`；
  - **不再有任何对 `/v1` 后缀的硬 400 校验**（原“假壳”根因已移除）；
  - 结果写回 `data["base_url"]` 供落盘使用。✅ 与 Test 1 实测一致。
- `add_custom_provider`（L1147-1153）：落盘 `data["base_url"].strip()`，即规范化后的值。✅

### 自动发现（`orchestrator.py`）
- `_discover_custom_models`（L587-619）：
  - `url = base_url.rstrip("/") + "/models"`，用 `urllib.request` + `ProxyHandler({})` 禁用系统代理（避免 127.0.0.1:10808 等代理导致 TLS 误报）；
  - 失败（网络/鉴权/超时）→ `ids=[]`，不抛异常、不伪造模型；
  - 缓存键含 `base_url + bool(api_key)`，TTL 120s。✅ 实测拉到 104 个模型。
- 合并循环（L673-709）：
  - `disabled` provider 跳过；
  - endpoint id = `custom:{id}`；
  - `model_ids` 为空 → 调 `_discover_custom_models` 自动发现（`discovered=True`）；
  - 按 `interface_id = custom:{id}:{mid}` 去重（同 provider 内 + 自动发现时跳过与网关/内置重名的 `model_id`）；
  - 每个模型生成独立 interface，`endpoint` 指向 `custom:{id}`，`source=custom`。✅ 与 Test 2b 实测一致。
- 关键配套：`add_custom_provider` / `delete_custom_provider` 均调 `_reset_chat_agent_singleton()`（api.py L1176/L1208），使下一次 `/chat` 重建 `NewApiLLMClient`、看到新端点——这是 Test 3 能即时用上自动发现模型的前提，逻辑闭环正确。✅

### 聊天记录搜索栏（`web/src/layouts/DefaultLayout.vue`）
- `searchQuery = ref('')` + `filteredSessions` computed（L166-173）：`q = searchQuery.trim().toLowerCase()`，空则返回全部；否则按 `(topic||'').includes(q) || (id||'').includes(q)` 大小写不敏感子串过滤。✅
- `<el-input>` 搜索框（L40-47）位于 `.chat-title`（L31-39）与 `.chat-list`（L48）之间。✅
- `v-for` 使用 `filteredSessions`（L50，`s in filteredSessions`）。✅
- 空状态（L60-61）：`chatSessions.length===0` → “暂无聊天记录”；`chatSessions.length>0` 但过滤为空 → “无匹配的聊天记录”。两种状态均覆盖。✅
- 构建产物含两处字符串（Test 4），说明 `dist` 已包含本次改动。✅

---

## (d) 边界与诚实备注

1. **轻微健壮性问题（非阻塞）**：若用户传入 `base_url=http://x/v1foo`（含 `v1` 但不是 `/v1` 结尾），`endswith("/v1")` 为 False → 追加成 `http://x/v1foo/v1`。属用户输入错误的窄边角，不影响正常用法，可不修。
2. **发现失败的缓存窗口**：`_discover_custom_models` 失败时缓存空列表 120s。若网关短暂抖动，provider 会暂时“零可选模型”（诚实空，而非旧版假壳），120s 后重试恢复。这是有意取舍，非缺陷。
3. **`dist` 是否为最新**：本次 grep 命中，证明当前 `dist` 已含改动；若他人未重新 `npm run build`，生产部署需重新构建（纯流程项，非代码缺陷）。
4. **测试地址依赖**：测试用 `host.docker.internal:3000` 在本环境可解析（故 104 模型与 chat 200 均成功）。若部署在无 docker 解析能力的裸机，需改指向真实网关地址——属环境配置，非本特性问题。
5. **未发现隐藏崩溃/回退**：`_discover_custom_models` 对“返回非 dict JSON / 列表 / 空”均有安全降级；合并循环对空 `model_ids`、重复 id、跨 provider 重名均有处理；前端过滤对 `topic`/`id` 为空有 `||''` 兜底。代码与实跑一致，无需 BLOCK。

**最终判定：PASS（两个特性均实跑通过，代码实现与意图吻合，无阻塞项）。**
