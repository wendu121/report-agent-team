<!-- reviewed-by: independent-subagent (gate-③) -->
<!-- 本文件由独立子代理签署，主代理不得自签 reviewed-by。验证命令与证据见各 gate 段落。 -->

**Review date: 2026-09-18**
**Reviewer: independent-subagent (gate ③)** —— 非主代理，独立复跑全部 gate，不采信 VERIFICATION_M12-4.md 的断言，所有结论均重新推导。

# M12-4 独立审议（Runtime Integration · gate ③）

## Verdict: **PASS_WITH_NOTES**

M12-4 的运行时接入目标（把 M12-1/2/3 建好的专家团 + 对话侧技能真正接进 `ChatAgent.step()`，并消费注册表 `expert.model`）**真实达成**：三道闸全过，核心缺口 A/B/C 已闭环，无 MAJOR/阻塞项。附 2 条 MINOR 观察（均非本次代码缺陷、非回归、非阻塞），已如实记录，未隐藏。

---

## 1. 改动清单与 git 状态（实测，非文档转述）

`git status --porcelain`（verified via `C:\Users\sfkj\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe`）：

| 状态 | 文件 |
|---|---|
| M | `chat_agent.py` |
| M | `tools/experts.py` |
| M | `tools/skill_importer.py` |
| ?? | `DESIGN_M12-4_runtime_integration.md` |
| ?? | `VERIFICATION_M12-4.md` |
| ?? | `scripts/verify_m12_e2e.py` |
| ?? | `tests/test_m12_runtime.py` |

与任务指派清单**完全一致**。

---

## 2. Gate ① 静态校验（独立复跑）

命令（托管 Python，非 venv——避免 asyncpg 缺失）：
```
C:\Users\sfkj\.workbuddy\binaries\python\versions\3.13.12\python.exe -m py_compile
    chat_agent.py tools/experts.py tools/skill_importer.py
    tests/test_m12_runtime.py scripts/verify_m12_e2e.py
```
**结果：`EXIT_CODE=0`**，无任何编译输出（无语法/编译错误）。✅

---

## 3. Gate ② 测试套件（独立复跑）

环境事实（按任务指派）：venv python `C:\Users\sfkj\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（含 asyncpg+pytest）；`PYTEST_DEBUG_TEMPROOT` 指向**全新空 ASCII 目录** `E:\第二电脑\report-agent-team\.pytest_review`（每次运行重建，规避 safe-delete 吞摘要）。

命令：
```
$env:PYTEST_DEBUG_TEMPROOT = "E:\第二电脑\report-agent-team\.pytest_review"
& venv\python -m pytest -q   (PYTHONPATH=E:\第二电脑\report-agent-team)
```
**结果（日志 `_review_pytest.log` 尾部）：**
```
278 passed, 61 warnings in 63.99s (0:01:03)
```
**退出码 0。** 与 VERIFICATION 声称的「278 passed / 0 failed / 0 errors」**逐字吻合**。其中 12 条为 `tests/test_m12_runtime.py` 新增用例（R1/R2/R3/R4/V8×3/V1×2/R5×2/R6），其余 266 条为既有套件，全绿、无回归。✅

---

## 4. Gate ③ 代码级核查（每一项都按真实 file:line 复证，不轻信 DESIGN）

| # | DESIGN_M12-4 §2 声明 | 实际代码 | 结论 |
|---|---|---|---|
| C1 | `_resolve_expert_context` 任何异常降级 `("", None)`，绝不冒泡（V8） | `chat_agent.py:272-276`（import 失败即 return `("", None)`）+ `chat_agent.py:300-302`（`except Exception → logger.warning + return "", None`） | ✅ 双重兜底，专家任何故障都不让 `/chat` 500 |
| C2 | `step()` 注入专家片段 + `build_skill_context("chat")`，位置在 `CHAT_SYSTEM_PROMPT` 之后、工具提示之前 | `chat_agent.py:410`（`_resolve_expert_context`）、`:413-419`（`build_skill_context("chat")`）、`:420` `system = CHAT_SYSTEM_PROMPT + expert_ctx + skill_ctx + _TOOL_HINT + ...` | ✅ 人设先于工具说明；V1 的 /chat 侧实证真正落地 |
| C3 | 消费 `expert.model`；用户显式指定时以用户为准 | `chat_agent.py:448` `chat_model = _resolve_chat_model(model or (expert_meta or {}).get("model") or None)`；`_resolve_chat_model` `:207` `if user_model and user_model not in ("auto","auto-chat"): return user_model` | ✅ 不留「能填但不消费」的假配置 |
| C4 | `skill_importer.VALID_ROLES` 含 `"chat"`；默认 `target_roles=["chat","researcher"]` | `tools/skill_importer.py:104` `VALID_ROLES = ("chat","researcher","analyst","writer")`；`:591` `"target_roles": target_roles or ["chat","researcher"]` | ✅ |
| C5 | `experts.register_expert` 对 `eid` 调 `_norm_id`（闭环 REVIEW_M12 残留 MINOR-1） | `tools/experts.py:335` `eid = _norm_id(eid)` | ✅ 与 M2 字面要求一致 |

**缺口闭环确认：**
- **缺口 A（专家团零调用）**：`chat_agent.py:273` 现 `from tools import experts as ex`，`read_expert`/`resolve_mention`/`route` 经 `_resolve_expert_context` 在 `step()` 中被真实调用 → 闭环。✅
- **缺口 B（V1 打折）**：`build_skill_context("chat")` 进入 `step()` 的 system，单测 `test_skill_context_reaches_chat` + 真实 e2e V1 双重实证 → 闭环。✅
- **缺口 C（REVIEW_M12 V8=partial）**：专家接入降级三测（损坏包 / team 型 / `list_experts` 抛异常）覆盖 → V8 由 partial 翻为闭环。✅

---

## 5. 真实网络 e2e（`scripts/verify_m12_e2e.py`，独立复跑）

脚本使用一次性 temp `TENANTS_ROOT`（`seed_from_base=True`），**未污染真实 `tenants/` 配置**。日志 `_review_e2e.log`：

| 步 | 结果 | 说明 |
|---|---|---|
| 真实 URL 导入 + /chat 可读（V1） | **PASS** | `status=pending_approval` → `accept` → installed，`target_roles=['chat','researcher']`，`/chat` 实测读到（ctx 5920 字符） |
| 重复导入幂等（V6） | **PASS** | 两次导入 id 一致（`imported-skill`） |
| 真实网页正文提取（V2） | **FAIL（脚本缺陷，非代码缺陷）** | `WEB_URL` 取的是 GitHub **仓库页** `https://github.com/langchain-ai/langchain`，`fetch_source` 将其识别为 GitHub 源并去找不存在的 `SKILL.md`（404），根本没走到「网页正文提取」。网络本身可达（V1 README 导入与 R1 均成功）。此步永不可能用这个 URL 验证 V2 |
| @专家 解析（R1） | **PASS** | `@financial-statement-analyst` → 1078 字符专家片段，`routed=False`（temp 租户从 base 播种了真实专家，证明确实能在运行时解析） |

