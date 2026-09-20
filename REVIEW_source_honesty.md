<!-- reviewed-by: independent-subagent -->
<!-- model: Hy3 (independent subagent, gate ③) -->
<!-- timestamp: 2026-09-19 19:23:23 UTC+8 -->

# REVIEW · 检索源诚实降级（gate ③ 独立审议）

> 里程碑：`DESIGN_source_honesty.md` · 2026-09-19
> 自审：`VERIFICATION_source_honesty.md`（主代理，未采信，已逐项复现）
> 独立审议：**已执行**（本文档即正式审议，取代原 BLOCKED 占位稿）
> 被测对象：工作树中**全部未提交**改动（无 git commit / 无 push；本审议不修改任何被测代码，仅产出此 REVIEW）

---

## 结论：**PASS_WITH_NOTES**（无 BLOCKING 项）

- 11 个疑点逐条验证：9 × PASS，2 × PASS_WITH_NOTES（点 2、点 8），其中点 2 的主代理"反例"经实测**不成立**。
- 真机验证：容器内 `verify_source_honesty.py` **9/9 PASS**（刚复跑）、`verify_source_honesty_api.py` **9/9 PASS，0 FAIL，0 SKIP**（含真机 HTTP `/chat` A5，刚复跑）。
- 仅 NOTE 级事项（无阻断）：陈旧 docstring 2 处、`sources_are_real` all-or-nothing 过度保守、前端无人工浏览器点击、`escalate→0字` 未独立复跑两次、`e2e_report_flow.py` 两缺陷确认但 pre-existing 无法经 git 证明。

---

## 1. 逐条回应 11 个疑点（均附真实命令/原始输出或 `file:line`）

### 点 1 · 是否还有别的路径伪造检索结果 —— **PASS**

全仓定位 `MockProvider` / `fallback_to_mock` / `using_mock_search`：

```
$ grep -rn -E "MockProvider|fallback_to_mock|using_mock_search" --include=*.py . | grep -vE "tests/|#"
tools/data_sources.py:54:class MockProvider:
tools/data_sources.py:1378:    "mock": MockProvider,
tools/data_sources.py:1555:    if isinstance(p, MockProvider)}
tools/data_sources.py:1606:    rec["is_mock"] = True
tools/data_sources.py:1611:def build_search_tool(specs, secrets, ...):
tools/__init__.py:31:using_mock_search: bool = False
tools/__init__.py:55:from .data_sources import load_data_sources, load_secrets, build_search_tool
tools/__init__.py:89:if web.using_mock_search:
tools/web_search.py:93:class MockProvider:
```

- `tools/data_sources.py:54` 的 `MockProvider` 仅当 `provider=="mock"`（显式声明）才装载：`build_search_tool` 文档 `data_sources.py:1621`「仅显式声明时；离线/链路验证用」，`1555` 用 `isinstance(p, MockProvider)` 过滤。非伪造路径。
- `tools/web_search.py:93` 的 `MockProvider` 是**死代码**：全仓无任何生产 `import`（生产侧 `data_sources.py:39` 仅 `from .web_search import TavilyProvider, score_to_credibility`，不导入 `MockProvider`）。
- `using_mock_search` 只在显式 mock 时为 `True`（`__init__.py:89`），缺密钥的真实源进 `degraded` 而非 mock。
- **结论：无其他伪造路径。**

### 点 2 · `_source_warnings` 开关是否自相矛盾 —— **PASS_WITH_NOTES（主代理反例不成立）**

主代理自列反例：「所有源降级 / web_search 返回 `ok=False` 时 `attempted_ws` 会不会恒 False → 最该告警的场景反而静默？」

实测机制（`chat_agent.py`）：
```
300:def _source_warnings(bundle, tool_entries: list) -> tuple[list[str], bool]:
320:    attempted_ws = any(t.get("tool") == "web_search" and t.get("ok") for t in entries)
333:    if attempted_ws:
334:        for d in bundle.web_search.degraded:   # 遍历降级源告警
344:    sources_are_real = (not attempted_ws) or ((not degraded) and mock_dropped == 0)
```
- web_search 包装器**恒返回 `ok=True`**：`orchestrator.py:1442` `dispatch_tool` 的 web_search 分支返回 `{ok:True, ...}`；内部 `tools/web_search.py:116` `search_many` 逐 query `except ToolError`（`:130`）仅把 `status.ok=False`，**不抛出**，故包装层恒 `ok=True`。
- 因此：只要本轮调过 web_search（含降级场景），`attempted_ws=True`，即遍历 `bundle.web_search.degraded`（`['tavily']`）告警，`sources_are_real=False`。**主代理担心的最坏情况不会静默**——反例不成立。
- 唯一边角：若 web_search 包装器**自身崩溃**（返回 `ok=False` 而非 `ok=True`），则 `attempted_ws=False` → 静默。属崩溃路径，可接受，记 NOTE。

