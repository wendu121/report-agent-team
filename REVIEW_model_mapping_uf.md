> ⚠️ **状态：BLOCKED（第 1 轮）**
>
> **独立审议未执行。** 派出的独立审议子代理（gate ③）因 API 配额耗尽（HTTP 429
> 「您的使用量已超出频率限制，将在 2026-09-20 00:11:04 UTC+8 重置」）**未能启动**，
> 未产出任何审议内容。
>
> 本文件目前**不是**审议结论，**不含**任何 `reviewed-by` 标记。
> 按项目铁律：**严禁主代理自审自签顶替**，因此本里程碑在补审 PASS 之前
> **不得 `git commit`**。
>
> 待配额重置后需重新派独立审议者，按下文「补审待办」执行。

---

# REVIEW · 模型映射「可由 UI 控制 + 解析宽容且可见」（待补审）

**里程碑**：`model-mapping-uf`（档 1 + 档 2）
**设计 SoT**：`DESIGN_model_mapping_uf.md`
**自审**：`VERIFICATION_model_mapping_uf.md`（主代理自审，**非**独立审议）
**状态**：`BLOCKED — 独立审议未执行（429）`
**日期**：2026-09-19

---

## 补审待办（配额重置后，独立审议者请逐条执行并给出 `file:line` + 实测证据）

### 必须验证（按重要性排序）

1. **主账号零回退（最高优先级）**
   主账号 `9910ebbc-112f-4ef2-ad7b-ed2582999382` 的映射全部是**裸模型名**（`LongCat-2.0` / `llama-3.3-70b` / `glm-4.7`），
   其中 `llama-3.3-70b` / `glm-4.7` 在接口表里 **miss**、靠「回落默认端点 = new-api 网关」才能工作。
   请独立复核 `orchestrator.py::resolve_model_ref()`（约 447 行）的**新增消歧分支**不会把它们改道到别的端点，
   也不会让它们抛错。实测命令（容器内，绑主账号）：
   ```
   docker compose exec -T -e RAT_ACCOUNT_ID=9910ebbc-112f-4ef2-ad7b-ed2582999382 api python -c "<用 resolve_model_ref 逐条解析 model_mapping 的 model>"
   ```

2. **「裸名唯一归属」消歧是否有害（对抗性）**
   新逻辑：裸名在当前账号**唯一**归属某接口时自动采用。请构造反例论证风险 ——
   例如某账号同时配了 new-api 网关与一个自定义 provider，某模型名恰好只在该 provider 出现时，
   用户的**本意**是否可能被改道？给出结论或明确写「未证实」。

3. **`POST /api/v1/admin/models/preview` 不得泄漏密钥**
   实际调用并全文扫描响应体（`api_key` / `token` / `secret` / `Authorization`）。
   鉴权为 JWT（`server.auth.create_access_token`），可参考 `scripts/verify_gate_real.py` 的取 token 方式。

4. **UI 是否真·controller（宪法要求）**
   读 `web/src/views/settings/Models.vue`：确认 `base` / `model` 是**可写下拉**而非只读展示、
   候选真的来自账号作用域的 `GET /api/v1/models`、保存真的走 `PUT /api/v1/admin/models`，
   且不存在「看着能点其实没用」的假控件。

5. **主代理的「修复生效」声明是否属实**
   核对 `tenants/af28b675-e0b8-46ac-96e7-9e4b3e3f485a/.engine_state/793c258f-bab6-456a-a592-c88bb80d6581_output.json`
   的 `routing_state.gate_review_history`：`eval_score` 是否真为 0.85/0.9/0.9（而非 `None`）、
   `reason` 是否已不是「审核 LLM 不可用（降级放行）」。
   ⚠️ 该文件在排查过程中被复现脚本**覆盖过一次**（原内容为 `{"error": "'list' object has no attribute 'get'"}`），
   若认为证据链不足请如实写「未证实」。

### 允许质疑的自审结论

- `VERIFICATION_model_mapping_uf.md` §5 列出的「诚实边界」是否完整、有无粉饰；
- 「7 个失败测试全是环境缺包」的判定是否成立（应为 `tests/test_doc_render.py`，缺 `docx`/`pptx`/`reportlab`）；
- 是否有**本轮引入但未被发现**的回归（尤其 `_resolve` 返回值形态变更的影响面）。

### 范围提醒

工作树中还有**其他 worker 的未提交改动**（`chat_agent.py`、`tools/*`、`server/api.py`、
`server/engine_client.py`、`web/src/services/*`、`web/src/views/ChatEntry.vue` 等），
**不属于本轮**，请勿把其问题计入本里程碑，但可提示其存在。

---

## 签署位（补审后由独立审议者填写）

```
<!-- reviewed-by: 待补审 -->
```
