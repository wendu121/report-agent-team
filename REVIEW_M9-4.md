# REVIEW_M9-4 · 研报推送渠道 独立审议（设计轮）

> **立场声明**：独立子代理（edict-gate 第三道闸）。与主代理无串通，所有结论基于本文件对真实代码的独立 Read 取证，不采信设计/自审文档自述。
> 审议对象：`DESIGN_M9-4.md`（待评审设计）；对照 `VERIFICATION_M9-4.md §17` 但不轻信。

---

## 1. 注入点真实性 ✅ PASS
- 实证 `orchestrator.py`：末闸 `make_gate` 在 `is_terminal and decision=="advance"` 时置 `rs["status"]="done"`（`:917`）；`node_escalate` 置 `"escalated"`（`:973`）；Router rework 超限亦置 `"escalated"`（`:955`）。三者均发生在 `graph.invoke` 落定前。
- `run_report` 当前 `:1135` 为 `return graph.invoke(init)` 纯返回。设计 §3.3 改为 `final = graph.invoke(init)` → `push_report(final)` → `return final`，**位置真实、可取终态**。
- `push_report` 读 `final_state["routing_state"]["status"]`（done→on_complete / escalated→on_gate_fail），与节点置位键一致。
- `engine_runner.py:54` 仅调 `run_report` 并 `json.dump(final_state)`；api.py WS 广播读固定字段，新增 `delivery_status` dict 键向后兼容 → **对 engine_runner / api.py 零改动成立**。

## 2. 控制器效应真实性（boss 铁律：真 controller 非 viewer）✅ PASS
`tools/push.py::build_push_targets`（设计 §3.2）：
- 禁用真不推：`if not c.get("enabled") or not (c.get("installed") or c.get("builtin")): continue`（`:252`）✅
- coming_soon 真永不发：`ctype=="coming_soon": continue`（`:257-258`），引擎永调其 send ✅
- 端点空诚实跳过：`if ctype=="webhook" and not (endpoint).strip(): continue`（`:259`），不 raise、不冒充 ✅
- 启+端点非空 → `WebhookChannel.send` 真 `requests.post`（`:189`）✅ —— 与 M9-2 `build_search_tool` 同构的真控制器逻辑。

## 3. 诚实边界不可逾越 ✅ PASS
- H1 coming_soon 永不调 send（§2）✅
- H2 空端点诚实跳过、不冒充、不崩（§2）✅；启用守卫 PUT 400 防误启用（§4.2）✅
- H3 best-effort：`push_report` 内层 `try/except ToolError`，外层 `run_report` 再包 `try/except Exception` → 单/全渠道失败仅入 `delivery_status`，不阻断研报 ✅
- H4 向后兼容：`load_channels()` 文件缺失返回 `[]`（`:226-227`）→ `push_report` 返回 `[]` → final 不变 ✅
- H5 无密钥：endpoint 即 URL 存 config，无 `.secrets/channels.env`（§0.3/§10）✅

## 4. 复合写原子可审计 ✅ PASS（机制复用成立）
admin.py 辅助齐全：`_yaml_rt`(:56)/`_dump_commented`(:81)/`_atomic_write`(:166)/`_write_audit`(:172, 支持 `subdir`)/`_require_admin`(:103)/`public_router`(:747)。设计 §4 复用 `_atomic_write`+`_write_audit(subdir="channels")`+失败回滚+builtin 拒删+启用守卫，与 M9-3 技能段（`:1639-1843`）同构，风险可控。
- `GET /channels` 经 `_channel_item` 不返回 `endpoint`（§4.1），公开视图不泄露 ✅
- builtin 拒删 / 启用守卫（webhook+空端点→400）对齐 skills 段纪律 ✅

## 5. 向后兼容 ✅ PASS
无 channels.yaml → `load_channels()==[]` → 不推送、不改既有行为；现有回归测试调 `run_report` 不受影响（测试环境无 channels.yaml 时 mock 亦不出现）。`delivery_status` 为 final_state 新增可选键 ✅。

## 6. 禁过度工程 / 范围 ✅ PASS
§0.3/§11 正确把「微信/Telegram 真实接入、独立手动推送管道、公网下载链接、密钥 env 化」列入不做；以 `/test` 端点满足手动推送诉求、payload 自含标题+摘要。无范围蔓延 ✅。

## 7. 与 M9-1/2/3 同构 & 与平台 §3.4 一致 —— ⚠️ 部分不符（见 NOTES-1）
操作 SoT 形状（id/name/category/enabled/installed/builtin/status）镜像 `config/skills.yaml` ✅；抽象层（`PROVIDER_REGISTRY`/`ComingSoon*`/`load_*`/`build_*`）镜像 `data_sources.py` ✅；注入点居 orchestrator 单点镜像 M9-3 `make_agent` 注入 ✅。
**但与平台 §3.4 形状并不"完全对齐"**：平台用嵌套 `config.webhook`、标量 `strategy`、含 `stats/pair_mode`、status 无 `coming_soon`；M9-4 用扁平 `endpoint`、列表 `strategy`、含 `coming_soon`。属有意且更诚实的扩展（删 stats=删假数据，正确），但自审 §17.1「完全对齐」措辞不实。

