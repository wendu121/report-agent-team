# VERIFICATION · 模型映射「可由 UI 控制 + 解析宽容且可见」

> 里程碑：`model-mapping-uf`（档 1 + 档 2）
> 设计 SoT：`DESIGN_model_mapping_uf.md`
> 自审人：主代理（**非**独立审议；gate ③ 见 `REVIEW_model_mapping_uf.md`）
> 日期：2026-09-19
> 范围声明：**系统级**（UI + 引擎 + 子账号模板），主账号须同步适配且行为不得回退。

---

## 1. 本轮改动清单（全部未提交，本地）

| # | 文件 | 改动 | 行号（改后） |
|---|---|---|---|
| 1 | `orchestrator.py` | 新增模块级 `build_bare_index()` | 437 |
| 2 | `orchestrator.py` | 新增模块级 `resolve_model_ref()`（单一事实源） | 447 |
| 3 | `orchestrator.py` | `NewApiLLMClient.__init__` 增 `self._bare_index` | 543 |
| 4 | `orchestrator.py` | `_resolve()` 收敛为委托 `resolve_model_ref` | 555–568 |
| 5 | `server/admin.py` | 新增 `POST /api/v1/admin/models/preview` | 431 |
| 6 | `server/tenancy.py` | `NEUTRAL_MODEL_MAPPING_YAML` 填写指引修正 | 84–102 |
| 7 | `web/src/views/settings/Models.vue` | 全量重写：base/model → `el-select`；新增「实际端点」列 | 全文 |
| 8 | `tenants/af28b675-…/config/model_mapping.yaml` | 配置修正（走真实 `PUT /admin/models`，非手改文件） | — |
| 9 | `scripts/verify_gate_real.py` / `scripts/repro_engine_crash.py` | 新增验证脚本 | — |
| 10 | `DESIGN_model_mapping_uf.md` | 新增设计文档 | — |

**不属于本轮**（工作树中其他 worker 的未提交改动）：`chat_agent.py`、`tools/*`、`server/api.py`、`server/engine_client.py`、`web/src/services/*`、`web/src/views/ChatEntry.vue` 等。

---

## 2. 验收项逐条结论

| # | 验收项 | 结论 | 证据 |
|---|---|---|---|
| A1 | 主账号页面 `model` 下拉含 `LongCat-2.0`/`llama-3.3-70b`，`base` 不丢值 | 🟡 **数据面 PASS / 界面未目视** | `/models` 实测主账号 122 项含 `LongCat-2.0`(concrete) 与 `llama-3.3-70b`(gateway)；`baseOptions` = `endpoint_id ∪ 现有值`（`Models.vue` computed），故 `custom`/`cloudflare` 被保留 |
| A2 | 子账号下拉只含本账号接口（不含主账号网关模型） | 🟡 **数据面 PASS / 界面未目视** | `/models` 实测子账号 3 项（`auto` + 2 个 concrete），`gateway_ok=false`；主账号模型不在其中 |
| A3 | 子账号选 `deepseek-v4-flash-vision` 写入的是接口 id | ✅ PASS | `preview` 实测该账号 7 条全部 `matched=True` 且为 `custom:…` 形式（下 §3.2） |
| A4 | 主账号 `llama-3.3-70b` 显示「未命中 → 回落」而非绿勾 | ✅ PASS（后端） | `preview` 实测 `gates.GateA/B/C` 均 `matched=False, endpoint_id=new-api`；UI 按 `pvTagType` 渲染 `warning` + `pvNote` 文案「未命中接口 → 回落默认端点」 |
| A5 | 真实任务三道闸**真正执行**（非降级） | 🟡 **部分成立，判据已更正** | 任务 `793c258f` 三道闸为**实质评审**（0.85/0.9/0.9，reason 是真实评审文本）；但复跑任务 `d483d809` 三道闸**又降级**（reason=「审核 LLM 多次返回不可解析内容」）。**关键更正：`eval_score` 非空 ≠ 闸真跑** —— 降级兜底也会写 `0.85`（见 §3.5） |
| A6 | 主账号解析逐条不回退 | ✅ PASS | 下 §3.3 逐条对照 |
| A7 | `preview` 响应不含密钥 | ✅ PASS | 下 §3.2 全文扫描 |

---

## 3. 关键证据（全部实测）

### 3.1 A5 · 修复前后对照（同一子账号、同一引擎产物字段）

