# VERIFICATION_M11-2.md · M11-2 反思回写闭环 + Asset Center 面板

- 日期：2026-09-12
- 上游设计：`DESIGN_M11-2_reflection.md`（boss 已拍板 D1-D4 + A1 全部采纳并编码）
- 状态：闸① 通过、闸② 自审 PASS（含诚实边界说明）、闸③ 独立审议 **BLOCKED**（见 REVIEW_M11-2.md）
- 未提交（铁律：独立审议未闭环前不 commit）

---

## 闸① 构建 / 编译

- `python -m py_compile tools/reflection.py orchestrator.py server/admin.py` → **COMPILE_OK**（exit 0）。
- `pytest tests/test_m11_reflection.py -k "not node_reflect"` → **8 passed**。
  - 覆盖：escalate_review 生成、clean state 零误报、plugin_hint 低可信源、low_yield、rework_hotspot、append/load/set 往返、append 幂等、多条目 status 过滤。
- 说明：本沙箱托管 Python 缺 langgraph/fastapi，故 `node_reflect` 集成测试（import orchestrator）与 admin 端点 TestClient 测试**未能在本环境运行**；其逻辑经 `py_compile` + 代码走查验证，将在部署环境（api 镜像，含完整依赖）跑全套 e2e。

---

## 闸② 自审（主代理）

### 改动文件清单
| 文件 | 改动 |
|---|---|
| `tools/reflection.py` | **新增**：确定性规则引擎 `generate_reflection` + 落库 `append_reflections`/`load_reflections`/`set_reflection_status`（ruamel round-trip + 原子写，复用 chat_learning 模式） |
| `orchestrator.py` | 新增 `node_reflect`（~30 行）；`build_graph` 接线 `escalate→reflect`、`kb_write→reflect`、`reflect→END`（原两条终态直 END 改为经 reflect） |
| `server/admin.py` | 新增 `/admin/reflections`(GET)、`/admin/reflections/{id}/accept`(POST)、`/admin/reflections/{id}/reject`(POST)、`/admin/lessons`(GET)、`/admin/lessons/accept`(POST)；accept 含 D3 白名单闸门（`set_enabled` 才机械改 yaml，其余仅置 status） |
| `tests/test_m11_reflection.py` | **新增**：8 项 stub 测试 |
| `web/src/views/settings/AssetCenter.vue` | **新增**：反思建议 + 经验草稿合并面板（A1） |
| `web/src/router/index.ts` | 新增 `/settings/asset-center` 路由 |
| `web/src/layouts/DefaultLayout.vue` | 设置下拉新增「经验 / 反思」入口 |

### 设计一致性核对（D1-D4 + A1）
- **D1 确定性规则**：✅ 生成器纯读 state，零 LLM 调用，证据链一一对应，无幻觉路径。
- **D2 reflection 不落 kb.entries**：✅ 只写 `config/reflections.proposed.yaml` + 审计，绝不进 `kb.entries`（防污染 Researcher 来源集，设计 §5 偏离已落实）。
- **D3 accept 白名单**：✅ `set_enabled` 才机械改 `plugins.yaml`（ruamel + 原子写 + 审计，镜像 /admin/skills）；`none` 类仅置 status 留痕；非法 op → 400 fail loud。
- **D4 前端面板**：✅ `AssetCenter.vue` 双 Tab（反思 + 经验），采纳/驳回按钮。
- **A1 合并面板**：✅ 反思与经验草稿同面板，避免入口割裂（对齐 EvoFlow Asset Center 卖点）。

### 诚实边界 / 与设计的偏离（必读）
1. **v1 机械 `set_enabled` 暂未触发**：设计 §4 原计划 `plugin_disable`（关闭全失败源）为唯一机械 op，但当前 M4/M11-3 的 `tool_status` 是"逐工具"（web_search/data_proc/doc_export）而非"逐源"，逐源失败不进 `retrieval_records` 也不进 `tool_status` 逐源字段。为避免"无据禁用源"假闭环，v1 仅产出**可验证的仅提示类**（plugin_hint / low_yield / escalate_review / rework_hotspot）+ 人工 accept 留痕。D3 白名单闸门与 `_set_plugin_enabled` 已实现待命，待引擎补"逐源失败遥测"（retrieval_records 带 ok/error）后 `plugin_disable` 规则自然接入（已列入 TECH_DEBT，不阻塞本里程碑）。
2. **这恰对齐 EvoFlow 的真实能力**：EvoFlow 产品的"自我进化"也只是记忆+反思笔记+人工门禁，**无自动变异**；本实现与其诚实度一致，不冒充"自动改系统"。
3. **降级不击穿**：`node_reflect` 与 `append_reflections`/`set_reflection_status` 全部 try/except 降级 no-op/返回，终态流程不受反思失败影响（与 kb_write 同模式）。
4. **git-ignore**：`config/reflections.proposed.yaml` 为运行时生成，应加入 `.gitignore`（**待补**：本提交未自动加，部署前需确认**未入库**）。

