# VERIFICATION · 检索源诚实降级（gate ② 自审）

> 里程碑：`DESIGN_source_honesty.md` 实现落地 · 2026-09-19
> 自查人：主代理（**不得自签 gate ③**，独立审议见 `REVIEW_source_honesty.md`）
> 判据来源：`DESIGN_source_honesty.md` §5 的 S1–S9 断言

---

## 0. 一句话

「缺密钥的数据源静默伪造检索结果」这条**假配置**已从检索层、日志层、接口层、前端层四处同时堵死：
**S1–S8 共 17 条断言全 PASS（含真机 HTTP）**，三个既有回归套件（隔离 13/13、中断/编辑 9/9、单测 57+250）不回归；
引擎全链路终态见 §R2-9（**未依赖 exit code，逐条看终态**）。

---

## 1. 门禁 ① 编译 / 构建

```
python -m py_compile tools/data_sources.py tools/__init__.py orchestrator.py run_real.py \
                      chat_agent.py server/api.py \
                      scripts/verify_source_honesty.py scripts/verify_source_honesty_api.py
→ COMPILE_OK（exit 0）

docker compose build api web   → BUILD_EXIT=0
docker compose up -d api web   → UP_EXIT=0，api/web 均 Started
web 镜像内执行 `vue-tsc -b && vite build` → 无 `error TS`，`✓ built in 12.14s`
```

**产物级证据**（不是只看「构建成功」）：新 UI 字符串确实进了 web 镜像的 JS bundle：

```
docker exec report-web grep -rl '本次检索数据不完整' /usr/share/nginx/html/assets/ → index-ewgqgvAx.js
docker exec report-web grep -rl '本次无可核验来源'  /usr/share/nginx/html/assets/ → index-ewgqgvAx.js
```

## 2. 门禁 ②-A · 工具层断言（`scripts/verify_source_honesty.py`）→ **9/9 PASS**

```
[PASS] S1 缺密钥源不注册且零结果        providers=0 results=0
[PASS] S2 degraded 含 id/reason/fix     reason=missing_key，fix 含环境变量名与操作路径
[PASS] S3a 显式 mock → using_mock_search=True 且结果带 is_mock   flag=True rows=5
[PASS] S3b keyless 真源 → using_mock_search=False               flag=False real_providers=1
[PASS] S5 build_tools 逐源打印缺密钥告警（含修复指引）
       - tavily（Tavily 搜索）原因=未配置密钥 → 修复：在「设置 → 数据源」中为「Tavily 搜索」填写 API Key
[PASS] S5b ToolBundle.degraded 透传
[PASS] S6 有真源时全局降级标志不得被中和   degraded=['tavily'] flag=False
[PASS] S7 search_many status 记录降级源(ok=False)
[PASS] S8 legacy 回退用通用 DS_<ID>_API_KEY（非硬编码 tavily）
       provider=duckduckgo → status=disconnected（对抗条件：secrets 里**只有** DS_TAVILY_API_KEY）
结果：9/9 PASS
```

**S6 是本次修复的核心**：`using_mock_search` 此前写成 `not has_real`，被 16 个 keyless 真源中和，
导致 `[WARN] 检索已降级` 分支**一次都没执行过**。S6 用「tavily(无密钥) + wikipedia(真源)」这一组合
把「标志必须仍然指向真问题」钉死。

**S8 的对抗性**：旧代码查 `DS_TAVILY_API_KEY`，在「只有 tavily key、provider 却是 duckduckgo」时会
**误报 connected**。这是可稳定复现的错报，不是理论风险。

## 3. 门禁 ②-B · 接口/对话层断言（`scripts/verify_source_honesty_api.py`）→ **8/8 PASS**

```
[PASS] A1 占位记录不进 sources、真实记录保留
[PASS] A2 纯占位 → sources 为空（前端不会渲染「来源（N）」）
[PASS] A3 ChatResponse 暴露 source_warnings / sources_are_real（默认 [], True）
[PASS] A4a 发起过检索 + 有降级源 → 告警非空且 sources_are_real=False
[PASS] A4b 占位被剔除 → 显式告警且 sources_are_real=False
[PASS] A4c 未发起检索时不要弹降级告警（避免噪声）
[PASS] A4d 无降级、无占位 → 无告警且 sources_are_real=True
[PASS] A5 真机 /chat 响应含新字段且 sources 无占位
       keys=True sources=0 warnings=[] are_real=True mock_leak=0
结果：8/8 PASS，0 FAIL，0 SKIP
```

