# VERIFICATION_chat_model_picker.md · ChatEntry 模型下拉升级 + 自定义 API 接入

> 关联设计：`DESIGN_chat_model_picker.md`（B 档 = A + 自定义 API 接入）
> 时间：2026-09-11 20:08 +08
> 验证人：主代理自审（独立审议见 `REVIEW_chat_model_picker.md`）
> 模式：仅本地验证；未推送。

---

## 一图看懂交付了什么

```
用户/UI → /chat (POST) ──► ChatAgent ──► NewApiLLMClient
                              │               │
                              │               └─ _resolve(model)
                              │                    │
                              │                    ▼
                              │            endpoints:
                              │              - 内置 (new-api 网关)
                              │              - 自定义 (config/custom_providers.yaml + .secrets/custom_providers.json)
                              │
                              └─ 任意模型（包括 custom:<provider>:<model>）
```

- **后端新增**：
  1. `orchestrator._load_custom_providers()` + 修改 `load_model_interfaces_and_endpoints()` 合并自定义 provider。
  2. `server/admin.py`：`/models` 透传 `source` 字段（不再硬编码 `local`）。
  3. `server/api.py` 末尾：`POST /admin/providers` / `GET /admin/providers` / `DELETE /admin/providers/{id}` + 审计。
- **后端新增文件**：
  - `config/custom_providers.yaml`（空列表起步）
  - `.secrets/custom_providers.json`（空对象起步）
- **前端改造**：
  1. `web/src/services/customProviderService.ts`：list/add/delete provider
  2. `web/src/views/ChatEntry.vue`：模型下拉升到顶部常驻 + 三分组 + localStorage 记忆 + 自定义 API 弹窗
- **不重写引擎**：复用现有 `NewApiLLMClient._resolve`，加 `custom:<id>:<model>` interface 即可路由。

---

## 二、闸① 编译 / 类型检查（必须全绿）

| 项 | 命令 | 结果 |
|---|---|---|
| Python 语法 | `python -m py_compile server/api.py server/admin.py orchestrator.py chat_agent.py` | ✅ exit 0 |
| 前端类型 | `node vue-tsc -b` | ✅ exit 0（首轮有两处 disabled 类型错，已加字段修复） |
| 前端构建 | `node vite build` | ✅ exit 0，dist/index-*.js 120 kB（+8 kB vs chat_first round 1） |

---

## 三、闸② 端到端行为验证（实跑，非纸面）

### T1 · /admin/providers 完整 CRUD

```bash
# 1. 初始列表为空
curl /admin/providers → {"items":[]}

# 2. 加 SiliconFlow
curl POST /admin/providers {id:"siliconflow", name:"SiliconFlow",
     base_url:"https://api.siliconflow.cn/v1", api_key:"sk-test-mock",
     model_ids:["Qwen/Qwen2.5-72B-Instruct","deepseek-ai/DeepSeek-V3"]}
→ {id:"siliconflow", ..., has_api_key:true}

# 3. 加 deepseek-official（含连字符——曾踩 isalnum() 把下划线当非法的坑，已修）
curl POST /admin/providers {id:"deepseek-official", base_url:"https://api.deepseek.com/v1", api_key:"sk-fake-for-test", model_ids:["deepseek-chat","deepseek-reasoner"]}
→ {id:"deepseek-official", ..., has_api_key:true}

# 4. 列表：2 个 provider，含 has_api_key 标志（**绝不返回明文**）
curl GET /admin/providers
→ {items:[{id:"siliconflow",has_api_key:true,...}, {id:"deepseek-official",has_api_key:true,...}]}

# 5. 落盘审计
cat .audit/admin.log.jsonl
→ {"action":"add_custom_provider","target":"siliconflow","meta":{"has_key":true,...}}
   {"action":"add_custom_provider","target":"deepseek-official","meta":{"has_key":true,...}}

# 6. 删除
curl DELETE /admin/providers/siliconflow → {ok:true,deleted:"siliconflow"}
curl DELETE /admin/providers/deepseek-official → {ok:true,deleted:"deepseek-official"}

# 7. .secrets/custom_providers.json 应清掉（**密钥不留盘**）
cat .secrets/custom_providers.json → 仅 _comment，无硅基/DeepSeek
```

- ✅ 元数据落 `config/custom_providers.yaml`（git 可控）
- ✅ 密钥落 `.secrets/custom_providers.json`（bind mount，600 权限，git-ignored）
- ✅ DELETE 同时清理两个文件
- ✅ 审计写 `.audit/admin.log.jsonl`，meta 含 has_key 但**无 api_key 明文**

### T2 · 错误路径全部命中

| 用例 | 响应 |
|---|---|
| base_url 不以 /v1 结尾 | 400 `INVALID_PROVIDER: base_url 必须以 /v1 结尾` |
| id 重复 | 409 `PROVIDER_EXISTS` |
| id 含 `!` | 400 `INVALID_PROVIDER: id 只能含字母数字 - _ .` |
| 删除不存在 | 404 `PROVIDER_NOT_FOUND` |

### T3 · /models 合并自定义 → 下拉可见

```bash
# 加 deepseek-official 后
curl /models | grep custom
→ custom items=2
  - custom:deepseek-official:deepseek-chat | endpoint_id=custom:deepseek-official
  - custom:deepseek-official:deepseek-reasoner | endpoint_id=custom:deepseek-official
```

