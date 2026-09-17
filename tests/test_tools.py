# tests/test_tools.py · M4 工具层单元测试
#
# 运行：pip install -r requirements.txt && pytest tests/ -q

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tools import ToolError, ToolBundle  # noqa: E402
from tools.web_search import (  # noqa: E402
    WebSearchTool, MockProvider, score_to_credibility,
)
from tools.data_proc import DataProcTool, safe_eval  # noqa: E402
from tools.doc_export import DocExportTool  # noqa: E402


# ---------------- data_proc：安全求值（绝不 eval 任意字符串）----------------

def test_safe_eval_arithmetic():
    assert safe_eval("1 + 2 * 3") == 7
    assert safe_eval("(1 + 2) * 3") == 9
    assert safe_eval("2 ** 10") == 1024
    assert safe_eval("7 % 3") == 1
    assert safe_eval("mean([1, 2, 3, 4])") == 2.5
    assert safe_eval("max([3, 9, 2])") == 9
    assert safe_eval("round(3.14159, 2)") == 3.14


@pytest.mark.parametrize("expr", [
    "__import__('os')",       # 任意函数调用
    "open('/etc/passwd')",    # 文件访问
    "os.system('ls')",        # 属性访问
    "eval('1+1')",            # 危险内建
    "x",                      # 名称访问
    "1 if 2 else 3",          # 条件表达式
    "[i for i in range(10)]",  # 推导式
    "'abc'",                  # 非数值常量
])
def test_safe_eval_rejects_dangerous(expr):
    with pytest.raises(ToolError):
        safe_eval(expr)


def test_data_proc_failure_isolation():
    """单条表达式非法不影响其余；失败进 tool_status.ok=false。"""
    tool = DataProcTool(enabled=True)
    results, status = tool.run_requests([{"expr": "1+1"}, {"expr": "__import__('os')"}, {"expr": "2*3"}])
    assert [r["value"] for r in results if r.get("ok")] == [2, 6]
    assert any(not s["ok"] for s in status)
    assert all("error" in s for s in status if not s["ok"])


# ---------------- web_search：契约化 + 失败隔离 + 去重 ----------------

def test_search_many_contract_and_dedupe():
    tool = WebSearchTool(provider=MockProvider(), max_results=3)
    results, status = tool.search_many(["q1", "q2"])
    assert len(results) == 6
    assert [r["id"] for r in results] == [f"rec-{i}" for i in range(1, 7)]
    assert all({"id", "url", "title", "snippet", "credibility"} <= set(r) for r in results)
    assert all(s["ok"] for s in status) and len(status) == 2


def test_search_failure_isolation():
    """单条 query 失败 → 该条 ok=false，其余继续，不整体崩。"""

    class Flaky:
        def search(self, query, max_results):
            if query == "bad":
                raise ToolError("模拟限流")
            # 每个 query 返回不同 url（否则会被按 url 去重合并，误判为隔离失效）
            return [{"title": "t", "url": f"https://a.example/{query}",
                     "content": "c", "score": 0.9}]

    tool = WebSearchTool(provider=Flaky(), max_results=3)
    results, status = tool.search_many(["good", "bad", "good2"])
    assert len(results) == 2, "失败 query 不应影响其余检索"
    assert [s["ok"] for s in status] == [True, False, True]
    assert "模拟限流" in status[1]["error"]


def test_credibility_mapping():
    assert score_to_credibility(0.9) == "high"
    assert score_to_credibility(0.5) == "medium"
    assert score_to_credibility(0.1) == "low"
    assert score_to_credibility(None) == "medium"  # 无分值保守取中


# ---------------- doc_export：引用章节真实性校验 ----------------

def _recs():
    return [{"id": "rec-1", "url": "https://real.example/1", "title": "T1", "credibility": "high"}]


def test_citation_added_when_missing():
    tool = DocExportTool(enabled=True, export_dir=None)
    md, regenerated, _ = tool.ensure_citation_section("# R\n\n## 正文\n内容\n", _recs())
    assert regenerated is True
    assert "## 引用 / 来源" in md and "https://real.example/1" in md


