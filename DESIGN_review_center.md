# DESIGN · 统一审核中心（Review Center）

> **状态**：v0.1 草稿（待老板评审，评审通过前不落地任何 `*.py` / `*.vue` 业务实现）
> **纪律**：先出设计文档，不写代码。
> **日期**：2026-09-20
> **作者**：AI 代理（团队 lead），按老板「审核在我手上」诉求起草

---

## 0. 元信息

| 项 | 值 |
|----|----|
| 项目名 | report-agent-team |
| 路径 | `E:\第二电脑\report-agent-team\` |
| 关联 SoT | `DESIGN.md` v2.3+；edict-gate 三道门禁 |
| 触发诉求 | 老板：「审核在我手上」——所有放行决策走 UI 人工点击；AI 代理只出材料、不拍板、不自动 push |

---

## 1. 目标与范围

**输入**：三类待老板人工审核的对象——
1. 技能提案（skill proposal，待 `accept`）
2. 专家提案（expert proposal，待 `accept`）
3. 已过三道闸、待 push 的代码变更集（pending-push change-set）

**输出**：一个 admin-only 的「审核中心」单页，把上述三类汇成一张可审核清单，老板逐一**通过 / 打回**。

**核心前提（已确认）**：
- 审核（放行）决策权**永远在老板**，UI 是决策面。
- AI 代理负责干活 + 出独立审议材料，**不**自签、不自动 push。
- 容器内**不能** `git push`（无宿主仓库、无凭证）→ 待推送变更的「批准」= 记录决策，真正 push 由宿主 AI 代理经系统 git 执行（见 §2、§9）。

**非目标（本版）**：不在容器内实现 git push；不做多人审核流；不做审批历史库（manifest 即留痕）；不做 IM/邮件通知。

---

## 2. 治理模型（决策 vs 执行分离）

```
          ┌─────────────────┐
   老板 ──▶│  审核中心 UI    │  决策：通过 / 打回  （人工点击）
          └────────┬────────┘
                   │ 通过(记录决策)
                   ▼
          ┌─────────────────┐         ┌──────────────────────────┐
          │ 决策落盘          │────────▶│ 宿主 AI 代理（我）          │
          │ (.audit 清单)    │ 消费    │ 系统 git push（带凭证）     │
          └─────────────────┘         └──────────────────────────┘
```

- **技能 / 专家提案**：`通过` 调现有 accept 端点，**容器内可直接执行**（无需宿主协助）。
- **待推送变更集**：`通过` 仅把决策写进清单（manifest）+ 记老板身份；**真正 push 由宿主 AI 代理消费清单后执行**，执行完回写 `pushed_ref`。
- 这把「审核在你手上」与「执行交给我」在架构上钉死，且不把 `.git` / 凭证挂进容器。

---

## 3. 现状盘点（复用，不重复造）

| 能力 | 现状 | 本设计动作 |
|------|------|-----------|
| `GET /admin/skills/proposals` | 已有（`server/admin.py:2783`，`skill_importer.list_proposals()`） | 复用 |
| `POST /admin/skills/proposals/{pid}/accept` | 已有（`server/admin.py:2796`） | 复用（通过） |
| `DELETE /admin/skills/proposals/{pid}` | 已有（`server/admin.py:2812`） | 复用（打回） |
| `GET /admin/experts/proposals` | 已有（`server/admin.py:2951`） | 复用 |
| `POST /admin/experts/proposals/{pid}/accept` | 已有（`server/admin.py:2964`） | 复用（通过） |
| `DELETE /admin/experts/proposals/{pid}` | 已有（`server/admin.py:2980`） | 复用（打回） |
| `Skills.vue` 「待审批提案」卡 | 已落地、浏览器实测通过 | 保留可用；本中心为统一入口，后续可去重 |

---

## 4. 架构与数据流

```
审核中心 (ReviewCenter.vue)
   │
   ├─ GET /admin/review/queue  ── 聚合三类 ──┐
   │                                         │
   │   ① skill proposals  ───────────────────┤  skill_importer.list_proposals()
   │   ② expert proposals  ───────────────────┤  experts.list_expert_proposals()
   │   ③ pending_push      ───────────────────┤  读 .audit/review_queue.json (按租户)
   │                                         │
   ├─ 通过/打回 技能提案 ── POST/DELETE /admin/skills/proposals/{pid}   (容器内执行)
   ├─ 通过/打回 专家提案 ── POST/DELETE /admin/experts/proposals/{pid}  (容器内执行)
   └─ 通过/打回 待推送  ── POST /admin/review/queue/{id}/approve|reject (仅写清单)
                                     │
                                     ▼ 宿主 AI 代理消费 approved 项
                         系统 git push (D:\Program Files\Git\bin\git.exe -c credential.helper=)
                                     │
                                     ▼ POST /admin/review/queue/{id}/mark-pushed (回写 pushed_ref)
