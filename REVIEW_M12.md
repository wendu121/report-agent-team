<!-- reviewed-by: independent-subagent -->
**Review date: 2026-09-12**

# M12 复审（独立 Subagent · gate ③）

## 总体结论：PASS（gate ③ 放行）

二次复测 5 项原 BLOCKED 发现：M2 / m1 / m4 / m5 维持**真实修复**；原残留 MAJOR **M1 现已闭环**——`_proposal_path`（tools/skill_importer.py:613）已改为 `return PROPOSAL_DIR / f"{_norm_id(pid)}.json"`，`save_proposal` / `get_proposal` / `accept_proposal` / `reject_proposal` 全部经此单一入口，用户可控 `pid` 在触盘前一律过 `_norm_id`，路径遍历（任意文件读取 + 任意文件删除，含经 HTTP 端点 `admin.py:2470/2486` 可达者）被关闭。回归测试 `test_malformed_id_path_traversal_blocked` 亦已消除假阳性（改 `p.resolve().is_relative_to(PROPOSAL_DIR.resolve())` 并新增 BASE 根陷阱文件断言 `get_proposal("../../escaped") is None`）。依审查准则「所有仍用原始用户可控 id 拼路径且未过 `_norm_id` 的路径构建点 = 残留 MAJOR → BLOCKED」，M1 已无残留，故翻为 PASS。

测试全绿（M12 三件套 19 passed；全量 174 passed，44 warnings，0 failed），M1 用例为**真实有效**拦截（见下）。

---

## 逐项核验（含精确 file:line）

### M1 · skill 提案路径遍历 —— 已修复（FIXED）
- ✅ **`_proposal_path` 已规范化**（tools/skill_importer.py:612-613）：
  ```python
  def _proposal_path(pid: str) -> Path:
      return PROPOSAL_DIR / f"{_norm_id(pid)}.json"  # 防路径遍历（DESIGN §6.2）
  ```
  **所有触盘调用方**均经此单一入口（用户可控 `pid` 在拼路径前一律过 `_norm_id`）：
  - `save_proposal` `:616-619` → `_proposal_path(pid)`（pid 已 `_norm_id`）
  - `get_proposal` `:638-642` → `p = _proposal_path(pid)`（任意文件**读取**已受控）
  - `accept_proposal` `:645-654` → `get_proposal(pid)` 与 `_proposal_path(pid).unlink()`（任意文件**删除**已受控）
  - `reject_proposal` `:658-664` → 同上
- ✅ **`_norm_id` 定义**：tools/skill_importer.py:397-402（经 `_slugify` 剥离 `/ . ..` 及非法字符），畸形 `id "../../evil"` 恒变为 `evil`，落盘/读/删均在 `PROPOSAL_DIR` 内。
- ✅ **回归测试已消除假阳性且真实有效**：`tests/test_m12_skill_importer.py:137-148`：
  - `:140-143` 对 `"../../evil"`, `"../evil"`, `"a/../../b"`, `"..\\..\\evil"` 断言 `p.resolve().is_relative_to(si.PROPOSAL_DIR.resolve())`（`resolve()` 真实解析 `..`，**非**词法判断）。
  - `:144-147` 在 BASE 根放陷阱文件 `tmp_paths/escaped.json`，断言 `si.get_proposal("../../escaped") is None`——证明恶意 pid **读不到**白名单外陷阱。
  - 若 `_proposal_path` 未规范化，`get_proposal("../../escaped")` 会解析到 BASE 根陷阱 `escaped.json`（`PROPOSAL_DIR/../../escaped.json` → `tmp_paths/escaped.json`），内容 `"SECRET-OUTSIDE"` 非合法 JSON 会抛 `JSONDecodeError` 或返回非 `None`，用例**必 FAIL**。该用例通过 ⇒ 遍历已闭。
- ✅ **HTTP 可达面收敛**：`server/admin.py:2470-2480` `admin_accept_skill_proposal` 与 `:2486-2496` `admin_reject_skill_proposal` 传入的 `{pid}` 现经 `accept_proposal`/`reject_proposal` → `_proposal_path` 统一 `_norm_id`，无法 `unlink` 白名单外文件；`get`（列表/详情）同理。
- **实证**（独立复测）：M12 三件套 19 passed（含本用例）；全量 174 passed，0 failed。

> M1 闭环断言：单一入口 `_proposal_path` 在拼路径前对 `pid` 调 `_norm_id`，且所有 `get/accept/reject_proposal` 均以同一入口拼路径，无任何调用方再以原始 `pid` 触盘。已满足。

