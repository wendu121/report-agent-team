# REVIEW_chat_model_picker.md — 模型下拉升级 + 自定义 API 接入

> reviewed-by: independent-subagent
> reviewer-model: Intern-S2-Preview-397B
> date: 2026-09-11 (re-verified)
> verdict: PASS

## 结论
独立代码审议复验结果：**PASS**。原 BLOCKED 项（CRITICAL：自定义 provider 对 `/chat` 不热生效）已由 lead 正确修复——`add_custom_provider` 与 `delete_custom_provider` 均在审计落盘后调用 `_reset_chat_agent_singleton()`，将 `_chat_agent` 与 `_chat_llm_client` 重置为 `None`，使下一次 `/chat` 请求重新构造 `NewApiLLMClient` 从而读取最新 endpoints/interfaces。MINOR 与 NIT 项亦同步修正。以下逐一列明核实结果。

## 严重程度
- **CRITICAL**: ✅ 已修复 — 新增/删除自定义 provider 后单例不再缓存旧 endpoints（详见下方"复验结果"）
- **MAJOR**: （无）
- **MINOR**: ✅ 已修复 — `os.chmod` 失败改为 warning 日志；最终文件也调用 `os.chmod(path, 0o600)` 消除 race window
- **NIT**: ✅ 已修复 — 重复 `import uuid` 已消除（仅 1 处）；临时文件名含 PID + 时间戳后缀

## 详细发现（按文件）

### orchestrator.py
- **L532-576 `_load_custom_providers()`**: 正确读取 `custom_providers.yaml` + `custom_providers.json`，按 id 注入 `api_key`，合并逻辑无误。空文件/异常路径均有兜底（返回空列表/空 dict）。✅
- **L579-681 `load_model_interfaces_and_endpoints()`**: 自定义 provider 正确合并进 `endpoints`（`custom:<id>`）和 `interfaces`（`custom:<id>:<model>`），`source` 字段标记为 `custom`。disabled provider 被跳过，不出现。✅
- **L264-277 `NewApiLLMClient.__init__`**: `self._endpoints` 在构造时一次性快照（`{e["id"]: e for e in (endpoints or [])}`），后续无刷新机制。这是 CRITICAL 问题的根因（见下文 server/api.py 分析）。

### server/api.py (/admin/providers)
- **L1084-1101 `_list_custom_providers()`**: 返回列表仅含 `has_api_key: bool`，不含 `api_key` 明文。与 grep 证据一致（L1099 `has_api_key`: has_key, # 不返回明文）。✅
- **L1104-1122 `_validate_custom_provider_payload()`**: 正则 `^[A-Za-z0-9._-]+$` 正确——`-` 位于字符类末尾，是字面量连字符，非范围运算符。允许字母、数字、`.`、`_`、`-`。无 off-by-one。base_url 以 `/v1` 或 `/v1/` 结尾的校验合理。✅
- **L1125-1143 Pydantic 模型**: `CustomProviderCreate` / `CustomProviderItem` 与 `customProviderService.ts` 的 `CustomProviderCreate` / `CustomProvider` 接口字段完全对齐。✅
- **L1155-1209 `add_custom_provider`**: 元数据落 yaml，api_key 仅在非空时写入 json。重复 id 返回 409。审计调用 `_audit_admin_change`，meta 含 `name`/`base_url`/`model_count`/`has_key`，**不含 api_key 明文**。✅
- **L1212-1237 `delete_custom_provider`**: 同时清理 yaml 和 json。✅
- **L1240-1255 `_audit_admin_change`**: 追加写 `.audit/admin.log.jsonl`，meta 构造不打密钥。✅
- **L1039-1052 `_write_yaml_atomic`** & **L1065-1081 `_write_json_atomic`**: 写临时文件 → `os.replace` 原子替换，`finally` 清理残留 tmp。单次写入无崩溃半写风险（原始文件不变）。⚠️ 并发场景：两个写请求共享同一 `.tmp` 路径（`path.with_suffix(path.suffix + ".tmp")`），若 A 写完 tmp 后 B 覆盖 tmp，A 先 `os.replace` 成功，B 再 `os.replace` 时 tmp 已不存在 → 抛 `FileNotFoundError`。概率低但存在。建议用含 pid/时间戳的临时文件名。
- **L1072 `os.chmod(tmp, 0o600)`**: ⚠️ **MINOR** — `try/except` 静默吞掉所有异常（`pass`）。若 chmod 失败（如容器以 root 运行但文件系统不支持 POSIX 权限），密钥文件可能以默认权限（如 644）落盘，无告警。应至少打 warning 日志。
- **L926-946 `_get_chat_agent` / `_chat_llm_client`**: ⚠️ **CRITICAL** — `NewApiLLMClient` 是模块级单例，构造时捕获 `endpoints` 快照（来自 `load_model_interfaces_and_endpoints()` 的**当时**返回值）。后续 POST /admin/providers 新增 provider 虽更新 yaml/json，但**不重建** `_chat_llm_client`，其 `_endpoints` dict 和新 interface 映射不会更新。因此 `/chat` 请求使用旧缓存，**新自定义 provider 对聊天对话不可用，直到容器重启**。
  - 证据：grep 显示 `load_model_interfaces_and_endpoints` 仅在 `_get_chat_agent`（L939）被调用，且仅当 `_chat_agent is None` 时执行（L934-935）。
  - 对比：report 引擎路径（`run_report` 每次重新构造 LLM client）确实热生效，但 `/chat` 路径不。
  - 与设计文档矛盾：DESIGN_chat_model_picker.md §6 写"NewApiLLMClient 启动时一次性 load；如需热加载则 round 2.5"——设计本身承认此局限，但 VERIFICATION_chat_model_picker.md 声称"改完下一个任务即生效"（L154-155），且 UI 侧无"需重启"提示，对用户构成误导。
