<!-- reviewed-by: independent-subagent -->

# REVIEW · M10-P3 电商 / 电商竞品分析数据源（第三闸 · 独立对抗式审议）

**审议者**：独立子代理（不参与实现，专职证伪）
**日期**：2026-09-10
**被审议变更**：`tools/data_sources.py` / `config/plugins.yaml` / `tests/test_tools.py` / `DESIGN_M10-P3.md` / `VERIFICATION_M10-P3.md`
**总裁定**：**PASS_WITH_NOTES**　`MAJOR=0`　`BLOCKER=0`

---

## §1 审议范围与独立执行的验证命令

所有命令均为本审议者实际执行，输出为真实截取，退出码附后。工作目录 `E:\第二电脑\report-agent-team`，解释器 `C:\Users\sfkj\.workbuddy\binaries\python\versions\3.13.12\python.exe`。

### 1.1 构建校验（本地）
```
$ python -m py_compile tools/data_sources.py
EXIT 0
$ python -c "import yaml; d=yaml.safe_load(open('config/plugins.yaml',encoding='utf-8')); print(len(d['plugins']))"
18
EXIT 0
$ python -m pytest tests/ -q
47 passed, 40 warnings in 2.01s   (EXIT 0)
$ python -m pytest tests/test_tools.py -q
23 passed   (EXIT 0)   # 17 既有 + 6 新增，与 VERIFICATION §2.3 一致
```

### 1.2 容器内核真值（container `report-api` Up/healthy，已用本次变更重建）
```
$ docker exec report-api python -c "from tools.data_sources import KEYLESS_PROVIDERS; print(len(KEYLESS_PROVIDERS)); print(sorted(KEYLESS_PROVIDERS))"
13
['accio_tariff','amazon_suggest','arxiv','crossref','ebay_suggest','europe_pmc','gdelt','openalex','semantic_scholar','taobao_suggest','tencent_quote','wikidata','wikipedia']

$ docker exec report-api python -c "from tools.data_sources import load_data_sources; [print(s['id'],s['category'],s['status'],s['auth_type']) for s in load_data_sources() if s['category']=='电商']"
taobao_suggest 电商 connected none
amazon_suggest 电商 connected none
ebay_suggest   电商 connected none
accio_tariff   电商 connected none

$ curl -s http://localhost:18080/api/v1/admin/plugins -> HTTP 200
total=18  电商=4  valid_providers_total=16 (含4新id)
category dist: {'数据源':1,'学术':6,'百科':2,'行情':2,'新闻':1,'财报':1,'工商':1,'电商':4}
  taobao_suggest 电商 connected none
  amazon_suggest 电商 connected none
  ebay_suggest   电商 connected none
  accio_tariff   电商 connected none
```

### 1.3 容器内真实取数（金标准，3 query × 4 源）
```
$ docker exec report-api python -c "...build_search_tool(e, load_secrets(), max_results=5, timeout=25); r,st=t.search_many(['蓝牙耳机','wireless earbuds','陶瓷咖啡杯|US'])..."
results: 27
dist: {'taobao_suggest': 6, 'amazon_suggest': 10, 'ebay_suggest': 10, 'accio_tariff': 1}
status ok: taobao=True/True/False(陶瓷咖啡杯|US空) amazon=True/True/False(空) ebay=False(蓝牙耳机空)/True/True accio=True/True/True
```
与 VERIFICATION §3.4 的「27 条 / 6-10-10-1」**逐字节一致**。

### 1.4 本审议者自建故障注入脚本（非复跑主代理）
写入并运行 `_review_faultinject.py`（已运行后删除），覆盖：Accio 5 类异常路径、ebay 7 种畸形 shape、taobao 5 种 result 形态、search_many 兜底网、max_results 7 种边界。关键输出：
- Accio：HTTPError/URLError/Timeout/非JSON → 全部 `ToolError`；缺 `data`/空 `hscodeInfo` → 返回 `[]`（诚实空）。**无未捕获异常**。
- ebay：`{}`/`["x"]`/`[1,2]`/`["kw","notalist"]` → `[]`；`["kw",["a","b"]]` → 2 条；`["kw",[1,2]]`（元素非 str）→ 产出 title `"1"/"2"`（**字符串化，非跳过**，见 M1）。
- taobao：`[]`/非 list/缺热度位 → 安全跳过或产出；无未捕获异常。
- search_many 兜底：将 amazon 实例 `search` 替换为 `raise RuntimeError` → 被 `except Exception` 拦下，聚合继续，结果非 0、不崩溃。**防御纵深成立**。
- max_results：None→5、0→5（见 M2）、-3→1、7→7、100→封顶20、`"abc"`→`int()` 抛 ValueError（见 N1）、5.0→5。**无崩溃**。