| | 修复前（任务 `be190eeb`） | 修复后（任务 `793c258f`） |
|---|---|---|
| GateA `eval_score` | `None` | **0.85** |
| GateB `eval_score` | `None` | **0.90** |
| GateC `eval_score` | `None` | **0.90** |
| 三道闸 `reason` | 「审核 LLM 不可用（降级放行）：代码硬校验已通过…」 | 「检索记录覆盖用户任务全部子问题…来源可信度均为 high」等**实质评审** |
| `routing_state.status` | `done` | `done` |
| `escalate_reason` | `None` | `None` |
| 报告字数 | 1585 | 1346 |

> 结论：**修改前「标 done 但三道质量闸一道未真审」的事实成立**；修改后闸真实调用成功。

### 3.2 A7 · `POST /api/v1/admin/models/preview` 实测

两个账号均 `HTTP 200`；响应体长度 1605 / 1846 字节；对 `api_key` / `key` / `token` / `secret` / `Authorization` 做全文扫描 → **命中数 0**。

子账号返回（节选）：
```
roles.Researcher  custom:agent:agnes-3.0-flash              -> custom:agent      matched=True
gates.GateA       custom:discovery:deepseek-v4-flash-vision -> custom:discovery  matched=True
eval              custom:discovery:deepseek-v4-flash-vision -> custom:discovery  matched=True
```
主账号返回（节选）：
```
roles.Researcher  LongCat-2.0      -> new-api  matched=True
gates.GateA       llama-3.3-70b    -> new-api  matched=False   ← 回落，UI 显示 warning
```

### 3.3 A6 · 主账号零回退逐条对照

| 主账号 model | 改动前解析 | 改动后解析 | 是否回退 |
|---|---|---|---|
| `LongCat-2.0` | 命中接口 → new-api | 命中接口 → new-api | 否 |
| `llama-3.3-70b` | 未命中 → 回落 new-api | 未命中 → 回落 new-api（**新增 WARNING 日志**） | 否 |
| `glm-4.7` | 未命中 → 回落 new-api | 未命中 → 回落 new-api（新增 WARNING） | 否 |
| `auto-chat` | 命中 `auto-chat` 接口 → new-api | 同左 | 否 |

### 3.4 档 2 消歧行为实测（子账号）

```
agnes-3.0-flash            -> custom:agent      matched=True  消歧=True    ← 裸名自动归位
deepseek-v4-flash-vision   -> custom:discovery  matched=True  消歧=True    ← 正是本次事故的模型
custom:agent:agnes-3.0-flash -> custom:agent    matched=True  消歧=False   ← 接口 id 写法
LongCat-2.0 / glm-4.7      -> custom:agent      matched=False 消歧=False   ← 留 WARNING + 可用接口清单
```

### 3.5 ⚠️ 新发现：`eval_score` 非空 ≠ 闸真跑（降级兜底会写 0.85）

复跑任务 `d483d809-3f14-464e-a2cb-f3ccff240e16`（同一子账号、同一配置、新镜像）暴露了原判据的漏洞：

| 闸 | `eval_score` | `reason` | `engine_events` | 真相 |
|---|---|---|---|---|
| GateA | `null` | 「审核 LLM 不可用（降级放行）…」 | `gate_llm_unavailable_degraded_advance` | **未真审** |
| GateB | **`0.85`** | 同上 | 同上 | **未真审（兜底分骗人）** |
| GateC | **`0.85`** | 同上 | 同上 | **未真审（兜底分骗人）** |

同任务还有两条并联证据，说明**角色侧模型同样结构化输出不稳**：
- `researcher_records_synthesized`：「模型最终 JSON 缺失/无效/空 retrieval_records，由引擎从真实检索结果**确定性合成**（count=42）」
- `writer_citation_regenerated`：「终稿引用章节缺失或含非检索来源的 url，已由 doc_export **确定性重建**」

**结论（诚实边界）**：
1. 本轮修复解决的是「**请求被打到错误厂商**」（503 `model_not_found`）—— 这条确实修好了。
2. **未**解决「**审核模型结构化输出不稳定 → 闸降级放行**」—— 这是**另一条独立缺陷**，
   且它的隐蔽形态是「**降级时写一个看起来正常的 0.85 兜底分**」，比 `null` 更容易骗过验收。
3. 因此**判定闸是否真跑的唯一可靠依据是 `reason` 文本 / `engine_events`，不是 `eval_score`**。
4. 报告字数暴涨（1585 → 5840）与检索记录 42 条，是引擎**确定性合成**兜底的结果，
   **不能**直接当作"模型质量提升"的证据。

---

## 4. 构建与测试