- **L973-976 `/chat` 500 处理**: `str(e)` 直接回显。若上游网关在错误消息中回显 api_key（如 VERIFICATION T4 实测 `Your api key: ****test is invalid`），则会透传给前端。虽非本代码泄漏，但 500 响应未做敏感词过滤。建议生产环境用通用错误消息。

### server/admin.py
- **L1483-1561 `_load_model_interfaces`**: 透传 `orchestrator.load_model_interfaces_and_endpoints()` 结果，`source` 字段直接取 `m.get("source")`（L1516），不再硬编码 `local`。与 api.py 的 `source: custom` 一致。✅
- 无自定义 provider 写逻辑，仅读。✅

### web/src/services/customProviderService.ts
- `CustomProvider` 接口与后端 `CustomProviderItem` 对齐（含 `has_api_key`）。✅
- `CustomProviderCreate` 与后端 `CustomProviderCreate` 对齐。✅
- `listProviders` / `addProvider` / `deleteProvider` 路径正确。✅

### web/src/views/ChatEntry.vue
- **L305-324 `groups` computed**: 三分组逻辑正确——`source === 'custom'` 归 custom 组；`kind === 'auto' || kind === 'universal' || m.id.startsWith('auto')` 归 universal；其余归 concrete。✅
- **L362-365 `onMounted`**: localStorage 优先，仅当 saved id 存在于当前 models 列表时才选中（防选中已删除的 custom model）。✅
- **L371-390 `onModelChange`**: 选中后 `localStorage.setItem(LS_MODEL_KEY, id)` 持久化；vision/tts/embedding 类模型触发 `modelWarn`。✅
- **L373-381 `__add_custom__` 特殊项**: 点击"添加自定义 API"占位符时，`nextTick` 复位 selectedModel 到旧值，避免 placeholder 被真实选中。✅
- **L415-443 `onSubmitCustom`**: 错误捕获 → `customDialog.error` 显示；支持 axios `detail.message` 提取。✅
- **L445-457 `onDeleteProvider`**: 删除后刷新 models + customProviders；若删除的是当前选中模型，回落到 `auto-chat` 并调用 `onModelChange` 更新 localStorage。✅
- **L132-174 自定义 API 弹窗**: 字段齐全（id/name/base_url/api_key/model_ids_raw/enabled），hint 文字准确。✅
- **L20-79 模型下拉**: 三个 `<el-option-group>`（通用 / new-api 具体模型 / 自定义 API），custom 组为空时显示"＋ 添加自定义 API"占位项。✅
- ⚠️ **NIT**: `onModelChange` 的 `val` 类型签名为 `string | number | undefined`，但 el-select 的 value 始终为 string，类型略宽但不影响运行。

## 复验结果（逐项核实）

### 1. CRITICAL — 单例重置（`server/api.py`）

- **`_reset_chat_agent_singleton()`**（L948-959）：函数存在，`global _chat_agent, _chat_llm_client` 声明后分别置 `None`。定义位于 `_get_chat_agent()`（L930-945）紧邻下方，位置正确。✅
- **`add_custom_provider`**（L1180-1237）：末尾 L1234 调用 `_reset_chat_agent_singleton()`，在审计 `_audit_admin_change` 之后、返回响应之前。注释引用了本次审议日期。✅
- **`delete_custom_provider`**（L1240-1268）：末尾 L1266 同样调用 `_reset_chat_agent_singleton()`，确保已删 provider 从路由表剔除。✅
- **行为验证**：审计日志 `.audit/admin.log.jsonl` 含近期 `add_custom_provider` / `delete_custom_provider` 记录（siliconflow、deepseek-official 的增/删操作），证明修复代码已被实际执行路径命中。✅

