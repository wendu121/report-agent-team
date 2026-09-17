# DESIGN_OUTPUT_RENDERING.md — 研报输出渲染（docx / pptx / pdf）

> 版本 v1.0 草案 · 2026-09-17 · 状态：**待 boss 拍板后开工**
> 上游事实源：DESIGN.md v2.4（`doc_export.py # Markdown 导出`，§102）+ API_SPEC.md v1.0
> 关联铁律：先设计后实现；诚实边界（缺库绝不挂可用）；精简克制不过度工程。

## 0. 一句话方案

**报告 SoT 仍是 `report_markdown`（Markdown 单一事实源不变）；新增服务端按需渲染**——
`GET /tasks/{id}/report?format=md|docx|pptx|pdf` 下载时才渲染，纯 Python 库直出三种格式，
引擎/Writer/ToolBundle **零改动**。

## 1. 现状（file:line 实证）

| 事实 | 位置 |
|---|---|
| 终稿 = `report_markdown`（md 文本）+ `report_path`（落盘 .md） | `orchestrator.py:88`、`tools/doc_export.py:99-111` |
| md 结构固定：`# 标题` + `## 章节`（正文/要点）+ `## 引用 / 来源`（`- [id](url) — title（可信度）`，代码确定性重建） | `tools/doc_export.py:21-47` |
| 工具形状 `SHAPE_TOOL` 里 writer = `doc_export`（已存在，仅 md） | `server/admin.py:926`、`tools/__init__.py:96-99` |
| 服务端唯一文件出口 = 审计 ZIP 端点，**且未接真实现**（返回 TODO JSON，见 §6） | `server/api.py:835-885` |
| 前端报告页已有「下载审计包 (ZIP)」blob 模式可复用 | `web/src/views/TaskResult.vue:32-33`、`web/src/utils/download.ts` |
| 模板声明 `output_format: string[]`（目前纯展示，无消费） | `web/src/types/ui.ts:106` |
| 容器 `python:3.13-slim`，构建期 pip、无 apt、无浏览器 | `Dockerfile:13` |
| requirements.txt 无任何文档库；venv 有 python-docx 1.2.0 + jinja2 **未声明**（漂移） | 环境实测（本会话） |

## 2. 架构决策

### D1 渲染时机：服务端按需（✅） vs 引擎完成时全量（❌）
- 按需：用户下载时才选格式才渲染；不浪费；`report_markdown` 已持久化在 Task 里；与审计导出同模式；**引擎零改动**。
- 全量（否决）：Writer 完成时渲染全部格式——没人下载也渲染、引擎耦合文档库、Task 状态膨胀。

### D2 库选型（容器无渲染器约束下全部纯 Python，无需 apt/改镜像）
| 格式 | 库 | 理由 |
|---|---|---|
| docx | `python-docx>=1.1` | 纯 Python；venv 已有 1.2.0，补声明同时修漂移 |
| pptx | `python-pptx>=1.0` | 纯 Python |
| pdf | `reportlab>=4.0` | 纯 Python；**CJK 用内置 `UnicodeCIDFont('STSong-Light')`**，无需系统字体文件 |
| ~~pdf 替代~~ | ~~weasyprint~~（否决） | 依赖 libpango/cairo，slim 镜像没有，须 apt 改镜像 |
| ~~pdf 替代~~ | ~~headless Chrome~~（否决） | 镜像无浏览器 |

⚠️ PDF 必须注册 CID 字体，否则中文全黑块——这是 reportlab 中文的第一坑，设计内锁定。

### D3 SoT 不变
`report_markdown` 是唯一事实源；docx/pptx/pdf 全部是**派生视图**（从 md 解析渲染），
不产生第二份需维护的文档状态。`doc_export.export()` 的 md 落盘行为不变。

## 3. 模块设计：`tools/doc_render.py`（新建，纯函数，不进 ToolBundle）

```
parse_markdown(md) -> list[Block]
  Block = {kind: heading|paragraph|bullet|table, level, text, items, rows}
  支持子集：# ## ### 标题 / 段落 / - * 要点 / **粗体** / [text](url) 链接 / |管道表|
  （报告 md 由自家管线确定性生成，结构受控；未知行降级为段落，绝不抛错）
  链接解析保留 (text, url) 二元组——docx 生成真超链接，pdf 加 link 注解，pptx 文本+URL。

render_docx(blocks, meta) -> bytes    # python-docx：标题样式/段落/要点/表格/超链接
render_pptx(blocks, meta) -> bytes    # python-pptx：见 §4 幻灯片策略
render_pdf(blocks, meta) -> bytes     # reportlab Platypus：注册 STSong-Light → 标题/段落/列表/表格/链接
```

- 每个渲染器**独立 import-guard**：库缺失 → 抛 `RendererUnavailable`（带缺哪个库），API 层转 503，
  绝不静默降级成空文件（诚实边界）。
- meta = `{title, topic, task_id, generated_at}`，用于页眉/文件名。

## 4. PPTX 幻灯片策略（md → 幻灯片必须有明确映射规则）

1. 封面页：报告标题 + 主题 + 日期；
2. 每个 `##` 章节一页：章节名作页标题，正文按要点化规则转 bullets；
3. 要点化：`- ` 行直入；段落按句切分，**每页 ≤6 条**，超出自动续页（标题加「(续)」）；
4. `## 引用 / 来源` → 末尾引用页（每条 `[id] title`，紧凑排版，每页 ≤10 条续页）；
5. 白底深字默认版式，不做主题美化（克制；主题化留待真需求）。

## 5. API 设计（对齐 API_SPEC 风格，新增 §2.x）

