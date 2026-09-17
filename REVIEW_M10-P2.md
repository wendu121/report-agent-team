# 独立审议 · M10-P2（keyless 数据源扩展）

> **立场声明**：本审议由独立子代理执行，与编码/自审主代理**无串通**。结论基于独立 `Read` 真实代码 + 独立实连端点 + 独立 `py_compile`/`pytest` 交叉取证，**不采信** `VERIFICATION_M10-P2.md` 自述。
> 取证文件：`tools/data_sources.py`、`config/plugins.yaml`、`server/admin.py`、`VERIFICATION_M10-P2.md`；独立运行 `py_compile tools/data_sources.py`、`pytest tests/ -q`，并对 openalex / europe_pmc / wikidata / gdelt / tencent_quote 逐一实连复现。

<!-- reviewed-by: independent-subagent -->

## 一句话结论

**BLOCK** —— 扩展点复用、Protocol 一致性、配置键匹配、41 passed 均属实，但 **腾讯行情源对中文名称返回未解码的 `\uXXXX` 转义（乱码输出）**，且 **`search_many` 单源容错只捕获 `ToolError`、解析异常会击穿整个聚合**；两处均被自审报告误判为通过，须修复后复验。

---

## ① 接口一致性（PASS）

5 个新 Provider 均满足 `DataSourcePlugin` Protocol（`id/name/category/auth_type` + `search(query,max_results)`），独立实例化核验：

```
openalex / europe_pmc / wikidata / tencent_quote / gdelt  attrs_ok=True  auth=none
```

`search` 签名与返回结构 `[{title,url,content,score}]` 与既有 P1 源一致，`build_search_tool` 调用 `KEYLESS_PROVIDERS[prov](timeout=timeout)` 的构造契约（各源 `__init__(self, timeout=20, ...)`）全部满足。

## ② 扩展点复用（PASS，含 1 处死代码）

- `tools/data_sources.py:624-627` 为既有分支，**未改动**；独立 `build_search_tool(load_data_sources(), …)` 实测聚合出 10 个真 Provider，5 个新源均以真类实例进入（非 Mock）：`openalex→OpenAlexProvider … gdelt→GDELTProvider`，`using_mock_search=False`。复用链真实生效。
- `config/plugins.yaml` 14 条中 `coming_soon` 4 条被正确跳过（`enabled:false`）。
- **死代码（MINOR-③/④）**：`data_sources.PROVIDER_REGISTRY`（`:451-455`）与 `ComingSoonProvider`（`:68-78`）在 `tools/data_sources.py` 内**无任何引用**（`build_search_tool` 直接 `continue` 跳过 coming_soon），全仓 `grep` 确认仅 `server/admin.py` 自带、同名但独立的是 `tools.push.PROVIDER_REGISTRY`。

## ③ 失败处理诚实性（**MAJOR**）

**自审主张**（VERIFICATION §3.3/§5）："`search_many` 对单源异常已 `except ToolError → ok=False → continue`，不阻塞其余源"。

**独立复现（真实击穿）**：

```python
# tools/data_sources.py:555-589
class BadJSON:                # 模拟 HTTP 200 但 body 非 JSON（GDELT 等常见返回纯文本页）
    def search(self, q, n): return json.loads('<html>Too Many Requests</html>')
tool = SearchTool([({'id':'gdelt'}, BadJSON()), ({'id':'arxiv'}, FakeOK())])
tool.search_many(['q1'])
# >>> CRASH  JSONDecodeError: Expecting value: line 1 column 1
```

- 根因：`_http_get_json`（`:99-100`）仅对网络/HTTP 错误包 `ToolError`（`:87-96`），**未保护 `json.loads`**；`ArxivProvider` 的 `ET.fromstring`（`:123`）同理。`SearchTool.search_many:565` 只 `except ToolError`，故 `JSONDecodeError / ET.ParseError` 会向上抛出，**使全部源的结果一并丢失**。
- 结论：单源容错**仅对 ToolError 生效**，对"200 但解析失败"这一真实失败模式无效。自审"容错机制已覆盖"表述不严谨。新增的 GDELT/OpenAlex/EuropePMC(JSON) 与 arXiv(XML) 均暴露于此路径。

