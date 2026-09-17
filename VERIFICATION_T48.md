# VERIFICATION_T48 — 自主 QA 批（UI / 功能表 / 核心引擎 e2e）

> 本批由主代理在无人值守夜班自主完成：机器比对前端功能表 ↔ 后端端点、全量 API 冒烟、核心研报任务
> 两轮真实 e2e、容器出网探针复核、并定位+修复一处真实检索健壮性缺陷。
> 门禁状态见文末：**独立审议仍 BLOCKED（子代理配额 429），未伪造 reviewed-by、未 commit**。

## 一、机器比对：前端功能表 vs 后端实现

- 后端端点：**81 个**（`GET/POST/PUT/DELETE` 全扫描 `server/*.py`）。
- 前端路由：**23 条**（`web/src/router/index.ts`）。
- 前端实际调用的后端路径：**36 处**（扫 `web/src/views/*.vue` + `services/*.ts` 路径字面量）。
- 结论：**功能表与实现基本对齐**。前端 36 处引用全部能在后端 81 端点中找到对应（含 `{param}`/`${id}` 参数名差异已按段归一化核对）。**没有"前端调了不存在的端点"这类硬 bug**。
- 反向（后端有、前端无入口）：后端 81 端点里多数为 POST/PUT/DELETE 写操作由 service 层调用，属正常；唯一**实质性 gap = experts**（见 §三）。

## 二、全量 API 冒烟（21 个 GET 管理端点）

全部返回 **200**，无 500。数据真值抽查：
- `/admin/plugins`：22 个数据源，状态诚实（`coming_soon` 源标 coming_soon、api_key 源按密钥实况标 connected/disconnected）。
- `/admin/skills`：15 个技能在册（含 7 个 agency-agents 自主装入），全部 `installed=True`。
- `/admin/models` / `/admin/templates` / `/admin/gates` / `/admin/agents-library` 校验均 `ok=true`。
- 前端资源部署层：`/` 200，全部 JS/CSS 资源（element-plus 1MB、vue、index 140KB 等）**全部 200**，SPA 可完整加载。

## 三、核心引擎 e2e（两轮真实研报任务）

`POST /tasks` → `engine_manager.launch_engine` 真实启动子进程跑 LangGraph 流水线。

- 第一轮（修复前）：任务 `2b5403e5` 检索阶段 `web_search` **64 条 tool_error 全失败**，根因 `[SSL: UNEXPECTED_EOF_WHILE_READING]`。
- 第二轮（修复后）：任务 `3ace2222` events 证实已推进至 `round_update 2` / `gate_complete 1` / `rework_trigger 1`
  ——即 Researcher→GateA→Analyst→rework 全链路在检索全空时仍能**优雅推进不崩溃**。

**关键诊断（诚实边界）**：两轮任务最终都未产出完整研报，根因是**当前容器出网处于持续性 SSL 故障窗口**，
**不是项目代码硬 bug**：
- 容器出网探针：`duckduckgo`/`wikipedia`/`arxiv`/`github` **全部 15s SSL handshake timeout**（同一窗口持续 12+ 分钟）。
- 历史 `.engine_state/` 含大量几十 KB 的成功 `output.json` → 以前出网是通的，**属运行环境回退**。
- LLM 网关**正常**（任务能跑 Gate/Analyst/rework，证明 LLM 调用成功），仅检索网络挂。
- Tavily 带真实 key 实测也是 SSL 抖动（非 key 失效；无 key 直连才 403）。

## 四、[已修] 真实缺陷：search_many 缺瞬时光键重试

- **现象**：14 个真 provider（Arxiv/Wikipedia/DuckDuckGo/TencentQuote/…）的 `search()` 走**无重试**的 `_http_get`/`_http_get_json`；
  仅 OpenMeteo/RssNews 用了已有的 `_http_get_retry`。撞 SSL 抖动窗口即整源 `ToolError` 失败、降级空。
- **修复**：`tools/data_sources.py` 新增模块级 `_search_with_retry`（瞬时光键 `SSL/UNEXPECTED_EOF/timed out/Connection reset/handshake`
  短退避重试 3 次，退避 0.8/1.6s），并在 `SearchTool.search_many` 的 `ex.submit` 外层包裹——
  **一次改动覆盖全部真 provider**，对齐 MEMORY 坑 15「SSL UNEXPECTED_EOF 是瞬时抖动须短退避重试」结论。
- **验证**：`py_compile` 通过；全量回归 **216 passed**（与修复前一致，零回归）。

## 五、[gap] experts 后端完整、前端零入口

- 后端：`/admin/experts`、`/experts/proposals`、`/experts/convert`、`/experts/{eid}/toggle`、`/proposals/{pid}/accept`
  共 6 个 CRUD 端点完整实现。
- 前端：`web/src/router` **无 `/experts` 路由**；`web/src` 全文 grep `expert`/`expertType`/`expert_type` **0 处引用**。
- `AssetCenter.vue` 是「经验/反思」中心（reflections+lessons），**非** experts 承载页。
- 结论：**M12 专家团后端做完、前端未接**。属规划性 gap，非 bug；建议补一个最小 experts 管理页（路由+列表+采纳/驳回）。

## 六、[minor] 空 TAVILY_API_KEY 环境变量

- 容器内 `TAVILY_API_KEY` 存在但值为空（docker-compose 透传了空 env）；真实 key 在 `.secrets/plugins.env`。
- 无害：`load_secrets` 用 `and v` 过滤空值，tavily 实际按 `.secrets` 真 key 判定 connected。**仅为整洁项**，可清理 docker-compose 空 env。

## 七、[虚惊] 字段缺失误报

- 早前 `qa_data` 脚本打印 `connected=None`/`needs_key=None`/`type=None` —— 是**我看错字段名**（后端返回 `status`/`channel_type`/`expert_type`，
  不返回 connected/needs_key/type）。前端 `Plugins.vue`/`ChatEntry.vue`/`Channels.vue` 全部用 `status` 字段渲染，
  CSS 里 `.pp-status.connected` 是 class 名非数据读取。**无 bug**。

## 八、测试与门禁

- 单测：216 passed（py_compile + 全量 pytest，忽略 api_boundary / m12_admin_api 两个需鉴权边界集）。
- 真实 e2e：2 次研报任务提交（`create_task` 200 + 引擎推进 events 验证）。
- **门禁**：独立审议子代理（Intern-S2 及默认模型）仍报 `429 使用量超出频率限制`。按硬规矩：
  `REVIEW_T48.md` 顶标 **BLOCKED**，未伪造 `<!-- reviewed-by -->`、未 `git commit`。配额恢复后补审 → PASS 再提交。

## 九、给 boss 的下一步建议

1. **网络层（阻塞当前研报产出）**：排查容器出网 SSL 持续失败——可能 Docker NAT/MTU/DNS 或宿主代理串扰。
   历史能通→现在不通，优先查 `docker network` / 容器 DNS / 宿主代理是否误入容器。
2. **可选**：补 experts 前端管理页；清理 docker-compose 空 TAVILY env。
3. **已就绪**：search_many 重试修复待门禁闭环后提交。
