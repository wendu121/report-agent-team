<!-- 状态：BLOCKED（第 1 轮，2026-09-15 18:05 前后） -->
<!-- 独立审议子代理启动失败：`您已超过输入 tokens 配额`（同窗口此前还撞过 429 使用量限制，
     配额重置时间：2026-09-16 11:45:42 UTC+8）。本文件**不含任何 reviewed-by: independent-subagent 标记**
     —— 因为审议没有发生。主代理自审见 VERIFICATION_T47.md，仅供参考，不计入门禁。 -->

# REVIEW_T47 · 独立审议（未执行）

## 判定

**BLOCKED** —— 非代码问题，是审议者配额不可用。三次尝试记录：
1. 默认模型 ×2（前一轮会话）：`429 使用量已超出频率限制` / `Tool Agent not found`
2. Intern-S2-Preview-397B（boss 指定，本轮）：`您已超过输入 tokens 配额`

**三次失败不得据此推定 PASS 或 FAIL。** 改动（+约 400 行，7 文件）留在工作区，补审 PASS 前不 commit。

## 待审范围（对应 VERIFICATION_T47.md 六节）

| # | 文件 | 性质 | 高危问题（审议者必答） |
|---|------|------|------------------------|
| 1 | tools/skill_importer.py | 代码块 L2→L1 + discover_candidates + contents API 降级 | L2→L1 后是否存在任何代码被执行的路径？（追踪 skills/*.md → build_skill_context → system prompt 全链）repo path/branch 拼接有无路径遍历？ |
| 2 | orchestrator.py | LibreChat dropParams + 一次性自愈 | 自愈是否有界（每调用 ≤1 次）？tools/tool_choice 排除是否在 complete 与 complete_with_tools 两路都严密？drop_params 三级 fallback 的空值判定是否正确？ |
| 3 | tools/skills.py | 注入预算（片段 6000/总 30000） | 截断标记是否真实可见？被丢弃技能是否 warning 点名而非静默？ |
| 4 | tools/web_fetch.py | fetch_url 的 contents API 降级 | 是否复用 SSRF 防护（_http_get 内 _host_blocked）？api.github.com 是否纳入同一防护？总预算 45s 是否仍成立？ |
| 5 | chat_agent.py | 纯提示词 | 是否与 SSRF 边界陈述、JSON 研报协议冲突？ |
| 6 | tests | 新增 10 + 迁移 3 断言 | 迁移是否诚实（旧 L2 断言是否全部挂到真实执行信号上，有无漏网）？ |

## 补审待办（下轮审议者照此执行）

1. `git -C "E:/第二电脑" diff -- report-agent-team` 通读全部改动，对照 VERIFICATION_T47.md 逐节核实，引用 file:line。
2. 重点核实第 1 行的安全问题：确认 `tools/skills.py` 无 exec/subprocess/eval，且无其他模块读取 skills/*.md 后执行。
3. 用托管 venv python（`C:/Users/sfkj/.workbuddy/binaries/python/envs/default/Scripts/python.exe`，**不是裸 python**——
   裸的缺 ruamel 会出 6 条环境误报，浪费一轮）跑：
   `PYTHONPATH=E:/第二电脑/report-agent-team <venv-python> -m pytest tests/test_m12_skill_importer.py tests/test_llm_drop_params.py tests/test_tools.py -q`
   期望 13 + 10 + 57 全过；全量 216 passed。
4. 若发现 BLOCKER/MAJOR：写明 file:line 与修法建议，**不要**自己动手改代码。
5. 审议完成后在本文件顶部加 `<!-- reviewed-by: independent-subagent -->` 并改判定为 PASS / PASS_WITH_NITS。
6. 提醒：`tax-strategist` 的 target_roles=researcher 是 LLM 自主决定的，boss 可能想改成 analyst——审议时不必当缺陷，但值得在报告里提一句。
