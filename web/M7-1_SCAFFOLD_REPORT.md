# M7-1 脚手架搭建完成报告

> 日期：2026-09-05 · 基于 `M7_FRONTEND_DESIGN.md`（已按审议清单 10 项修改）+ `API_SPEC.md v1.0`（已冻结契约）
> 技术栈：Vue 3.5 + Vite 6 + TypeScript 5.7 + Pinia 3 + pinia-plugin-persistedstate 4 + Element Plus 2.9 + axios + @vueuse/core + marked + DOMPurify

## 一、验证结果（全部通过）

| 验证项 | 命令 | 结果 |
|--------|------|------|
| 类型检查 | `vue-tsc --noEmit` | ✅ 0 error |
| 生产构建 | `vite build` | ✅ 1708 modules transformed，dist 产出 |
| 本地运行 | `vite`（:5173） | ✅ HTTP 200 |

> 体积告警（非错误）：Element Plus 全量引入致 JS chunk 1.3MB。后续可在 M7-3+ 做按需引入 / 路由级 code-split。

## 二、文件结构（web/）

```
web/
├── package.json            # 依赖（pinia 升 3 匹配 persistedstate 4）
├── vite.config.ts          # /api 代理（REST+WS 统一转发到 :8000）
├── tsconfig.json / tsconfig.node.json
├── index.html / env.d.ts
└── src/
    ├── main.ts             # 入口（Pinia+persist / Router / ElementPlus / 图标全局注册）
    ├── App.vue
    ├── types/              # 强类型层（无 any/int）
    │   ├── api.ts          # 对齐 API_SPEC §2（TaskStatus 6 态）
    │   ├── engine.ts       # 对齐 API_SPEC §3.2（payload 平铺）
    │   ├── ui.ts           # meta: Record<string, unknown>
    │   └── index.ts
    ├── api/                # client.ts（拦截器仅抛 ApiError）+ errors.ts
    ├── services/           # taskService.ts + wsService.ts（连接缓存 Map）
    ├── stores/             # task（核心）/ template / websocket / settings（persist）
    ├── utils/              # timeline.ts（语义转译）/ formatter.ts / download.ts（Blob 异常检测）
    ├── router/index.ts     # 5 路由
    ├── layouts/DefaultLayout.vue
    ├── views/              # TemplateSelect / TaskSubmit / TaskTracking / TaskResult / TaskReview
    └── components/
        ├── common/StatusBadge.vue
        └── timeline/{Timeline.vue, TimelineNode.vue}
```

## 三、M7-1 期间发现并修复的契约漂移（重要）

| # | 问题 | 修复 |
|---|------|------|
| 1 | 前端 WS 路径 `/ws/{id}` 与后端不符 | 改为 `/api/v1/tasks/{id}/stream`（对齐 API_SPEC §3.1 + main.py 前缀） |
| 2 | 前端假设 WS payload 包在 `data` 字段 | API_SPEC §3.2 实际平铺顶层 → `wsEventToNode` 改为读 `ev` 顶层 |
| 3 | design doc 4.2 的 Data 类型字段与 API_SPEC §3.2 不符 | `AgentCompleteData`/`ReworkTriggerData`/`TaskDoneData`/`TaskEscalatedData` 等全部对齐（rework_reason / escalate_reason / audit_url / tool_status / engine_events 等） |
| 4 | `pinia@2` 与 `pinia-plugin-persistedstate@4` peer 冲突 | pinia 升 `^3.0.1` |
| 5 | 设计文档漏改：`TaskStatus` 缺 created/rework、`TimelineNode.meta` 为 any | 以 API_SPEC 6 态为准；meta → `unknown` |

## 四、下一步选项

M7-2~M7-5 的页面骨架已在 M7-1 一并落地且可编译运行（含核心 WS 兜底同步、连接缓存、DOMPurify 清洗、语义转译）。

建议下一步二选一：
- **M7-3 时间线深化**：节点点击展开详情（rawEvent/meta）、虚拟滚动（>100 阈值）、视觉与动效增强——设计核心差异化界面；
- **M7-6 前后端联调**：启动 `server/main.py`（:8000）+ 前端，创建任务走真实/ mock 引擎流程，验证 REST+WS 端到端闭环。