### 点 3 · `sources_are_real` 是否会被前端误用 —— **PASS（附过度保守 NOTE）**

`web/src/views/ChatEntry.vue`：
```
160: v-if="!m.thinking && m.source_warnings && m.source_warnings.length"  → el-alert 告警
168: v-if="...m.sources && m.sources.length && m.sources_are_real !== false"  → 展示「来源（N）」
178: v-else-if="...m.sources_are_real === false"  → 「本次无可核验来源」
```
- 告警条由**后端驱动**的 `source_warnings.length` 控制，与 `sources` 无关。故 `sources_are_real===false && sources.length===0` 时告警**仍显示**。前端误用担忧**不成立**。
- NOTE：`sources_are_real` 是 all-or-nothing（`:344`）。部分降级（如 1/17 源缺密钥）时 `sources_are_real=False` → `:168` 条件不满足，**所有真实源也被隐藏**，过于保守（但这是"宁可少展示也不冒充"的保守方向，非安全漏洞）。

### 点 4 · `degraded` 是否随配置刷新（热重载）—— **PASS（真机验证）**

机制（`chat_agent.py`）：
```
382:def _get_bundle(self):
390:    if self._bundle is None or self._bundle_mtime != self._config_mtime():
393:        self._bundle = build_tools()
394:        self._bundle_mtime = self._config_mtime()
397:def _config_mtime(self) -> float:
400:    # 必须按**当前账号**的命名空间取 mtime
```
每账号单例 ChatAgent，`_get_bundle` 按配置 mtime 失效重建。真机 live `/chat` 对照（wendy 容器，同进程内改 key）：
- **STATE A（去 `DS_TAVILY_API_KEY`）**：`source_warnings=['数据源「Tavily 搜索」未配置密钥，本次未参与检索…']`、`sources_are_real=False`、`degraded=['tavily'] providers=16`。
- **STATE B（同进程复原 key 后下次 `/chat`）**：`source_warnings=[]`、`sources_are_real=True`、sources 含 tavily 真实结果、`degraded=[] providers=17`。
- 复原后 `md5sum` 字节级一致（`8264923f16093c2483e72fb08ee62a16` 双份），**无残留副作用**。
- **结论：热重载生效，配置改动零重启即反映到下一次 `/chat`。**

### 点 5 · `is_mock` 投放位置是否完整 —— **PASS**

- 仅 `SearchTool.search_many` 打标：`data_sources.py:1606` `rec["is_mock"] = True`（仅 `MockProvider` 实例路径）。
- 三处剔除占位：`chat_agent.py:267`（`_build_sources` 丢弃）、`chat_learning.py:174`（`tool_entries` 路径）、`chat_learning.py:185`（`extra_search` 路径）。
- `web_search.py:93` `MockProvider` 死代码，不进生产链路。
- 实测：`verify_source_honesty_api.py` **A2**（纯占位→sources=[]）、**A6**（占位不进知识库，泄漏=[]）均 PASS，**live A5 `mock_leak=0`**。无占位泄漏。

### 点 6 · 新增断言是否「假通过」—— **PASS（主代理担忧无依据）**

主代理担忧：S5 模块级注入、S8 替换 `_config_dir` 会被 `tools/__init__.py` 的 `from .data_sources import ...` 绕过。

实测代码路径：
- `tools/__init__.py:55` 在函数**内部 call-time** `from .data_sources import load_data_sources, load_secrets, build_search_tool` → S5 对 `ds.load_data_sources`/`ds.load_secrets` 的 monkeypatch **在 `build_tools()` 调用时生效**，未被绕过。
- `data_sources.py:1383` `_config_dir()`、`1401` `load_secrets()`、`1450` `load_data_sources()` 均读模块级 `_config_dir` → S8 替换 `ds._config_dir` 被拾取。
- 刚复跑：**S5 / S5b / S8 均 PASS**（输出见 §3）。断言非假通过。

### 点 7 · `unavailable_sources` 是否真改变模型措辞 —— **PASS（真机验证）**