def test_citation_regenerated_when_foreign_url():
    """形式上有引用章节、但含编造 url → 必须重建（不能只看有没有标题）。"""
    tool = DocExportTool(enabled=True, export_dir=None)
    fake_md = "# R\n\n## 引用 / 来源\n- [rec-1](https://fake.example/xxx)\n"
    md, regenerated, _ = tool.ensure_citation_section(fake_md, _recs())
    assert regenerated is True
    assert "fake.example" not in md
    assert "https://real.example/1" in md


def test_citation_kept_when_valid():
    tool = DocExportTool(enabled=True, export_dir=None)
    good = "# R\n\n## 引用 / 来源\n- [rec-1](https://real.example/1) — T1\n"
    md, regenerated, _ = tool.ensure_citation_section(good, _recs())
    assert regenerated is False and md == good


def test_export_writes_file(tmp_path):
    tool = DocExportTool(enabled=True, export_dir=tmp_path)
    path = tool.export("# R\n", topic="测 试/主题")
    assert path and Path(path).exists()
    assert Path(path).read_text(encoding="utf-8") == "# R\n"


# ---------------- M10-P3 电商源：离线解析器契约（monkeypatch，不发真实网络）----------------
# 锁协议：这 4 个源的响应 shape 各异（eBay 是裸数组、Accio 是三层嵌套），
# 一旦端点协议漂移，须在此先红——不依赖联网 smoke。

from tools import data_sources as ds  # noqa: E402


def test_taobao_suggest_parses_pairs(monkeypatch):
    """淘宝 v_hint 是 [[词, 热度], ...] 二维数组；URL 必须带 q 编码。"""
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: {
        "result": [["蓝牙耳机2026新款", "100"], ["蓝牙耳机降噪", "98"], [], ["只有词"]],
    })
    out = ds.TaobaoSuggestProvider(timeout=1).search("蓝牙耳机", 5)
    # 空数组与缺热度项仍须产出（容错），但必须按键取词
    assert out[0]["title"] == "蓝牙耳机2026新款"
    assert "热度分 100" in out[0]["content"]
    assert out[0]["url"].startswith("https://s.taobao.com/search?")
    assert all({"title", "url", "content", "score"} <= set(r) for r in out)


def test_amazon_suggest_parses_values(monkeypatch):
    """Amazon shape: {"suggestions":[{"value":...}]}；URL 落到 /s?k=。"""
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: {
        "suggestions": [{"value": "wireless earbuds", "type": "KEYWORD"},
                        {"value": "  "},  # 空白须跳过
                        {"value": "wireless earbuds 2026"}],
    })
    out = ds.AmazonSuggestProvider(timeout=1).search("wireless earbuds", 5)
    assert [r["title"] for r in out] == ["wireless earbuds", "wireless earbuds 2026"]
    assert out[0]["url"].startswith("https://www.amazon.com/s?")


def test_ebay_suggest_parses_bare_array(monkeypatch):
    """eBay 返回裸 JSON 数组 ["echo", [sug...]]；非该 shape 必须安全返回空，不得抛错。"""
    monkeypatch.setattr(ds, "_http_get_json",
                        lambda url, timeout, headers=None: ["kwd", ["a b", "c d"]])
    out = ds.EbaySuggestProvider(timeout=1).search("kwd", 5)
    assert [r["title"] for r in out] == ["a b", "c d"]
    assert out[0]["url"].startswith("https://www.ebay.com/sch/i.html?")

    # 异常 shape（错误对象 / 缺第二项）→ 空，不崩
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: {"err": 1})
    assert ds.EbaySuggestProvider(timeout=1).search("kwd", 5) == []
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: ["only"])
    assert ds.EbaySuggestProvider(timeout=1).search("kwd", 5) == []

    # 非字符串元素须跳过（协议漂移守卫，M10-P3 审议 M1）
    monkeypatch.setattr(ds, "_http_get_json",
                        lambda url, timeout, headers=None: ["kw", ["ok", 1, None, ["x"], "ok2"]])
    assert [r["title"] for r in ds.EbaySuggestProvider(timeout=1).search("kwd", 5)] == ["ok", "ok2"]


