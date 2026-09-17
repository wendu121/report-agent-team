# VERIFICATION · P0+P1 市场页 Accio 模式增强

> 门禁 ②（主代理自审）— 配套 `REVIEW_P0P1.md`（独立子代理评审）
> 探测依据：`E:/第二电脑/_accio_probe/accio_reference_analysis.md`

## 一、范围

对齐 Accio Work 探测结论，对 report-agent-team **三个已存在的市场页**做针对性增强（非从零新建）：

- **P0 · Plugins.vue（数据源市场）**：补「来源」筛选 + 作者徽标（官方/自定义）
- **P1 · Skills.vue（研报技能市场）**：改为按领域分组 + 数量徽标
- **P1 · Agents.vue（智能体市场）**：加「精选 + 更多」两段式 + 作者徽标

> 设计澄清：先前讨论把"三主 Tab"与"新建 Skills/Agents 页"混说，落地时采用**三页并列**（数据源 / 研报技能 / 智能体各一页），而非把三页塞进一个文件的 Tab 里。左导航新增对应入口（M9 已完成）。

## 二、改动文件（仅前端，无后端变更）

| 文件 | 改动 |
|---|---|
| `web/src/views/Plugins.vue` | ① 新增 `sourceFilter` ref + 来源 `el-select`（none/api_key/oauth）；② `filtered` 增加 `auth_type` 过滤；③ 卡片 badges 由「内置」改为「官方 / 自定义」双徽标 |
| `web/src/views/Skills.vue` | ① 渲染由扁平 grid 改为按 `category` 分组的 `section` + 每组数量徽标 `count`；② `categories` 由 `filtered` 派生、`itemsByCat()` 取组内条目；③ badges 改官方/自定义；④ 补 `.cat-section / .cat-title / .count` 样式 |
| `web/src/views/Agents.vue` | ① 渲染改为 `featured`(builtin) / `others`(自定义) 两段 section；② 新增 `featured` / `others` computed；③ badges 改官方/自定义；④ 补 `.cat-section / .cat-title` 样式 |

## 三、确认已存在、无需重复改动

- 路由 `router/index.ts` 已注册 `/agents` `/plugins` `/skills` `/channels`（M9）。
- 左侧导航 `web/src/layouts/DefaultLayout.vue:18-34` 已有四入口 + active 映射（`:97-100`）。
- 后端公共列表端点 `public_router` 已就绪：`GET /plugins`、`/skills`、`/agents-library`、`/channels`（admin.py:1414 / 1876 / 1114 / 2102）。
- `web/src/types/ui.ts` 无需变更——作者徽标由既有 `builtin` 布尔派生，未新增传输字段。

## 四、门禁 ① 构建校验（已跑）

```
cd web && vue-tsc -b        → EXIT=0（无类型错误）
        vite build          → 1749 modules transformed, EXIT=0, dist 产出正常
```

## 五、诚实边界核查（遵循 boss「不冒充可用」铁律）

- 作者徽标由 `builtin` 布尔派生（`官方` / `自定义`），**未伪造任何第三方署名**（不冒充 Accio/@publisher）。
- 状态 pill 沿用既有诚实表述（`coming_soon` = 后端未接入 等），未把占位源标成可用。
- Agents「对话」沿用既有实现（回跳 `ChatEntry` 并预置 `agents` 子集），**未宣称独立 chat agent**——与 `accio_reference_analysis.md` 标注的"关键鸿沟"一致：RAT 的 Agent 是流水线角色，非对话式单 Agent。

## 六、自审结论

需求项（来源筛选 / 作者徽标 / 技能领域分组+数量 / 智能体精选+更多）逐条落地；构建全绿，无类型/构建回归；未触碰后端、未引入假配置、未越界冒充。

**待门禁 ③**：独立子代理静态评审（写 `REVIEW_P0P1.md`），主代理不自签。评审通过后再 `git commit`（不 push）。若独立审议因 API 429 受阻，按治理硬约定在 `REVIEW_P0P1.md` 顶部标 `BLOCKED` + 补审待办，不伪造通过、不 commit。