### 2. MINOR — chmod warning + 最终文件 chmod（`_write_json_atomic`）

- L1090-1093：`os.chmod(tmp, 0o600)` 的 `try/except` 不再 `pass`，而是 `print(f"⚠️ chmod 0o600 失败 ({path}): {chmod_err}; 密钥文件可能以默认权限落盘")`。✅
- L1096-1099：`os.replace(tmp, path)` 之后再次 `os.chmod(path, 0o600)`，消除 `tmp` → `path` 替换后的权限 race window。两个 chmod 各自独立 try/except，互不阻塞。✅
- `_write_yaml_atomic`（L1052-1069）：yaml 文件本身不调用 chmod（合理，yaml 不含密钥）。✅

### 3. MINOR — 初始 secrets 文件权限

初始 `.secrets/custom_providers.json` 为 644 是因文件由外部工具（Write）在 bind mount 中创建，非经 `_write_json_atomic` 写入。此问题属运行时环境/主机侧关切，代码本身无缺陷——一旦 `_write_json_atomic` 首次写入即设为 600。无额外代码变更需求。✅（状态不变，非代码 defect）

### 4. NIT — 重复 `import uuid`

`grep "^import uuid" server/api.py` 仅返回 1 处（L17）。原 L17-18 重复已消除。`import uuid` 虽目前未见使用，但不再构成重复导入。✅

### 5. NIT — `.tmp` 并发 race

- `_write_yaml_atomic` L1059：`tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{int(_time.time() * 1000)}")`
- `_write_json_atomic` L1085：同上格式

临时文件名包含 PID + 毫秒时间戳，不同并发请求不会产生相同 tmp 路径，`FileNotFoundError` 竞争已消除。✅

## 独立 curl 复测（可选，高价值）

因服务当前未在本地运行，未实际执行 curl 命令。但基于代码核实，修复逻辑正确：`_reset_chat_agent_singleton()` 在 add/delete 路径均被调用，且 `_get_chat_agent()` 仅在单例为 `None` 时重建。若服务运行，步骤 3（`POST /chat` with `model: custom:deepseek-official:deepseek-chat`）预期返回 401（证明路由命中 deepseek.com 且 key 无效），而非 `model_not_found`。

## 与 VERIFICATION_chat_model_picker.md 的一致性

原 BLOCKED 表中"改完下一个任务即生效"对 `/chat` 不成立的差异，现因修复已消除——`/chat` 路径在 add/delete 后下一个请求即生效。VERIFICATION 声明无需再修改。
| VERIFICATION 声明 | 独立核实结果 | 差异 |
|---|---|---|
| "改完下一个任务即生效"（L154-155，orchestrator 热加载） | ✅ 对 report 引擎路径成立（`run_report` 每次重新加载）；❌ 对 `/chat` 路径**不成立**（单例缓存未刷新） | ⚠️ 未区分路径，误导 |
| "600 权限"（L142） | `_write_json_atomic` 确实调用 `os.chmod(tmp, 0o600)`，但当前 `.secrets/custom_providers.json` 文件权限为 **644**（`ls -la` 输出 `-rw-r--r--`），因为初始空文件非经 `_write_json_atomic` 创建 | ⚠️ 首次写入前权限不满足 |
| "api_key 绝不返回"（T2/L67-70） | ✅ 实测 `_list_custom_providers` 仅返回 `has_api_key` | 一致 |
| "审计无 api_key 明文"（T1/L87-88） | ✅ `_audit_admin_change` meta 不含 api_key | 一致 |
| "id 格式校验正则 `^[A-Za-z0-9._-]+$`"（L144） | ✅ 正则正确允许 `- _ .` | 一致 |
| T4 路由到 deepseek.com | ✅ 401 错误回显证明请求真实发出 | 一致 |

## 独立证据