```
GET /api/v1/tasks/{task_id}/report?format=md|docx|pptx|pdf
```
| 项 | 规则 |
|---|---|
| 鉴权 | 同其余任务端点：`_owned_task(task_id)` 按 owner 隔离，越权一律 404 |
| 状态 | 仅 `done|escalated`；否则 409 `TASK_NOT_COMPLETED`（与审计导出一致） |
| format 校验 | 非四者之一 → 400 `UNSUPPORTED_FORMAT` |
| 渲染库缺失 | 503 `RENDERER_UNAVAILABLE`（如实报缺哪个库，不挂可用） |
| md | 直接回 `report_markdown`（text/markdown，凑齐全集） |
| 响应 | bytes + 对应 content-type + `Content-Disposition: attachment; filename="{topic-slug}-{task_id}.{ext}"` |

ext/MIME：`.docx`=application/vnd.openxmlformats-officedocument.wordprocessingml.document；
`.pptx`=application/vnd.openxmlformats-officedocument.presentationml.presentation；
`.pdf`=application/pdf；`.md`=text/markdown。

## 6. 顺手修复：审计导出端点接真 ZIP（同文件同模式的死代码）

调研发现：`server/audit_export.py` 有完整 `AuditExportGenerator`（`generate_zip()->bytes` + `get_filename()`，
audit_export.py:22,115-117），但 `server/api.py:844` `export_audit` **没接它**，返回 TODO 临时 JSON——
前端 `downloadAuditPackage` 按 ZIP blob 下载必然触发「审计包下载异常（非 ZIP）」。

修复：`export_audit` 改调 `AuditExportGenerator(task).generate_zip()` 返回真 ZIP。
⚠️ 接线时须核对属性路径：生成器读 `task.gate_reviews / task.engine_events / task.retrieval_records`
（audit_export.py:101-105），而端点临时 JSON 读 `task.routing_state.gate_review_history` 等——
两边 Task 形状不一致，接线时以真实 Task 模型为准适配 + 测试锁定，不迁就任何一边的臆想属性。

## 7. 前端设计

- `web/src/utils/download.ts`：新增 `downloadReport(taskId, format)`，复用现有 blob 模式
  （content-type 嗅探 + 小体积非预期类型给可读错误，同 `downloadAuditPackage` 的防御）。
- `web/src/views/TaskResult.vue`：「下载审计包 (ZIP)」旁加格式按钮组 **MD / DOCX / PPTX / PDF**，
  独立 loading 态，失败走 `taskStore.setError`。
- `web/src/services/taskService.ts`：加 `reportDownloadUrl(taskId, format)`。
- 模板 `output_format: string[]` 暂不联动按钮（纯展示现状保持；联动等真需求，避免过度工程）。

## 8. 依赖与部署

- `requirements.txt` 追加：`python-pptx>=1.0`、`python-docx>=1.1`、`reportlab>=4.0`。
- 容器生效须**重建镜像**（构建期 pip，无运行期安装）：boss 宿主跑 `docker compose up -d --build api`。
  纯 Python 三库，无 apt、不改基础镜像，重建代价低（走清华镜像 ARG）。
- 本地测试：三库装进托管 venv（`binaries/python/envs/default`），不动系统环境。

## 9. 测试计划（hermetic，零网络）

- `tests/test_doc_render.py`：parser 用例（标题/段落/要点/链接/管道表/引用节/未知行降级）；
  三渲染器 smoke（bytes 非空 + 用库自身重新打开验证结构完整）；**PDF 中文断言**（注册字体后
  bytes 内含 CJK 文本编码，防黑块回归）；pptx 续页规则（>6 要点出续页）。
- `tests/test_api_report_download.py`：404 越权 / 409 未完成 / 400 格式 / 503 缺库（monkeypatch 掐 import）/
  200 四格式 content-type 与 Content-Disposition。
- 审计修复回归：`export_audit` 返回真 ZIP（zipfile 校验成员含 report.md）。

## 10. 明确不做（防过度工程）

图表/图片嵌入（报告无图数据）· pptx 主题美化 · HTML/weasyprint 管线 · docx→pdf 转换链
· 引擎/Writer/ToolBundle 改动 · `output_format` 字段联动 · 流式渲染 · 渲染结果缓存落盘
（小文档毫秒级，缓存是无用复杂度）。

## 11. 实施任务拆分（每步过三道闸后并一步交付）

| # | 任务 | 产出 |
|---|---|---|
| OR-1 | parser + docx 渲染器 + 测试 | `tools/doc_render.py`（半）+ `test_doc_render.py`（半） |
| OR-2 | pptx + pdf 渲染器 + 测试 | 同上补全 |
| OR-3 | API 端点 + API_SPEC.md §2.x + 测试 | `server/api.py` + `test_api_report_download.py` |
| OR-4 | 审计导出接真 ZIP（含属性适配） | `server/api.py` `export_audit` |
| OR-5 | 前端下载按钮 + download util | `download.ts`/`taskService.ts`/`TaskResult.vue` |
| OR-6 | requirements + 依赖声明 + VERIFICATION/REVIEW 门禁 | `requirements.txt` + 两份门禁文档 |

## 12. 风险

| 风险 | 缓解 |
|---|---|
| reportlab 中文黑块 | §2-D2 锁定 UnicodeCIDFont + 专项测试断言 |
| LLM 正文 md 超出受控子集（如嵌套列表） | parser 未知行降级为段落，宁简不崩；测试覆盖 |
| audit 属性路径不一致（§6） | 接线以真实模型为准 + 回归锁定 |
| 独立审议配额（今日 429，19:50 重置） | 按硬约定：BLOCKED 如实标注，不自签；重置后补审 |
| 容器重建依赖 boss 宿主操作 | 交付清单明示 `docker compose up -d --build api` |
