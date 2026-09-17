# VERIFICATION.md — LLM 接口 OpenAI 兼容 + 输入 token 预算闸

> 变更目标（用户指令）：把 LLM 接口改成通用 OpenAI 兼容模式 + 配 new-api 跑通真实端到端。
> 实查：仓库已有 `NewApiLLMClient`（本就 OpenAI 兼容），真问题是**缺输入 token 预算**导致 new-api 超窗 400 `input length too long`。本变更补预算闸 + 自愈，并实测跑通。

## 变更范围
- `orchestrator.py`（未提交，工作树改动）：
  - 新增模块级函数：`estimate_tokens` / `_trunc_keep_tail` / `fit_input_budget` / `_INPUT_TOO_LONG_RE` / `_is_input_too_long`
  - `NewApiLLMClient.__init__` 新增 `max_input_tokens: int = 128000`、`reserved_output_tokens: int = 4096`（可被 `LLM_MAX_INPUT_TOKENS` 覆盖）
  - `complete()` 与 `complete_with_tools()` 增加 `factor` 输入预算 + input-too-long 自愈（factor 最低 0.125，逐级减半）
- `scripts/verify_llm_openai_e2e.py`（新增）：真实 e2e 验证脚本

## gate ① 编译校验
- `py_compile orchestrator.py` → rc=0 ✓

## gate ② 真实 e2e 实证（搭配 new-api，脚本实跑输出）
- **[1] preflight**：`ok: True`，模型数 **120** → 证明 new-api 标准 OpenAI 兼容 + 网关连通正常
- **[2] 真实 complete**：首跑 `auto-chat` 短提问返回 47 字中文真回答（"LangGraph 是一种基于图结构的框架…"）。复跑时 `auto-chat` 通用别名背后的免费通道（aihubmix）触达「未充值账号仅 10 次」免费额度上限，网关返回 200 + 额度提示文案（非 400/非崩溃，代码优雅透传）。改用具体模型 `LongCat-2.0` 复测返回 66 字真回答（"LangGraph 是一个基于 LangChain 构建的…"）→ 证明 OpenAI 兼容路径真实出 token（额度问题属上游环境变量，非代码缺陷）。
- **[3] 预算压测**：超大 user 估算 **110001** token → `fit_input_budget` 截断到 **3908**（≤3900）；`complete()` 用 `max_input_tokens=8000` 跑通返回 103 字 → 截断 + input-too-long 自愈生效，**零 400 崩溃**
- **[4] estimate_tokens sanity**：中文 48 字→77（≈48×1.6）、英文 20 词→12（≈拉丁词+字符）→ 中英文计数合理

## 自审要点（主代理自查，非独立审议）
- **安全**：无硬编码密钥。`KEY` 经 env `NEWAPI_API_KEY` 或仓库 `.env` 读取；`.env` 已被 `.gitignore`，不会入库。
- **通用性**：`fit_input_budget` 与 input-too-long 自愈不依赖具体模型窗口大小 → 适配 new-api 背后 26+ 通道窗口各异。
- **不裁 tools**：`complete_with_tools` 自愈仅裁 system/user 文本，**绝不**自动剔除 `tools`/`tool_choice`（否则把"有工具依据的调用"偷偷降成凭空作答 = 制造幻觉）。
- **保序**：input-too-long 自愈先于 `_is_non_retryable` 黑名单判定（注释已说明，避免成为死代码）。
- **日志可见性**：已修 `root logger` 无 handler 问题（前序坑 16），自愈 `logger.warning` 会真实落盘。

## 已知限制
- `estimate_tokens` 为启发式近似（沙箱无 tiktoken），极端混合文本可能低估；自愈重试下限 `factor=0.125`（1/8），仍超则如实抛错。
- 仅验证 new-api 网关路径；离线 stub 路径未改动、未回归。

## 结论
gate ① ✓、gate ② ✓（真实 e2e 实证）。提交独立子代理审议（gate ③）。

## 复审后修复（gate ③ 笔记落实，reviewer=agent-768f2a15，verdict=PASS_WITH_NOTES）
独立审议提出 3 条笔记，均已落实并复验：

1. **`fit_input_budget` 漏裁门（#1，真 bug）**：原 user 截断门 `if usr_budget < max_input_tokens` 代数等价于 `sys_t > 0`，system 为空时超大 user 永不截断 → 400 无法自愈。改为 `if usr_t > usr_budget`（以 user 自身是否超预算为准）。**复验 [3b]：空 system + 110001 token 超大 user → 截断返回 188 字，零 400。已修。**
2. **负预算（#2，同根）**：`cur_max - reserved_output_tokens` 可能 ≤0 致负预算漏裁。改为 `budget = max(1, cur_max - reserved_output_tokens)` 夹紧下限。已修。
3. **factor 1/8 不可达（#3，文档/行为不符）**：默认 `max_retries=2` 仅 3 次尝试，factor 降到 0.25 即耗尽，0.125 被设但永不发出。改为 `for attempt in range(max(self._max_retries + 1, 4))` 保证 ≥4 次，1/8 可达。已修（注释同步）。

修复后 `py_compile` rc=0；e2e 四项全绿（含新增 [3b]）。`REVIEW.md` 顶部 `reviewed-by: independent-subagent` 与 verdict 维持，本文为落实记录，非主代理自签结论。