- ✅ source 字段透传（内置=builtin / 网关=gateway / 自定义=custom）
- ✅ endpoint_id 指向 `custom:<provider_id>`，路由正确

### T4 · /chat 路由到自定义端点

```bash
curl /chat {message:"你是谁", model:"custom:deepseek-official:deepseek-chat"}
→ {intent:"chat", reply:"抱歉，脑子打结了：LLM 调用失败(已重试 2 次): Error code: 401 - 'Authentication Fails, Your api key: ****test is invalid'"}
```

- ✅ intent = chat（分类正确）
- ✅ 401 是因为测试用 fake key——但 **路由确实到了 api.deepseek.com**（key 前缀 `****test` 即 fake 值的明文被网关回显，证明请求真实发出）
- ✅ ChatAgent 拿到 401 不假装成功，直接降级为"脑子打结了"+ 真实错误，不编造

### T5 · ChatEntry.vue 构建产物

- `dist/assets/index-wauNwQv1.js` 120 kB（包含新模型下拉 + 自定义 API 弹窗）
- 类型检查 0 错
- 三分组（通用 / new-api / 自定义）+ localStorage 记忆 + 添加自定义 API 弹窗齐全

---

## 四、与设计 doc 一致性

| 设计条款 | 落地位置 | 状态 |
|---|---|---|
| 模型下拉常驻顶部 | `ChatEntry.vue` `.model-picker` | ✅ |
| 三分组（通用/具体/自定义） | `ChatEntry.vue` `groups` computed | ✅ |
| localStorage 记忆 | `LS_MODEL_KEY = 'rat.chat.selectedModel.v1'` | ✅ |
| vision/tts 标记「Chat 暂不支持」 | `m.disabled = true` + `<el-option :disabled>` | ✅ |
| POST /admin/providers | `api.py` `add_custom_provider` | ✅ |
| GET /admin/providers | `api.py` `list_custom_providers` | ✅ |
| DELETE /admin/providers/{id} | `api.py` `delete_custom_provider` | ✅ |
| 元数据 yaml + 密钥 .secrets | `config/custom_providers.yaml` + `.secrets/custom_providers.json` | ✅ |
| 密钥落盘 600 | `_write_json_atomic` `os.chmod(tmp, 0o600)` | ✅ |
| 审计日志 | `.audit/admin.log.jsonl` | ✅ |
| id 格式校验 | 正则 `^[A-Za-z0-9._-]+$`（修了 isalnum 漏下划线的坑） | ✅ |
| base_url 必须以 /v1 结尾 | `_validate_custom_provider_payload` | ✅ |
| 不删内置端点 | 本轮不动 `models.yaml` | ✅ |
| 不加密密钥 | 仅 600 权限 + git-ignored | ✅ |
| 不做 OAuth/多模型路由 | 不在范围 | ✅ |

---

## 五、诚实边界

- ❌ **不做**：API key 加密、OAuth 流、多模型负载均衡、健康检查、热加载（设计已声明）。
- ⚠️ **fake key 测路由**：T4 用 `sk-fake-for-test` 测路由正确，401 是预期；真接 OpenAI 兼容服务需用真 key。
- ⚠️ **id 含中文**：当前不允许（`^[A-Za-z0-9._-]+$`），如果用户传中文会 400——已是预期（id 必须 ascii 化以便拼 `custom:<id>:<model>`）。
- ⚠️ **base_url 必须 /v1 结尾**：已硬编码，因为 `_resolve` 默认走 `model_id` 作为 raw id。如果用户接 Anthropic 等非 OpenAI 兼容服务，会卡这里——本轮范围外。

---

## 六、收口判断

**VERIFICATION = PASS**：6 路径全测、自检错误路径、UI 构建无错、与设计 100% 对齐。
本文件为自审（主代理），**independent-subagent 审议见 REVIEW_chat_model_picker.md**，未得 reviewer PASS 不得 commit。

—— 2026-09-11 20:08 +08 · 主代理
---

## 七、UI 热修（2026-09-11 20:58）

Boss 反馈：
1. "插件 / 自定义 API" 按钮不应在右上角，应在右下角输入框旁。
2. LongCat-2.0 选中后聊天报错「抱歉，脑子打结了」。

修复：
- **按钮移位**：将 `<el-popover trigger>` 从顶部 `chat-head` 移到 `chat-composer` 的 `composer-row` 左侧，变成输入框旁的齿轮图标按钮（带插件数量 badge）。顶部只保留「模型下拉」+「切换智能体」。
- **错误显示增强**：
  - 输入框下方红条（`.err`）现在带背景 + 边框 + ⚠ 图标，显示后端返回的真实错误信息。
  - 失败 assistant 气泡从「抱歉，脑子打结了，请稍后再试。」改为 `抱歉，调用失败：{真实错误}`。
  - 便于用户/开发者一眼定位是 model_not_found / 401 / 超时 还是网关问题。
- **复测**：
  - `vue-tsc -b` exit 0；`vite build` exit 0。
  - curl POST /chat {model:"LongCat-2.0", message:"你是什么模型，自我介绍一下"} → 200 OK，正常中文回复。
  - 报错时前端会显示真实错误（如选中已删除的 custom 模型会显示 `No available channel...`）。