def test_accio_tariff_parses_nested_and_splits_dest(monkeypatch):
    """Accio 三层嵌套 data.data；query 的 `|目的国` 须被解析并回显；无 HS 码 → 空。"""
    captured = {}

    class FakeResp:
        status = 200

        def read(self_inner):
            return json.dumps({"data": {"success": True, "data": {
                "hscodeInfo": {"hscode": "69111045",
                               "descriptionEn": "Tableware ... Mugs and other steins"},
                "tariffRate": 26.5,
                "tariffFormula": "一般关税[14%] + 附加关税[12.5%]",
            }}}).encode("utf-8")

        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(ds.urllib.request, "urlopen", fake_urlopen)
    out = ds.AccioTariffProvider(timeout=1).search("陶瓷咖啡杯|US", 5)
    assert captured["body"]["destinationCountryCode"] == "US"
    assert captured["body"]["originCountryCode"] == "CN"
    assert captured["body"]["productName"] == "陶瓷咖啡杯"
    assert "HS 69111045" in out[0]["title"] and "26.5" in out[0]["title"]
    assert "一般关税[14%]" in out[0]["content"]

    # 无 HS 码 → 诚实返回空（不编造）
    monkeypatch.setattr(ds.urllib.request, "urlopen",
                        lambda req, timeout=None: type("R", (), {
                            "read": lambda s: b'{"data":{"data":{}}}',
                            "__enter__": lambda s: s, "__exit__": lambda s, *a: False})())
    assert ds.AccioTariffProvider(timeout=1).search("x", 5) == []

    # 缺省目的国必须回落 US
    monkeypatch.setattr(ds.urllib.request, "urlopen", fake_urlopen)
    ds.AccioTariffProvider(timeout=1).search("mug", 5)
    assert captured["body"]["destinationCountryCode"] == "US"


def test_ecom_providers_registered_keyless():
    """4 个电商源必须在 KEYLESS_PROVIDERS 登记（否则 build_search_tool 会当 coming_soon 跳过）。"""
    for pid, cls in [("taobao_suggest", ds.TaobaoSuggestProvider),
                     ("amazon_suggest", ds.AmazonSuggestProvider),
                     ("ebay_suggest", ds.EbaySuggestProvider),
                     ("accio_tariff", ds.AccioTariffProvider)]:
        assert ds.KEYLESS_PROVIDERS.get(pid) is cls
    # 且均为 auth=none / category=电商
    for cls in (ds.TaobaoSuggestProvider, ds.AmazonSuggestProvider,
                ds.EbaySuggestProvider, ds.AccioTariffProvider):
        inst = cls(timeout=1)
        assert inst.auth_type == "none" and inst.category == "电商"


def test_ecom_sources_reach_aggregation(monkeypatch):
    """端到端接缝：4 个电商 spec 经 build_search_tool 应装载真 Provider（非 Mock），
    且结果带 source=各自 id。"""
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: (
        {"result": [["kw", "100"]]} if "suggest.taobao" in url
        else {"suggestions": [{"value": "kw"}]} if "completion.amazon" in url
        else ["kw", ["kw"]]
    ))
    specs = [
        {"id": "taobao_suggest", "name": "淘宝", "provider": "taobao_suggest", "enabled": True},
        {"id": "amazon_suggest", "name": "Amazon", "provider": "amazon_suggest", "enabled": True},
        {"id": "ebay_suggest", "name": "eBay", "provider": "ebay_suggest", "enabled": True},
    ]
    tool = ds.build_search_tool(specs, secrets={}, max_results=5, timeout=1)
    assert tool.using_mock_search is False
    assert len(tool._providers) == 3
    results, status = tool.search_many(["kw"], agent="Researcher")
    assert {r["source"] for r in results} == {"taobao_suggest", "amazon_suggest", "ebay_suggest"}
    assert all(s["ok"] for s in status)



# ---------------------------------------------------------------------------
# M10-P4：句子式 query → 核心关键词抽取（联想词接口只吃短语，不吃整句）
# 背景实测：引擎 build_search_queries() 下发的是「主题整句」，直接请求 suggest
# 接口返回空数组（整句 0 结果 / 短语 5 结果）。此抽取器是该 gap 的修复点，
# 协议或策略一改须在此先红。
# ---------------------------------------------------------------------------

def test_extract_keyword_cjk_sentence_to_phrase():
    """CJK 整句应抽到主品类短语，而非整句原样透传。"""
    kw, trunc = ds._extract_keyword("无线蓝牙耳机跨境选品与关税成本分析")
    assert kw == "无线蓝牙耳机"
    assert trunc is True


