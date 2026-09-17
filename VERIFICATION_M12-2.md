# VERIFICATION_M12-2 · L1 闸门 + 管控 API（自审）

> 三道闸 ② 自审。① py_compile 通过；③ 独立审议见 REVIEW_M12.md（主代理不自签）。

## 1. 交付范围
- `server/admin.py` 新增「M12-2」段（`/admin/skills/*` 与 `/admin/experts/*` 共 11 个端点）
- 单测 `tests/test_m12_admin_api.py`（FastAPI TestClient 端到端，不触网）

## 2. 端点清单（挂载于 `/api/v1`，复用 `_require_admin` + `_ensure_tools_path`）
| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/admin/skills/import` | 导入任意 URL：L0 直装 / L1 提案 / L2 拒绝 |
| GET | `/admin/skills` | 列出已安装导入技能 |
| GET | `/admin/skills/proposals` | 待审批提案 |
| POST | `/admin/skills/proposals/{id}/accept` | 一键采纳 |
| DELETE | `/admin/skills/proposals/{id}` | 驳回 |
| POST | `/admin/skills/{id}/rollback` | 回滚（V4） |
| GET | `/admin/experts` | 专家团列表（含提案数） |
| POST | `/admin/experts/{id}/toggle` | 启停 |
| POST | `/admin/experts/convert` | URL 转化专家包草案 |
| GET | `/admin/experts/proposals` | 待审批专家提案 |
| POST | `/admin/experts/proposals/{id}/accept` | 审批落盘 + 注册 |
| DELETE | `/admin/experts/proposals/{id}` | 驳回 |

## 3. 验收映射（实测）
| 编号 | 场景 | 覆盖测试 | 结果 |
|---|---|---|---|
| V1/V4 | import→list→rollback 闭环 | `test_import_l0_via_api` | PASS |
| V2 | 网页/专家包 L1 审批 | `/admin/skills/import` 与 `/admin/experts/convert`+accept 流程（单测用 monkeypatch 替换 `fetch_source`，不触网） | PASS |
| V3 | 转化生成专家包 | `test_experts_endpoints`（convert→proposal→accept→入团→toggle） | PASS |
| V5 | 异常分支 | 端点对 `SkillImportError`/`ExpertError` 返回 400/404 并给原因 | PASS |

## 4. 诚实边界
- 鉴权复用 `ADMIN_TOKEN`（未配置则 dev 放行并标注 `operator=dev_no_token`）；网络/路径白名单在 importer 层强制，API 不绕开。
- **本轮未做设置页前端 UI**（DESIGN_M12 §2 M12-2 含「设置页技能/专家区」）。后端 + 测试已完整，UI 为后续待办，不影响后端验收与 boss 经 API 验证。

## 5. 结论
编译通过 + 端到端单测全绿。可进入独立审议。