> 透明度声明：首轮组合脚本中 ebay 一度显示「空结果」，本审议者随后做隔离复跑，确认 ebay 在 `search_many` 下正确返回 5（组合）/10（隔离）条且 `ok=True`——首轮为 monkeypatch 顺序的**测试台假象**，非代码缺陷。容错契约结论不受影响。

---

## §2 逐项裁定

### 2.1 设计-实现一致性 —— PASS
- DESIGN §6b.5 四端点（`suggest.taobao.com/sug`、`completion.amazon.com/api/2017/suggestions`、`autosug.ebay.com/autosug`、`POST www.accio.com/api/turtle/classify`）与实现逐一吻合。
- 4 类均注册进 `KEYLESS_PROVIDERS`（9→13，容器内证实 13）。
- accio 为唯一 POST 源，自建 `urllib.request.Request`（既有 `_http_get` 仅 GET），符合描述。
- 解析逻辑与 DESIGN 描述一致；无 HS 码返空不编造，符合 §6b.4 诚实边界。
- 偏差点：DESIGN 标题承诺的「通用 REST 桥 / `GenericRestProvider`」本里程碑**未交付**，实际交付 4 个硬编码 keyless 源——但 DESIGN §6.3 已显式将桥降为可选、改为先直连真源。**属已记录的重范围，非隐瞒**（见 M3）。

### 2.2 容错契约（M10-P2 审议确立：单源任何异常不得击穿聚合）—— PASS
- Accio 全异常路径包成 `ToolError` 或诚实空，已实测。
- ebay/taobao 畸形响应安全返空或跳过，已实测。
- `search_many` 的 `except ToolError` + `except Exception` 双兜底，经注入 `RuntimeError` 实测拦住，聚合不击穿。契约**成立**。

### 2.3 参数边界（max_results）—— PASS_WITH_NOTES
- 正常/负数/超限/None/浮点均不崩溃且合理封顶（20）。
- `0` 静默视作 5（M2）；非数字字符串在 `search()` 内抛 ValueError——但生产路径 `tools/__init__.py:58` 已 `int(...)` 提前收敛，不可达（N1）。与全部 13 源同模式，非本里程碑回归。

### 2.4 诚实性（VERIFICATION_M10-P3.md 是否被高估/伪造）—— PASS
独立复跑逐条核对：
- §3.4「27 条 / 6-10-10-1」→ 实测 27 / 分布完全一致 ✓
- §3.4 Accio 表（69111080/40.8%、69111045/26.5%、85183020/12.5%）→ 实测逐项一致 ✓
- §3.4「`不锈钢保温杯|DE` 返回空」（不编造的正面证据）→ 独立复跑 **EMPTY** ✓ **无伪造**
- §3.5 API 18 总数 / 电商 4 / 全 connected / valid_providers 含 4 新 id → 一致 ✓
- §3.7「ebay 该轮 False 系 query 语种匹配，非连接故障」→ 实测 ebay 对中文「蓝牙耳机」空、对英文「wireless earbuds」10 条 ✓ 解释属实
- §2.2 pytest 47 passed / test_tools.py 23 passed → 实测一致 ✓
- 结论：**VERIFICATION 文档无夸大、无伪造，自审诚实**。

### 2.5 分类（boss「要记得分类」）—— PASS
- 4 源代码内 `self.category="电商"`（data_sources.py:486/524/560/604）。
- plugins.yaml 4 条 `category: 电商`（diff 仅 `+` 追加，未覆盖既有条目）。
- 容器内 + API 均确认 4 源落入独立「电商」分类。boss 诉求满足。

---

## §3 发现清单

### BLOCKER — 无（0）

### MAJOR — 无（0）

### MINOR
- **M1｜EbaySuggestProvider 非字符串元素字符串化**
  `tools/data_sources.py:573` `kw = str(kw).strip()`。证据：注入 `["kw",[1,2]]` → 产出 `title "1"/"2"`（未跳过）。建议：对元素加 `isinstance(kw,str)` 守卫，非 str 跳过。低严重度（现实 API 返字符串，漂移时才可能出脏记录）。