def test_extract_keyword_cjk_already_short():
    """已是短语则原样返回且标记未截断（不得误报 truncated）。"""
    kw, trunc = ds._extract_keyword("蓝牙耳机")
    assert kw == "蓝牙耳机" and trunc is False


def test_extract_keyword_strips_modifier_suffix():
    kw, _ = ds._extract_keyword("陶瓷马克杯市场分析")
    assert kw == "陶瓷马克杯"


def test_extract_keyword_keeps_latin_spaces():
    """英文短语的空格是词内分隔，不得按空格切成单词（回归：曾把 'wireless earbuds' 切成 'wireless'）。"""
    kw, trunc = ds._extract_keyword("wireless earbuds")
    assert kw == "wireless earbuds", "空格短语须保留为整体"
    assert trunc is False
    # 多词短语：截到核心品类词（前 2 词），但仍是多词短语而非单词
    kw2, trunc2 = ds._extract_keyword("bluetooth speaker portable")
    assert kw2 == "bluetooth speaker", "应保留为短语，不得退化成单字"
    assert " " in kw2 and trunc2 is True


def test_extract_keyword_latin_sentence_to_core():
    """英文整句应剥掉分析类虚词，收敛到核心品类词。"""
    kw, trunc = ds._extract_keyword("wireless earbuds cross-border selection and tariff analysis")
    assert kw == "wireless earbuds" and trunc is True


def test_extract_keyword_empty_is_safe():
    """空/空白输入返回空串且不抛异常；调用方据此直接返回空结果。"""
    for src in ("", "   ", None):
        kw, trunc = ds._extract_keyword(src)
        assert kw == "" and trunc is False


def test_suggest_providers_annotate_truncation(monkeypatch):
    """整句查询时，结果正文必须显式标注发生了关键词截取（诚实边界）。"""
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: {
        "result": [["蓝牙耳机降噪", "98"]],
    })
    out = ds.TaobaoSuggestProvider(timeout=1).search("无线蓝牙耳机跨境选品与关税成本分析", 5)
    assert out, "整句经抽取后应能取到结果"
    assert "已截取核心词" in out[0]["content"]


def test_suggest_providers_empty_query_makes_no_request(monkeypatch):
    """抽取结果为空时不得发起无意义请求（提前返回，_http_get_json 不应被调用）。"""
    called = []
    monkeypatch.setattr(ds, "_http_get_json",
                        lambda url, timeout, headers=None: called.append(url) or {})
    assert ds.TaobaoSuggestProvider(timeout=1).search("   ", 5) == []
    assert ds.AmazonSuggestProvider(timeout=1).search("", 5) == []
    assert ds.EbaySuggestProvider(timeout=1).search("", 5) == []
    assert called == [], "空查询不应触发任何 HTTP 请求"


# ---------------------------------------------------------------------------
# M10-P4 补强（独立审议 M5 指出的未测边界）
# ---------------------------------------------------------------------------

def test_extract_keyword_prefers_cjk_in_mixed_input():
    """中英混合须优先取 CJK 商品词。

    回归：独立审议实测 `iPhone 16 Pro Max 手机壳` 曾抽成 `iPhone`（按片段长度取最长
    命中了英文片段），导致检索误靶向——真实商品意图是「手机壳」。
    """
    for src, want in [("iPhone 16 Pro Max 手机壳", "手机壳"),
                      ("HS 8518.30 耳机", "耳机")]:
        kw, _ = ds._extract_keyword(src)
        assert kw == want, f"{src!r} 应抽出 CJK 商品词 {want!r}，实际 {kw!r}"


def test_extract_keyword_rejects_garbage_input(capsys):
    """纯标点 / 纯 emoji 无检索语义，须判为「无可抽取」→ 调用方不发请求。

    回归：独立审议实测 `！！！` / `😀🔥` 曾原样透传并触发无意义 HTTP。
    """
    for src in ("！！！", "😀🔥", "！！！???", "..."):
        kw, trunc = ds._extract_keyword(src)
        assert kw == "", f"{src!r} 应判为无可抽取，实际返回 {kw!r}"
        assert trunc is False, "无可抽取时不应声称发生了截断"


