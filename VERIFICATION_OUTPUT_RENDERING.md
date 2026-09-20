# VERIFICATION.md — 研报输出渲染（docx / pptx / pdf）

> 设计依据：`DESIGN_OUTPUT_RENDERING.md v1.0`（先设计后实现，boss 铁律）
> 范围：OR-1 parser+docx / OR-2 pptx+pdf / OR-3 API / OR-4 审计接真 ZIP / OR-5 前端 / OR-6 依赖
> 原则：**`report_markdown` 仍是唯一事实源**，三格式是下载时渲染的派生视图，引擎零改动。

## gate ① 静态校验
- `py_compile`：`orchestrator.py` / `tools/doc_render.py` / `server/api.py` / `server/audit_export.py` 全过 ✅
- 前端 `vue-tsc --noEmit`：**exit 0，零错误** ✅
- 模块导入期校验：`import server.api` 通过（新增 `from tools.doc_render ...` 不会因缺库让容器启动失败——
  文档库在渲染函数内惰性导入，这是刻意设计）

## gate ② 真实实证（不靠纸面）
| 项 | 证据 |
|---|---|
| 解析子集 | 15 条单测：标题层级 / 要点成组 / 段落合并 / 管道表（分隔行不入数据）/ 引用节保留 `[id](url)` / 未知行降级段落 |
| docx | 真文件可重开；含真表格；引用生成**真超链接**（`w:hyperlink` + 关系部件 URL） |
| pptx | 真文件可重开；封面+章节页；>6 要点自动续页「（续）」；引用页每页 ≤10（23 条 → 3 页） |
| pdf | 真 PDF（`%PDF` 头）；字体 `/BaseFont /STSong-Light /Encoding /UniGB-UCS2-H`（CID 中文字体）→ **中文不黑块**；链接为 URI 注解 |
| 端点 | 14 条单测：md/四格式 MIME 与魔数、默认格式、RFC5987 中文文件名、404 越权 / 409 未完成 / 400 格式 / 503 缺库 |
| 审计 ZIP | 端点此前返回 TODO JSON（前端按 ZIP 下载必报「非 ZIP」）→ 已接真 ZIP；6 个成员齐全；status 可序列化 |
| 样张 | `outputs/sample_report.{docx,pptx,pdf}`（37KB / 33KB / 4KB） |
| **全量测试** | **266 passed / 0 failed / 0 errors** ✅ |

## 过程中修掉的 4 个真缺陷（含 1 个我自己引入的回归）
1. **我自己引入的回归（教训级）**：为修审议 F3「1/8 不可达」把重试循环无脑放宽到 ≥4 次，
   破坏「自愈有界、持续报错必须最终失败」语义 → 3 条老测试由绿转红（fake client 第 4 次返回成功，不再抛错）。
   改为**减半走独立有界预算**（`halvings < 3`），绝不放大通用重试次数。
2. **pdf import-guard 形同虚设**：`from reportlab.lib... import` 命中子模块缓存，
   `sys.modules['reportlab']=None` 触发不到 ImportError → 守卫首行改为 `import reportlab`。
3. **`skill_importer._backup()` 写死 `BASE`**：`relative_to(BASE)` 违反多租户契约，
   `TENANTS_ROOT` 指向别处时必 ValueError → 改相对 `_tenant_root()`，失败兜底绝对路径。
4. **`experts._write_package()` 同源**：`root.relative_to(BASE)` → 同样修复。

## 顺带修的既有缺陷（与渲染同文件/同模式）
- `web/src/utils/download.ts` 裸 fetch **漏 Bearer token**（auth 走 localStorage `rat_token` + axios 拦截器）
  → 鉴权开启时审计包下载必 401；新 `downloadReport` 与旧 `downloadAuditPackage` 统一走 `authHeaders()`。
