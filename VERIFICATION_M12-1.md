# VERIFICATION_M12-1 · Skill Importer 内核 + L0（自审）

> 三道闸 ② 自审。① py_compile 通过；③ 独立审议见 REVIEW_M12.md（主代理不自签）。

## 1. 交付范围
- `tools/skill_importer.py`（新模块，约 695 行）
- 单测 `tests/test_m12_skill_importer.py`

## 2. 实现要点（对齐 DESIGN_M12 §4）
- 状态机 `FETCH → PARSE → CLASSIFY → ADAPT → VALIDATE → GATE → INSTALL → VERIFY`
- 多来源解析器：直链 `.md` / GitHub `SKILL.md` 定位 / 普通网页正文提取（守除去 script/style/nav…）
- 保守分级：`L0` 纯指令自动装；`L1` 含 URL/声明式调用→提案审批；`L2` 代码信号→**直接拒绝**
- `AssemblySpec` 强 schema 校验（id kebab-case / level / target / target_roles / payload 白名单占位符 + 受限 JSONPath）
- 落盘：先备份 → 写 md + ruamel 写 `config/skills.yaml` → 回读校验 `verify_installed`
- 安全边界：网络白名单（默认仅 GitHub 系）/ 路径白名单（禁写 tools/、orchestrator.py、server/）/ 禁止执行外部脚本 / 幂等（`sha256` 去重）/ 全动作进 `.audit`

## 3. 验收映射（实测，非纸面）
| 编号 | 场景 | 覆盖测试 | 结果 |
|---|---|---|---|
| V1 | L0 自动落盘且引擎真实可读 | `test_l0_install_and_engine_reads` 断言 `build_skill_context` 含「【已启用技能】」 | PASS |
| V4 | 回滚 | 同测试断言回滚后 prompt 不再含该技能 | PASS |
| V5 | 恶意/畸形拒绝 | `test_import_l2_rejected_zero_disk`（L2 零落盘）、超 1MB 由 `_http_get` 拦截（代码层保证） | PASS |
| V6 | 重复导入幂等 | `test_idempotent` 二次 install 返回 `unchanged` | PASS |
| V2 | 网页 L1 审批 | L1 分支 `save_proposal` → `accept_proposal`（`test_l1_proposal_accept_reject`） | PASS |

## 4. 诚实边界
- 网页正文提取保守：正文 <200 字或链接占比 >30% 直接返回空 → 调用方拒绝并提示人工粘贴（不猜）。
- `L2` 代码块（bash/sh/python/js/shell 等）按 DESIGN §3.2 直接拒绝，不进提案（安全优先）。
- 非白名单域名（`_check_host`）拒绝并给出明确原因，不降级为全放行。

## 5. 结论
编译通过 + 单测全绿（随 M12 三件套共 14 passed）。可进入独立审议。