## ④ 配置一致性（PASS，含 1 处 MINOR）

- `plugins.yaml` 5 条新条目字段齐备（`id/name/icon/category/description/auth_type/provider/enabled/status/builtin`）。
- `provider` 值与 `KEYLESS_PROVIDERS` 的 key **逐一精确匹配**（独立脚本比对：missing=[]，多余=[]）。
- `_recompute_connected`（`:491-505`）对 `auth_type=none` 返回 `connected`，实测 5 源 `status=connected`，自洽。
- **MINOR-③**：`server/admin.py:1363` `VALID_PROVIDERS = ["tavily","mock","coming_soon"]`（注释自称"新增真源须在此登记"）**未登记** 9 个 keyless provider；`:1611` 创建插件时据此校验，导致这类源无法经控制台新建（编辑/启停走 `PUT :1655` 不校验，故仅影响 create 与前端下拉）。

## ⑤ 解析健壮性（**MAJOR ×1 + MINOR ×3**）

**MAJOR-① 腾讯名称乱码（实连复现）**
- 证据：`tools/data_sources.py:382-395`。
- 实测原始响应（任何参数组合、utf-8/gbk 解码均同）：
  `v_hint="sh~600519~\u8d35\u5dde\u8305\u53f0~gzmt~GP-A"`
- 实跑：`TencentQuoteProvider().search('贵州茅台',3)[0]['title']` → `\u8d35\u5dde\u8305\u53f0 (sh600519)`（**未解码的转义串**，非"贵州茅台"）。
- 根因：smartbox 对中文名以 `\uXXXX` 转义返回，代码直接 `parts[2]` 作 name，未解码。URL/代码正确，但**面向用户的 title 全量乱码**（A股中文名场景 100% 触发）。
- 自审 §3.4 只核对了"命中 sh600519 个股页"（URL 为真），**漏检 title 乱码**，属验证不完整。
- 修复：对 `name` 做转义解码，如 `name = json.loads(f'"{parts[2]}"')`（响应为纯 ASCII 转义，安全）。

**MINOR-① OpenAlex 死回退/死读**
- `:284` `select=display_name,doi,id,abstract_inverted_index` —— 已**不再含** `title_display`（实连返回 work keys 恰为这 4 项，证实自审所述修复为真）。
- 但 `:292` 仍保留 `w.get("title_display")` 回退（永不命中，死分支）；`:294` `w.get("relevance_score")` 亦不在 `select` 内，实连确认 `has relevance_score: False` → **score 恒为 0.7**（非缺陷，但为死读）。建议删死回退并改固定分或补 select。
- `_reconstruct_abstract`（`:269-277`）反构**正确**：实连摘要 `len=136`，与自审 §3.4 一致。

**MINOR-② EuropePMC 摘要含 HTML 标签**
- `:330` `it.get("abstractText")` 未去标签；实连样本含 `<h4>Objectives</h4>…`。对比 `CrossrefProvider:204` 已 `re.sub(r"<[^>]+>"," ")` 去标签，本源遗漏，污染 snippet。建议对齐。

**其余解析核对**
- Wikidata `concepturi`（`:358`）：为 `wbsearchentities` 合法字段（静态确认）；沙箱对本域 TLS 握手超时（`URLError`），**未能在沙箱实连复现**，与自审 §4 所述一致，非代码缺陷。
- GDELT `articles`（`:424`）：实连返回 HTTP 429（源方限流，`_http_get` 转 ToolError），沙箱内无法取得正常体；`articles[].title/url/domain/seendate` 结构与文档一致。
- EuropePMC `resultList.result`（`:320`）与 doi→pmid 降级链（`:324-329`）：**实连通过**（返回 doi.org 链接）。
- 腾讯空匹配：实连得 `v_hint="N"` → 正则命中后由 `len(parts)<4`（`:389`）兜底返回 `[]`，行为正确；但代码注释写 `v_pv_none_match`（`:385`）与实际标记不符（NOTE）。

