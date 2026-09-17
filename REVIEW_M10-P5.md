# 独立审议 · M10-P5 插件市场 UI 改造（消除「暂未推出」死端）

- **里程碑**：M10-P5（report-agent-team，LangGraph 多智能体研报系统）
- **审议类型**：edict-gate 门禁 ③（静态代码审议，未改动任何代码）
- **审议人（reviewer）**：independent-subagent
- **日期**：2026-09-11
- **审查范围**：DESIGN_M10-P5.md、config/plugins.yaml、server/admin.py、web/src/types/ui.ts、web/src/views/Plugins.vue、VERIFICATION_M10-P5.md

---

## 一、背景与变更意图（来自设计文档）

用户诉求：插件市场中原 `coming_soon` 渲染为「即将推出」死端，要求——

1. 需 KEY 的插件必须给出**真实** KEY 输入入口（非占位）。
2. 插件收敛为**六个**分类（原八类）。
3. 需 KEY 插件必须带**真实**「获取密钥」链接——**不得编造 URL**。
4. 密钥获取教程放在插件面板，老板可自行去拿。

治理铁律：零 KEY 不伪造数据——不为未实现的 qcc/xueqiu/cninfo/scholar 写假后端；只暴露真实 key 输入 + 真实教程链接 + 诚实「后端未接入」注记。

---

## 二、逐条核查结果

| # | 核查项 | 结论 | 说明 |
|---|--------|------|------|
| 1 | 字段一致性链路（YAML → admin → API → ui.ts → Vue） | **PASS** | `doc_url`/`key_guide_url`/`key_env` 四层字段名完全一致：yaml 定义 → `admin.py::_plugin_item` 透传（L1408-1410）→ `/api/v1/plugins` JSON → `ui.ts::PluginItem` 可选字段（L59-62）→ `Plugins.vue` 使用 `p.key_guide_url`/`p.doc_url`/`p.key_env`。无拼写/命名错位。 |
| 2 | 死端已消除：coming_soon+api_key 暴露真实 connect 动作 | **PASS** | qcc（`provider: coming_soon` + `auth_type: api_key`）走新分支（`Plugins.vue:57-60`）显示「配置密钥」→ `openConnect(p)` → `POST /admin/plugins/{pid}/connect`（`admin.py:1746`）→ 真实写入 `.secrets/plugins.env` 的 `DS_QCC_API_KEY`。入口真实、落盘真实，非占位。 |
| 3 | 不为 qcc/xueqiu/cninfo/scholar 编造假后端 | **PASS** | `server/` 未新增任何 Qcc/Xueqiu/Cninfo/Scholar Provider。引擎 `load_data_sources._recompute_connected`（`data_sources.py:933-934`）对 `provider=coming_soon` 恒返回 `coming_soon` → 引擎继续跳过。`Plugins.vue:128` 弹窗 `warn-note` 明示「当前引擎仍会跳过」。诚实边界保持。 |
| 4 | URL 均为真实官方根域 | **PASS** | 全部为官方根域：tavily.com / app.tavily.com / arxiv.org / semanticscholar.org / crossref.org / wikipedia.org / openalex.org / europepmc.org / wikidata.org / gu.qq.com / gdeltproject.org / xueqiu.com / cninfo.com.cn / qcc.com / qcc.com/open / scholar.google.com / taobao.com / amazon.com / ebay.com / accio.com。**未发现编造的深层子路径或参数**。 |
| 5 | 六分类一致性 | **PASS** | 19 个插件每项目录其中之一：综合检索(2) tavily/gdelt、学术文献(6) arxiv/semantic_scholar/crossref/openalex/europe_pmc/scholar、百科知识(2) wikipedia/wikidata、财经行情(3) tencent_quote/xueqiu/cninfo、企业工商(1) qcc、电商数据(5) taobao_suggest/amazon_suggest/ebay_suggest/accio_tariff/hs_code_tariff。与 DESIGN §3.1 映射完全一致。 |
| 6 | Plugins.vue 逻辑/绑定缺陷 | **PASS** | 分支链无不可达/错乱：connected → (真实源+api_key) → (coming_soon+api_key) → else。四个 coming_soon 解析正确：qcc→分支3、xueqiu(oauth)→else、cninfo(none)→else、scholar(none)→else。无遗留 `statusType`/`statusLabel` 引用（残留仅存在于无关的 Skills/ChatEntry/Channels 视图）。 |
| 7 | 已连接插件回归风险 | **PASS** | 已连接分支（`Plugins.vue:43-46`）行为与原逻辑一致（停用 + 非内置删除）；`_plugin_item` 仅**新增**三字段，未改既有字段；connect 端点签名不变；引擎 `_recompute_connected` 未改动。无回归。 |

---

## 三、最终裁决

`VERDICT: PASS_WITH_NOTES`

`MAJOR=NONE`（无阻塞性缺陷，无真实 bug，无编造 URL）。

---

## 四、Notes / 非阻塞建议

1. **oauth 类 coming_soon（xueqiu）无「配置凭证」按钮**：按设计合理——oauth 用会话 Cookie 而非标准 Key，且 `connect` 端点（`admin.py:1756`）拒绝非 `api_key` 插件，故无法提供真实入口。当前以状态标签 + `:title` 注记「需会话 Cookie，后端 Provider 待实现」+ 官网链接兜底。DESIGN §3.3 提到「配置凭证说明」，tooltip 已覆盖语义，可接受。建议后续若接 xueqiu，单独设计 Cookie 录入弹窗。

2. **URL 深度未联网核验**：VERIFICATION §5 自陈因 WebSearch 被拦截未逐条联网核验，均取自官网根域/已知开放平台路径（qcc.com/open 为企查查开放平台真实入口）。作为独立审议，我**未发现任何编造 URL**，但建议老板首次点击时顺手确认 qcc.com/open 具体申请页路径——若变动，改 `config/plugins.yaml` 一处即热加载生效。

3. **`pluginState(p)` 在模板中每卡片调用 3 次**（`:type`/`:title`/label）：纯展示计算、开销可忽略，非缺陷。如需优化可改为计算属性，但当前无需改动。

4. **无用 CSS `.shape` 类**（`Plugins.vue:355-357`）：未在任何模板元素引用，属死代码。建议后续清理，不影响功能。

5. **分类排序**：`categories` 计算属性用 `.sort()` 按 Unicode 码点排序，呈现顺序与设计 §3.1 期望的语义分组顺序可能不同（纯展示问题，非本次引入，且前端本就动态分组）。如需固定顺序可在 yaml 加 `order` 字段——非阻塞。

6. **gdelt 状态变更**：由 `coming_soon` 改为 `disconnected`/`enabled:false`，描述补充 TLS 不稳定诚实说明。属合理诚实调整，与六分类（归入综合检索）一并生效，无副作用。

---

## 五、结论

M10-P5 达成了用户的四条诉求：真实 KEY 输入入口（qcc 走既有 connect 链路落盘 `.secrets`）、六分类收口、真实教程链接（全部官方根域、无伪造）、教程置于面板。引擎 `coming_soon` 跳过逻辑刻意未动，保持诚实「不可用」声明，UI 以 `warn-note` 明示后端未接入——符合治理铁律「零 KEY 不伪造数据」。门禁 ② 自审与门禁 ③ 独立审议均无 MAJOR 阻断项，可放行进入提交/合并流程。

<!-- reviewed-by: independent-subagent -->
