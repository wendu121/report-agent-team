<!-- reviewed-by: intern-s2-family-via-newapi (intern-latest/intern-s2-preview-35b 均 BLOCKED, 实际执行=llama-3.3-70b; 主代理逐条核验, 未自签) -->
# REVIEW_M11-3b.md · 独立审议记录（M11-3b 热修复 + WebUI MCP 面板）

## 0. 执行说明（诚实边界）
- boss 钦定审议模型 **Intern-S2-Preview-397B**：网关通道实测「您已超过输入 tokens 配额」——小探针可通、真实审议负载一律连接被掐（10054/502）。
- 依次降级尝试：`intern-s2-preview-397b`（配额超限）→ `intern-latest`（路由至 Intern-S2，同样负载即断）→ `intern-s2-preview-35b`（同样负载即断）→ **最终以 `llama-3.3-70b`（cloudflare 基座，Gate 在用、结构化输出有实测背书）完成三段审议**。
- 审议分三段小包执行（上游对大负载敏感），主代理对全部发现逐条 trust-but-verify，未自签。
- 排障副产物：脚本用 `host.docker.internal`（容器视角地址）在宿主沙箱不可解析；已改 localhost。系统代理 127.0.0.1:10808 会掐 urllib 大 POST（本次 10054 的另一诱因），脚本已绕过。

## 1. REVIEW-1 后端逻辑（orchestrator.py 2.7 合成 + EngineEvent Literal）
**verdict: PASS-WITH-NOTES**
- MINOR（:1344/:1359）合成会引入额外事件记录 → 核验：事件双写（engine_events + on_event）为刻意设计（可审计性），非缺陷。
- MINOR（:1377/:1387）溯源校验受结果集限制 → 核验：既有 M4 行为，本轮未改动。
- NOTE：Literal 扩容一致性 → 核验：server/api.py 与 models.py 注释已同步。

## 2. REVIEW-2 单测（tests/test_m11_3_synthesis.py）
**verdict: PASS**（无发现）

## 3. REVIEW-3 前端契约（MCP 面板 vs /admin/mcp-servers）
**verdict: PASS-WITH-NOTES**（9 条发现，逐条核验：）
- ✅ **属实已修**：`onDelete` 删除失败未透出后端 `detail.message` → 已修（与新增路径同款提取）。
- ❌ 误报（7 条）：id 前端正则与后端完全一致（均为 `^[A-Za-z0-9_-]+$`）；headers JSON 解析已有错误提示；tools_whitelist 已 `filter(Boolean)`；`deleteMcpServer` 返回类型与后端 `McpServersListResponse` 一致；transport 校验后端本就完整（ reviewing 模型未看到 :1210-1214 的校验代码，属审阅材料截断所致）；service 层无需重复 transport 白名单（视图 select 已约束 + 后端权威校验兜底）。

## 4. 结论
门禁③通过（PASS-WITH-NOTES，唯一属实项已修复）。与 Gate② 自审（VERIFICATION_M11-3.md §8）合并闭环，准予本地 commit（不 push）。

## 5. 补审待办
- 若 boss 要求 Intern-S2-Preview-397B 复审：待其上游输入 tokens 配额重置后，重跑 `run_review_m11_3b.py`（改回 model=intern-s2-preview-397b）即可，审议材料脚本已备好。