---

## 最终裁定：PASS_WITH_NOTES
**MAJOR = NONE**。注入点真实、控制器效应真实、诚实边界不可逾越、复合写原子可审计、向后兼容、范围克制，均经代码实证成立。无会破坏真控制器效应或诚实边界的设计缺陷，不需 BLOCKED。

## NOTES（非阻塞，建议修订）
1. **自审措辞纠偏**：§17.1「与平台 §3.4 完全对齐」不成立（webhook↔endpoint、嵌套↔扁平、status 无 coming_soon、缺 stats）。建议改为「领域替换一致 + 操作 SoT 对齐 M9-3（skills.yaml 形状）」；平台 §3.4 仅为高层草图，M9-4 是有意超集。
2. **诚实跳过「留痕告警」未兑现**：§0.2/H2 声称空端点/禁用/coming_soon「诚实跳过 + 留痕告警」，但 `build_push_targets` 仅 `continue`、`push_report` 不写任何 `delivery_status` 条目 → 无留痕。硬边界（不冒充、不崩）满足，但"留痕"落空。建议对诚实跳过的渠道补一条 `delivery_status(ok=False, detail="skipped:<reason>")`，兑现声称且不泄露端点。
3. **测试端点空端点态**：`POST /admin/channels/{id}/test` 对 webhook 空端点会 `raise ToolError`→返回 `{ok:False}`。建议明确返回码（400 或与启用守卫一致），避免前端歧义。
4. **`requests` 软依赖**：懒加载于 `WebhookChannel.send`，缺失被 best-effort 捕获（已记 §17.7）。实施阶段确认镜像层已装（M9-2 web_search 已用），无碍。

---

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 设计独立审议 · 独立子代理，主代理严禁自签 -->

---

## 实施独立审议（第 2 轮）

> **立场声明**：独立子代理（edict-gate 第三道闸第 ③ 道）。与主代理无串通，所有结论基于本文件对真实代码的独立 Read 取证 + 交叉运行 `m9-4_verify.py` / `pytest` / `py_compile` 取证，不采信设计/自审文档自述。对照 `VERIFICATION_M9-4.md §18` 但不轻信，逐条独立复证。

### 1. 真·控制器效应 ✅ PASS（E1-E6 实证）
独立运行 `python "C:/Users/sfkj/.workbuddy/m9-4_verify.py"` → `ALL_OK: 14/14 passed`（E1-E7 全覆盖）。关键代码证据：
- **禁用真不推**：`tools/push.py:146-149` `if not c.get("enabled") or not (c.get("installed") or c.get("builtin")): continue`；E2_disabled_skipped / E2_enabled_included PASS。
- **coming_soon 真永不发（引擎不调 send）**：`:152-154` `if ctype=="coming_soon": continue`；E4_coming_soon_no_send PASS（不 raise、delivery_status 无该渠道条目）。
- **端点空真跳过 + 诚实留痕**：`:155-156` webhook 空端点跳过；且 `push_report:184-189` 为「启用但 webhook 端点空」补写 `delivery_status(ok=False, detail="skipped: enabled but endpoint empty (honest no-op)")`——落实设计轮 NOTE-2。E5_skip_recorded PASS。
- **webhook 真 POST**：`WebhookChannel.send:64-72` 真 `requests.post(endpoint, json=_body(payload))`；E3_dingtalk_sent_ok / E3_receiver_got_markdown（本地接收器收到 `{"msgtype":"markdown",...}`）PASS。非装饰。
- **mock 真落盘**：`MockChannel.send:83-88` 写 `push_log.jsonl`；E3_mock_logged PASS。

### 2. 注入点真实 ✅ PASS（E6 真实节点流）
`orchestrator.py:25` `from tools.push import push_report`；`:1136-1145` 在 `final = graph.invoke(init)` 后注入 `push_report(final)`，best-effort `try/except Exception` 包住，成功则 `final.setdefault("delivery_status", []).extend(delivery)`。`E6_status_done` + `E6_mock_injected` PASS：真实 `run_report(StubLLMClient, max_rounds=1)` 在 status=done 节点流注入后，final.delivery_status 含 mock 条目。对 `engine_runner.py`/`api.py`(WS) 零改动；`delivery_status` 为 final 新增可选 dict 键，序列化/广播向后兼容。

### 3. 诚实边界 ✅ PASS
- H1 coming_soon 永不调 send（§1）✅
- H2 空端点诚实跳过 + 留痕（§1）✅；启用守卫 `server/admin.py:1999-2005` PUT `enabled=True & webhook & endpoint空 → 400` ✅
- H3 best-effort：内层 `try/except ToolError` + 外层 `run_report` `try/except Exception` 防推送异常阻断研报 ✅（E6 即使异常也不影响 status=done）
- H4 向后兼容：`load_channels()` 文件缺失返回 `[]`（push.py:115-116）→ push_report 返回 `[]` → final 不变 ✅（E1_no_yaml_returns_empty PASS）
- H5 无密钥引入：endpoint 即 URL 存 config/channels.yaml，无 `.secrets/channels.env` ✅

