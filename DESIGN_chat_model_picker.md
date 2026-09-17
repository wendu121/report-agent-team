# DESIGN_chat_model_picker.md · ChatEntry 模型下拉升级 + 自定义 API 接入

> 关联设计：`DESIGN_OPENAI_ENDPOINTS.md`、`DESIGN_chat_first.md`
> 时间：2026-09-11 19:50

## 1. 现状

- ChatEntry 模型选择藏在小齿轮 popover 里，**默认 "auto"** 在 new-api 网关无对应 channel（昨天修了回落到 `auto-chat`）。
- 用户选具体模型必须打开 popover，UX 不顺。
- 「自定义 API 接入」目前只能改 `config/models.yaml`，无 UI 入口。

## 2. 目标

### 2.1 必须做（round 1，今天）
- **模型下拉升到顶部**——ChatEntry 头部常驻一行「模型」select，可一眼看到当前用的是哪个模型。
- **分组清晰**：下拉里分三组显示——
  - **通用（new-api 自动路由）**：`auto-chat` / `auto-reasoning` / `auto-fast` / `auto-vision`
  - **new-api 具体模型**（从 `/v1/models` 拉的真实列表，隐藏 _internal）
  - **自定义**（自定义 API 接入，见 round 2）
- **localStorage 记忆**：刷新页面保留上次选择。
- **禁用项明示**：chat 阶段 1 暂不支持 tts/vision/embedding 类，下拉中标记「Chat 暂不支持」。

### 2.2 进阶做（round 2，同 PR）
- **自定义 API 接入**：在「插件/模型」popover 里增加「添加自定义 API」入口，弹窗收集：
  - 提供商名（显示用）
  - `base_url`（OpenAI 兼容根 URL，如 `https://api.deepseek.com/v1`）
  - `api_key`（前端不保存明文到日志；写 `.secrets/custom_providers.json` 容器内文件，600 权限）
  - 模型 ID（该 provider 上提供的具体模型，可选）
- **API**：`POST /api/v1/admin/providers` 增；`GET` 列表；`DELETE /api/v1/admin/providers/{id}` 删。
- **持久化**：`config/custom_providers.yaml` + `.secrets/custom_providers.json`（密钥）。容器内 bind mount 已存在，沿用。
- **网关模型列表自动合并**：自定义 provider 的模型也加入下拉「自定义」分组。

## 3. 不做（避免过度工程）

- ❌ 加密 API key（容器内 600 权限足矣；如需加密是另一个 PR）。
- ❌ OAuth 流（暂只支持纯 API key）。
- ❌ 多模型路由策略（轮询/权重）。
- ❌ Provider 健康检查（沿用 new-api 的熔断，custom 端点用 OpenAI SDK 默认重试）。
- ❌ 删除/编辑 `config/models.yaml` 内置端点（只追加自定义）。

## 4. UI 草图

```
┌──────────────────────────────────────────────────────────────────┐
│  📑 研报助手      能聊天、能调用多智能体研报技能                  │
│                                                                  │
│  [🤖 auto-chat ▼]   [🔌 切换智能体]   [⚙ 插件/模型 (0)]         │
└──────────────────────────────────────────────────────────────────┘
                │                                            │
                └─ 下拉内容                                    
                   ┌─ 通用（new-api 自动路由）                
                   │  ● auto-chat (通用对话) ✓当前          
                   │  ● auto-reasoning (深度推理)            
                   │  ● auto-fast (快速响应)                  
                   │  ○ auto-vision (Chat 暂不支持)          
                   ├─ new-api 具体模型 (45 个可用)           
                   │  LongCat-2.0                             
                   │  glm-4.7                                 
                   │  ...                                      
                   ├─ 自定义 (round 2)                        
                   │  + 添加自定义 API                       
                   └───────────────────────────────────────── 
```

## 5. 验收标准

### round 1（必须）
- [ ] 模型下拉可见在 ChatEntry 顶部（不再藏在 popover）。
- [ ] 三分组展示（通用 / new-api / 自定义分组占位）。
- [ ] 选完模型后，刷新页面仍保留（localStorage）。
- [ ] 选 `auto-vision` 等不支持的模型时，输入框下方显示「⚠ Chat 暂不支持该模型」。
- [ ] 选 LongCat-2.0 等具体模型，下一次对话真实发到对应模型（curl 复测 `/v1/models` 拿到该 id，后端正常路由）。

### round 2（可选）
- [ ] 「添加自定义 API」弹窗可填 base_url/api_key/model_id，提交后端落盘。
- [ ] 列表里出现新自定义 provider，且模型下拉「自定义」分组自动出现该模型。
- [ ] 删除自定义 provider 后列表实时更新。

## 6. 风险与边界

- **api_key 落盘**：必须落到 `.secrets/custom_providers.json`（bind mount 已配），不能落 `config/models.yaml`（已 git tracked）。落盘前日志打 `***` 掩码。
- **CORS**：现有 CORS 配置已放行 PUT/DELETE + `X-Admin-Token`，新增 `/admin/providers` 走相同中间件。
- **reload 时机**：新增 provider 后立即生效（NewApiLLMClient 启动时一次性 load；如需热加载则 round 2.5）。

## 7. 你拍板

- **A**：只做 round 1（下拉可见 + 分组 + 记忆），15 分钟交付。
- **B**：A + round 2（自定义 API 接入 UI + 后端 + 持久化），1 小时交付，代码量翻倍。
- **C**：B + 端点热加载（新增 provider 不重启即可用），2 小时。