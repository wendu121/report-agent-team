<!-- reviewed-by: independent-subagent -->

# REVIEW_M11-2.md · 独立审议（M11-2 反思回写闭环 + Asset Center 面板 + 天气/500 修复）

- 被审提交：M11-2（尚未 commit；含未提交工作项，见 VERIFICATION_M11-2.md）
- 审议模型：独立子代理（证据驱动，动态复现优先于静态走查）
- **VERDICT: PASS** —— 前次 BLOCKING 的 "reflect" 节点未注册缺陷已修复，`g.compile()` 动态复现通过，不再抛 `ValueError`。其余 A–E 项与天气/500 修复（F 节）经复核对结论 PASS。

---

## 编译核验

- `python -m py_compile orchestrator.py tools/data_sources.py tools/_async_util.py tools/mcp_client.py chat_agent.py server/api.py` → **COMPILE_OK**（exit 0，本环境实测）。
- 注意：py_compile 仅查语法，抓不到 graph 接线缺口；故本次除 py_compile 外，额外对 `build_graph()` 做了**动态复现**（见 A.1 / RESOLVED）。

---

## 补审 CHECKLIST

### A. 终态图接线正确性（orchestrator.py）
- [x] **`build_graph` 中 `g.add_node("reflect", node_reflect)` 已注册（RESOLVED）**。位于 `orchestrator.py:1963`，紧跟 `g.add_node("kb_write", node_kb_write)`（:1962）之后、`add_edge` 之前。前次 ISSUES #1 阻塞项已闭环。
- [x] 边 `escalate→reflect`（:1979）、`kb_write→reflect`（:1980）、`reflect→END`（:1981）已加；原 `escalate→END` / `kb_write→END` 直终态已移除（diff 可见）。
- [x] 条件边 `END: "kb_write"`（:1975 段）末闸终态先落库→reflect→END 逻辑链路可达。
- [x] `node_reflect`（:1915-1928）返回 `{}` 不改 state；整段 try/except 降级 no-op，不阻断终态。
- [x] **全图节点/边交叉校验（静态）**：`build_graph` 内 `add_node` 字面节点 = {router, escalate, kb_retrieve, kb_write, reflect}（+ 循环中 `role.lower()`/`gate_<role>` 动态注册）；所有 `add_edge`/`add_conditional_edges` 引用的目标节点（kb_retrieve/router/escalate/kb_write/reflect/各 role/gate_<role>/END）均**已注册**，**无其它未注册节点**。

### B. 确定性生成器（tools/reflection.py · 新文件）
- [x] `generate_reflection`（:80-161）纯函数、零 LLM 调用；证据取自真实 state。
- [x] clean state（未 escalate + ≥3 条素材）不触发任何规则 → 返回 `[]`，零误报。
- [x] id 幂等 `rfl-<date>-<seq>`（:69-70）；`append_reflections`（:167-188）按 seen-id 去重。
- [x] 缺"逐源失败遥测"前不产出 `plugin_disable`；v1 全为 op=none，机械 `set_enabled` 路径在 admin 保留白名单（诚实不假闭环）。

### C. accept 白名单闸门（server/admin.py）
- [x] `POST /admin/reflections/{rid}/accept`：op=none → 仅 `set_reflection_status`；op=set_enabled → `_set_plugin_enabled` 改 yaml（ruamel round-trip + 原子写 + 审计）；非法 op/非法 target.key → 400。
- [x] `reject` 仅置 status，不删不改。
- [x] 鉴权 `_require_admin(x_admin_token)`；CORS 沿用 M8-1。

### D. 前端 Asset Center（A1 合并面板）
- [x] `AssetCenter.vue`（新文件）双 Tab；路由 `/settings/asset-center` 已注册；`DefaultLayout.vue` 下拉新增入口。
- [x] 经 axios `api` 调 `/admin/reflections` + `/admin/lessons`。

### E. 诚实边界 / 治理
- [x] `config/reflections.proposed.yaml` 已加入 `.gitignore`（运行时生成不入库）。
- [x] py_compile 通过（含 reflection/admin）。
- [x] **`node_reflect` 集成（build_graph）本环境动态复现通过**——前次被 #1 阻塞项已消除，`g.compile()` 不再抛错（见 RESOLVED）。
- [x] 无过度工程：未引入"进化算法"；与 EvoFlow 真实能力对齐（记忆+反思+人工门禁）。