### M2 · 专家转化路径遍历 —— 已修复（FIXED）
- `tools/experts.py:396` `_expert_proposal_path`：`return PROPOSAL_DIR / f"{_norm_id(pid)}.json"`（内部即规范化）。
- `:402` `save_expert_proposal`：`pid = _norm_id(proposal.get("id") or "imported-expert")`。
- `:433` `_write_package`：`eid = _norm_id(proposal["id"])`；`:437` `root = EXPERT_DIR / eid`；`:439-440` 额外硬拒 `".."`/`/`/`\`：`if ".." in eid or "/" in eid or "\\" in eid: raise ExpertError(...)`。
- ⚠️ 轻微不一致（非遍历）：`register_expert`（`:295-341`，尤其 `:328` `entry["id"] = eid`）未对 `eid` 调 `_norm_id`，把请求传入的原始 `id` 以字符串存入注册表；但该函数**不**以 `eid` 拼路径（路径取自已规范化的 `dir_rel`），`read_expert`（`:131-133`）亦以 `dir` 解析，故无越界写。建议补 `_norm_id` 以与 M2 字面要求一致，但不构成 MAJOR。

### m1 · 内联命令未被判 L2 —— 已修复（FIXED）
- `tools/skill_importer.py:266-267` `_L2_SIGNALS` 含行首命令式信号：
  ```python
  (r"(?m)^\s*(python|python3|bash|sh|pwsh|powershell|node|npm|pip|pip3)\s+[\w\-./\\]",
   "行首命令式代码调用（首批不执行外部代码，直接拒绝）"),
  ```
- 回归 `test_inline_command_classified_l2`（`:126-134`）对 `python train.py`/`bash ./setup.sh` 均判 L2，散文中的 "python" 不误判。✅

### m4 · team 专家自动降级绕过 §5.6 —— 已修复（FIXED）
- `server/admin.py:2611`：
  ```python
  "allow_team_downgrade": bool(payload.allow_team_downgrade),
  ```
  `or is_team` 已**移除**（对照原 `:2603` `is_team` 仅用于 `shape`/`expert_type` 推导，不再强开降级）。team 包除非请求方显式 `allow_team_downgrade: true`，否则在 `read_expert`（experts.py:143-148）按 §5.6 默认拒绝。✅
- 回归 `test_team_expert_rejected_by_default`（experts.py 测试）覆盖。✅

### m5 · 网页 100KB 截断未实施 —— 已修复（FIXED）
- `tools/skill_importer.py:181-182`：
  ```python
  if len(extracted) > 100_000:  # DESIGN §3.1：超 100KB 截断
      extracted = extracted[:100_000]
  ```
  ✅

---

## 回归测试存在性与通过性
- `tests/test_m12_skill_importer.py`：`test_malformed_id_path_traversal_blocked`（存在，**现真实有效**：resolve()+BASE 根陷阱）、`test_install_spec_malformed_id_contained`（存在）、`test_inline_command_classified_l2`（存在）——**均通过**。
- `tests/test_m12_experts.py`：`test_malformed_id_proposal_path_traversal_blocked`（存在，且因 experts 侧 `_expert_proposal_path` 真正规范化，该用例为**真实有效**）、`test_write_package_malformed_id_contained`（存在）——**均通过**。
- 运行：`tests/test_m12_skill_importer.py + test_m12_experts.py + test_m12_admin_api.py` → **19 passed**。全量 `tests/` → **174 passed，44 warnings，0 failed**。

---

## 验收矩阵 V1–V8（保留，状态更新至 2026-09-12）

| # | 场景 | 结论 | 证据 |
|---|---|---|---|
| V1 | L0 自动落盘且引擎真实可读 | ✓ | `test_l0_install_and_engine_reads` 断言 `build_skill_context("researcher")` 含「【已启用技能】」；`tools/skills.py` 注入对应 role |
| V2 | 网页 L1 → 提取 → 审批 → 生效，不残留 HTML/脚本 | partial | `extract_main_text` 去噪后落盘为纯文本；真实 HTML 提取路径无单测。m5 截断已修复 |
| V3 | URL 转化生成专家包 | ✓ | `test_convert_proposal_accept` + `test_experts_endpoints`；`experts/financial-statement-analyst/` 真实存在 |
| V4 | 回滚 | ✓ | `test_l0_install_and_engine_reads` / `test_import_l0_via_api`（rollback 后列表消失） |
| V5 | 恶意/畸形输入拒绝、零落盘、不污染配置 | ✓ | M2/m1/m4/m5 已修复；**M1 已闭环**：`_proposal_path`（skill_importer.py:613）统一 `_norm_id`，`get/accept/reject_proposal` 全经此入口；回归 `test_malformed_id_path_traversal_blocked`（:137-148，resolve()+BASE 根陷阱）真实拦截越界读 |
| V6 | 重复导入幂等 | ✓ | `test_idempotent`；`_idempotent`（skill_importer.py:472-488） |
| V7 | 路由误判保护（低置信回退通用 ChatAgent） | ✓ | `route()`（experts.py:259-289）`best<2` 或并列第一返回 `None`；`test_route_fallback_when_low_confidence` |
| V8 | 工具/专家 API 超时或鉴权失败 → 诚实降级，/chat 不 500 | partial | 管理端对 `SkillImportError`/`ExpertError` 返回 400/404；M12 未接入 orchestrator 运行时 |

---

## 残留关注点（非阻塞）
1. **（MINOR）** `register_expert`（experts.py:328）未对 `eid` 调 `_norm_id`，注册表 `id` 字段存原始字符串；无越界写（路径取自已规范化的 `dir_rel`），建议与 M2 字面要求一致，不构成 MAJOR。
2. **（NIT，沿用）** dev 模式 `_require_admin` 未配置 `ADMIN_TOKEN` 即放行；生产前须强制 `ADMIN_TOKEN`（属部署配置，非本次代码遍历缺陷）。

---

## 结论
M2 / m1 / m4 / m5 维持**真实且经测试验证**修复；原残留 MAJOR **M1 已闭环**——`_proposal_path`（tools/skill_importer.py:613）统一 `_norm_id`，`get/accept/reject_proposal` 全部经此入口，路径遍历（读/删，含 HTTP 端点可达面）被关闭，且回归测试已消除假阳性（resolve()+BASE 根陷阱，真实拦截）。5 项发现（M1/M2/m1/m4/m5）全部 FIXED，无残留 MAJOR/NINOR 阻塞项 ⇒ 总体结论翻为 **PASS**。
