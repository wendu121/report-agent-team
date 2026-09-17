# VERIFICATION_ACCIO_FULL.md · Accio 模式补全（分类筛选 + 统一能力市场）

> 门禁②：主代理自审。对应 commit（待落）：Accio 模式补全（Plugins 双筛选 + 统一能力市场页 /market + 导航收口）。
> 时间：2026-09-11

## 一、本轮范围（为什么做）

boss 指出 P0+P1 只是"在已完整的市场上修边角"，要求"能做的都做上"。重新核对探测分析
`../_accio_probe/accio_reference_analysis.md` 与现状后确认：

- Skills.vue / Channels.vue / ChatEntry.vue 在 **M9-5 / M10-P5b 已落地**大部分 Accio 模式
  （市场/已安装 Tab、分类筛选、分组+数量、启用/禁用、首页场景 chips、输入栏数据源挂载），
  故分析文档（写于这些落地之前）严重低估现状。
- 真正仍缺、且纯前端可做的：
  1. **Plugins 缺分类筛选**（Skills/Channels 均有 `catFilter`，唯独 Plugins 只有来源筛选）→ 补齐双筛选。
  2. **缺 Accio 招牌的「统一能力市场页」**：Accio 是**一页内 插件/技能/应用授权 三主 Tab**；
     RAT 此前是四个分散路由（/plugins /skills /channels /agents），缺这个统一结构 → 新建 Market.vue。

## 二、改动文件

| 文件 | 改动 | 后端改动 |
|---|---|---|
| `web/src/views/Plugins.vue` | 新增分类筛选：① `catFilter` ref ② 模板新增分类 `el-select`（选项由全量 `allCategories` 派生，规避 Skills 已知"选中后选项折叠"小问题）③ `filtered` 增加 `category` 判断 | 无 |
| `web/src/views/Market.vue` | **新建**：`el-tabs` 三主 Tab（数据源/研报技能/推送渠道），按 `active` 内嵌既有 `Plugins/Skills/Channels` 视图组件（复用，不重写）；仅挂载当前 Tab | 无 |
| `web/src/router/index.ts` | 新增 `{ path:'/market', name:'Market', component: Market }`；保留 /plugins /skills /channels 直达路由（Market 内嵌复用其组件） | 无 |
| `web/src/layouts/DefaultLayout.vue` | 左导航将「插件/技能/消息渠道」三入口合并为「能力市场」→ /market；保留「智能体」单列（对齐 Accio 智能体市场单列）；`activeMenu` 让 /market 及其内嵌直达路由统一高亮「能力市场」 | 无 |

## 三、构建校验（门禁①）

```
cd web
PATH=/c/Users/sfkj/.workbuddy/binaries/node/versions/22.22.2-2:$PATH
./node_modules/.bin/vue-tsc -b   -> EXIT=0
./node_modules/.bin/vite build   -> EXIT=0（✓ built in 11.73s）
```

## 四、诚实边界核查（关键，对齐 boss「不冒充」立约）

1. **统一市场页是纯前端结构重组**：内嵌的是既有 `Plugins/Skills/Channels` 组件，未新增任何
   后端端点、未新增任何"能力"。数据源启用/技能启用/渠道推送等行为与此前完全一致（真控制器）。
2. **徽标仍仅由 `builtin` 派生**（官方/自定义/内置），不冒充 Accio / @publisher / 第三方作者。
3. **状态标签/可用性表述未改动**：沿用既有 `pluginState` / `statusLabel`，无新增 `coming_soon`
   之外的假可用性声明；`coming_soon` 源仍明示"后端未接入/引擎永不推送"。
4. **分类筛选用真实 `category` 字段**：选项来自 `items` 全量派生，非编造分类。
5. **智能体市场保持单列**（`/agents`，Accio 智能体市场单列结构），未塞进三主 Tab——与 Accio 一致。
6. **未触碰的"做不到"项（如实标注，非本次范围）**：
   - 对话式单 Agent（点卡片直接对话）：需新增单 Agent 对话模式后端，非纯 UI；
   - 插件自带技能（plugins.yaml 无 skills 字段）：需扩 schema + 引擎注入；
   二者均非"纯前端抄"，本次未做，待 boss 决策是否立项。

## 五、自审结论

- 门禁①：vue-tsc -b + vite build 均 EXIT=0。
- 改动范围收敛：4 前端文件，0 后端改动，0 配置改动。
- 诚实边界：PASS（无冒充、无假可用、无编造数据）。
- 建议独立子代理按门禁③出具 REVIEW_ACCIO_FULL.md。