## ⑥ 安全性（PASS）

- 无 SSRF：5 源端点均为**固定常量**，用户输入仅经 `urllib.parse.urlencode` 进入查询参数（`:281/313/349/381/418`），无用户可控 URL。
- `User-Agent` 在 `_http_get:89` 统一设置。
- 无命令/SQL/模板注入面；`ET.fromstring` 解析不可信 XML（arXiv）在 stdlib 层面无已知实体展开风险（未显式禁 DTD，属极低风险，不作阻塞项）。

## ⑦ 过度工程（PASS）

- 依赖零新增：`data_sources.py` 仅 `json/os/re/urllib/xml.etree/typing`，符合 stdlib 约定；`requirements.txt` 未因本变更引入第三方。
- 新增 5 类为**平铺直写**，无多余抽象；未引入重试/缓存/协程等超范围复杂度。

## ⑧ 文档诚实性（部分不实）

- **"41 passed" 属实**：独立 `pytest tests/ -q` → `41 passed, 40 warnings in 2.75s`（退出码 0）；`py_compile tools/data_sources.py` → `COMPILE_OK`。
- **不实/过强处**：①§3.4 将 tencent 源标为通过，实为 title 乱码（见 MAJOR-①）；②§3.3/§5 "单源失败不阻塞/容错已覆盖"仅对 ToolError 成立（见 MAJOR-②）。

---

## 发现表

| 严重度 | 项 | 文件:行证据 | 建议 |
|---|---|---|---|
| **BLOCK** | 腾讯源中文名返回未解码 `\uXXXX`，title 全量乱码 | `tools/data_sources.py:382-395`；实测 `search('贵州茅台',3)[0]['title']`→`\u8d35\u5dde\u8305\u53f0 (sh600519)` | 对 name 做转义解码（`json.loads(f'"{parts[2]}"')`），补一条中文名断言单测 |
| **MAJOR** | 单源解析异常击穿聚合（只捕获 ToolError） | `:99-100`（未护 `json.loads`）、`:123`（`ET.fromstring`）、`:565`（`except ToolError`）；复现见 ③ | 在 `_http_get_json`/XML 解析处将解析错误包为 `ToolError`，或在 `search_many` 增加 `except Exception` 兜底 |
| MINOR | OpenAlex `title_display` 死回退 + `relevance_score` 死读（score 恒 0.7） | `:284`、`:292`、`:294` | 删死回退；score 改固定值或将其纳入 `select` |
| MINOR | EuropePMC 摘要未去 HTML 标签，污染 snippet | `:330` | 对齐 `:204` 的 `re.sub(r"<[^>]+>"," ")` |
| MINOR | admin `VALID_PROVIDERS` 未登记 9 个 keyless provider（注释自称须登记） | `server/admin.py:1363`、`:1611` | 同步登记，或改为从 `KEYLESS_PROVIDERS` 派生校验清单 |
| MINOR | `data_sources.PROVIDER_REGISTRY` 与 `ComingSoonProvider` 死代码（无引用） | `:451-455`、`:68-78` | 若保留供 admin 未来使用应加引用/测试，否则删除 |
| NOTE | 注释称空匹配标记为 `v_pv_none_match`，实际响应为 `v_hint="N"` | `:385` | 更正注释（行为已被 `len(parts)<4` 兜底，无功能影响） |

---

## 复现路径（供主代理修复后自验）

```bash
cd "E:/第二电脑/report-agent-team"
# MAJOR-① 腾讯乱码
python -c "import sys;sys.path.insert(0,'.');from tools.data_sources import TencentQuoteProvider as T;print(T().search('贵州茅台',3)[0]['title'])"
# 期望：贵州茅台 (sh600519)     实际：\u8d35\u5dde\u8305\u53f0 (sh600519)

# MAJOR-② 解析异常击穿
python -c "import sys,json;sys.path.insert(0,'.');from tools.data_sources import SearchTool
class B:
 id='gdelt'
 def search(s,q,n): return json.loads('<html>x</html>')
try:
 SearchTool([({'id':'gdelt'},B())]).search_many(['q']); print('OK')
except Exception as e: print('CRASH',type(e).__name__)"
# 期望：单源被标记 ok=False、整体不抛    实际：CRASH JSONDecodeError

# 文档诚实性复核
python -m py_compile tools/data_sources.py && python -m pytest tests/ -q   # 41 passed 属实
```

