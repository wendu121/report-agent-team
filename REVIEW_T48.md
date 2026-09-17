<!-- 本文件由主代理在独立审议子代理不可用时代为占位；顶标 reviewed-by 在补审 PASS 前不得添加 -->
# REVIEW_T48 — 独立审议状态：BLOCKED

**状态**：独立审议子代理（Intern-S2-Preview-397B 及默认模型）调用即报 `429 您的使用量已超出频率限制`
（重置窗口 09-16 11:45 UTC+8 已过，但本会话子代理通道仍 429，疑似冷却未完全释放）。

**主代理未自签**：未添加 `<!-- reviewed-by: independent-subagent -->`，未 `git commit`。改动（含 T47 未提交修复 + T48 search_many 重试）
暂留工作区，待独立审议 PASS 后再提交。

## 待独立审议补审清单（T48 批）

1. **search_many 瞬时光键重试**（`tools/data_sources.py`）：
   - 核实 `_is_transient` 光键集合是否覆盖实际抖动形态（SSL UNEXPECTED_EOF / handshake timeout / Connection reset）。
   - 核实 `_search_with_retry` 仅重试 `ToolError` 且只拦瞬时光键，**不掩盖**持久错误（403/解析失败/coming_soon）。
   - 核实 `search_many` 的 `ex.submit(_search_with_retry, ...)` 未破坏结果顺序/并发语义（jobs 仍按提交序收集）。
   - 确认重试退避（0.8/1.6s × 3）在 `timeout=20` 单源预算内不会导致最坏情况超 `ChatAgent` step 超时。

2. **断言性结论核验**：
   - 核实「前端 36 处调用 ↔ 后端 81 端点对齐、无悬空端点」是否真（参数名差异 `{task_id}` vs `${id}` 已逐段归一化核对）。
   - 核实「experts 前端零入口」：确认 `web/src` 确实 0 处 expert 引用、路由无 `/experts`、AssetCenter 非 experts 页。
   - 核实「字段缺失为虚惊」：确认前端用 `status`/`channel_type`/`expert_type`，后端 `_plugin_item`/`_channel_item`/`read_expert` 返回一致。

3. **环境诊断不可作为代码判据**：确认「研报未产出 = 容器出网 SSL 持续故障窗口」结论基于探针（4 源全 15s SSL 超时）+ 历史成功 output 佐证，非单次日志臆断（对齐 MEMORY 坑 15）。

4. **测试**：216 passed 零回归（py_compile + 全量 pytest，忽略 api_boundary / m12_admin_api 两个需鉴权边界集）。

## 补审通过前禁止
- 不得 `git commit` T47/T48 任何改动。
- 不得声称「已通过独立审议」。