- **M2｜`max_results=0` 静默回退为 5**
  `tools/data_sources.py:510/546/582/655`（及既有 139/175/211/246/302/335/373/434/454）。证据：注入 `max_results=0` → 各源返回 5 条。根因 `int(max_results or 5)` 中 `0 or 5 == 5`。建议：若需「0 条」语义，改为 `int(max_results) if max_results is not None else 5`；否则文档注明 0 视为默认。**注意：与 M10-P1/P2 全部既有源一致，非本里程碑引入的回归。**

- **M3｜设计中心交付物 `GenericRestProvider`（通用 REST 桥）本里程碑未实现**
  DESIGN_M10-P3.md 标题/§3 以「桥」为核心，落地为 4 个硬编码 keyless 源。属 DESIGN §6.3 已记录的重新范围，非隐瞒。建议：向 boss 明确「桥未做、仅 4 真源」，避免误以为万能桥已就绪。

### NIT
- **N1｜非数字字符串 `max_results` 抛 ValueError**
  `tools/data_sources.py:510/546/582/655`。证据：注入 `"abc"` → `int("abc")` ValueError。但 `tools/__init__.py:58` 已 `int(ws_cfg.get("max_results",5))` 提前收敛，生产不可达；即便到达 `search_many` 也被 `except Exception` 兜住。与 13 源同模式，非阻塞。

- **N2｜`_recompute_connected` 对 `auth=none` 无条件返回 connected**
  `tools/data_sources.py:731-732`。「connected」语义为「引擎会真实发起请求」，非「端点已验证可达」。本里程碑 4 源经容器真取数证实可用，故当前无虚假连接；但标签属静态，下游端点宕机时仍显示 connected（与 M10-P1/P2 全部 keyless 源一致）。可选建议：UI 区分「已配置/待验证」与「已验证可达」。

---

## §4 总裁定

**PASS_WITH_NOTES**

- 设计-实现一致（含已记录的桥降级）。
- 容错契约成立（Accio/ebay/taobao 异常全包 ToolError 或诚实空；`search_many` 兜底实测拦住 RuntimeError）。
- 诚实性：VERIFICATION_M10-P3.md 经独立复跑逐条证实，**无夸大、无伪造**，自审可信。
- 分类：4 源确属独立「电商」类，满足 boss「要记得分类」。
- `MAJOR = 0`，`BLOCKER = 0`。

---

## §5 建议但非阻塞项（PASS_WITH_NOTES）

| 编号 | 项 | 建议 | 是否阻塞 |
|---|---|---|---|
| M1 | ebay 非 str 元素字符串化（:573） | 加 `isinstance(kw,str)` 守卫跳过 | 否 |
| M2 | `max_results=0` 静默为 5（:510/546/582/655） | 明确 0 语义或文档注明 | 否 |
| M3 | `GenericRestProvider` 桥未交付 | 向 boss 明示范围，避免误解 | 否 |
| N1 | 非数字 `max_results` 抛错（不可达） | 可加类型断言，非必须 | 否 |
| N2 | `auth=none` 静态 connected 标签 | UI 可细分可达性 | 否 |

**结论**：可放行。上述项均不阻断 M10-P3 合并；其中 M3 建议尽快向 boss 说明范围，以免「通用 REST 桥」预期落空。

---

## §6 补记：M1 修复（主代理实施，审议者裁决仍有效）

**时间**：2026-09-10（审议 PASS 后）

| 编号 | 处置 | 证据 |
|---|---|---|
| M1 | **已修复**。`EbaySuggestProvider` 加 `isinstance(kw, str)` 守卫，非字符串元素跳过（不再 `str()` 字符串化） | `tools/data_sources.py`；新增断言 `test_ebay_suggest_parses_bare_array` 内 `["ok",1,None,["x"],"ok2"] → ["ok","ok2"]`；回归 `pytest tests/ -q` → **47 passed** |
| M2 | **不修（有意）**。`max_results=0` 回退默认与全部 13 个既有源同模式，单独改 4 个源反造成不一致；属既有约定，另案统一处理 | 审议原文已注明「非本里程碑引入的回归」 |
| M3 | **已知范围**。`GenericRestProvider` 通用 REST 桥本里程碑未交付，仅交付 4 个 keyless 真源 —— 已向 boss 明示，待其决策是否做 P3-b 桥 | DESIGN_M10-P3.md §6.3 |
| N1/N2 | 记录在案，不处理 | 不可达 / 与既有源一致 |

**说明**：§1-§5 为独立子代理原始审议内容，未改动；本 §6 为主代理在审议 PASS 后的实施补记。裁决不因此变更（M1 非阻塞项，修复后更佳）。