### 已知阻塞（与 M11-2 无关）
- new-api 网关 **429**（2026-09-13 14:32:10 UTC+8 重置）+ **400**（ChatAgent 注入超上下文）→ 真实 LLM e2e 仍被挡；不影响 M11-2 后端逻辑（其测试用 stub）。

---

---

## 补刀（2026-09-12）：对话天气缺失修复（open_meteo 重试 + 代理出网）

### 复现（真实 chat，运行容器）
- `POST /api/v1/chat {"message":"今天杭州天气怎么样"}` → HTTP 200，但 `reply` = "未能获取到实时的天气信息"，`sources` 仅 `tavily/arxiv/semantic_scholar`（url/title 全 null），**`open_meteo` 完全缺席**。
- 对照：boss 在容器内 `OpenMeteoProvider().search('今天杭州天气怎么样')` 独立运行**正确返回**杭州实时天气（26.7°C 大致晴朗）→ 代码/网络/provider 本身 OK，问题在「对话聚合路径里天气被静默丢弃」。

### 根因
1. `OpenMeteoProvider.search` 的 geocoding / forecast 两步任一步 `_http_get_json` 抛 `ToolError`（容器直连 NAT 偶发连接重置 / TLS 抖动）→ 直接 `return []`，天气凭空消失。
2. **更隐蔽的可见性黑洞**：`orchestrator.dispatch_tool` 中 `_, entries = bundle.web_search.search_many(...)` 把 `search_many` 返回的第二个元素（**逐源 ok/error 状态**）直接丢弃；`_build_sources` 只取 `result=entries`（聚合结果），单源静默失败无任何痕迹 → 模型看不到天气，还**误报「HTTP 429/403/404」**（凭空猜测）。

### 修复
| 文件 | 改动 |
|---|---|
| `tools/data_sources.py` | 新增 `_http_get_json_retry`（3 次指数退避 0.6/1.2s，仅捕获 `ToolError`）；`OpenMeteoProvider.search` 的 geocoding + forecast 改走重试。 |
| `tools/data_sources.py` | `SearchTool.search_many` 三个失败分支（ToolError / 未捕获异常 / 返回空）加 `logger.warning/info`，含源 id + query → `docker compose logs api` 可查「天气为何没出来」。 |
| `docker-compose.yml` | `api` 服务新增 `CHAT_PROXY` 入参（默认空=直连；置 `http://host.docker.internal:10808` 则外网源经宿主代理出网，对齐 boss「绕过大陆」规则）；`NO_PROXY` 含 `localhost,127.0.0.1,postgres,redis,api,web,host.docker.internal` 防自环。 |

### 验证（沙箱）
- `py_compile tools/data_sources.py orchestrator.py chat_agent.py` → **COMPILE_OK**。
- 确定性复现（`tools/_verify_openmeteo_retry.py`，mock 前 2 次 `_http_get_json` 抛 `ToolError`、第 3 次成功）：`OpenMeteoProvider().search("今天杭州天气怎么样")` 仍返回 `杭州，浙江，中国 实时天气：26.7°C 大致晴朗`，总调用 4 次（含 2 次重试）→ **PASS**；脚本已删。
- 真实 chat e2e 需 boss 重建容器后验证（沙箱无法 `docker compose up --build`）：`docker compose up -d --build api` 后 WebUI 再问「今天杭州天气怎么样」应出真实天气；若外网仍偶发不通，在 `.env` 加 `CHAT_PROXY=http://host.docker.internal:10808`（前提：宿主代理开 **Allow LAN** / 监听 0.0.0.0，否则容器连不上会反更糟）。

### 诚实边界
- 代理默认为**关闭**（空值直连），避免「代理不可达」时全盘退化——这是有意为之的安全默认，不是漏配。启用需 boss 一步确认代理 LAN 可达。
- 重试只兜「瞬时」故障；若 geocoding/forecast 持续不可达（非瞬时），天气仍会缺席——此时 `search_many` 日志会记录 `源 open_meteo 返回空`，boss 看日志即可定位，不再像以前凭模型瞎猜。

---

## 闸③ 独立审议
**BLOCKED** — 见 `REVIEW_M11-2.md`（独立子代理经 Intern-S2-Preview-397B 审议；本会话因 Agent 工具基础设施错误 + 网关 429 未能执行，补审待环境恢复后跑，补审 PASS 前**不 commit**）。