### F. Weather-fix + 500-fix（本会话新增）
- [x] **F.1** `_http_get_json_retry`（data_sources.py:123-142）参数 `attempts: int = 3, backoff: float = 0.6`，**仅 `except ToolError`**（:136，不掩逻辑错误），指数退避 `time.sleep(wait)`（:140）有界；`OpenMeteoProvider.search` 对 **geocoding**（:926）与 **forecast**（:946）两步均调用它。重试有界（3 次），不无限。
- [x] **F.2** `search_many`（:1220）三失败分支加 `logger.warning/info`（:1233/1238/1245），含源 id + query，`docker compose logs api` 可查天气缺失原因；`dispatch_tool`（`orchestrator.py:1181+`）`_, entries = bundle.web_search.search_many(...)` 状态有意丢弃、由日志补足可见性，可接受。
- [x] **F.3** `_async_util.run_async`（:35、:50-52）两处 `except asyncio.CancelledError` 均转 `RuntimeError`（:41、:51），可被调用方 `except Exception` 捕获，杜绝 /chat 500 逃逸。
- [x] **F.4** `mcp_client.py:15 import asyncio`；`:57`、`:69` 守卫扩为 `(Exception, asyncio.CancelledError)`，仅包裹网络/可达性调用、记 warning 并降级 `[]` / `ok=False`，未静默吞掉（非阻塞观察见 #2）。
- [x] **F.5** `docker-compose.yml` `CHAT_PROXY` 默认 `${CHAT_PROXY:-}`（:62/63/65/66，空=直连安全默认）；`NO_PROXY=localhost,127.0.0.1,::1,postgres,redis,api,web,host.docker.internal`（:64）覆盖内部服务防自环（非阻塞观察见 #3）。
- [x] **F.6** 无密钥硬编码：data_sources.py 仅 `load_secrets()`（:1124）从 `.secrets/plugins.env` + 进程 env 读取，无字面 `sk-`/`api_key="..."`；plugins.yaml 两源 `auth_type: none`，docker-compose 仅引用 `${CHAT_PROXY}` 环境变量。

---

## RESOLVED

### reflect 节点已注册（前次 BLOCKING #1）
- **位置**：`orchestrator.py:1963`
  ```python
  g.add_node("reflect", node_reflect)  # M11-2：反思回写节点（落库后生成建议，降级 no-op）；缺则 g.compile() 崩
  ```
- **动态复现（本沙箱，langgraph 可 import）**：
  ```
  >>> import orchestrator
  >>> g = orchestrator.build_graph(llm=None, models={})
  >>> type(g).__name__
  'CompiledStateGraph'   # g.compile() 不再抛 ValueError
  ```
  实测 `build_graph(llm=None, models={})` 返回 `CompiledStateGraph`，证明 `reflect` 已注册、`g.compile()` 不再抛 `ValueError`。前次复现的 `ValueError: Found edge starting at unknown node 'reflect'` 已消除。
- **影响闭环**：报告图可正常编译，研报引擎初始化不再崩溃。

---

## 非阻塞观察（不阻塞 PASS）

### #2 — `_async_util.run_async` worker 线程 `except BaseException`（:52）
- 该兜底会捕获所有 `BaseException`（不止 `CancelledError`），理论上可能掩盖某条 async 任务的真实编码缺陷。当前为修 500 逃逸的有意取舍且已注释，风险可接受；建议后续收窄为仅 `except (Exception, asyncio.CancelledError)`。非阻塞。

### #3 — `docker-compose.yml` 的 `NO_PROXY` 未含 new-api LLM 网关主机名
- 启用 `CHAT_PROXY` 后，若 LLM 网关经未在 `NO_PROXY` 内的主机名访问，流量会被导入宿主代理而可能失败。compose 注释（:60）已提示，且默认代理为空（安全）。部署启用代理时务必确认网关主机名在 `NO_PROXY`。非阻塞。

---

## 结论

**VERDICT: PASS** —— 前次 BLOCKING 的 "reflect" 节点未注册缺陷（`orchestrator.py:1963` 已补 `g.add_node("reflect", node_reflect)`）经**动态复现**确认 `g.compile()` 不再抛 `ValueError`；py_compile 全绿；A–E 复核通过；天气/500 修复（F 节）全部 PASS；#2/#3 为非阻塞观察，不影响合并。未提交（遵守铁律：独立审议闭环后再由主代理提交）。