def test_extract_keyword_garbage_makes_no_http_request(monkeypatch):
    """垃圾输入经 Provider 时不得触发任何 HTTP（端到端守卫生效）。"""
    called = []
    monkeypatch.setattr(ds, "_http_get_json",
                        lambda url, timeout, headers=None: called.append(url) or {})
    for src in ("！！！", "😀🔥"):
        assert ds.TaobaoSuggestProvider(timeout=1).search(src, 5) == []
        assert ds.AmazonSuggestProvider(timeout=1).search(src, 5) == []
        assert ds.EbaySuggestProvider(timeout=1).search(src, 5) == []
    assert called == [], f"垃圾输入不应发请求，实际发了 {len(called)} 次"


def test_has_searchable_content_helper():
    """守卫函数本身：含 CJK/字母/数字 → True；纯符号/emoji/空 → False。"""
    assert ds._has_searchable_content("蓝牙")
    assert ds._has_searchable_content("iPhone")
    assert ds._has_searchable_content("8518")
    assert not ds._has_searchable_content("！！！")
    assert not ds._has_searchable_content("😀")
    assert not ds._has_searchable_content("")


# ---------------------------------------------------------------------------
# M12 / T40：Open-Meteo 中文城市 geocoding 别名回落
# 背景实测：geocoding 直接用中文地名查 zh/en 两遍都空（Open-Meteo 对中文名无匹配），
# 导致「厦门天气」永远返回空。修复：命中 _CN_CITY_ALIASES → 追加英文名再查（Xiamen 稳定命中）。
# ---------------------------------------------------------------------------

def test_open_meteo_cn_city_geocoding_alias(monkeypatch):
    """中文城市「厦门」geocoding 无匹配时，必须回落英文别名 Xiamen 并命中。
    回归：此前 geocoding 直接用中文地名查两遍都空 → 天气永远返回空。"""
    GEO = "geocoding-api.open-meteo.com"
    FC = "api.open-meteo.com/v1/forecast"

    def fake_retry(url, timeout, attempts=3, backoff=0.6, headers=None):
        if GEO in url:
            # geocoding：Xiamen 命中；厦门（中文）给空（模拟真实无匹配）
            if "name=Xiamen" in url:
                return {"results": [{"latitude": 24.48, "longitude": 118.09,
                                     "name": "Xiamen", "country": "China"}]}
            return {}
        assert FC in url, url
        return {"current": {"temperature_2m": 28.5, "weather_code": 1,
                            "apparent_temperature": 30.0, "relative_humidity_2m": 70,
                            "wind_speed_10m": 10, "precipitation": 0}}

    monkeypatch.setattr(ds, "_http_get_json_retry", fake_retry)
    out = ds.OpenMeteoProvider(timeout=1).search("厦门天气怎么样", 5)
    assert out, "厦门应经 Xiamen 别名命中，不得返回空"
    assert any("Xiamen" in (r.get("title") or "") for r in out), "title 须含命中地名 Xiamen"
    assert any("28.5" in (r.get("content") or "") for r in out), "content 须含 forecast 气温"


def test_open_meteo_english_city_direct(monkeypatch):
    """英文城市名（Xiamen weather）直接命中，无需别名。"""
    GEO = "geocoding-api.open-meteo.com"
    FC = "api.open-meteo.com/v1/forecast"

    def fake_retry(url, timeout, attempts=3, backoff=0.6, headers=None):
        if GEO in url:
            if "name=Xiamen" in url:
                return {"results": [{"latitude": 24.48, "longitude": 118.09,
                                     "name": "Xiamen", "country": "China"}]}
            return {}
        assert FC in url, url
        return {"current": {"temperature_2m": 25.0, "weather_code": 0,
                            "relative_humidity_2m": 60, "wind_speed_10m": 8, "precipitation": 0}}

    monkeypatch.setattr(ds, "_http_get_json_retry", fake_retry)
    out = ds.OpenMeteoProvider(timeout=1).search("Xiamen weather today", 5)
    assert out and any("Xiamen" in (r.get("title") or "") for r in out)