- **43 条老测试被多租户改造搞挂**（25 failed + 15 errors）→ 全部修绿：
  - 新增 `tests/conftest.py` autouse fixture：绑定测试账号 + 租户根指向 tmp + 从 BASE 播种配置
    （**拒绝**用 `RAT_LEGACY_GLOBAL=1` 逃生阀——那是官方标注「假隔离、Phase 2 前必删」）；
  - 老 fixture 改打**惰性函数**（`_config_dir()` / `_expert_dir()` …）而非已删除/已无人消费的旧常量；
  - admin API 测试补显式后门令牌（改造堵掉了 `dev_no_token` 放行缺口，端点现在必须带凭据）。

## 部署要求（必须宿主执行）
- `requirements.txt` 追加 `python-docx>=1.1` / `python-pptx>=1.0` / `reportlab>=4.0`（均纯 Python，无需 apt）。
- 容器生效须**重建镜像**（构建期 pip，无运行期安装）：`docker compose up -d --build api`。
  未重建前，三种新格式会返回 **503 RENDERER_UNAVAILABLE**（如实报错，不冒充成功）。

## 已知边界（诚实声明，不夸大）
- 不支持图表/图片嵌入（报告为纯文本 md，无图数据）；pptx 不做主题美化（白底深字）。
- md 解析只覆盖自家管线生成的受控子集；嵌套列表等复杂语法降级为段落（宁简不崩）。
- `output_format`（模板字段）暂不与下载按钮联动（纯展示现状保持，等真需求，防过度工程）。
- 渲染结果不缓存落盘（小文档毫秒级，缓存是无用复杂度）。

## 结论
gate ① ✅、gate ② ✅（真实实证 + 全量 266 绿）。提交独立子代理审议（gate ③）。

## gate ③ 独立审议结果（reviewer=agent-8e608a2a，非主代理自签）
**PASS_WITH_NOTES**（BLOCKER 0 / MAJOR 0 / MINOR 4）。审议者独立复跑全量：266 passed，与自审一致。
重点核查均通过：owner 隔离（越权 404 不返 403）、缺库 503 诚实边界、重试循环有界收敛
（`_MAX_HALVINGS=3` + `attempt` 单调 → 必终止，旧回归已消除）、多租户契约（无新增写死 BASE、
conftest 未用 `RAT_LEGACY_GLOBAL` 逃生阀）、测试证明行为而非确认存在。

4 条 MINOR 处置：
1. **渲染器无显式输入体积上限** —— 记为已知边界（报告由自家引擎产出，md 规模受 LLM 输出约束；
   加截断会静默改内容，反而违反诚实边界，故不硬加）。
2. **`report_markdown` 为空时 ZIP 不含 report.md** —— 属实且可接受（ZIP 仍合法，其余 5 成员齐全）。
3. **URL 正则会截断含 `)` 的链接**（如维基 `.../Foo_(bar)`） —— **已修**：正则支持一层成对括号。
4. **`_ApiTaskView` 对畸形 TaskResponse 宽容降级** —— 刻意设计（审计导出不应因一条脏字段整体失败）。

---

## 交付固化（第二轮：从「代码写完」到「运行环境真的能跑」）

上一轮只证明「宿主 venv 里单测绿」，但**交付物并没有落到真实运行环境**（记忆坑 15：沙箱 ≠ 容器）。
本轮补齐，证据如下：

