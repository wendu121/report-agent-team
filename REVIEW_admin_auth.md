<!-- reviewed-by: independent-subagent (gate-③) -->

# 审议结论：FAIL_WITH_MAJOR

## 结论
根因定位正确，`authFetch` 实现无缺陷；但 3 个视图调用了 `authFetch` 却漏加 import，前端无法编译（硬失败）。补齐 import 后即可 PASS。

## 必须修（MAJOR）
1. `web/src/views/Channels.vue`：193/258/277/293/317 行调用 `authFetch`，但 script 区（135–141）无 `import { authFetch } from '@/api/client';` → 编译报 `Cannot find name 'authFetch'`。
2. `web/src/views/Skills.vue`：251/281/300/324 行调用 `authFetch`，import 区（150–156）缺 import → 同上。
3. `web/src/views/settings/Templates.vue`：70/93 行调用 `authFetch`，import 区（46–53）缺 import → 同上。
（对照：Plugins.vue:217、Agents.vue:142、TemplateEdit.vue:77 已正确 import，调用正常。）

## 证据
- client.ts:39-45 `authFetch` 实现正确：`new Headers(init.headers)` 对各类 HeadersInit 均安全；Content-Type 经 `has()` 守卫；仅 token 存在时 set Authorization；委托原生 fetch。
- 6 视图对 `/admin/*` 调用（Channels 258/277/293/317、Plugins 371/390/416/440、Agents 237/271、Skills 281/300/324、Templates 70/93、TemplateEdit 109/118/143）已全部改 `authFetch`，无残留原生 fetch 打 /admin。

## 非阻断 NOTE（MINOR）
- authFetch 对 Authorization 用 `set` 而非 `has` 守卫，若调用方自带该头会被覆盖；当前 6 视图均未自带，无害。
- GET/DELETE（Channels:193、Templates:70）被补 Content-Type，多余但无害。
- 其余 fetch 残留均非 /admin：download.ts:36/67（自带 Bearer，/tasks/*）、stores/template.ts:18（/api/v1/templates 公开）、ChatEntry.vue:359/370（公开），无级联登出风险。

---

## lead 响应（MAJOR 已修复）
- 审议抓出的 3 处漏 import 已补齐：`Channels.vue:137`、`Skills.vue:152`、`settings/Templates.vue:49` 均已加 `import { authFetch } from '@/api/client';`（Grep 复核 6 视图 import 齐全）。
- 重建验证：`docker compose build web` → `vue-tsc -b && vite build` ✓ 1786 模块、0 TS 错误；`up -d web` 部署后运行包 `index-DjJMRl1p.js` 含 `new Headers`（authFetch 体标记）命中 1 次。
- 结论闭环：FAIL_WITH_MAJOR 的阻断项已消除，门禁①/②/③ 全部通过。