def test_open_meteo_geocoding_truly_empty_returns_empty(monkeypatch):
    """geocoding 全空（含别名也查不到）→ 诚实返回空，不得冒充可用。"""
    monkeypatch.setattr(ds, "_http_get_json", lambda url, timeout, headers=None: {})
    monkeypatch.setattr(ds, "_http_get_json_retry",
                        lambda url, timeout, attempts=3, backoff=0.6, headers=None: {})
    assert ds.OpenMeteoProvider(timeout=1).search("火星天气", 5) == []


# ---------------------------------------------------------------------------
# M12 / T41：search_many 按查询意图路由源（仅 ChatAgent 启用 route_by_intent）
# 天气意图只调 open_meteo(+web 兜底)，新闻意图只调 rss_news(+web)，通用/研究调全部。
# ---------------------------------------------------------------------------

class _RouteProbe:
    def __init__(self, pid, intent):
        self.id = pid
        self.INTENT = intent
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append(query)
        return [{"title": self.id, "url": f"https://x/{self.id}",
                 "content": "", "score": 0.5, "source": self.id}]


def _build_routed_tool():
    probes = {
        "open_meteo": _RouteProbe("open_meteo", "weather"),
        "rss_news": _RouteProbe("rss_news", "news"),
        "duckduckgo": _RouteProbe("duckduckgo", "web"),
        "crossref": _RouteProbe("crossref", "research"),
    }
    providers = [({"id": pid}, p) for pid, p in probes.items()]
    tool = ds.SearchTool(providers=providers, max_results=3, timeout=1,
                         using_mock_search=False)
    return probes, tool


def test_query_intent_classification():
    assert ds._query_intent("厦门今天天气") == "weather"
    assert ds._query_intent("今天有什么热门新闻") == "news"
    assert ds._query_intent("量子计算最新进展") == "general"


def test_search_many_routes_weather_only_openmeto_and_web():
    probes, tool = _build_routed_tool()
    tool.search_many(["厦门天气"], agent="ChatAgent", route_by_intent=True)
    assert probes["open_meteo"].calls and probes["duckduckgo"].calls
    assert not probes["rss_news"].calls, "新闻源不应在天气意图中被调用"
    assert not probes["crossref"].calls, "学术源不应在天气意图中被调用"


def test_search_many_routes_news_only_rss_and_web():
    probes, tool = _build_routed_tool()
    tool.search_many(["今天热门新闻"], agent="ChatAgent", route_by_intent=True)
    assert probes["rss_news"].calls and probes["duckduckgo"].calls
    assert not probes["open_meteo"].calls, "天气源不应在新闻意图中被调用"
    assert not probes["crossref"].calls, "学术源不应在新闻意图中被调用"


def test_search_many_general_hits_all_sources():
    probes, tool = _build_routed_tool()
    tool.search_many(["量子计算最新进展"], agent="ChatAgent", route_by_intent=True)
    assert all(p.calls for p in probes.values()), "通用意图须调全部源"


def test_search_many_no_route_calls_all():
    """route_by_intent=False（引擎研究检索）须调全部源，零回归。"""
    probes, tool = _build_routed_tool()
    tool.search_many(["厦门天气"], agent="Researcher", route_by_intent=False)
    assert all(p.calls for p in probes.values())


# ---------------------------------------------------------------------------
# M12 / T44：聊天体受限沙箱（run_python / run_shell）
# ---------------------------------------------------------------------------

from tools import chat_sandbox  # noqa: E402


def test_chat_sandbox_blocks_dangerous_command():
    res = chat_sandbox.run_shell("rm -rf /")
    assert res["ok"] is False
    assert "拦截" in res["error"], "危险命令必须被拦截"


def test_chat_sandbox_blocks_windows_bypass_and_separators():
    """Windows 绕过手法（rd /s /q、powershell、python -c）与分隔符串联（echo hi; rm -rf /）
    必须被拦截，不得靠子串巧合放行。"""
    for cmd in ("rd /s /q C:\\", "powershell -c 'rm x'", "python -c 'import os'",
                "echo hi; rm -rf /", "echo a && del /f b", "cat x | sh"):
        res = chat_sandbox.run_shell(cmd)
        assert res["ok"] is False, f"应拦截命令：{cmd!r}（实际 {res}）"