| 项 | 命令 | 结果 |
|---|---|---|
| Python 语法 | `ast.parse` × 4 文件（orchestrator / admin / tenancy / scripts） | ✅ OK |
| 前端类型检查 | `web: vue-tsc --noEmit` | ✅ **exit=0**（无输出=无错） |
| 后端单测（宿主） | `pytest tests/ -q --ignore=<3 个缺 asyncpg 的文件>` | **250 passed, 7 failed** |
| 镜像 | `docker compose build api && build web && up -d api web` | ✅ 双镜像重建，四服务健康 |
| 容器内代码核验 | `inspect.getsource(_resolve)` 含 `resolve_model_ref(`；`admin.router.routes` 含 `preview` | ✅ |
| 前端产物 | `report-web` 内 `assets/index-BcWZ6SK-.js`（14:22 新构建） | ✅ |

**7 个失败全部为 `tests/test_doc_render.py`，原因清一色是宿主缺 `docx`/`pptx`/`reportlab`**（`ModuleNotFoundError`），
与本轮改动无任何交集 —— 判定为**环境缺包，非回归**。

---

## 5. 诚实边界（未完成 / 存疑，不得含糊）

1. **gate ③ 独立审议被 429 阻塞**：派出的独立审议子代理因 API 配额耗尽（重置时间 2026-09-20 00:11:04 UTC+8）**未能启动**。
   按项目铁律**严禁自审自签顶替** → `REVIEW_model_mapping_uf.md` 顶标 `BLOCKED`，附补审待办。
   **在独立审议 PASS 之前，本里程碑不得 commit。**
2. **界面未目视验收**：A1/A2/A4 的「页面实际渲染」未由我目视确认（未取得登录凭据做浏览器实测）。
   已完成的是**数据面**验证（`/models` 候选集、`preview` 解析结果、`vue-tsc` 通过）。建议 boss 打开
   `http://localhost:18080/settings/models` 目视一次（注意浏览器缓存需强刷，带 `?cb=<时间戳>`）。
3. **`vue-tsc` 通过 ≠ 运行时必对**：`modelGroups` 分组、`el-option-group` 渲染等属运行时行为，未做实机点击验证。
4. **本轮未覆盖的两个既有缺陷**（已记录，不在本轮范围）：
   - **偶发引擎崩溃**：2026-09-19 21:54 任务 `793c258f` 首次运行时引擎写出
     `{"error": "'list' object has no attribute 'get'"}` 后无产物（`status=None`）；
     用**同一份 input** 复跑则完全成功（`status=done`）。**不可稳定复现**，根因**未证实**。
     加重因素：`server/engine_client.py` 的 `wait()` 只读 output 文件，**丢弃子进程 stderr**，
     而 `engine_runner.py:119-120` 的 `traceback.print_exc()` 正是打在 stderr → **traceback 被吞**。
   - **KBWrite 节点降级**：引擎 stderr 出现 `KBWrite 节点降级: '>=' not supported between instances of 'str' and 'float'`
     —— 字符串与 float 比较，属类型缺陷（当前为降级、不致命）。
5. **闸降级对用户不可见（高风险，本轮未修）**：降级时会写 `eval_score=0.85` 兜底分，
   UI / 报告界面**无法区分**「真审 0.85」与「降级兜底 0.85」。
   这直接违反项目宪法「禁假配置 / 诚实边界」。证据见 §3.5。**建议列为下一个里程碑**。
6. **子账号模型结构化输出不稳**：`agnes-3.0-flash`（角色侧）与 `deepseek-v4-flash-vision`（闸侧）
   均出现「多次返回不可解析内容」，靠引擎确定性合成兜底才产出报告。
   属**模型能力 / 提示词**问题，**非配置问题**（配置已改为接口 id 且解析全部命中）。
7. **未改**：`GateReview.eval_score` 类型契约（另一条独立修复线）、任务持久化（`tasks` 表仍空转）、
   第三档「未命中即报错」（**明确不做**：会当场废掉主账号的 `llama-3.3-70b`/`glm-4.7`）。
8. **主账号 `model_mapping.yaml` 未改动**（保持裸名）。它目前工作正常，本轮只保证其**继续**正常工作。

---

## 6. 自审结论

- 档 1（UI controller 化 + 解析预检）与档 2（引擎宽容解析 + WARNING）**代码已完成并落盘**，双镜像已重建。
- 核心目标（子账号三道闸从「静默降级通行」变为「真实审核」）**已被真机产物证实**。
- 主账号**零回退**已逐条实测。
- **尚未闭环**：gate ③ 独立审议（429 阻塞）、界面目视验收。二者完成前不宣告里程碑完成、不 commit。
