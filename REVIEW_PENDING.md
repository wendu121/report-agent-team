<!-- BLOCKED: 独立审议未执行，原因 API 429，重置时间 2026-09-11 23:13:24 UTC+8 -->

# 独立审议 · 待补审（复核页修复 / 侧边栏历史 / 可折叠栏）

- **项目**：report-agent-team（LangGraph 多智能体研报系统）
- **审议类型**：edict-gate 门禁 ③（静态代码审议）
- **状态**：**BLOCKED**
- **阻塞原因**：独立审议子代理触发 API 限流（429），主代理未执行任何代码修改
- **限流重置时间**：2026-09-11 23:13:24 UTC+8（约 6.5 小时后）
- **记录时间**：2026-09-11 16:49 UTC+8

---

## 一、本轮待审变更清单

| 文件 | 变更要点 |
|------|----------|
| `server/api.py` | `escalate_reason` 从 `routing_state` 透传到 `TaskResponse`；新增 `GET /api/v1/tasks` 历史列表；新增 `_load_task_from_file` 支持 API 重启后恢复已结束任务。 |
| `orchestrator.py` | `_agent_fail` 新增 `on_event` 参数，失败时实时推送 `agent_output_unusable` WS 事件；5 处调用点全部透传。 |
| `web/src/types/engine.ts` | `WSEvent` 增加 `agent_output_unusable` 判别联合分支。 |
| `web/src/utils/timeline.ts` | `wsEventToNode` 增加 `agent_output_unusable` 节点渲染。 |
| `web/src/types/api.ts` | 新增 `TaskHistoryItem` 接口。 |
| `web/src/services/taskService.ts` | 新增 `listTasks()` 调用 `GET /api/v1/tasks`。 |
| `web/src/layouts/DefaultLayout.vue` | 新增侧边栏「历史记录」区域；改为使用 `useHistoryStore`（共享 Pinia store）实现提交后自动刷新；按 WorkBuddy 导航栏风格重排为扁平行 + 单行截断 + 箭头 + 可折叠标题栏（任务数量徽章 + 本地状态记忆）。 |
| `web/src/stores/history.ts` *(新增)* | 侧边栏历史任务 Pinia store（`items` / `refresh()`），供 DefaultLayout 与 ChatEntry 共用。 |
| `web/src/views/ChatEntry.vue` | ① **插件默认改为「全部不勾选」**（原默认全选 → 一个 topic 调 15 个数据源、56 次 API）；② 「插件」按钮显示数量徽章（0 红色 / >5 橙色警告）；③ 未选数据源时显示黄色提示横幅；④ 任务提交成功后 `useHistoryStore().refresh()` 让新条目立即冒到侧边栏；⑤ **对话优先重排**：输入框放大居中、引导气泡精简、插件/模型/生成按钮作为输入框底部工具栏、场景建议 chips 移到输入框下方（对齐 Accio 布局）。 |

---

## 二、已完成的门禁 ① / ②

- **门禁 ① 校验**：`py_compile` 通过（`server/api.py`、`orchestrator.py`）；`npm run build` / `vue-tsc -b` 0 TS 错误。
- **门禁 ② 自审**：已通过 e2e 验证——历史接口返回列表、已升级任务恢复时 `escalate_reason` 正确、实时 WS 事件 `agent_output_unusable` 到达、折叠栏 UI 经真实浏览器截图验证。

---

## 三、BLOCKED 说明

原暂停的两位独立审议子代理（M10-P5 评审）在恢复后均因 API 限流无法启动：

```
429 ... 请稍候再试 ... 重置时间：2026-09-11 23:13:24
```

按治理铁律，**主代理不得顶替自签 `reviewed-by: independent-subagent`**，故门禁 ③ 未完成。本轮变更在补审 PASS 前**不 commit、不 push**。

---

## 四、补审待办（重置后执行）

1. 重新 spawn 独立审议子代理（或恢复已暂停代理），评审范围为本轮 `report-agent-team/` 下的 7 个文件变更。
2. 重点核查点：
   - `server/api.py` 中 `list_tasks` 路径遍历是否安全（仅读取 `.engine_state/*.json`）。
   - `orchestrator.py` 中 `_agent_fail` 的 `on_event` 是否在异常时不会抛错中断流程。
   - `DefaultLayout.vue` 中 `localStorage` 访问在 SSR/无 storage 环境是否安全（已有 `typeof localStorage !== 'undefined'` 保护）。
   - 可折叠栏交互是否可访问（键盘、ARIA 不是本次重点，但需确认无 JS 报错）。
3. 子代理将结论写入本文件并覆盖 `BLOCKED` 状态；`VERDICT` 为 `PASS`/`PASS_WITH_NOTES` 后由主代理执行 commit。

<!-- reviewed-by: BLOCKED -->