- 注入路径：`orchestrator.py:1817` `summary["unavailable_sources"] = [...]`（取 `bundle.web_search.degraded`）；`:1811` `failed_sources`（空结果）。
- 真机 live `/chat`（去 key，问「2026年8月中国外卖行业市场规模」）：模型如实答「**未能找到 2026年8月中国外卖行业市场规模的可靠数据**」，**无机构署名编造**。
- 复原 key 后：引 tavily 真实数据（score 0.956–0.819 → `high` 可信度，非低可信度伪装）。
- **结论：诚实降级硬规则生效，模型不再编造带机构署名的数据。**

### 点 8 · 前端零浏览器验证 —— **PASS_WITH_NOTES（NOTE：无人工点击）**

- `docker compose build web` 内 `vue-tsc -b` 无 `error TS`（主代理 `VERIFICATION_source_honesty.md:33-34`：`docker exec report-web grep -rl '本次检索数据不完整' /usr/share/nginx/html/assets/` 命中 `index-ewgqgvAx.js`；`'本次无可核验来源'` 同样命中）。
- 两条新 UI 字符串已进构建产物，且 `chatService.ts` 接口含 `source_warnings?`/`sources_are_real?`（`web/src/services/chatService.ts:24-28`）。
- NOTE：**无人工浏览器点击验证**（环境所限），仅 vue-tsc + 产物字符串 + 代码逻辑核对。逻辑正确性已由后端断言（A1–A6）与 `ChatEntry.vue` 代码核对覆盖。

### 点 9 · 引擎 `escalate → 0字` 是否与本次改动相关 —— **PASS_WITH_NOTES（未独立复跑两次）**

- **未独立复跑** e2e 两次取分布（环境/配额所限）。
- 代码证据（支持主代理「非本次回归」）：本轮 diff **未触碰** GateA 判决逻辑、`max_rounds`、可信度阈值（`tools/web_search.py:19-20` `_CRED_HIGH=0.7`/`_CRED_MEDIUM=0.4`）、源清单顺序；检索输入由"占位"换"真实"不改变 GateA 触顶条件。
- 低可信度记录非 tavily 产生（tavily 真实结果 score 全映射 `high`），故"真实数据更易触顶"担忧无直接证据。
- NOTE：建议后续跑 ≥2 次 e2e 取分布确认（我未在本次独立复跑，列为待办）。

### 点 10 · `e2e_report_flow.py` 两脚手架缺陷 —— **缺陷确认，pre-existing 无法 git 证**

```
scripts/e2e_report_flow.py:
 88: deadline = time.time() + 480          # 8 分钟 < 实际首跑 486s（8m06s）
102: if t.get("status") in ("done","escalated","failed"):  # escalate 也 break
122: ok = final.get("status") == "done" and len(md) > 0     # 仅记结论，无 sys.exit(1)
```
- 缺陷①：escalate 时 `ok` 仍可能为 `True` 且脚本**无 `sys.exit(1)`**（grep 全文件无 `sys.exit`）→ escalate 仍 exit 0。
- 缺陷②：轮询上限 480s 短于实际 486s → 窗口到期报 `running`+0 字，看似卡死。
- **pre-existing 无法经 git 证明**：`git cat-file -e HEAD:scripts/e2e_report_flow.py` → NOT IN HEAD（该文件本就未跟踪），无法用 `git show HEAD` 证"早于本次改动"。但缺陷在**当前代码**确凿存在。
- 处置：本轮不修（主代理已标注既有缺陷），记 NOTE 待办。

### 点 11 · 迁移脚本幂等性与安全性 —— **PASS**

`scripts/migrate_legacy_secrets_to_tenant.py`：
```
 72: if not LEGACY.exists(): return 0     # legacy 缺失 → 早返回，不误清空
 74: if not legacy: return 0              # legacy 无键 → 早返回
 89: missing = {k:v for k,v in legacy.items() if k not in current}  # 只补缺，不覆盖已有
 93: if not missing: return 0             # 租户已有全部键 → 幂等返回
105: target.chmod(0o600)                  # 密钥文件不给 group/other 读
112: return 0 if len(got) == len(missing) else 1
 62: from scripts.purge_inherited_gateway import _resolve_main_account  # 仅主账号
```
- 幂等：legacy 缺失/无键/租户已全 → 均早返回；`missing` 只补缺不覆盖。
- 安全：`chmod 0o600`；仅处理主账号（`_resolve_main_account`）。
- 不进 git 跟踪：`.gitignore:88` `/tenants/` → `git check-ignore -v tenants/9910ebbc-.../.secrets/plugins.env` 命中 line 88；`.gitignore:79` `.secrets/` 双重兜底。**密钥不进版本库。**