## 裁定说明

- **MAJOR = 2**：均为可复现的确定性缺陷，且均被自审报告判为通过 → 自审结论不可采信，第三闸判定 **BLOCK**。
- 修复面小（腾讯转义解码 ~1 行 + `_http_get_json` 包一层 try），修订后重跑 `pytest` 与上述两条复现即可复验。

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-10 · M10-P2 独立审议 · 独立子代理，主代理严禁自签 -->

---

# 第二轮复审（修复验证）

<!-- reviewed-by: independent-subagent -->

> **立场声明**：本轮由独立子代理复验，与主代理无利益关联。**不采信** `VERIFICATION_M10-P2.md §6` 自述，
> 全部结论基于独立 `Read` 生产代码 + 独立实连端点 + 独立实跑复现。工作区 `git status` 确认
> `tools/data_sources.py`、`server/admin.py`、`config/plugins.yaml` 三处均为**未提交**修改（复审目标即当前工作区）。

## 一句话结论

**PASS_WITH_NOTES —— BLOCK 可解除**。原 BLOCK（腾讯中文名乱码）与原 MAJOR（解析异常击穿聚合）**均已真实修复并经独立实跑复现通过**；
原 3 项 MINOR 已修、1 项 NOTE 已更正；回归仍 **41 passed**。仅剩 1 项**非阻塞残留**（`search_many` 仍只捕 `ToolError`，对「合法 JSON 但形状异常」无兜底）与 1 项 M9-2 遗留死代码，建议记入技术债。

---

## 逐条修复独立验证

### ① [原 BLOCK] 腾讯中文名乱码 —— ✅ 已修

**独立方法**：`Read` 代码 + 独立实连 smartbox 端点（代理 `127.0.0.1:8048`）。

代码核实（`tools/data_sources.py`）：
- 新增 `TencentQuoteProvider._unescape_js()`（`:396-405`），对含 `\u` 的字面量做 `latin-1/unicode_escape` 解码，失败退回原文不抛错；
- `search()` 在 `:419` 以 `name = self._unescape_js(parts[2]).strip()` 调用，替换了原先的直接 `parts[2]`。

**实测输出**：
```
search('贵州茅台', 3)[0]['title'] → '贵州茅台 (sh600519)'   # repr 无 \u，HAS_UESC=False
```
**边界/防过度修复**：`_unescape_js('Apple Inc')→'Apple Inc'`（无转义原样）；`('\u8d35\u5dde')→'贵州'`；`('\u817e\u8baf-Test')→'腾讯-Test'`；`('\uZZZZ')→'\uZZZZ'`（非法转义不抛）。
**多语言实跑**：`腾讯控股→腾讯控股 (hk00700)`；`苹果→苹果 (usaapl.oq)`；`TSLA→特斯拉 (ustsla.oq)`。
> 判定：**✅ 已修**，且无回归、无过度解码。

### ② [原 MAJOR] 聚合容错击穿 —— ✅ 已修（含 1 项非阻塞残留）

**独立方法**：`Read` 代码 + 独立实跑违规响应。

代码核实：
- `_http_get_json`（`:99-106`）已将 `json.loads` 的 `JSONDecodeError/ValueError` 包为 `ToolError`；
- 新增 `_parse_xml`（`:109-114`）将 `ET.ParseError` 包为 `ToolError`；`ArxivProvider.search`（`:137`）已改用 `_parse_xml`。