| 项 | 命令 | 结果 |
|---|---|---|
| 镜像重建 | `docker compose -f docker-compose.yml build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple api` | exit=0，58s（清华源；先前沙箱里清华源缺 pptx/reportlab 是**沙箱侧**问题，容器网络内三个包都在） |
| 容器重建 | `docker compose ... up -d api` | exit=0，report-api 起来 |
| 容器内依赖 | `docker exec report-api python -c "import docx,pptx,reportlab,asyncpg;print('OK')"` | OK（reportlab 实际解析到 **5.0.1**，`>=4.0` 的上界未锁，实测 5.x 的 `UnicodeCIDFont('STSong-Light')` API 仍可用） |
| **真实产物渲染** | `docker exec -w /app -i report-api python - < scripts/smoke_render_real.py` | **RESULT: PASS** —— 取 `/app/outputs` 里真实中文研报（9414B / 5 blocks）→ docx 42263B、pptx 39980B、pdf 28470B，三种魔数均对，PDF 内含 `STSong` 字体（中文不黑块） |
| 全量单测 | 宿主 venv（`envs/default`，唯一装了 asyncpg 的解释器）+ `testpaths=tests` | **266 passed, 0 failed, 0 errors**（exit=0） |
| 前端类型 | `vue-tsc --noEmit` | exit=0，零输出 |

### 新增/修改（本轮）
- `scripts/smoke_render_real.py`（新）：容器内真实产物渲染冒烟，可被任何人复跑验证，不是一次性手敲命令。
- `pytest.ini`（新）：`testpaths = tests`。
  原因：仓库根的 `test_server.py` / `test_service.py` 是 M6-2 的**手工冒烟脚本**（裸 `async def test_*`，
  直连 `http://localhost:8000`，宿主未暴露该端口），不是单测，被 pytest 收集会稳定报
  `async def functions are not natively supported`（1 failed + 1 error）。
  处置原则：**限定收集范围 = 根治；不删脚本、也不给它们打 skip 掩盖红灯**。脚本原地保留，
  由人在服务起来后显式 `python test_server.py` 执行。
- `.gitignore`（改）：新增 `/tenants/`（多租户运行时落盘，含 `.audit` 配置快照与测试专家包）
  与 `.pytest_tmp/`。

### 复跑命令（含两个沙箱坑，照抄即可）
```
cd "E:/第二电脑/report-agent-team" && PYTEST_DEBUG_TEMPROOT="C:/Users/sfkj/AppData/Local/Temp/rat-pytest-root" \
  C:/Users/sfkj/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest --tb=line -p no:cacheprovider
```
坑 A：**必须用 `envs/default` 这个 venv**，系统 Python 与托管 Python 都缺 `asyncpg`，会 266 个 collection error。
坑 B：**必须设 `PYTEST_DEBUG_TEMPROOT` 指向新建的纯 ASCII 目录**。默认临时根 `Temp/pytest-of-sfkj` 下
已有上万个历史残留，pytest 收尾清理时会被沙箱 safe-delete「批量删除 >50 需确认」拦截 → 总结行被吞、
退出码变 1（**全绿但看起来像失败**）。指向中文路径同样会建目录失败（266 errors）。

## 最终交付 gate ③（reviewer=general-purpose-17，非主代理自签）
**PASS_WITH_NOTES**，0 BLOCKER / 0 MAJOR / 1 MINOR。审议者独立复跑：collected 266 → **266 passed, 0 failed, 0 errors**（68.77s）；
并独立确认容器依赖 OK、`smoke_render_real.py` RESULT: PASS（PDF 内含 STSong-Light）。
独立抽查均通过：pdf import-guard 首行 `import reportlab` 真实有效（doc_render.py:331）、
四个错误码路径均可达且被覆盖（404/409 api.py:927 / 400 api.py:938 / 503 api.py:962）、
前端 `download.ts:8-11 authHeaders()` 已带 Bearer、conftest 拒绝 `RAT_LEGACY_GLOBAL`、
`experts.py:525` / `skill_importer.py:675` 的租户契约修复真实。
对 `pytest.ini` 的诚实性判定：**诚实，非藏红灯**（两个冒烟脚本未删未 skip，仍可手工执行）。

- MINOR-1（**.gitignore 缺 `.pytest_tmp/`**）：**属实但已被追平** —— 审议者读到的是我补该条之前的旧版；
  现 `.gitignore` 第 88 行 `/tenants/`、第 91 行 `.pytest_tmp/` 均在（`git grep` 可复核）。功能无影响。