**A5 抓到过一个真 bug（如实记录）**：首跑报
`HTTP 500 CHAT_AGENT_FAILED: 'ChatAgent' object has no attribute 'bundle'` ——
我把新代码写在 `self.bundle` 上，而该类真实访问器是 `self._get_bundle()`（`chat_agent.py:382`，带按配置
mtime 的热重载语义）。**单元级断言全绿、只有真机 HTTP 才暴露**。修正后复跑 8/8。
这正是「不能只跑单测」的又一个实例。

## 4. 门禁 ②-C · 回归

| 套件 | 结果 | 说明 |
|---|---|---|
| `verify_subaccount_isolation.py` | **13/13 PASS**（最终镜像复跑） | 含 B0 探针状态归零、B1 子账号 /models 不泄漏、B3 无 token 401 |
| `verify_chat_interrupt_edit.py` | **9/9 PASS**（最终镜像复跑） | A0 中间件 / A1·A2 直连断开 / B1 反代断开 / A3 落库 / A4 截断+幂等 / A5 越权 / A6 无归属 |
| `pytest tests/`（宿主，排除 3 个缺 asyncpg 的采集错误） | **250 passed, 7 failed** | **7 个失败全部是宿主缺 `reportlab`/`python-docx`/`python-pptx`**（`RendererUnavailable: 缺 reportlab`），属环境依赖缺失，与本次 diff 无关（本次未触碰 `tools/doc_render.py`） |
| `pytest tests/test_tools.py` | **57 passed** | 含 `using_mock_search is False` 等既有断言，未被本次语义变更破坏 |

**最终镜像复跑声明**：上面 S 9/9、A 9/9、隔离 13/13、中断/编辑 9/9 四套，
均在 `docker compose build api && up -d api` **重建后的镜像**上重跑通过（不是 `docker cp` 热替换的临时态）。

**诚实声明**：容器内**装不了 pytest**（`No module named pytest`），所以「容器内跑全套单测」这条
证据**不成立**，以上单测是在宿主托管 Python 上跑的；宿主缺 asyncpg/reportlab，故 3 个测试文件
未能采集、7 个渲染用例失败 —— 都不是本次改动引入，但也**没有在容器内被证明为通过**。

## 5. 门禁 ②-D · 引擎全链路（`e2e_report_flow.py wendy`）

见 §R2-9。**必须看终态，不能看 exit code** —— 该脚本 escalate 时 exit 仍为 0（既有缺陷，本轮再次复现）。

---

## R2-9 引擎全链路：终态如实记录

### 第一次 run（本次改动后）

- 脚本自报：`E2E_EXIT=0` —— **无效判据**，脚本在 508s 的轮询窗口到期时报 `终态: running`、`report_markdown 0 字`。
- 实际终态（读 `output.json` / `routing_state`，**不是脚本口径**）：

```
routing_state.status = "escalated"
round = 2, max_rounds = 2, last_gate = "GateA"
gate_output.decision = "rework"   eval_score=0.6
gate_output.reason   = 检索记录中存在来源可信度较低（credibility=low）的记录，且部分记录与主题相关性不强
report_markdown      = 0 字    report_path = None
tool_status          = 13 条全部 ok=True（其中 11 次真实 web_search，**无一次工具失败**）
检索记录             = 21 条
```

**定性（重要，不许含糊）**：

1. **不是崩溃、不是工具故障**：13 条工具调用全 ok，检索记录 21 条。
2. **不是本次 diff 引入的逻辑回归**：本次 diff 未触碰 GateA 判决、`max_rounds`、可信度阈值
   （`tools/web_search.py:19-20` 的 `_CRED_HIGH=0.7 / _CRED_MEDIUM=0.4`）或源清单顺序。
3. **但本次改动确实改变了检索输入**：此前 tavily 是**占位**（`score=0.6` → credibility 恒为 medium），
   现在 tavily 是**真实结果**（实测 score 0.956/0.949/0.930/0.921/0.819 → 全 `high`）。
   也就是说：**低可信度记录不是 tavily 产生的**，而 GateA 的「低可信度」批评在改动前后都存在
   （历史 run `4651fcec` 在 mock tavily 下同样两轮 rework 后 escalate）。
4. 结论：这是**既有脆弱点**（`max_rounds=2` 只容一次 rework，第二轮再不满意就直接 escalate →
   用户等 8 分钟后拿到 0 字），本次改动让它更容易被触发。**属产品口径决策，主代理不擅自放宽质量闸。**

### 第二次 run —— **跑通并产出真实报告**

```
[3] 轮询   [ 8.1s] status=running round=1
           [413.4s] status=done    round=2
[4] 终态: done      escalate_reason: None      report_markdown 长度: 5096
```

读 `output.json` 交叉核对（不信脚本口径）：