**实测输出**：
```
_http_get_json('https://www.baidu.com', 15)  →  GUARD OK（正确抛 ToolError，非 JSONDecodeError 击穿）
_parse_xml(b'<not>xml', ...)                 →  ToolError OK
```
**真源数据路径穷举**（`inspect.getsource` 逐一核对 8 个真源）：`Arxiv/SemanticScholar/Crossref/Wikipedia/OpenAlex/EuropePMC/Wikidata/GDELT` 全部 `raw_json.loads=False`、`guarded_helper=True` —— **无一真源直接 `json.loads`**，故均受守卫。
**真源端到端聚合**（把 OpenAlex 端点指向返回 HTML 的站点）：
```
search_many(['python']) → 未击穿
  status = [('openalex', ok=False, '响应非 JSON: Expecting value...'), ('arxiv', ok=True)]
  其它源存活结果数 = 5
```
> 判定：**✅ 已修**。原 MAJOR 指向的失败模式「200 却返回 HTML/纯文本」（GDELT 等限流实况）已被规训为 `ToolError`，单源失败不再击穿整体聚合。
>
> **非阻塞残留（NOTE-R1）**：`SearchTool.search_many:593` 仍**仅捕获 `ToolError`**。实测两类边界仍会击穿：
> (a) 绕开 helper 的裸 `json.loads`（原始人工复现 `class BadJSON` 仍 `CRASH JSONDecodeError`）；(b) 合法 JSON 但顶层形状异常（`_http_get_json` 返回 list → `OpenAlexProvider` `CRASH AttributeError`）。
> 二者均非本轮 BLOCK/MAJOR 所指路径（真源全走 helper、真源解析均用 `.get` 取值），**现实触发概率低**，故不阻塞；建议后续在 `search_many` 增 `except Exception` 兜底（防御纵深），本轮不作要求。

### ③ [原 MINOR] OpenAlex 死回退 / 死读 —— ✅ 已修

- `:298` `select="display_name,doi,id,abstract_inverted_index"` —— **已无** `title_display`；
- `:306` 用 `w.get("display_name")`，**已删** `title_display` 死回退；
- `:307-309` 摘要反构保留，score 改为**显式常量 0.7** 并加注释「relevance_score 已被 select 排除」，死读消除。
- 实跑：`search('cancer immunotherapy',2)` → `n=2`、`score=0.7`（恒定）、摘要正常反构（如 `'The clinical relevance of T cells...'`），无 HTML。
> 判定：**✅ 已修**。

### ④ [原 MINOR] EuropePMC 摘要含 HTML —— ✅ 已修

- `:344` 已加 `re.sub(r"<[^>]+>", " ", it.get("abstractText") or "")`，与 `CrossrefProvider:218` 对齐。
- 实跑：`search('cancer',2)` → `content[:80]='Objectives This study aimed to investigate...'`（原 `<h4>Objectives</h4>` 标签已剥除），`has_HTML_tag=False`。
> 判定：**✅ 已修**。

### ⑤ [原 MINOR] admin `VALID_PROVIDERS` 未登记 keyless 源 —— ✅ 已修

- `server/admin.py:1363-1369` 改为**从引擎注册表派生**：`try: from tools.data_sources import KEYLESS_PROVIDERS as _KEYLESS …`，`VALID_PROVIDERS = ["tavily","mock","coming_soon"] + _KEYLESS_IDS`，并带 `try/except` 退化，不阻塞 admin。
- 独立复刻派生逻辑（等价 `:1364-1369`）：
```
VALID_PROVIDERS = ['tavily','mock','coming_soon','arxiv','crossref','europe_pmc','gdelt','openalex','semantic_scholar','tencent_quote','wikidata','wikipedia']
MISSING_NEW = []      # 5 新源全部在列
P1_ALSO = True        # P1 4 源亦补齐
```
> 说明：本沙箱解释器未安装 `ruamel`（`requirements.txt:34` 为**既有**声明依赖，非本次引入），故 `import server.admin` 无法整体执行；但派生所依赖的 `tools.data_sources` 导入正常，白名单结果如上，与源码逐字一致。
> 判定：**✅ 已修**（P1 遗漏一并补齐）。