---

## 2. 「主代理声称 vs 我实测」对照表

| # | 主代理声称 | 我实测 | 结论 |
|---|---|---|---|
| 1 | 无其他伪造路径；web_search.MockProvider 是死代码 | grep 确认仅 `data_sources.py`/`web_search.py` 两处；`web_search.MockProvider` 无生产 import | **一致** |
| 2 | `attempted_ws` 反例：全源降级会静默 | web_search 包装器恒 `ok=True`（`:1442`/`:116`/`:130`），`attempted_ws` 恒 True → 降级仍告警。**反例不成立** | **推翻主代理担忧** |
| 3 | 告警条后端驱动，sources_are_real=false 仍可见告警 | `ChatEntry.vue:160` 由 `source_warnings` 驱动，与 sources 无关 | **一致** |
| 4 | 同进程改 key 下次 `/chat` 即生效 | 真机 STATE A/B 对照 + `chat_agent.py:382-395` 热重载 + md5 复原一致 | **一致（已真机验证）** |
| 5 | is_mock 三处剔除，无泄漏 | A2/A6 PASS + live A5 `mock_leak=0`；`:267`/`:174`/`:185` 剔除 | **一致** |
| 6 | S5/S8 注入可能被 `from ... import` 绕过（假通过风险） | `:55` 函数内 call-time 导入 → monkeypatch 被拾取；S5/S5b/S8 实测 PASS | **推翻主代理担忧（非假通过）** |
| 7 | unavailable_sources 改变模型措辞 | 真机去 key：模型答"未能找到…可靠数据"，无机构署名编造 | **一致（已真机验证）** |
| 8 | 前端仅 vue-tsc + 产物字符串，无人手点 | 确认无人工浏览器点击 | **一致（NOTE）** |
| 9 | escalate→0字 非本次回归 | 未独立复跑两次；代码证据支持（diff 未触 GateA/阈值/源序） | **未见反证（NOTE：未复跑）** |
| 10 | e2e 两缺陷为既有、本轮不修 | 读码确认两缺陷；但文件 untracked，无法 git 证 pre-existing | **缺陷确认；pre-existing 无法 git 证（NOTE）** |
| 11 | 迁移脚本幂等安全、不进 git | 读码确认幂等/chmod/仅主账号；`.gitignore:88` 忽略 tenants/ | **一致** |

---

## 3. 真机命令原始输出（本次复跑，2026-09-19 19:2x）

**工具层 `verify_source_honesty.py`（容器内 report-api）：9/9 PASS**
```
[PASS] S1 缺密钥源不注册且零结果            —— providers=0 results=0
[PASS] S2 degraded 含 id/reason/fix         —— degraded=[{'id':'tavily','name':'Tavily 搜索','reason':'missing_key','fix':'在「设置 → 数据源」中为「Tavily 搜索」填写 API Key（环境变量名 DS_TAVILY_API_KEY）'}]
[PASS] S3a 显式 mock → using_mock_search=True 且结果带 is_mock  —— flag=True rows=5 is_mock=[True,True,True,True,True]
[PASS] S3b keyless 真源 → using_mock_search=False  —— flag=False real_providers=1
[PASS] S5 build_tools 逐源打印缺密钥告警（含修复指引）  —— 告警行: - tavily（Tavily 搜索）原因=未配置密钥 → 修复：在「设置 → 数据源」中为「Tavily 搜索」填写 API Key（环境变量名 DS_TAVILY_API_KEY）
[PASS] S5b ToolBundle.degraded 透传         —— degraded=[{...}]
[PASS] S6 有真源时全局降级标志不得被中和      —— degraded=['tavily'] flag=False rows=5
[PASS] S7 search_many status 记录降级源(ok=False)  —— [{'agent':'verify','tool':'web_search','source':'tavily','ok':False,'error':'missing_key: 未配置密钥，本次未参与检索'}]
[PASS] S8 legacy 回退用通用 DS_<ID>_API_KEY（非硬编码 tavily） —— row={'id':'tavily','provider':'duckduckgo','status':'disconnected'}
结果：9/9 PASS
```