def test_chat_sandbox_runs_python():
    res = chat_sandbox.run_python("print(1 + 1)\nprint('hi')")
    assert res["ok"] is True
    assert "2" in res["stdout"] and "hi" in res["stdout"]
    assert res["exit_code"] == 0


def test_chat_sandbox_python_is_confined():
    """受限命名空间：不得导入 os/subprocess，不得读写沙箱外文件，不得 os.system。"""
    res = chat_sandbox.run_python("import os\nsys.exit(0)")
    assert res["ok"] is False, "os 模块必须被禁止导入"
    assert "禁止导入" in res["stderr"] or "Error" in res["stderr"]
    # 白名单内模块可用
    ok = chat_sandbox.run_python("import math\nprint(math.sqrt(16))")
    assert ok["ok"] is True and "4.0" in ok["stdout"]


def test_chat_sandbox_runs_shell():
    res = chat_sandbox.run_shell("echo hello-sandbox")
    assert res["ok"] is True
    assert "hello-sandbox" in res["stdout"]


# ---------------------------------------------------------------------------
# M12 / T42：聊天体专用工具 schema（仅 ChatAgent 暴露，引擎研究检索不可见）
# ---------------------------------------------------------------------------

def test_chat_agent_only_schemas_exposed_to_chatagent():
    from orchestrator import _build_tool_schemas  # noqa: E402

    chat_schemas = _build_tool_schemas(None, None, role="ChatAgent")
    chat_names = {s["function"]["name"] for s in chat_schemas}
    assert {"fetch_url", "install_skill", "run_python", "run_shell"} <= chat_names

    research_schemas = _build_tool_schemas(None, None, role="Researcher")
    research_names = {s["function"]["name"] for s in research_schemas}
    assert not ({"fetch_url", "install_skill", "run_python", "run_shell"} & research_names), \
        "聊天体专用工具不得泄漏给引擎研究检索"


# ---------------------------------------------------------------------------
# M12-2：聊天体只读抓取原语 fetch_url（抓上网，研究/学习用）
# ---------------------------------------------------------------------------
def test_web_fetch_blocks_non_http_scheme():
    from tools import web_fetch

    res = web_fetch.fetch_url("file:///etc/passwd")
    assert res["ok"] is False
    assert "http" in res["error"].lower()


def test_web_fetch_blocks_ssrf_private_loopback():
    """私有/环回/链路本地地址必须被 SSRF 防护拒绝（含云元数据 169.254.169.254）。"""
    from tools import web_fetch

    for url in ("http://127.0.0.1:8080/secret",
                "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.1/",
                "http://localhost:9000/",
                "http://[::1]/"):
        res = web_fetch.fetch_url(url)
        assert res["ok"] is False, f"应拦截 SSRF 地址：{url!r}（实际 {res}）"
        assert "SSRF" in res["error"], f"应给出 SSRF 原因：{url!r}（实际 {res['error']}）"


def test_web_fetch_missing_url():
    from tools import web_fetch

    res = web_fetch.fetch_url("")
    assert res["ok"] is False and "url" in res["error"].lower()


def test_web_fetch_github_repo_readme(monkeypatch):
    """GitHub 仓库根链接 → 自动定位 README 直链抓取（无需真实联网）。"""
    from tools import web_fetch

    readme_body = "# agency-agents\n\nA multi-agent framework for research.\n"

    def fake_get(url, timeout=20):
        # 依次尝试的 README 候选里，命中 README.md 时返回正文
        if url.endswith("README.md"):
            return (readme_body.encode("utf-8"), "text/plain; charset=utf-8", url)
        raise web_fetch.WebFetchError(f"HTTP 404 抓取失败: {url}")

    monkeypatch.setattr(web_fetch, "_http_get", fake_get)
    res = web_fetch.fetch_url("https://github.com/msitarzewski/agency-agents.git")
    assert res["ok"] is True, res
    assert res["source_type"] == "github_readme"
    assert "agency-agents" in res["text"]
    assert res["final_url"].endswith("README.md")