**诚实结论：** 脚本 `RESULT: FAIL` 仅因 V2 步的 `WEB_URL` 选型不当（GitHub 仓库页而非普通网页），**与 M12-4 改动无关**（V2 测的是 `extract_main_text`，不在本次改动文件内）。M12-4 的核心运行时接线（V1 / V6 / R1）在真实网络下**均 PASS**。V2 真实网页提取仍属「未实测」——与 VERIFICATION_M12-4 §4 所记 SKIP 一致，未夸大。

---

## 6. 诚实边界确认（均为 EXPECTED，已文档化、未隐藏）

| 边界 | 状态 |
|---|---|
| 专家 `shape` 字段仍不消费研报流水线 | DESIGN_M12-4 §1 N1 / §3 表 + VERIFICATION_M12-4 §4 明示「留给 M12-5」，未静默忽略 ✅ |
| `expertType: team` 仍默认拒绝 | `tools/experts.py:171-177`（除非显式 `allow_team_downgrade`）；DESIGN §1 N3 + VERIFICATION §4 ✅ |
| V2 真实网页提取未实测 | 本节第 5 段及 VERIFICATION §4 明示 SKIP ✅ |
| 前端未展示「当前专家」 | 后端已返回 `expert` 字段；DESIGN §1 N4 明确本档不动 UI ✅ |

---

## 7. MINOR 观察（非阻塞、非回归、非本次缺陷）

1. **（MINOR，一致性缺口）** `tools/skills.py:70` 的 `VALID_ROLES = ["researcher","analyst","writer"]` **不含 `"chat"`**，而 `server/admin.py:2060` 从 `tools.skills` 导入该常量并在 `create_skill`（`:2134`，手动新建技能向导）用它校验 `target_roles`。后果：经 M12-4 importer 路径安装的技能带 `"chat"` 完全正常（importer 用自身 `VALID_ROLES`，且 `build_skill_context` 不校验角色白名单）；但**手动新建技能向导**既不会向 UI 提供 `chat` 选项（`admin_list_skills` 返回的 `valid_roles` 亦不含 `chat`），若强行传 `chat` 会被 400。DESIGN_M12-4 §2.2 明示「`skills.py` 无改」，故本缺口属作者有意留白、非回归，但为求「对话侧技能」在**所有**入口一致可达，建议后续把 `"chat"` 一并加入 `tools/skills.py` 的 `VALID_ROLES`。不构成 BLOCK。

2. **（MINOR，交付脚本缺陷）** `scripts/verify_m12_e2e.py` 的 `WEB_URL`（V2 步）使用 GitHub 仓库页而非普通网页，导致 V2 永远因「GitHub 源找不到 SKILL.md」而 FAIL，无法真正验证网页正文提取。属 M12-1 遗留脚本瑕疵，与 M12-4 代码无关；建议后续将 `WEB_URL` 改为白名单内、且**非 GitHub** 的真实网页（如某公开文档页）方能验证 V2。不构成 BLOCK。

---

## 8. 结论

三道闸：① 静态 `py_compile` 全 0 错（exit 0）；② 全量 **278 passed / 0 failed / 0 errors**（exit 0），与 VERIFICATION 逐字吻合；③ 独立子代理逐行复证 DESIGN §2 五项改动全部真实存在、缺口 A/B/C 已闭环。真实网络 e2e 的 V1/V6/R1 **PASS**，V2 因脚本 URL 选型不当未实测（非代码缺陷）。无 MAJOR、无阻塞项。

⇒ 总体结论：**PASS_WITH_NOTES**（2 条 MINOR 观察已如实记录，不影响 M12-4 运行时接入目标的达成与放行）。