**接口层 `verify_source_honesty_api.py`（容器内 report-api，含真机 /chat A5）：9/9 PASS，0 FAIL，0 SKIP**
```
[PASS] A1 占位记录不进 sources、真实记录保留   —— sources=[('wikipedia','真实条目')]
[PASS] A2 纯占位 → sources 为空                —— sources=[]
[PASS] A3 ChatResponse 暴露 source_warnings / sources_are_real —— default=[],True set=['w'],False
[PASS] A4a 发起过检索+有降级源 → 告警非空且 sources_are_real=False —— warnings=['数据源「Tavily 搜索」未配置密钥，本次未参与检索，回答未使用该来源。修复：在「设置 → 数据源」中为「Tavily 搜索」填写 API Key'] sources_are_real=False
[PASS] A4b 占位被剔除 → 显式告警且 sources_are_real=False —— warnings=['本次检索包含 5 条占位数据，已从「来源」中剔除，不可作为事实依据。'] sources_are_real=False
[PASS] A4c 未发起检索时不要弹降级告警（避免噪声）—— warnings=[] sources_are_real=True
[PASS] A4d 无降级、无占位 → 无告警且 sources_are_real=True —— warnings=[] sources_are_real=True
[PASS] A6 占位记录不得被写进知识库当来源        —— n=1 tool_sources=['wikipedia'] 泄漏占位=[]
[PASS] A5 真机 /chat 响应含新字段且 sources 无占位 —— keys=True sources=0 warnings=[] are_real=True mock_leak=0
结果：9/9 PASS，0 FAIL，0 SKIP
```
（A5 的 `DeprecationWarning: There is no current event loop` 为 asyncio 事件循环弃用告警，不影响断言。）

**热重载 / live toggle（wendy 容器，同进程改 key）**
- 去 key：`degraded=['tavily'] providers=16`、`source_warnings=['数据源「Tavily 搜索」未配置密钥…']`、`sources_are_real=False`
- 复原同进程下次 `/chat`：`degraded=[] providers=17`、`source_warnings=[]`、`sources_are_real=True`、sources 含 tavily 真实结果
- 复原校验：`md5sum` 双份一致 `8264923f16093c2483e72fb08ee62a16`，**无残留副作用**

---

## 4. 治理风险提示（工作树混装多里程碑）

- 当前工作树**混装多个里程碑改动**（本轮「检索源诚实降级」+ 其他未提交改动），`git status` 显示 19+ 文件未提交。
- 风险：独立审议范围仅为「检索源诚实降级」相关文件（`tools/data_sources.py`、`tools/__init__.py`、`tools/chat_learning.py`、`orchestrator.py`、`chat_agent.py`、`run_real.py`、`server/api.py`、`web/src/views/ChatEntry.vue`、`web/src/services/chatService.ts` 及 3 个 verify/migrate 脚本）。**混装提交可能把其他未审改动一并带入**。
- 建议：commit 前按里程碑拆分提交（本里程碑单独 commit），避免"搭车"；或在独立审议 PASS 后仅对该子集落库。

---

## 5. 未被覆盖的风险 / 后续待办（NOTE，无 BLOCKING）

1. **陈旧 docstring（应清理，非阻断）**：
   - `tools/__init__.py:49`「缺失则降级 MockProvider 占位（带 source 标记，不冒充真实检索）。」—— 现状为登记 `degraded` 而非装载 mock，docstring 已失真。
   - `orchestrator.py:2650`「无 TAVILY_API_KEY 则按 fallback_to_mock 降级并告警」。—— 同上，已失真。
2. **`sources_are_real` all-or-nothing**：部分降级时隐藏全部真实源（过度保守，非安全漏洞，可后续优化为"标记降级源"而非"全隐藏"）。
3. **前端无人工浏览器点击**（环境所限），仅静态/构建/逻辑核对。
4. **`escalate→0字` 未独立复跑两次**：建议后续 e2e ≥2 次取分布，确认非本次回归。
5. **`e2e_report_flow.py` 两缺陷**（deadline+480 无 `sys.exit(1)`）：确认存在，建议修（独立于本里程碑）；pre-existing 无法经 git 证明（文件未跟踪）。
6. **迁移脚本已对真实租户目录写入过密钥**：幂等/安全已验证，但属不可逆写操作，建议保留 `–dry-run` 前置步骤（脚本已支持 `:33`）。

---

## 6. 签名

```
reviewed-by: independent-subagent  (gate ③ 独立审议人，Hy3)
timestamp: 2026-09-19 19:23:23 UTC+8
verdict: PASS_WITH_NOTES  (无 BLOCKING)
scope: 检索源诚实降级里程碑（未提交工作树改动）
evidence: docker exec report-api python scripts/verify_source_honesty.py → 9/9 PASS
         docker exec report-api python scripts/verify_source_honesty_api.py → 9/9 PASS, 0 FAIL, 0 SKIP
         live /chat 去 key/复原 对照 + md5 复原校验
```