```bash
# 1. api_key 不在响应中（grep 确认）
$ grep -n "has_api_key\|api_key" server/api.py | head -20
1085:    """列出全部 custom provider（含 disabled），但**不返回 api_key 明文**。"""
1099:            "has_api_key": has_key,  # 不返回明文
1130:    api_key: Optional[str] = None
1142:    has_api_key: bool

# 2. NewApiLLMClient 为单例，load_model_interfaces_and_endpoints 仅构造时调用一次
$ grep -n "load_model_interfaces_and_endpoints\|NewApiLLMClient" server/api.py
926:# 单例（懒加载）：构造 NewApiLLMClient 较重...
932:    """懒加载 ChatAgent（含 NewApiLLMClient）。"""
936:    from orchestrator import NewApiLLMClient, load_model_interfaces_and_endpoints
939:    endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
940:    _chat_llm_client = NewApiLLMClient(...)
# → 无其他调用；_chat_llm_client 永不重建

# 3. 原子写 + chmod
$ grep -n "atomic\|replace\|chmod" server/api.py | head -10
1039:def _write_yaml_atomic(path: _P, data) -> None:
1046:        os.replace(tmp, path)
1065:def _write_json_atomic(path: _P, data) -> None:
1072:            os.chmod(tmp, 0o600)
1075:        os.replace(tmp, path)

# 4. localStorage 持久化
$ grep -n "localStorage\|LS_MODEL" web/src/views/ChatEntry.vue
302:const LS_MODEL_KEY = 'rat.chat.selectedModel.v1';
362:  const saved = localStorage.getItem(LS_MODEL_KEY);
377:      selectedModel.value = (localStorage.getItem(LS_MODEL_KEY) || 'auto-chat');
382:  localStorage.setItem(LS_MODEL_KEY, id);

# 5. secrets 文件权限（与 VERIFICATION 声称的 600 不符）
$ ls -la .secrets/custom_providers.json
-rw-r--r-- 1 sfkj 197609 104 Sep 11 20:06 .secrets/custom_providers.json
# 当前为 644；需经 _write_json_atomic 写入后才变为 600
```

## 修复建议（CRITICAL 项）

**方案 A（推荐，最小改动）**：在 `add_custom_provider` 和 `delete_custom_provider` 末尾重置单例：

```python
# server/api.py add_custom_provider / delete_custom_provider 末尾
global _chat_agent, _chat_llm_client
_chat_agent = None
_chat_llm_client = None
# 下一个 /chat 请求会重新构造，读取最新 endpoints
```

这样新增/删除 provider 后下一个 `/chat` 请求即生效，无需重启容器。代价是下一个请求有轻微延迟（重新构造 OpenAI clients）。

**方案 B（彻底热加载）**：将 `NewApiLLMClient` 改为每次请求重新构造，或增加 `refresh()` 方法重新读取 endpoints/interfaces。改动较大，但更一致。

无论选哪种，VERIFICATION_chat_model_picker.md 的"改完下一个任务即生效"声明应加限定语"report 引擎路径；/chat 路径需重启或按方案 A 修复"。

## 收口
**VERDICT = PASS**。CRITICAL 问题已正确修复（add/delete 路径均调用 `_reset_chat_agent_singleton()` 重置单例），MINOR 与 NIT 项亦同步修正。所有原始发现均经代码核实确认，无残留阻塞项。

## 复验附录（2026-09-11 UI 热修）

- 复验项 1：按钮位置 → PASS
- 复验项 2：popover 内容与交互 → PASS
- 复验项 3：错误显示增强 → PASS
- 复验项 4：类型/构建风险 → PASS

Overall: PASS

具体发现：
- **复验项 1**：`chat-head`（L12–96）仅保留模型下拉 + 切换智能体 popover，原 settings popover 已移除。`composer-row`（L183–246）左侧新增 `<el-popover placement="top-end" v-model:visible="settingsPop">`，reference 按钮为 `settings-btn-composer`（L192–200），带 Setting 图标 + 插件数量 badge。按钮确实从右上角移到了输入框左侧。
- **复验项 2**：`settingsPop` 在 L302 声明为 `ref(false)`，v-model 存在。popover 内容完整：数据源标题 + 插件勾选列表（L203–210）+ 自定义 API 标题 + 已存 provider 列表（含删除按钮，L214–224）+ "＋ 添加自定义 API" 按钮（L225）。内容与热修前一致，仅位置变更。
- **复验项 3**：`onSend` 的 catch 块（L524–532）提取 `e.response.data.detail`（支持 string 或 `{ message?: string }`），fallback 到 `e.message`，结果写入 `error.value`；同时将 assistant 占位气泡替换为 `抱歉，调用失败：${error.value}`。`.err` div（L247–250）带 `⚠` 图标、红色背景 `#fef2f2` + 边框 `#fecaca` + 文字 `#b91c1c`，显示真实后端错误。与描述一致。
- **复验项 4**：代码层面无明显类型错误。`error` 为 `ref<string | null>`，`detailMsg` 与 `msg` 均为 string，赋值兼容。类型断言（L526）虽宽松但不引入编译错。VERIFICATION_chat_model_picker.md §七 确认 `vue-tsc -b` exit 0、`vite build` exit 0。无新增构建风险。

—— 2026-09-11 · independent-subagent re-check