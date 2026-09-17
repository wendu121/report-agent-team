<!-- reviewed-by: independent-subagent -->
# REVIEW · P0+P1 市场页 Accio 模式增强（独立静态评审 · 门禁 ③）

**Verdict: PASS_WITH_NOTES**

## 摘要
三市场页的改动（来源筛选 / 作者徽标 / 技能领域分组+数量 / 智能体精选+更多）均在逻辑层正确落地，构建已全绿（vue-tsc/vite 均 EXIT=0），无类型或绑定断裂，无越界冒充。发现 1 处功能性不一致（Plugins 分类标题在筛选后可能空渲染）与 1 处轻微 UX 副作用（Skills 分类下拉在选中后折叠），均非阻断，列作 MINOR / NOTE。诚实边界通过。

## 评审范围与依据
- 读：`web/src/views/{Plugins,Skills,Agents}.vue`、`web/src/components/EntityCard.vue`、`web/src/types/ui.ts`
- 对照：`VERIFICATION_P0P1.md`、`_accio_probe/accio_reference_analysis.md`
- 方法：仅静态审阅，未运行、未改动任何代码。

## Findings

### MAJOR
**MAJOR=0** —— 无必须修复项。

### MINOR
**MINOR-1 · Plugins.vue:210-212（categories 由未过滤的 items.value 派生）**
- 现象：`categories` 用 `items.value.map(p=>p.category)` 取全量分类；而 `itemsByCat(cat)`（:226-228）对 `filtered.value` 过滤。当 `sourceFilter`、`已安装` tab 或 `keyword` 把某分类下所有条目排除后，该分类标题仍渲染、其内 `grid` 为空 —— 出现「空分类段」。
- 后果：来源筛选 / 已安装筛选下会出现无卡片的分类标题，视觉 defect。
- 修复：将 :211 的 `items.value` 改为 `filtered.value`，与 Skills.vue:130 保持一致：
  ```ts
  const categories = computed(() =>
    Array.from(new Set(filtered.value.map((p) => p.category).filter(Boolean))).sort(),
  );
  ```
  （Vue computed 惰性求值，引用晚声明于 :214 的 `filtered` 无副作用；Skills 同序已验证可用。）

**MINOR-2 · Skills.vue:18-20 + :129-131（分类下拉在选中后折叠）**
- 现象：`categories` 由 `filtered.value` 派生，而分类 `<el-select>` 的选项 `v-for="c in categories"` 也消费同一集合。选中某 `catFilter` 后 `filtered` 仅剩该分类，`categories` 塌缩为单项，下拉里其它分类消失（依赖 `clearable` 才能复位）。
- 后果：轻微 UX 不便，功能不受影响。
- 修复（可选，二选一）：(a) 分类下拉选项改用全量分类 `Array.from(new Set(items.value.map(p=>p.category)))` 的独立 computed；或 (b) 保持现状并在自审说明中标注为已知交互取舍。

### NOTE
**NOTE-1 · Agents.vue:42,66（徽标硬编码）** —— featured 段恒为 `官方`、others 段恒为 `自定义`，与 `featured=builtin` / `others=!builtin` 定义冗余但无害，可保留。
**NOTE-2 · 一致性** —— Plugins 与 Skills 现都已用 `.cat-section/.cat-title`；Agents 仅用二者（无 `.count`，模板也未引用 `.count`），无悬空样式引用；三页均无旧扁平 grid 残留。
**NOTE-3 · 回归** —— Agents `goChat`(:206-209) 仍 `router.push({ path:'/', query:{ agents: a.id } })`，ChatEntry.vue:247-252 仍读 `route.query.agents` 预置子集；`goConfig`(:210-211) 仍走 `/settings/agents?name=...`。两处绑定未被改动，闭环未断。
**NOTE-4 · 类型** —— `sourceFilter` 取值 `none|api_key|oauth` 与 `PluginItem.auth_type` 联合类型一致；`catFilter`/`shapeFilter` 取值与各 `category`/`shape` 一致；vue-tsc EXIT=0 已佐证，无 broken binding、无 unused ref。

## 诚实边界核查
**PASS**
- 作者徽标完全由既有 `builtin` 布尔派生（官方 / 自定义），**未伪造任何第三方署名**（不冒充 Accio / @publisher / @用户名）。
- 状态 pill 沿用既有诚实表述（Plugins 的 `pluginState` 未改；Skills 的 已启用/未启用 未改；Agents 无状态 pill），未把占位 / 未接入源标成「可用」。
- 未新增任何「可选 / 已上线」式虚假可用性声明；后端字段 / 文案未改动。
- Agents「对话」仍回跳 ChatEntry 预置 Agent 子集，未宣称独立对话式 chat agent，与 `accio_reference_analysis.md` 标注的「关键鸿沟」一致。

## 结论
可合并（建议先修 MINOR-1，MINOR-2 可在合并前或后续迭代处理）。独立评审通过，主代理不予自签。
