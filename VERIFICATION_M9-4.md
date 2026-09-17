# VERIFICATION_M9-4 · 研报推送渠道 自审

> 三道闸：① 编译校验 ② 本文件自审 ③ REVIEW_M9-4.md 独立审议
> 本文件 §17 = 设计自审（编码前）；§18 = 实施自审（编码后填）。

---

## §17 设计自审（编码前，对照 DESIGN_M9-4.md）

### §17.1 一致性（设计内部 + 与既有范式）
- **与平台设计一致（字段为适配引擎做的扁平化超集，独立审议 NOTE-1）**：§3.4 渠道目录（钉钉/微信/飞书/企业微信/Telegram/Discord）、`/channels` 页、触发点语义（done/escalated/manual）与 `DESIGN_PLATFORM_FUNCTIONS.md §3.4` 对齐；领域替换正确（IM 渠道直接适用，无电商内容搬入）。**字段差异**：平台 §3.4 YAML 是参考示意（嵌套 `config.webhook`、标量 `strategy`、无 `coming_soon`），本设计 `config/channels.yaml` 为适配真控制器引擎实现做了**扁平化超集**——扁平 `endpoint`、列表 `strategy`、新增 `channel_type`(webhook/mock/coming_soon) 与 `mock` 验证通道。目录/页/触发点语义一致，字段扩展非不一致。
- **与 M9-3 同构**：注入点同处 orchestrator 层（M9-3 在 `make_agent:646` 注入 system；M9-4 在 `run_report:1135` 注入推送），均为单点、热加载、最小改动。SoT 形状镜像 `config/skills.yaml`（id/name/category/enabled/installed/builtin/status）。
- **与 M9-2 同构**：`ComingSoonChannel` / `PROVIDER_REGISTRY` / `load_channels` 热加载 / `build_push_targets` 聚合守卫，对齐 `tools/data_sources.py` 的诚实占位范式。
- **与 admin 范式一致**：channels CRUD 复用 `_atomic_write` / `_write_audit(subdir="channels")` / `_dump_commented`(ruamel) / 失败回滚 / builtin 拒删 / 启用守卫，对齐 `admin.py:1632-1843` skills 段。
- **字段命名统一**：channels 用 `endpoint`（非 webhook_url），避免与未来插件字段漂移；`channel_type` 区分 webhook/mock/coming_soon，与 `provider` 在 plugins.yaml 同义但独立命名（避免跨文件耦合）。

### §17.2 代码实证（注入点真实可用，非臆想）
- `orchestrator.run_report` 确实在 `:1135` 返回 `graph.invoke(init)`；`status` 由 `node_gate*`（`:917` done）/ `node_escalate`（`:973` escalated）先于该返回置位 → 注入点可拿到终态 status。✅
- `server/engine_runner.py:54` 调 `run_report` 并写 `outputs/<task_id>.json`；在 `run_report` 内注入 push，对 engine_runner / api.py（WS 广播）**零改动**，仅 final_state 多 `delivery_status` 字段（dict 扩展，向后兼容）。✅
- `admin.py:1632-1843` skills 段已实证 ruamel round-trip + 审计 + 回滚 + builtin 拒删 + 启用守卫；channels 段复刻同机制，风险可控。✅
- `config/` 自 M8 已 bind mount（`./config:/app/config`），channels.yaml UI 改动落盘即可被引擎 `load_channels` 读，无新增 bind mount（channels 无片段目录，区别于 M9-3 的 `./skills`）。✅

### §17.3 诚实边界（不可逾越，对照 DESIGN §3.4 / §10）
- H1 `coming_soon` 永不发：`build_push_targets` 显式 `if ctype == "coming_soon": continue`，引擎永不调其 send。✅
- H2 端点空诚实跳过：webhook 启用但 endpoint 空 → `build_push_targets` 跳过（不 raise、不冒充）；UI 给「未配置端点」提示；启用守卫 PUT 400 防误启用。✅
- H3 best-effort：`push_report` 内 `try/except ToolError` 单渠道失败仅记 `delivery_status`，`run_report` 外层再包 `try/except Exception` 防推送异常阻断研报。✅
- H4 向后兼容：无 channels.yaml → `load_channels` 返回 [] → `push_report` 返回 [] → final 不变。✅
- H5 无密钥引入：webhook 端点存 config（URL 非 API key）；coming_soon 无密钥需求；不新建 `.secrets/channels.env`。✅

### §17.4 控制器效应（真·控制器，非装饰）
- 启用 + 端点已填 → `WebhookChannel.send` 真 `requests.post`（E3/E6 实证）。
- 禁用 → `build_push_targets` 首关 `enabled` 跳过（E2）。
- 启停经 `load_channels` 热加载，下一个任务即生效（E6）。
- 与 M9-2 同源逻辑：控制器效应 = 「配置状态真实驱动引擎外发行为」，非 UI 自嗨。✅

