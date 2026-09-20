<!-- reviewed-by: independent-subagent -->

# 独立审议报告 · 最终交付增量（gate ③）

## 结论：PASS_WITH_NOTES

本人独立执行了全部复核项（测试复跑、容器验证、代码抽查、C-1 诚实性判断），结论为 **PASS_WITH_NOTES**：功能与交付增量真实可信，仅有 1 条 MINOR（交付说明与实际文件不符，无功能影响）。

## 一、实际执行的命令与输出摘要（真实数字）

1. **测试复跑**（指定 python 3.13.14 + `PYTEST_DEBUG_TEMPROOT` 指向新建纯 ASCII 目录，避开历史残留拦截）：
   ```
   collected 266 items
   ================= 266 passed, 61 warnings in 68.77s (0:01:08) =================
   ```
   与预期 **266 passed / 0 failed / 0 errors** 完全一致。`test_api_report_download.py`(14) 与 `test_doc_render.py`(15) 均全绿。

2. **容器依赖验证**（`docker exec report-api python -c "import docx,pptx,reportlab,asyncpg"`）：
   输出 `OK` —— 四类库在重建后的 `report-api` 容器内均可用。

3. **真实产物渲染冒烟**（`scripts/smoke_render_real.py`，容器内读 `/app/outputs/*.md`）：
   ```
   source      : /app/outputs/测试主题-20260911-064805.md
   docx : 42263 bytes magic_ok=True
   pptx : 39980 bytes magic_ok=True
   pdf  : 28470 bytes magic_ok=True
         STSong-Light registered in pdf = True
   RESULT: PASS
   ```
   PDF 确认内嵌 `STSong-Light` CID 字体，中文黑块问题已根治；且容器内确有真实研报产物（9414B），证明重建镜像已生效。

## 二、代码真实性抽查（本人独立判断）

- **import-guard 真实**：`tools/doc_render.py` 三渲染器均在函数内惰性导入；PDF 路径**显式先 `import reportlab` 顶层包**（line 331）再导入子模块，规避「子模块缓存使 guard 失效」的坑；docx/pptx 同样先导入顶层包（`from docx import Document` / `from pptx import Presentation`）。缺库时抛 `RendererUnavailable`，无静默空文件。
- **四错误码路径均可达且被测试覆盖**（`server/api.py:918 download_report`）：
  - 404 越权/不存在 → `_owned_task`（api.py:1574/1588；测试 `test_foreign_task_returns_404`）
  - 409 未完成 → 状态检查（api.py:927；测试 `test_running_task_returns_409`）
  - 400 格式非法 → 格式校验（api.py:938；测试 `test_unsupported_format_returns_400`）
  - 503 渲染器不可用 → `RendererUnavailable` catch（api.py:962；测试 `test_renderer_unavailable_returns_503_not_empty_file`）
  无「永远走不到」的死分支。
- **前端 token 修复真实**：`web/src/utils/download.ts:8-11` `authHeaders()` 从 `@/api/client` 取 token 并以 `Bearer` 注入；`downloadAuditPackage` 与 `downloadReport` 均带该 header，既有「裸 fetch 不带 token → 401」bug 已修。
- **conftest 真实隔离**：`tests/conftest.py` 通过 `monkeypatch.setattr(tenancy,"TENANTS_ROOT",tmp_path/tenants)` + `set_current_account` + `ensure_account_layout(seed_from_base=True)` 注入测试账号；**明确拒绝 `RAT_LEGACY_GLOBAL` 逃生阀**（注释 line 11），非假隔离。所有 266 测试在此隔离下通过，可信。
- **多租户契约 bug 修复真实**：`tools/experts.py:525` 改 `relative_to(_root())`、`tools/skill_importer.py:675` 改 `relative_to(_tenant_root())`，二者均带 `ValueError` 兜底退回绝对路径（原 `relative_to(BASE)` 在 TENANTS_ROOT 指向别处时抛错）。

## 三、C-1（pytest.ini 处置）诚实性判断：**诚实，非藏红灯**

- `pytest.ini` 仅新增 `testpaths = tests`，**未删除、未 skip** 根目录 `test_server.py`(3145B) / `test_service.py`(5494B)。两脚本仍保留于仓库根，可人工 `python test_server.py` 在活服务下执行。
- 经核实 `test_server.py:13` 确为裸 `async def test_rest_api()` 直连 `http://localhost:8000`（宿主未暴露该端口），非单测。原被 pytest 收集会稳定报 `async def ... not natively supported` 1 failed + 1 error —— 属「冒烟脚本被误当单测收集」，非被测代码缺陷。
- 处置为「限定 testpaths 让单测集合干净」+「保留脚本供手工冒烟」，而非掩盖。266 单测凭自身 merit 全绿，红灯未被隐藏。判定：**诚实**。

## 四、Findings

分级：BLOCKER / MAJOR / MINOR

- **MINOR-1**（交付说明不符）：任务交付描述称 `.gitignore` 新增了 `/tenants/` 与 `.pytest_tmp/` 两项，但 `git grep pytest_tmp` 在 `.gitignore` 中**无任何匹配**——仅 `/tenants/`（line 88）落地，`.pytest_tmp/` 条目缺失。功能无影响（pytest 临时根走 `PYTEST_DEBUG_TEMPROOT` 或 pytest 默认系统临时目录，`.pytest_tmp/` 从未被创建），属交付说明与文件不一致，建议补一行或更正说明。file: `E:/第二电脑/report-agent-team/.gitignore`(末尾，缺 `.pytest_tmp/`)。

（无 BLOCKER、无 MAJOR。）

## 五、审计范围说明

- 本轮交付 A（token 预算）/ B（输出渲染）已在上轮获 PASS_WITH_NOTES，本次作为增量环境复核一并确认其产物在重建容器内真实可用（docx/pptx/pdf 渲染 PASS + STSong）。
- 未做 git commit / push，未修改被测代码，仅产出本评审文档。

---
报告（≤250字）：结论 PASS_WITH_NOTES。本人独立复跑测试：**266 passed, 0 failed, 0 errors**（68.77s）；容器内 `import docx,pptx,reportlab,asyncpg` 返回 OK；真实产物冒烟 **RESULT: PASS**，PDF 内嵌 STSong-Light（中文黑块已根治）。代码抽查确认 import-guard 真实（PDF 显式先 import 顶层包）、四错误码(404/409/400/503)均可达且被测试覆盖、前端已带 Bearer token、conftest 真实租户隔离（未用 RAT_LEGACY_GLOBAL）、两处多租户契约 bug 修复真实。C-1 判定诚实：pytest.ini 仅限 testpaths，根冒烟脚本保留未删未 skip，非藏红灯。Finding 仅 1 条 MINOR（交付说 .gitignore 加了 `.pytest_tmp/` 实际缺失，无功能影响）。