def test_web_fetch_github_blob_file(monkeypatch):
    """GitHub blob 文件直链 → 直接抓 raw 文件。"""
    from tools import web_fetch

    doc = "# Design\n\nDetails.\n"

    def fake_get(url, timeout=20):
        if url.endswith("DESIGN.md"):
            return (doc.encode("utf-8"), "text/plain; charset=utf-8", url)
        raise web_fetch.WebFetchError(f"HTTP 404 抓取失败: {url}")

    monkeypatch.setattr(web_fetch, "_http_get", fake_get)
    res = web_fetch.fetch_url(
        "https://github.com/msitarzewski/agency-agents/blob/main/DESIGN.md")
    assert res["ok"] is True, res
    assert res["source_type"] == "github_file"
    assert "Design" in res["text"]


def test_web_fetch_webpage_extraction(monkeypatch):
    """普通网页 → 提炼 <article>/<main>/<body> 正文并抽取 <title>。"""
    from tools import web_fetch

    # 正文需 > 200 字符（extract_main_text 质量阈值），否则回退原文
    paragraph = (
        "这是正文内容，足够长以便通过质量校验阈值，描述一些有用的知识点。"
        "智能体应当能从网页里读到这部分核心论述，而不是被导航与页脚噪音淹没。"
        "我们继续补充篇幅确保明显超过两百字符的下限，从而验证正文提炼确实生效，"
        "剥离掉菜单与页脚等无关结构，只保留文章主体供对话体阅读与学习使用。"
        "第一段之后还需要更多论述来支撑篇幅，例如说明抓取原语的设计动机与安全边界，"
        "以及它如何与既有 web_search 多源检索互补，而不是互相替代。"
        "再补充一段以确保质量阈值被稳定越过，避免触发回退到原始整页 HTML 的分支。"
        "最终模型拿到的应当是一段干净、可读、无导航噪音的文章正文摘要。"
    )
    html = (
        "<html><head><title>Example Doc</title></head>"
        "<body><nav>menu</nav><article><h1>Hello</h1>"
        f"<p>{paragraph}</p>"
        "</article><footer>footer</footer></body></html>"
    )

    def fake_get(url, timeout=20):
        return (html.encode("utf-8"), "text/html; charset=utf-8", url)

    monkeypatch.setattr(web_fetch, "_http_get", fake_get)
    res = web_fetch.fetch_url("https://example.com/doc")
    assert res["ok"] is True, res
    assert res["source_type"] == "webpage"
    assert res["title"] == "Example Doc"
    assert "正文内容" in res["text"]
    assert "menu" not in res["text"]  # 导航/页脚噪音应被剔除


def test_web_fetch_http_error_reported(monkeypatch):
    """抓取失败给出明确原因（不静默吞错、不谎称成功）。"""
    from tools import web_fetch

    def fake_get(url, timeout=20):
        raise web_fetch.WebFetchError("HTTP 404 抓取失败: " + url)

    monkeypatch.setattr(web_fetch, "_http_get", fake_get)
    res = web_fetch.fetch_url("https://github.com/ghost/nonexistent-repo")
    assert res["ok"] is False
    assert "404" in res["error"]


def test_web_fetch_github_loop_bounded_by_budget(monkeypatch):
    """回归：raw.githubusercontent 在受限网络下「连得上但不回包」时，候选循环不得挂到 300s。

    旧实现无总预算，仓库根 15 候选 × 20s = 300s，正好把 ChatAgent step 拖到 300s 超时
    （12:42 那次 300s 超时即此症状）。这里压短预算 + 挂起 _http_get，断言 fetch_url 在预算内
    返回 ok=False，而非无限逼近 step 超时。
    """
    import time as _t
    from tools import web_fetch

    monkeypatch.setattr(web_fetch, "_GITHUB_TOTAL_BUDGET", 1.0)
    monkeypatch.setattr(web_fetch, "_GITHUB_PER_TIMEOUT", 0.3)

    def hang(url, timeout=20):
        _t.sleep(timeout)  # 模拟连接挂起（防火墙 accept 但不回包）
        raise web_fetch.WebFetchError("timeout")

    monkeypatch.setattr(web_fetch, "_http_get", hang)
    start = _t.monotonic()
    res = web_fetch.fetch_url("https://github.com/some/repo")
    elapsed = _t.monotonic() - start
    assert res["ok"] is False
    # 必须在预算附近返回（留 3s 余量覆盖多候选与调度），远小于旧的 300s
    assert elapsed < 3.0, f"候选循环未受预算约束，耗时 {elapsed:.1f}s（应 < 3s）"