### §17.5 复合写（后端写操作的原子性与可审计）
- POST/PUT：ruamel `_dump_commented` 保注释 + `_atomic_write` 落盘 + `_write_audit(subdir="channels", src_path=CHANNELS_PATH)`。
- 失败：`_atomic_write(CHANNELS_PATH, before)` 回滚 yaml（对齐 skills `:1745/:1804/:1837`）。
- builtin 拒删：DELETE `builtin=True` → 400（对齐 skills `:1813` 段）。
- 启用守卫：PUT `enabled=True` 且 webhook 且 endpoint 空 → 400（对应 H2）。
- `POST /test` 复用 `ch.send` 单发，coming_soon → 400（诚实，不冒充）。✅

### §17.6 向后兼容（不破坏既有）
- 新增 `from tools.push import push_report` 在 `run_report` 内调用；`push_report` 默认 `channels=None` 时 `load_channels()`，无 channels.yaml 即 []。
- 现有 21 passing 回归测试调 `run_report` 不会因 push 失败（best-effort + mock 默认开仅落盘）；3 个 TD-009 失败（rework/max_rounds/json）与推送无关，不增减。
- `delivery_status` 是 final_state 新增可选字段，既有序列化/WS 广播不依赖它。✅

### §17.7 风险与缓解
| 风险 | 缓解 |
|---|---|
| requests 未安装（webhook 真发依赖） | 设计用 `import requests` 懒加载于 `WebhookChannel.send`；若缺失，send raise 被 best-effort 捕获记 `delivery_status`，不阻断；实施阶段确认 requests 在镜像层可用（M9-2 web_search 已用 requests，已装） |
| 推送慢阻塞任务（webhook 超时） | `requests.post(timeout=10)`；best-effort 不阻断研报产出 |
| 端点含敏感 token（webhook URL 自带） | 公开 `GET /channels` 视图 `_channel_item` 不返回 endpoint（对齐 H5，不泄露）；管理视图才含 |
| 多渠道失败难排查 | `delivery_status` 逐渠道 `{ok, detail}` 入 final_state + mock 落盘日志，可审计 |
| 范围蔓延（接入微信/Telegram 真实） | §0.3 / §11 明确 coming_soon，引擎永不发，留接口待后续里程碑 |

### §17.8 设计阶段结论
设计内部一致、与 M9-1/2/3 范式同构、注入点实证可用、诚实边界不可逾越、控制器效应真实、复合写原子可审计、向后兼容。达到「可进入编码」条件，提交独立子代理设计审议（REVIEW_M9-4 设计轮）。

### §17.9 设计修订记录
- **独立审议第 1 轮（agent-d379f1be，2026-09-09）：PASS_WITH_NOTES，MAJOR=NONE**。落款 `REVIEW_M9-4.md`（独立子代理，主代理未自签）。
- 采纳 NOTE-1：§17.1 措辞由「完全对齐」改为「对齐目录/页/触发点语义；字段为扁平化超集」，说明平台 §3.4 YAML 是参考示意、本设计做了适配引擎的字段扩展。
- 采纳 NOTE-2：`push_report` 增「启用但 webhook 端点空」的 skip 留痕条目（写 `delivery_status`，不冒充、不崩），落实 §3.4/§0.3 的「诚实跳过 + 留痕告警」。
- 采纳 NOTE-3：`POST /admin/channels/:id/test` 返回码明确（coming_soon→400 / webhook 空端点→400 / mock→200 / webhook 已填→200 真实 POST）。
- NOTE-4（requests 软依赖）：实施期确认镜像已装 requests（M9-2 web_search 已用，无需新增依赖），不另处理。

---

## §18 实施自审（编码后，对照 DESIGN_M9-4.md）

### §18.1 实现落点对照表
| 设计条款 | 实现落点 | 状态 |
|---|---|---|
| §3.2 抽象层 tools/push.py | `tools/push.py` 新建：PushChannel/WebhookChannel/MockChannel/ComingSoonChannel + PROVIDER_REGISTRY + load_channels + _recompute_status + build_push_targets + push_report | ✅ |
| §3.1 SoT config/channels.yaml | `config/channels.yaml` 新建：钉钉/飞书/企业微信/Discord(webhook,disabled) + mock(enabled) + telegram/wechat(coming_soon) | ✅ |
| §3.3 orchestrator 注入 | `orchestrator.py:25` import；`run_report` 末 `final=graph.invoke(init)` 后注入 push_report（best-effort try/except → delivery_status） | ✅ |
| §4 admin API | `server/admin.py` 文件尾追加 ~230 行：GET/POST/PUT/DELETE /admin/channels + GET /channels(公开) + POST /admin/channels/:id/test；ruamel + _atomic_write + _write_audit(subdir="channels") + 回滚 + builtin 拒删 + 启用守卫 | ✅ |
| §5 前端 | `web/src/views/Channels.vue` 新建（镜像 Skills.vue，增端点输入+测试发送）；`router/index.ts` 加 `/channels`；`DefaultLayout.vue` 加「推送渠道」菜单；`ui.ts` 加 `ChannelItem` | ✅ |
| §3.4/§0.3 无新 bind mount | channels 无片段目录，config 已在 M8 bind mount；无需新增 volume | ✅ |
| §12 平台文档 | `DESIGN_PLATFORM_FUNCTIONS.md` §3.4 标 ✅、路由表与路线表标已交付 | ✅ |

