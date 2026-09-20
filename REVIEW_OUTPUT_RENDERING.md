<!-- reviewed-by: independent-subagent -->

## Verdict

PASS_WITH_NOTES

## Findings

### BLOCKER
（无）

### MAJOR
（无）

### MINOR

- **M1 · 渲染器无显式输入体积上限** — `tools/doc_render.py`
  `parse_markdown()`（L61-125）与三个渲染器均无对 `report_markdown` 体积/块数的上限；
  `render_pdf()`（L326-389）把全部 blocks 一次性装进 `story` 再 `build()`。正常研报由引擎确定性生成、
  体量受控（通常数百 KB），此点不构成立即风险，但若出现病理级超大 md 仍可能拖慢/吃内存。
  与「诚实边界」精神一致，建议在入口加一个防御性体积/章节数上限（超限即 `RendererUnavailable` 或 413），属锦上添花，非阻断。

- **M2 · `export_audit` 在 `report_markdown` 为空时不产出 `report.md`** — `server/audit_export.py:101`
  `if self.task.report_markdown:` 才写 `report.md`。对 `escalated` 且无终稿的任务，ZIP 仍合法
  （其它成员齐全、`validate_audit_package` 不强制 `report.md`），但消费方若假定必有 `report.md` 会扑空。
  状态闸已要求 `DONE/ESCALATED/ABORTED`，影响面小；如需更稳，可在空时仍写一份占位 `report.md`（如「（本报告无终稿）」）。

- **M3 · 引用链接 URL 正则截断含 `)` 的 URL** — `tools/doc_render.py:26`
  `_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")` 的 URL 段 `[^)\s]+` 遇 `)` 即止
  （如维基消歧义链接 `...(disambiguation)`）。自家管线生成的引用 URL 均为简洁直链，实测样例
  `https://a.com/1` 不受影响（`test_parse_citation_section_keeps_links`/`test_render_docx_hyperlink_for_citations`
  通过），故为已知子集内低风险；若未来 URL 形态变化需放宽。

- **M4 · `_ApiTaskView` 对畸形 `TaskResponse` 宽容降级（非崩溃、亦非假成功）** — `server/audit_export.py:46-80`
  全程 `getattr(..., None)` + `or []`，`routing_state`/`user_task` 为 `None` 时产出空列表而非报错，
  ZIP 仍带 `metadata.json` 且可被 `validate_audit_package` 校验。这不是「静默冒充成功」——下载的仍是真实 ZIP、
  端点不会返回 200 空壳；仅是该极端内部态下某些成员为空。风险低，记录备查。

## Notes

- **正确性 — markdown 解析**：`parse_markdown` 对标题层级、`-`/`*` 要点成组、段落合并、
  管道表分隔行剔除（`_is_table_sep` L47-49，已将 `| --- | --- |` 正确识别为分隔）、未知行降级段落均按设计实现，
  `test_parse_*` 系列（L43-78）逐条断言行为而非仅确认存在。

- **正确性 — pptx 分页**：封面+每 `##` 章节一页；要点 >6 续页「（续）」、引用每页 ≤10（`server/api.py` 设计 §4，
  实际在 `tools/doc_render.py:287-300` + `cap=10 if is_cite else 6` L288）；`test_render_pptx_*` 用 15 条 / 23 条
  实测续页与页数，证明行为。

- **正确性 — PDF 中文**：`render_pdf` 先 `import reportlab`（修复子模块缓存绕过 import-guard，L328-339），
  再 `pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))`（L343-347，失败即 `RendererUnavailable`）。
  `test_render_pdf_is_real_pdf_and_registers_cjk_font` 断言 `b"%PDF"` 与 `b"STSong-Light"` 均在字节流中，
  黑块回归被专项锁定。

- **安全 — 租户隔离**：`download_report`（`server/api.py:918`）与 `export_audit`（L852）均先 `_owned_task(task_id)`
  （L925 / L862）；`_owned_task`（`server/api.py:1564-1591`）对内存态与文件态都按 `owner_id` 校验，越权一律
  `HTTP_404_NOT_FOUND`（不返 403，防枚举探测）。`test_foreign_task_returns_404` 验证接线。路径穿越：
  ZIP 成员名均为固定字符串、`_ApiTaskView` 仅读属性，无用户可控路径参与。

- **诚实边界 — 缺库响亮失败**：三个渲染器 import-guard 抛 `RendererUnavailable`（`tools/doc_render.py:30,156-157,230-231,338-339`）；
  端点 `except RendererUnavailable` → 503 `RENDERER_UNAVAILABLE`（`server/api.py:962-971`），绝不返回空字节。
  `test_renderer_unavailable_returns_503_not_empty_file` 验证 503 + 有内容错误体；`test_doc_render.py:149-157`
  用 `sys.modules[mod]=None` 验证三种库缺失均抛。

- **重试循环（曾回归区）已正确收敛**：`orchestrator.py:559-615` 与 `:632-682` 的减半走**独立有界预算**
  `_MAX_HALVINGS = 3`（`halvings < _MAX_HALVINGS`，L590 / L659），循环条件 `attempt <= self._max_retries + halvings`
  （L565 / L636）。因 `attempt` 单调递增、`halvings` 封顶 3，循环必然终止；通用重试 `max_retries=2` 未被放大，
  持续报错最终 `raise LLMError`（L615 / L682），「自愈有界、持续报错必须最终失败」语义保持。
  `tests/test_llm_drop_params.py::test_heal_bounded_to_once`（L140-152）仍绿，确认此前「放宽到 ≥4 次」的回归已修复。

- **多租户契约**：`tools/skill_importer.py:_backup`（L664-677）与 `tools/experts.py:_write_package`（L521-527）
  已把写死的 `relative_to(BASE)` 改为 `relative_to(_tenant_root())` / `relative_to(_root())` + `ValueError` 兜底绝对路径，
  diff 已核对，无新引入的硬编码 `BASE` 资源路径（`BASE` 仅在 `tenancy` 不可导入时作回落，合理）。
  `tests/conftest.py` 仅以注释形式**明确拒绝** `RAT_LEGACY_GLOBAL` 逃生阀（L11），实际未使用该阀——符合契约。

- **测试质量**：`test_doc_render.py`（16 例）与 `test_api_report_download.py`（14 例）均为「证明行为」型——
  重开文件校验结构（docx 真超链接写入 `word/_rels`、pptx 真续页、pdf 真 CID 字体字节）、断言 MIME/魔数/
  `Content-Disposition` RFC5987、`404/409/400/503` 错误体，非仅确认存在。

- **全量实证**：在托管 venv（`C:/Users/sfkj/.workbuddy/binaries/python/envs/default`，reportlab 5.0.1 / python-docx / python-pptx 均装）
  以 `PYTHONPATH="E:/第二电脑/report-agent-team" python -m pytest tests -q -p no:warnings` 运行，
  结果 **266 passed**（与 VERIFICATION_OUTPUT_RENDERING.md 声明一致），exit 0。

- **前端**：`web/src/utils/download.ts` 新增 `downloadReport`（L63-79）并统一走 `authHeaders()`（L8-11），
  修复了旧 `downloadAuditPackage` 漏 Bearer token 的同源缺陷（鉴权开启后下载不再 401）；小体积非预期响应
  读文本给出可读错误，与审计包同策略。