### 4. 复合写原子可审计 ✅ PASS
`server/admin.py:1846-2087` channels CRUD 复用 `_yaml_rt(56)/_dump_commented(81)/_atomic_write(166)/_write_audit(172, 支持 subdir)/_require_admin(103)/public_router(747)`，与 M9-3 skills 段（1639-1843）同构：
- **公开不泄露 endpoint**：`GET /channels`(1892) 经 `_channel_item(c, public=True)`（1873-1889，仅 `public=False` 含 endpoint）✅
- **复合写**：POST/PUT 走 ruamel `_dump_commented` + `_atomic_write` 落盘 + `_write_audit(subdir="channels", src_path=CHANNELS_PATH)` ✅
- **失败回滚**：`:1966-1974` / `:2021-2025` / `:2051-2055` 异常时 `_atomic_write(CHANNELS_PATH, before)` 全回滚 ✅
- **builtin 拒删**：DELETE `builtin=True → 400`（2041-2042，仅可停用）✅
- **启用守卫**：PUT 空端点 → 400（1999-2005）✅
- **测试端点返回码**：`POST /admin/channels/:id/test`(2060-2086) coming_soon→400、webhook 空端点→400、mock→200、webhook 已填→200 真 POST（E7_coming_soon_raises / E7_empty_endpoint_raises / E7_mock_ok / E7_webhook_post_ok 全 PASS）——落实设计轮 NOTE-3。

### 5. 向后兼容 ✅ PASS
无 channels.yaml → 不推送、不改既有行为（§3 H4）；`py_compile orchestrator.py tools/push.py server/admin.py` → **COMPILE_OK**。回归：`python -m pytest tests/ -q` → **3 failed / 38 passed**，3 失败 = TD-009 预存债（`test_rework_loop_and_convergence` / `test_json_recover_by_retry` / `test_max_rounds_exhausted_escalates_with_bounds`，根因 `gate_review_history` 轮次 off-by-one，实测断言 `assert 3 == 2`），与 M9-3 基线同集合同根因，M9-4 未触碰 rework/json 逻辑、**未新增失败**。

### 6. 与 M9-1/2/3 同构 & 与平台 §3.4 一致 ✅ PASS
抽象层（`PROVIDER_REGISTRY`/`ComingSoonChannel`/`load_channels` 热加载/`build_push_targets` 守卫）镜像 `data_sources.py`；注入点居 orchestrator 单点镜像 M9-3 `make_agent`；SoT 形状镜像 `skills.yaml`。`DESIGN_PLATFORM_FUNCTIONS.md:159` §3.4 标「✅ M9-4 已交付」、`:219` `/channels ✅`、`:300` 路线表 ✅——一致。前端 `web/src/views/Channels.vue`（镜像 Skills.vue，增端点输入+测试发送）、`router/index.ts:27` `/channels`、`DefaultLayout.vue:30`「推送渠道」菜单、`ui.ts:79 ChannelItem` 齐备。设计轮 NOTES 1-4 已在本轮实现落点全部兑现（诚实留痕/测试端点返回码/requests 可用；NOTE-1 为文档措辞，§17.1 已修正）。

---

## 最终裁定：PASS_WITH_NOTES
**MAJOR = NONE**。真控制器效应（E1-E6 14/14 PASS，含 E3 真 POST、E6 真实节点流注入）、注入点真实（对 engine_runner/WS 零改动）、诚实边界不可逾越、复合写原子可审计、向后兼容（3 失败均为 TD-009 预存债、COMPILE_OK）、与 M9 范式同构 & 平台 §3.4 一致，均经独立代码 Read + 交叉运行取证成立。无破坏真控制器效应 / 诚实边界 / 向后兼容的缺陷，不 BLOCKED。

## NOTES（非阻塞改进项）
1. **前端 endpoint 编辑盲区（潜在数据丢失）**：`Channels.vue:198` `editEndpoint[p.id] = p.endpoint || ''` 依赖 `GET /channels`（公开视图不含 endpoint，admin.py:1888 诚实隐藏）→ 端点输入框初始恒为空。对**已禁用**的 webhook 渠道，若用户在空白框点「保存端点」，PUT 发送 `endpoint:""` 会通过守卫（仅 enabled+webhook 拦截）、落盘清空已有端点（数据丢失）；对**已启用**渠道则被 PUT 400 拦截（无丢失，仅报错）。建议：编辑端点改由 `GET /admin/channels`（管理视图含 endpoint）预填，或前端在 endpoint 为空且非新建时禁用「保存」。不影响控制器/诚实契约，仅 UX/数据安全收敛。
2. **pytest 3 失败为 TD-009 历史债**：与 M9-4 无关，建议后续里程碑单独清偿；本轮确认 M9-4 未增减失败数。

---

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-09 · 实施独立审议第 2 轮（独立子代理，主代理严禁自签） -->