### §18.2 E1–E7 实证（脚本 `C:\Users\sfkj\.workbuddy\m9-4_verify.py`，14/14 ALL_OK）
- E1 无 channels.yaml → push_report 返回 []（不破坏，load_channels 缺省 []）。
- E2 禁用 mock → build_push_targets 排除；启用 → 含。控制器开关生效。
- E3 **webhook 真发**：起本地 HTTP 接收器，dingtalk endpoint=本地，push_report 后接收器收到 `{"msgtype":"markdown",...}` POST；mock 同时落盘。证明非装饰。
- E4 coming_soon(telegram) 启用 → push_report 返回 []（引擎永不调 send，不 raise）。
- E5 webhook 启用但 endpoint 空 → 跳过且记 `delivery_status` skip 条目（ok=False, "endpoint empty"），不冒充、不崩。
- E6 **真实节点流注入**：`run_report(StubLLMClient(scenario="pass"), max_rounds=1)` → status=done，final.delivery_status 含 mock 条目（ok=True）。控制器效应在真实引擎路径生效。
- E7 测试端点 send 路径：coming_soon.send raise（→400）、webhook 空端点.send raise（→400）、mock.send ok（→200）、webhook 已填.send 真 POST ok（→200）。与 `POST /admin/channels/:id/test` 实现一致。

### §18.3 关键实测发现
- **注入点真实可用**：`run_report` 在 `graph.invoke` 后、`status` 已置 done/escalated，注入 `push_report(final)` 取到终态并真实外发（E6 实证）。对 `engine_runner.py` / `api.py`(WS) 零改动。
- **requests 软依赖可用**（NOTE-4 落实）：E3/E7 webhook 真 POST 成功，证明镜像已装 requests（M9-2 web_search 已引入），`import requests` 懒加载且异常被 best-effort 捕获。
- **delivery_status 入 final_state**：`run_report` 用 `final.setdefault("delivery_status", []).extend(delivery)`，不影响既有序列化/WS 广播（向后兼容）。

### §18.4 诚实边界（R1-R5，对照 §3.4/§10）
- R1 coming_soon 永不发：build_push_targets 显式 `continue` + E4 实证。
- R2 端点空诚实跳过+留痕：E5 实证（skip 条目入 delivery_status）。
- R3 best-effort：单渠道失败仅记 delivery_status；run_report 外层 try/except 防推送异常阻断研报（E6 即使 mock 异常也不影响 status=done）。
- R4 向后兼容：E1 实证（无 yaml 不推送）。
- R5 无密钥引入：webhook 端点存 config/channels.yaml（非 API key）；coming_soon 无密钥需求；公开 GET /channels 不返回 endpoint（_channel_item(public=True) 不含 endpoint）。

### §18.5 复合写与回滚
- channels CRUD 复用 `_dump_commented`(ruamel) + `_atomic_write` + `_write_audit(subdir="channels", src_path=CHANNELS_PATH)`；失败 `_atomic_write(before)` 回滚（对齐 skills 段）。
- builtin 拒删：DELETE builtin → 400（仅可停用）。启用守卫：PUT enabled=True 且 webhook 且 endpoint 空 → 400。
- 新增渠道默认 enabled=False（须填 endpoint 后启用，诚实）。

### §18.6 回归
- `pytest tests/`：**3 failed / 38 passed**。3 失败 = TD-009 预存债（test_rework_loop_and_convergence / test_json_recover_by_retry / test_max_rounds_exhausted_escalates_with_bounds，gate_review_history 轮次 off-by-one），与 M9-3 基线同集合、同根因，M9-4 未触碰 rework/json 逻辑，**非引入**。新增 push_report best-effort 注入未改变任何既有测试行为。

### §18.7 结论
实现与设计契约逐项对齐，E1-E7 实证全 PASS（含 E3 真 webhook POST、E6 真实节点流注入），诚实边界 R1-R5 不可逾越，复合写原子可审计，回归债 TD-009 预存且未增减。达到实施独立审议条件。

### §18.8 实施修订记录
（实施独立审议 NOTE 回填处，待 REVIEW_M9-4 实施轮产出后补）