```
routing_state.status = done        round/max = 2/2
report_markdown 长度 = 5096        检索记录 = 13        工具调用 = 10（失败 2）
报告开头：# 2026年中国外卖行业深度研究报告 …
         2026年中国外卖行业已进入万亿规模市场阶段…[rec-1][rec-2][rec-6]
```

**关键结论**：
1. 引擎在本次改动后**能跑通**，报告 5096 字且**带 `[rec-N]` 引用标记**（有据可查，不是无源长文）。
2. 同一份代码、同一条 prompt，两次跑出 `escalated(0字)` / `done(5096字)` —— **波动是既有特性**，
   与 §R2-9 历史表（`4aa456ef` done / `317799d1` done / `4651fcec` escalated）一致。
3. 因此**不把第一次的 escalate 判为本次回归**；但**保留**「本次改动改变了检索输入分布（占位→真实）
   可能提高触顶概率」这一保留意见，交由独立审议复核（见 `REVIEW_source_honesty.md` 待办 9）。

### 新发现的测试脚手架缺陷（必须报，不修）

`e2e_report_flow.py` 有两个会**伪造绿灯**的缺陷：
① escalate 时 exit code 仍为 0；② 轮询上限（8 分钟）短于实际运行时长（首跑 486s≈8m06s），
窗口到期即报 `running` + 0 字，**看起来像卡死，实际是跑完了**。作为回归门禁用会漏报/误报。
→ 已列入 `REVIEW_source_honesty.md` 的补审待办与后续待办。

---

## R2-10 本次改动清单（供独立审议逐文件核）

| 文件 | 改动 |
|---|---|
| `tools/data_sources.py` | `build_search_tool` 删掉缺密钥时的静默 `MockProvider` 兜底，改为 `degraded` 登记；`has_explicit_mock` 取代 `not has_real`；`SearchTool.__init__` 新增 `degraded`；新增只读 `providers` 属性；结果对 mock 源打 `is_mock`；`search_many` 把降级源写进 `status(ok=False)`；文件头注释与实际行为对齐；legacy 回退改为通用 `DS_<ID>_API_KEY` |
| `tools/__init__.py` | `ToolBundle.degraded` 新字段；`build_tools` 透传；`[WARN]` 改为**逐源**打印（含 reason + 修复指引），显式 mock 另有一条独立告警 |
| `orchestrator.py` | `_run_fc_loop` 的 web_search 摘要新增 `unavailable_sources`（不再只在「零结果」时才说明）；控制台打印改为「已装载 N 源 / 显式 mock / 未参与列表」 |
| `run_real.py` | 同上控制台打印 |
| `chat_agent.py` | `_build_sources` 丢弃 `is_mock`；新增 `_source_warnings()`；`step()` 产出 `source_warnings`/`sources_are_real` 并新增降级告警日志；系统提示新增「检索数据的诚实边界（硬规则）」 |
| `server/api.py` | `ChatResponse` 新增 `source_warnings`/`sources_are_real` 并透传 |
| `web/src/views/ChatEntry.vue` | 警告 `el-alert` + `sources_are_real=false` 时不展示「来源（N）」而显示「本次无可核验来源」；`UIMessage` 类型与赋值；样式 |
| `web/src/services/chatService.ts` | `ChatResponse` 补两字段类型 |
| `scripts/verify_source_honesty.py` / `verify_source_honesty_api.py` | 新增（S1–S8 / A1–A5） |
| `DESIGN_source_honesty.md` | 新增（设计，含实现期精化说明） |
| `scripts/migrate_legacy_secrets_to_tenant.py` | 新增（存量密钥迁移，幂等，已执行） |

## R2-11 未由主代理验证的部分（独立审议请重点打）

1. **前端零浏览器验证**：新增的告警条与「本次无可核验来源」分支只有 `vue-tsc` + 产物字符串两条证据，
   **没有人手点过**；`sources_are_real=false` 与 `sources.length>0` 同时成立时的渲染分支尤其值得实测。
2. **`unavailable_sources` 是否真的让模型变诚实**：只验证了字段被注入 `summaries`，**没有验证模型的最终措辞**。
3. **删除静默 mock 的连带影响**：`chat_learn_write`（知识沉淀）此前可能吃到占位记录，现在 `sources` 里
   已无占位，但 `extra_search`（另一条通路）**未做同样过滤** —— 请核查后者是否仍会把占位写进知识库。
4. `tests/` 中 3 个文件（asyncpg）与 7 个渲染用例在宿主未跑通，属环境缺失，未复现性阻塞。
5. 引擎 `escalate → 0 字` 与 `e2e_report_flow.py` 的 exit-code/轮询缺陷**本轮不修**（前者是产品口径，后者待办）。