```

**pending_push 清单由谁写入**：edict-gate 在 gate ③（独立审议）产出 `PASS` / `PASS_WITH_NOTES` 后，由 AI 代理（或 Stop hook）追加一条 `pending_push` 条目到 `.audit/review_queue.json`（按租户隔离）。已存在同 `id` 则更新而非新增（幂等）。

---

## 5. 数据模型（`.audit/review_queue.json`，按租户）

```json
{
  "version": 1,
  "items": [
    {
      "id": "cs-skill-idempotent",
      "kind": "pending_push",
      "title": "skill 幂等修复 + 采纳裁剪",
      "commits": ["cfbfd48", "8ca917e", "70d85ce", "9e011ee", "4b15c1a"],
      "base_ref": "origin/master",
      "gate": {
        "verification": "VERIFICATION.md",
        "review": "REVIEW.md",
        "status": "PASS_WITH_NOTES"
      },
      "decision": "pending",
      "approved_by": null,
      "approved_at": null,
      "pushed_at": null,
      "pushed_ref": null,
      "reject_reason": null,
      "created_at": "2026-09-20T17:00:00+08:00"
    }
  ]
}
```

`decision` 状态机：`pending → approved → pushered`；`pending → rejected`（可重开为 `pending`）。

---

## 6. 后端端点设计

| 方法 | 路径 | 说明 | 是否新增 |
|------|------|------|----------|
| GET | `/admin/review/queue` | 聚合三类审核项（skill/expert 提案 + pending_push），统一返回 `{items:[{kind,id,title,status,decision,...}]}` | **新增**（薄聚合层，委托现有 list + 读 manifest） |
| POST | `/admin/skills/proposals/{pid}/accept` | 通过技能提案 | 复用 |
| DELETE | `/admin/skills/proposals/{pid}` | 打回技能提案 | 复用 |
| POST | `/admin/experts/proposals/{pid}/accept` | 通过专家提案 | 复用 |
| DELETE | `/admin/experts/proposals/{pid}` | 打回专家提案 | 复用 |
| POST | `/admin/review/queue/{id}/approve` | 待推送：记录老板批准（写 `decision=approved` + `approved_by` 取自 JWT `sub`） | **新增** |
| POST | `/admin/review/queue/{id}/reject` | 待推送：打回（body `{reason}`，写 `decision=rejected`） | **新增** |
| POST | `/admin/review/queue/{id}/mark-pushed` | 宿主代理 push 后回写（`decision=pushered` + `pushed_ref`） | **新增**（仅代理/宿主侧可调，需独立令牌或本地守护） |

权限：全部 require admin / tenant-admin（沿用现有 admin 鉴权）。

---

## 7. 前端设计

- **新路由**：`/admin/review` → 组件 `ReviewCenter.vue`（admin 可见）。
- **导航**：侧边栏新增「审核中心」入口（admin only），角标显示 `pending` 总数。
- **布局**：
  - 顶部汇总条：待审总数 + 三类分项计数。
  - 统一表格（按 `kind` 分组或加 `kind` 列 + 筛选）：每行显示 标题 / 类型 / 闸门状态 / 决策状态 / 操作。
  - 行展开详情抽屉：技能/专家提案看正文；pending_push 看 commit 列表 + `VERIFICATION.md`/`REVIEW.md` 链接 + gate 结论。
- **操作按钮**：
  - 技能/专家提案：`通过`（绿）/ `打回`（红）→ 调现有端点。
  - 待推送：`批准推送`（绿）/ `打回`（红）。点「批准推送」后该行状态转「已批准·待代理推送」，直到 `mark-pushed` 回写后变「已推送 <ref>」。
- **复用**：沿用 `Skills.vue` 已有的 `skillService` 模式，新增 `reviewService` 封装新端点。
- **去重策略**：`Skills.vue` 现有「待审批提案」卡保留（已验证可用），本中心为统一入口；后续版本可把卡收敛进中心（非本版必做）。

---

## 8. 关键流程

**A. 通过技能提案（全容器内）**
UI 点「通过」→ `POST /admin/skills/proposals/{pid}/accept` → `install_spec` 落盘 + 删提案 → 列表刷新、卡片消失（与现有行为一致，已实测）。

**B. 批准待推送变更集（决策 + 代理执行）**
1. UI 点「批准推送」→ `POST /admin/review/queue/{id}/approve` → 写 `decision=approved` + `approved_by` + `approved_at`。
2. 宿主 AI 代理（我）在会话内消费 approved 项 → 用系统 git 执行 `git push -u origin master`（或按需分支）+ `-c credential.helper=`。
3. 代理 push 成功 → `POST /admin/review/queue/{id}/mark-pushed` 回写 `decision=pushered` + `pushed_ref`。
4. UI 轮询/刷新 → 显示「已推送 <ref>」。

**C. 打回**
- 技能/专家提案：`DELETE` 删提案。
- 待推送：`POST .../reject` 写 `decision=rejected` + 原因；可备注回流给代理重做（见 §11 待拍板 4）。

---

## 9. 约束与风险

| 约束 | 影响 | 处置 |
|------|------|------|
| 容器无宿主 `.git` / 无推送凭证 | 不能在容器内 push | §2 决策/执行分离；push 由宿主代理经系统 git 执行 |
| push 凭证 | 系统 git 需 `-c credential.helper=` 绕过 GCM 崩溃 | 仅宿主代理侧使用，不进容器、不写前端 |
| 幂等 | 重复点「批准推送」 | `approve` 已 `approved` 直接返回；manifest 原子写 |
| 条目重复 | gate 多次写同一 change-set | 按 `id` upsert |
| 未登录/非 admin | 越权看审核 | 沿用现有 admin 鉴权中间件 |
| 代理未在线 | approved 项堆积未 push | UI 显示「待代理推送」；老板可随时催代理；非阻塞 |

---

## 10. 验收标准

1. 打开 `/admin/review` 能看到三类：技能提案 + 专家提案 + 待推送变更（无则显示空态）。
2. 技能/专家提案点「通过」→ 后端 `installed=true`、提案从列表消失（行为等同现有 Skills 卡，已实测）。
3. 待推送点「批准推送」→ manifest `decision=approved`、UI 显「待代理推送」；代理 push 后显「已推送 <ref>`。
4. 任意类型点「打回」→ 对应项按 §8C 处理并从待审计数扣除。
5. 非 admin 访问 `/admin/review` 被拒。
6. 三道闸未过的变更集（gate `BLOCKED`）不在待推送清单出现（只列 `PASS`/`PASS_WITH_NOTES`）。

---

## 11. 待老板拍板（open questions）

1. **条目粒度**：待推送按「单个 commit」还是「一次 gate 产出的 change-set（一组 commit）」？建议 **change-set**（一个里程碑/一次门禁 = 一条），更贴合「已过三道闸待 push」语义。
2. **专家提案是否纳入本期**？后端端点已存在，纳入成本低 → 建议**纳入**。
3. **`Skills.vue` 现有卡是否收敛进中心**？建议本版保留卡、中心为统一入口；去重留待后续。
4. **打回待推送后是否支持「请求修改」备注回流代理重做**？建议支持（`reject_reason` 已预留），但重做触发机制（代理如何消费）需确认。

---

## 12. 防过度工程（non-goals 重申）

- 不在容器内实现 git push。
- 不做多人/会签审核流（当前单人 boss 审核）。
- 不做独立审批历史库（manifest 即审计留痕，可回看）。
- 不做邮件/IM 推送通知（代理会话内沟通即够）。
- 不引入新前端框架/状态库（沿用 Vue3 + Pinia + Element Plus 现有栈）。