### ⑥ [原 MINOR] `PROVIDER_REGISTRY` / `ComingSoonProvider` 死代码 —— ⚠️ 未处置（诚实留痕）

- 代码核实：`data_sources.PROVIDER_REGISTRY`（`:479-483`）与 `ComingSoonProvider`（`:68-78`）仍存在且**仍无引用**（`build_search_tool` 对 coming_soon 直接 `continue`）。
- `VERIFICATION_M10-P2.md §6` 已**主动声明**该遗留为 M9-2 债务、非本次引入、未扩大变更面。属诚实披露。
> 判定：**⚠️ 未修**（原即 MINOR，非阻塞）。

### ⑦ [原 NOTE] 注释 `v_pv_none_match` 与实际 `v_hint="N"` 不符 —— ✅ 已更正

- `:412` 注释现为 `# 无匹配：返回体为 v_pv_none_match="1"; / v_hint="N";`，两标记并列，反映实际响应。
> 判定：**✅ 已更正**。

---

## 回归与新增问题排查

| 项 | 独立方法 | 结果 |
|---|---|---|
| **回归 41 passed** | 独立 `python -m pytest tests/ -q` | ✅ `41 passed, 40 warnings in 3.53s`（退出码 0） |
| 编译 | `py_compile` `tools/data_sources.py` | ✅ OK |
| 新增依赖 | 检视 import + `requirements.txt` | ✅ 无新增第三方（仍 `json/os/re/urllib/xml.etree/typing`） |
| 破坏其它源 | 8 真源数据路径穷举 + OpenAlex/EuropePMC/腾讯实连 | ✅ 无回归 |
| 过度修复 | `_unescape_js` 边界 5 例 + 多语言实跑 | ✅ 无（ASCII/混合/非法转义均安全） |

**新增问题**：仅 NOTE-R1（`search_many` catch-all 缺失）——为本轮新观察，非修复引入的退化，现实触发概率低，判为**非阻塞**。

---

## 第二轮发现表

| 严重度 | 项 | 独立证据 | 判定 |
|---|---|---|---|
| ~~BLOCK~~ | 腾讯中文名乱码 | 实跑 `title='贵州茅台 (sh600519)'`，无 `\u` | ✅ 已修，**解除** |
| ~~MAJOR~~ | 解析异常击穿聚合 | `_http_get_json`→`GUARD OK`；8 真源全走守卫；E2E 单源失败其它源存活 | ✅ 已修，**解除** |
| MINOR | OpenAlex 死回退/死读 | `:298/:306/:309` 已清理，实跑 score=0.7 | ✅ 已修 |
| MINOR | EuropePMC HTML | 实跑 tags 已剥除 | ✅ 已修 |
| MINOR | admin `VALID_PROVIDERS` | 派生含全部 9 keyless id | ✅ 已修 |
| MINOR | 死代码 `PROVIDER_REGISTRY`/`ComingSoonProvider` | `:479-483`/`:68-78` 仍无引用 | ⚠️ 未处置（已声明债务，非阻塞） |
| NOTE | 注释标记不符 | `:412` 已更正 | ✅ |
| NOTE-R1 | `search_many` 仍只捕 `ToolError`，形状异常无兜底 | 裸 `json.loads`/顶层 list → 仍 `CRASH` | ⚠️ 非阻塞残留，建议后续加 `except Exception` |

---

## 最终裁定

**PASS_WITH_NOTES —— BLOCK 已解除**。

- 原 **1 BLOCK + 1 MAJOR** 两项阻塞级问题，均**独立实跑复现修复成功**，非自述采信；
- 原 4 项 MINOR 中 3 项已修、1 项（死代码）已诚实声明并留待办；原 NOTE 已更正；
- 回归 **41 passed**，无新增依赖、无回归、无过度修复；
- 仅剩 2 项**非阻塞**残留（NOTE-R1 + 死代码），不构成阻塞，建议并入技术债。

**可进入提交环节。**

<!-- reviewed-by: independent-subagent -->
<!-- 2026-09-10 · M10-P2 第二轮独立复审 · 独立子代理，主代理严禁自签 -->
