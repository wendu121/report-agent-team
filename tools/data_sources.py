# tools/data_sources.py · 数据源插件抽象层（M9-2）
#
# 设计：DESIGN_M9-2.md。数据源插件 = 真·控制器（插件的 enabled/连接状态真实驱动引擎检索）。
# - DataSourcePlugin Protocol：search(query, max_results) -> list[{title,url,content,score}]
# - TavilyProvider（复用 web_search，真联网，需 DS_TAVILY_API_KEY）
# - MockProvider（按源标记 [MOCK] <name>；**仅在显式 provider=mock 时装载**，
#   结果带 is_mock=True，不得进「来源」列表冒充引用）
# - ComingSoonProvider（raise，引擎永远跳过，绝不冒充可用）
# - load_data_sources()：读 config/plugins.yaml（mirror load_model_mapping），重算 connected
# - load_secrets()：读 tenants/<account_id>/.secrets/plugins.env + env，映射 DS_<ID大写>_API_KEY
# - build_search_tool()：聚合 enabled 且非 coming_soon 的源，结果带 source 字段；
#   **缺密钥的 api_key 源不注册**，登记到 SearchTool.degraded（reason/fix）——
#   「不可用就明说不可用」，绝不静默伪造检索结果（2026-09-19 修真缺陷）。
#
# 真源扩展（auth=none，零密钥，stdlib urllib）：
# - M10-P1：arxiv / semantic_scholar / crossref / wikipedia
# - M10-P2：openalex / europe_pmc / wikidata / tencent_quote / gdelt
# - M10-P3：taobao_suggest / amazon_suggest / ebay_suggest（电商竞品关键词）
#           + accio_tariff（关税/HS码，跨境电商成本结构）— DESIGN_M10-P3.md

from __future__ import annotations

import json
import logging
import os
import re
import time
import html
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

from . import ToolError
from .web_search import TavilyProvider, score_to_credibility


class DataSourcePlugin(Protocol):
    """数据源插件接口。实现须返回列表，每项含 title/url/content，可选 score。"""

    id: str
    name: str
    category: str
    auth_type: str

    def search(self, query: str, max_results: int) -> list[dict]:
        ...


class MockProvider:
    """离线 / keyless 占位实现。输出带 [MOCK] <source_name> 前缀，可被审计一眼识别。

    与 web_search.MockProvider 的区别：本类按 source 标记（结果带 source 字段），
    用于 M9-2 多源聚合时仍能区分「这条来自哪个插件」。
    """

    def __init__(self, source_id: str, source_name: str, max_results: int = 5):
        self.id = source_id
        self.name = source_name
        self.category = "mock"
        self.auth_type = "none"
        self.max_results = max_results

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        n = max_results or self.max_results
        seed = abs(hash(f"{self.id}:{query}")) % 10000
        return [
            {
                "title": f"[MOCK] {self.name}：{query} · 结果 {i + 1}",
                "url": f"https://mock.local/{self.id}/{seed}/{i + 1}",
                "content": f"[MOCK] 这是来自「{self.name}」的占位内容，非真实检索结果。",
                "score": 0.6,
            }
            for i in range(n)
        ]


class ComingSoonProvider:
    """尚未接入的数据源。search 直接 raise，引擎 build_search_tool 会跳过它，绝不冒充可用。"""

    def __init__(self, source_id: str, source_name: str = ""):
        self.id = source_id
        self.name = source_name or source_id
        self.category = "coming_soon"
        self.auth_type = "none"

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        raise ToolError(f"数据源尚未接入：{self.name}（{self.id}）")


# ---------------------------------------------------------------------------
# Keyless 真·公开 API 源（M10-P1，DESIGN_PLUGIN_EXPANSION.md §2 Phase 1）
# 约定：stdlib urllib（零第三方依赖，对齐 web_search.TavilyProvider）；
# 统一 __init__(timeout)；search() 返回 [{title,url,content,score}]。
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int, headers: Optional[dict] = None,
              data: Optional[bytes] = None) -> bytes:
    """GET/POST 原始字节。data 非空即 POST（如 DDG lite 表单提交）。
    非 200 / 网络错误一律 ToolError（诚实失败，不静默）。"""
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "report-agent-team/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ToolError(f"HTTP {e.code}: {url}") from e
    except Exception as e:  # URLError / timeout / proxy 错误
        raise ToolError(f"请求失败: {e}") from e


def _http_get_json(url: str, timeout: int, headers: Optional[dict] = None):
    """GET + 解析 JSON。响应体非 JSON（部分源 200 却返回 HTML/纯文本）→ ToolError，
    确保单源解析失败被 SearchTool.search_many 的 except ToolError 捕获，不击穿整体聚合。"""
    body = _http_get(url, timeout, headers).decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise ToolError(f"响应非 JSON: {e} :: {url}") from e


def _http_get_json_retry(url: str, timeout: int, attempts: int = 3, backoff: float = 0.6,
                         headers: Optional[dict] = None):
    """带指数退避的 _http_get_json（应对外网源瞬时 TLS/连接重置/429 抖动）。

    open-meteo / arxiv 等公开 API 经容器直连 NAT 时可能偶发连接重置；单次失败不应让
    整条天气/检索结果静默蒸发（此前 open_meteo 一次 geocoding 抖动 → search() 返回 [] →
    对话里天气凭空消失，模型还误报「HTTP 429」）。重试仅捕获 ToolError（网络/HTTP/非 JSON），
    不掩盖解析层逻辑错误。
    """
    last = None
    for i in range(attempts):
        try:
            return _http_get_json(url, timeout, headers)
        except ToolError as e:
            last = e
            if i < attempts - 1:
                wait = backoff * (2 ** i)
                time.sleep(wait)
                logger.warning("[retry] %s 第 %d 次失败，%.1fs 后重试: %s",
                               url[:90], i + 1, wait, e)
    raise last or ToolError(f"重试耗尽: {url}")


def _http_get_retry(url: str, timeout: int, attempts: int = 2, backoff: float = 0.6,
                    headers: Optional[dict] = None) -> bytes:
    """_http_get 的重试版（与 _http_get_json_retry 同策略），用于 RSS/XML 等文本源。

    应对容器直连 NAT 时的偶发 TLS/连接重置（boss 实测出现过成片 SSL 连接错误）。
    仅重试 ToolError，不掩盖解析层逻辑错误。
    """
    last = None
    for i in range(attempts):
        try:
            return _http_get(url, timeout, headers)
        except ToolError as e:
            last = e
            if i < attempts - 1:
                wait = backoff * (2 ** i)
                time.sleep(wait)
                logger.warning("[retry] %s 第 %d 次失败，%.1fs 后重试: %s",
                               url[:90], i + 1, wait, e)
    raise last or ToolError(f"重试耗尽: {url}")


# 瞬时光键（容器直连 NAT 偶发 TLS/连接重置/握手超时，boss 实测成片出现，属抖动非故障）。
# 见 MEMORY 坑 15：SSL UNEXPECTED_EOF 是瞬时抖动须短退避重试，单次日志不可当网络不通。
_TRANSIENT_HINTS = (
    "UNEXPECTED_EOF", "SSL", "timed out", "TimeoutError", "Connection reset",
    "Connection aborted", "urlopen error", "ECONNRESET", "handshake",
)


def _is_transient(msg: str) -> bool:
    m = (msg or "").lower()
    return any(h.lower() in m for h in _TRANSIENT_HINTS)


def _search_with_retry(provider, query: str, max_results: int, timeout: int,
                       attempts: int = 3, backoff: float = 0.8):
    """对单 provider.search 做瞬时光键重试（跨越容器出口偶发 SSL/连接重置窗口）。

    对齐 OpenMeteoProvider / RssNewsProvider 已用的 _http_get_retry 策略，但覆盖**所有**
    真 provider（此前 Arxiv/Wikipedia/DuckDuckGo/Tencent 等 14 个 search 走无重试 _http_get，
    撞抖动窗口即整源失败降级空，导致研报检索全面落空）。仅重试瞬时光键（SSL/timeout/连接重置）；
    持久错误（403/解析失败/coming_soon）直接抛出不浪费重试预算。
    """
    last = None
    for i in range(attempts):
        try:
            return provider.search(query, max_results)
        except ToolError as e:
            last = e
            if not _is_transient(str(e)) or i >= attempts - 1:
                raise
            wait = backoff * (2 ** i)
            logger.warning("[search_retry] 源 %s 瞬时失败(第%d次)，%.1fs 后重试: %s",
                           getattr(provider, "id", "?"), i + 1, wait, e)
            time.sleep(wait)
    raise last or ToolError(f"检索重试耗尽: {getattr(provider, 'id', '?')}")


def _parse_xml(raw: bytes, url: str):
    """解析 XML。格式异常 → ToolError（同 _http_get_json 的容错语义）。"""
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise ToolError(f"响应非合法 XML: {e} :: {url}") from e


class ArxivProvider:
    """arXiv 学术预印本（公开 API，无需 key）。Atom XML → title/summary/link。"""

    ENDPOINT = "https://export.arxiv.org/api/query"

    def __init__(self, timeout: int = 20):
        self.id = "arxiv"
        self.name = "arXiv 学术"
        self.category = "学术"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max(1, min(int(max_results or 5), 20)),
        })
        raw = _http_get(f"{self.ENDPOINT}?{qs}", self.timeout)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = _parse_xml(raw, self.ENDPOINT)
        out = []
        for i, entry in enumerate(root.findall("a:entry", ns), start=1):
            title = (entry.findtext("a:title", "", ns) or "").strip()
            summary = (entry.findtext("a:summary", "", ns) or "").strip()
            link = (entry.findtext("a:id", "", ns) or "").strip()
            if not link:
                continue
            out.append({
                "title": re.sub(r"\s+", " ", title) or link,
                "url": link,
                "content": re.sub(r"\s+", " ", summary)[:500],
                "score": 0.8,
            })
        return out


class SemanticScholarProvider:
    """Semantic Scholar 论文检索（公开 API，匿名池限流 100 req/5min，无需 key）。"""

    ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"

    def __init__(self, timeout: int = 20):
        self.id = "semantic_scholar"
        self.name = "Semantic Scholar"
        self.category = "学术"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "query": query,
            "limit": max(1, min(int(max_results or 5), 20)),
            "fields": "title,abstract,url,year,externalIds",
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for p in (data.get("data") or []):
            url = (p.get("url") or "").strip()
            if not url:
                continue
            abstract = (p.get("abstract") or "")[:500]
            year = p.get("year")
            out.append({
                "title": (p.get("title") or url).strip(),
                "url": url,
                "content": (f"[{year}] " if year else "") + abstract,
                "score": 0.8,
            })
        return out


class CrossrefProvider:
    """CrossRef DOI 文献元数据（公开 API，polite pool：mailto 提限，无需 key）。"""

    ENDPOINT = "https://api.crossref.org/works"

    def __init__(self, timeout: int = 20, mailto: str = "report-agent@example.com"):
        self.id = "crossref"
        self.name = "CrossRef"
        self.category = "学术"
        self.auth_type = "none"
        self.timeout = timeout
        self.mailto = mailto

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "query.bibliographic": query,
            "rows": max(1, min(int(max_results or 5), 20)),
            "mailto": self.mailto,
            "select": "title,URL,abstract,container-title",
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for it in ((data.get("message") or {}).get("items") or []):
            url = (it.get("URL") or "").strip()
            if not url:
                continue
            titles = it.get("title") or []
            title = re.sub(r"\s+", " ", titles[0]) if titles else url
            journal = (it.get("container-title") or [""])[0]
            abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")  # JATS 去 XML 标签
            content = re.sub(r"\s+", " ", (f"《{journal}》 " if journal else "") + abstract).strip()
            out.append({"title": title, "url": url, "content": content[:500], "score": 0.75})
        return out


class WikipediaProvider:
    """维基百科中文优先（MediaWiki API，无需 key）。搜索标题+片段。"""

    ENDPOINT = "https://zh.wikipedia.org/w/api.php"

    def __init__(self, timeout: int = 20, lang: str = "zh"):
        self.id = "wikipedia"
        self.name = "维基百科"
        self.category = "百科"
        self.auth_type = "none"
        self.timeout = timeout
        self.lang = lang

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": max(1, min(int(max_results or 5), 20)),
            "format": "json",
        })
        data = _http_get_json(f"https://{self.lang}.wikipedia.org/w/api.php?{qs}", self.timeout)
        base = f"https://{self.lang}.wikipedia.org/wiki/"
        out = []
        for hit in ((data.get("query") or {}).get("search") or []):
            title = hit.get("title") or ""
            if not title:
                continue
            snippet = re.sub(r"<[^>]+>", "", hit.get("snippet") or "")  # 搜索片段带 <span class="searchmatch">
            out.append({
                "title": title,
                "url": base + urllib.parse.quote(title.replace(" ", "_")),
                "content": snippet,
                "score": 0.7,
            })
        return out


# ---------------------------------------------------------------------------
# Keyless 真·公开 API 源（M10-P2 扩展，DESIGN_PLUGIN_EXPANSION.md §2 Phase 2）
# 目标：在「用户零 key」前提下把引擎数据源层堆到自洽——学术/百科/新闻全用零密钥源，
# A股金融用腾讯 smartbox 等公开端点（亦零密钥）。密钥类（tavily/xueqiu/cninfo/qcc）
# 维持 coming_soon 诚实占位，等用户以后想接再填。
# 约定同上：stdlib urllib（零依赖）、统一 __init__(timeout)、search 返回 [{title,url,content,score}]。
# 选型均经实测：openalex / europe_pmc / wikidata / gdelt / tencent_quote 五项端点连通性已验证。
# ---------------------------------------------------------------------------

class OpenAlexProvider:
    """OpenAlex 开放学术图谱（公开 API，无需 key，覆盖期刊/预印本/专利/数据集，比 SS 更广）。
    abstract_inverted_index 需反构；无摘要则退回标题。"""

    ENDPOINT = "https://api.openalex.org/works"

    def __init__(self, timeout: int = 20, mailto: str = "report-agent@example.com"):
        self.id = "openalex"
        self.name = "OpenAlex"
        self.category = "学术"
        self.auth_type = "none"
        self.timeout = timeout
        self.mailto = mailto

    @staticmethod
    def _reconstruct_abstract(inv_idx: Optional[dict]) -> str:
        if not inv_idx:
            return ""
        slots: dict[int, str] = {}
        for word, positions in inv_idx.items():
            for p in positions:
                slots[p] = word
        return " ".join(slots[i] for i in sorted(slots))

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "search": query,
            "per-page": max(1, min(int(max_results or 5), 20)),
            "mailto": self.mailto,
            "select": "display_name,doi,id,abstract_inverted_index",
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for w in (data.get("results") or []):
            url = (w.get("doi") or w.get("id") or "").strip()
            if not url:
                continue
            title = (w.get("display_name") or url).strip()
            abstract = self._reconstruct_abstract(w.get("abstract_inverted_index"))
            # relevance_score 已被 select 排除（OpenAlex 不允许混选），统一给中性分 0.7
            out.append({"title": title, "url": url,
                        "content": abstract[:500], "score": 0.7})
        return out


class EuropePMCProvider:
    """Europe PMC 生物医学/学术文献（EBI 公开 API，无需 key）。"""

    ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self, timeout: int = 20):
        self.id = "europe_pmc"
        self.name = "Europe PMC"
        self.category = "学术"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "query": query, "format": "json",
            "pageSize": max(1, min(int(max_results or 5), 20)),
            "resultType": "core",
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for it in ((data.get("resultList") or {}).get("result") or []):
            title = (it.get("title") or "").strip()
            if not title:
                continue
            doi = (it.get("doi") or "").strip()
            pmid = (it.get("pmid") or "").strip()
            url = (f"https://doi.org/{doi}" if doi
                   else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
            if not url:
                continue
            abstract = re.sub(r"<[^>]+>", " ", it.get("abstractText") or "")  # 去 HTML 标签
            abstract = re.sub(r"\s+", " ", abstract).strip()[:500]
            out.append({"title": title, "url": url, "content": abstract, "score": 0.78})
        return out


class WikidataProvider:
    """Wikidata 结构化实体检索（MediaWiki wbsearchentities，无需 key；中文优先）。"""

    ENDPOINT = "https://www.wikidata.org/w/api.php"

    def __init__(self, timeout: int = 20, lang: str = "zh"):
        self.id = "wikidata"
        self.name = "Wikidata"
        self.category = "百科"
        self.auth_type = "none"
        self.timeout = timeout
        self.lang = lang

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "action": "wbsearchentities", "search": query,
            "language": self.lang, "format": "json",
            "limit": max(1, min(int(max_results or 5), 20)),
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for hit in (data.get("search") or []):
            label = (hit.get("label") or "").strip()
            url = (hit.get("concepturi") or "").strip()
            if not url or not label:
                continue
            desc = re.sub(r"\s+", " ", hit.get("description") or "")
            out.append({"title": label, "url": url, "content": desc, "score": 0.7})
        return out


class TencentQuoteProvider:
    """腾讯证券行情搜索（smartbox.gtimg.cn 公开端点，无需 key）。
    按名称检索 A股/港股/ETF/基金；返回 market~code~name。URL 指向腾讯个股页。
    v_hint 多条以 ^ 分隔，单条格式 market~code~name~pinyin~type。
    注意：smartbox 返回的 name 是 \\uXXXX 转义**字面量**（非真 UTF-8），须 _unescape_js 解码。"""

    ENDPOINT = "https://smartbox.gtimg.cn/s3/"

    def __init__(self, timeout: int = 20):
        self.id = "tencent_quote"
        self.name = "腾讯证券行情"
        self.category = "行情"
        self.auth_type = "none"
        self.timeout = timeout

    @staticmethod
    def _unescape_js(s: str) -> str:
        """把字面量 \\uXXXX 转义解成真字符（smartbox 的 name 字段为转义字面量）。
        非转义文本原样返回；解码失败退回原文，绝不抛错。"""
        if "\\u" not in s:
            return s
        try:
            return s.encode("latin-1", "backslashreplace").decode("unicode_escape")
        except Exception:
            return s

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({"q": query, "t": "all", "c": "1"})
        raw = _http_get(f"{self.ENDPOINT}?{qs}", self.timeout).decode("utf-8", "replace")
        m = re.search(r'v_hint="([^"]*)"', raw)
        if not m:
            return []  # 无匹配：返回体为 v_pv_none_match="1"; / v_hint="N";
        out = []
        for entry in m.group(1).split("^"):
            parts = entry.split("~")
            if len(parts) < 4:
                continue
            market, code = parts[0], parts[1]
            name = self._unescape_js(parts[2]).strip()
            if not code or not name:
                continue
            out.append({
                "title": f"{name} ({market}{code})",
                "url": f"https://gu.qq.com/{market}{code}",
                "content": f"市场 {market}｜代码 {code}｜类型 {parts[4] if len(parts) > 4 else ''}".strip(),
                "score": 0.72,
            })
            if len(out) >= max(1, min(int(max_results or 5), 20)):
                break
        return out


class GDELTProvider:
    """GDELT 全球新闻事件库（doc API，无需 key；覆盖全球媒体）。"""

    ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, timeout: int = 20):
        self.id = "gdelt"
        self.name = "GDELT 全球新闻"
        self.category = "新闻"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        qs = urllib.parse.urlencode({
            "query": query, "mode": "ArtList", "format": "json",
            "maxrecords": max(1, min(int(max_results or 5), 20)),
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for a in (data.get("articles") or []):
            title = (a.get("title") or "").strip()
            url = (a.get("url") or "").strip()
            if not url or not title:
                continue
            domain = a.get("domain") or ""
            seendate = (a.get("seendate") or "")[:8]
            content = (f"来源 {domain}｜{seendate}" if domain else seendate)
            out.append({"title": title, "url": url, "content": content, "score": 0.65})
        return out


# ---------------------------------------------------------------------------
# 电商 / 电商竞品分析 keyless 真源（M10-P3，DESIGN_M10-P3.md §6b.5）
# 说明：三类"关键词联想"源给的是**竞品关键词趋势**（候选词 + 热度分），
# 不是价格/销量（DESIGN_M10-P3.md §7 Q3 已与 boss 对齐粒度）；
# accio_tariff 给的是**关税/HS 码/落地成本**维度（成本结构分析）。
# ---------------------------------------------------------------------------

# 联想词/关税类源的查询词提取（M10-P4 修复）。
# 背景：引擎 build_search_queries() 下发的 query 是「主题整句」与「主题+scope 子问题」
# （见 orchestrator.py），例如「无线蓝牙耳机跨境选品与关税成本分析」。这类长句直接丢给
# 联想词接口（suggest.taobao.com / completion.amazon.com / autosug.ebay.com）会返回空数组
# ——它们面向「短语级输入」而非句子。实测：整句 → 0 结果；「蓝牙耳机」→ 5 结果。
# 处置：抽出句子里的「核心短语」再查，并显式标注这是截断而非原文（诚实边界，不假装等价）。
#
# 抽取规则（区分语种，避免弄坏英文短语）：
#   - 拉丁文字（英/数字/空格）：空格是词内合法分隔（"wireless earbuds" 是一个查询短语），
#     故**不按空格切分**，仅在过长时截断到前若干词。
#   - CJK：先按「连接词」把句子切成并列片段，再在片段内剥离修饰性停用词，
#     取最长片段（"无线蓝牙耳机跨境选品与关税成本分析" → 切两段 → "无线蓝牙耳机跨境选品" → "无线蓝牙耳机"）。
_CONJ_TOKENS = ("与", "和", "及", "以及", "、")
_STOP_TOKENS = (
    "跨境", "选品", "分析", "研究", "报告", "研报", "评估", "对标", "对比",
    "策略", "方案", "调研", "面向", "含", "成本", "市场", "趋势", "情况",
)
_CJK_SEP_RE = re.compile(r"[\s,，;；|/]+")
_CJK_CONJ_RE = re.compile("|".join(_CONJ_TOKENS))
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-'\.]*")
_MAX_KEYWORD_LEN = 40
# 英文「句子式 query」里需要剥掉的连接/分析类虚词（suggest 接口只吃品类短语）。
_EN_STOPWORDS = frozenset({
    "cross", "border", "crossborder", "selection", "selecting", "analysis", "analyze",
    "analytical", "and", "or", "the", "for", "of", "in", "on", "with", "market",
    "markets", "research", "report", "study", "strategy", "trend", "trends", "cost",
    "costs", "tariff", "tariffs", "duty", "duties", "sourcing", "sourcing.", "vs",
    "comparison", "compare", "overview", "review", "a", "an", "to",
})


def _has_searchable_content(kw: str) -> bool:
    """判断抽取结果是否含「可检索内容」——至少一个 CJK 字、字母或数字。

    M10-P4 独立审议指出的边界：纯标点（`！！！`）或纯 emoji（`😀🔥`）既非空串，
    也无任何可检索语义，若放行会向联想词接口发无意义 HTTP 请求。此守卫把它们判为
    「无可抽取内容」→ 调用方提前返回空结果。
    """
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", kw or ""))


def _extract_keyword(query: str) -> tuple[str, bool]:
    """从句子式 query 中抽取适合联想词接口的核心短语。

    返回 (keyword, truncated)。truncated=True 表示发生了截断/清洗（调用方应据此
    在结果正文中标注，避免下游把「截断词的结果」误读为「整句的结果」）。
    完全无法抽取（含纯标点/emoji 等无可检索内容）时返回 ("", False)，
    调用方据此直接返回空结果而不发无意义请求。
    """
    raw = (query or "").strip()
    if not raw:
        return "", False

    # --- 拉丁为主：保留空格短语，仅在超长时截断 ---
    if not _CJK_RE.search(raw):
        words = _LATIN_WORD_RE.findall(raw)
        # 无拉丁词（如纯标点 `！！！`、纯 emoji）→ 视为无可检索内容，不发请求。
        if not words:
            return "", False
        # suggest 类接口只对「短前缀」有响应；实测 4 词以上基本返回空。
        # 抠掉分析类虚词后取前 2 词，是最稳的「核心品类词」近似。
        core = [w for w in words if w.lower() not in _EN_STOPWORDS]
        if not core:
            core = words
        kw = " ".join(core[:2])
        if not _has_searchable_content(kw):
            return "", False
        return kw, kw != raw

    # --- CJK 为主 ---
    # 先按分隔符 + 连接词切成并列片段，避免 "耳机与关税" 这种跨概念粘连；
    # 再在片段内剥离修饰性停用词，取第一个（即最长片段中）仍成词的结果。
    #
    # M10-P4 独立审议修正：中英混合时（如 "iPhone 16 Pro Max 手机壳"），
    # 按「片段字符长度」取最长会选中英文片段 `iPhone`，丢掉真正的商品词「手机壳」。
    # 中文商品名几乎必含 CJK，故**优先取含 CJK 的片段**；仅当整句无 CJK 片段时
    # 才退回拉丁片段（此时上面 `if not _CJK_RE.search(raw)` 分支已处理，理论到不了）。
    text = raw
    chunks: list[str] = []
    for part in _CJK_SEP_RE.split(text):
        for sub in _CJK_CONJ_RE.split(part):
            sub = sub.strip()
            if sub:
                chunks.append(sub)
    if not chunks:
        chunks = [raw]
    cjk_chunks = [c for c in chunks if _CJK_RE.search(c)]
    if cjk_chunks:
        chunks = cjk_chunks

    # 在片段内剥离修饰性停用词，取第一个（最长片段中）仍成词的结果
    best = ""
    for ch in sorted(chunks, key=len, reverse=True):
        cjk_only = "".join(_CJK_RE.findall(ch)) or ch
        cleaned = cjk_only
        for t in _STOP_TOKENS:
            cleaned = cleaned.replace(t, "")
        cleaned = cleaned.strip()
        if len(cleaned) >= 2:
            best = cleaned
            break
        if len(best) < 2:
            best = cjk_only
    if not best:
        best = "".join(_CJK_RE.findall(raw)) or raw
    if not _has_searchable_content(best):
        return "", False
    return best, best != raw


class TaobaoSuggestProvider:
    """淘宝搜索联想词（suggest.taobao.com 公开端点，无需 key）。
    国内电商竞品关键词：返回候选词 + 热度分。code=utf-8 保证中文编码。"""

    ENDPOINT = "https://suggest.taobao.com/sug"

    def __init__(self, timeout: int = 20):
        self.id = "taobao_suggest"
        self.name = "淘宝联想词"
        self.category = "电商"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        query, truncated = _extract_keyword(query)
        if not query:
            return []
        note = f"（引擎整句已截取核心词「{query}」）" if truncated else ""
        qs = urllib.parse.urlencode({
            "code": "utf-8", "q": query, "k": "1", "area": "c2c",
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for item in (data.get("result") or []):
            # 每条形如 [候选词, 热度分字符串]
            if not isinstance(item, list) or not item:
                continue
            kw = str(item[0]).strip()
            if not kw:
                continue
            heat = str(item[1]).strip() if len(item) > 1 else ""
            out.append({
                "title": kw,
                "url": "https://s.taobao.com/search?" + urllib.parse.urlencode({"q": kw}),
                "content": f"淘宝联想词｜热度分 {heat}{note}".strip(),
                "score": 0.62,
            })
            if len(out) >= max(1, min(int(max_results or 5), 20)):
                break
        return out


class AmazonSuggestProvider:
    """Amazon 搜索补全（completion.amazon.com 公开端点，无需 key）。
    跨境（站点 US，mid=ATVPDKIKX0DER）竞品关键词联想。"""

    ENDPOINT = "https://completion.amazon.com/api/2017/suggestions"

    def __init__(self, timeout: int = 20, marketplace: str = "ATVPDKIKX0DER"):
        self.id = "amazon_suggest"
        self.name = "Amazon 联想词"
        self.category = "电商"
        self.auth_type = "none"
        self.timeout = timeout
        self.marketplace = marketplace

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        query, truncated = _extract_keyword(query)
        if not query:
            return []
        note = f"｜引擎整句已截取核心词「{query}」" if truncated else ""
        qs = urllib.parse.urlencode({
            "limit": "11", "prefix": query, "suggestion-type": "KEYWORD",
            "alias": "aps", "site-variant": "desktop", "mid": self.marketplace,
        })
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        out = []
        for s in (data.get("suggestions") or []):
            kw = str(s.get("value") or "").strip()
            if not kw:
                continue
            out.append({
                "title": kw,
                "url": "https://www.amazon.com/s?" + urllib.parse.urlencode({"k": kw}),
                "content": f"Amazon(US) 搜索补全｜类型 {s.get('type') or ''}{note}".strip(),
                "score": 0.62,
            })
            if len(out) >= max(1, min(int(max_results or 5), 20)):
                break
        return out


class EbaySuggestProvider:
    """eBay 搜索联想（autosug.ebay.com 公开端点，无需 key）。
    返回 JSON 数组：["原词", ["候选1","候选2",...]]（fmt=osr）。"""

    ENDPOINT = "https://autosug.ebay.com/autosug"

    def __init__(self, timeout: int = 20):
        self.id = "ebay_suggest"
        self.name = "eBay 联想词"
        self.category = "电商"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        query, truncated = _extract_keyword(query)
        if not query:
            return []
        note = f"｜引擎整句已截取核心词「{query}」" if truncated else ""
        qs = urllib.parse.urlencode({"kwd": query, "sId": "0", "fmt": "osr"})
        data = _http_get_json(f"{self.ENDPOINT}?{qs}", self.timeout)
        # 期望 shape: [echo, [sug...]]；非该 shape（如错误对象）→ 空结果
        sugg = []
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            sugg = data[1]
        out = []
        for kw in sugg:
            if not isinstance(kw, str):  # 协议漂移守卫：非字符串元素跳过（M10-P3 审议 M1）
                continue
            kw = kw.strip()
            if not kw:
                continue
            out.append({
                "title": kw,
                "url": "https://www.ebay.com/sch/i.html?" + urllib.parse.urlencode({"_nkw": kw}),
                "content": f"eBay 搜索联想词{note}",
                "score": 0.6,
            })
            if len(out) >= max(1, min(int(max_results or 5), 20)):
                break
        return out


class AccioTariffProvider:
    """Accio（阿里）关税 / HS 码查询（www.accio.com/api/turtle/classify，公开端点，无需 key）。

    来源：AccioWork/agent-skills 的开源 skill 暴露的公开 API（M10-P3 §6b.4 实测 200）。
    用途：跨境电商竞品分析的「成本结构」维度 —— HS 编码 + 关税税率 + 构成。
    国别对：HS 分类支持任意国别；税率明细实测仅 CN→US 完整。
    风险：第三方公开 API，无限流契约；失败即 ToolError，引擎按单源降级处理。

    query 约定：`<商品名>|<目的国>`（目的国可省，默认 US）。例：「陶瓷咖啡杯|US」。
    """

    ENDPOINT = "https://www.accio.com/api/turtle/classify"
    DEFAULT_DEST = "US"

    def __init__(self, timeout: int = 20, origin: str = "CN", source: str = "alibaba"):
        self.id = "accio_tariff"
        self.name = "Accio 关税查询"
        self.category = "电商"
        self.auth_type = "none"
        self.timeout = timeout
        self.origin = origin
        self.source = source

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        product, _, dest = (query or "").partition("|")
        product = product.strip()
        dest = (dest.strip() or self.DEFAULT_DEST).upper()
        if not product:
            return []
        payload = json.dumps({
            "source": self.source, "originCountryCode": self.origin,
            "destinationCountryCode": dest, "productName": product, "digit": 8,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.ENDPOINT, data=payload, method="POST",
            headers={"User-Agent": "report-agent-team/1.0",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise ToolError(f"HTTP {e.code}: {self.ENDPOINT}") from e
        except Exception as e:  # URLError / timeout / proxy
            raise ToolError(f"请求失败: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError) as e:
            raise ToolError(f"响应非 JSON: {e} :: {self.ENDPOINT}") from e

        # shape: {"data":{"success":..,"data":{"hscodeInfo":{...},"tariffRate":..,
        #          "tariffFormula":"..","tariffCalRuleDetailList":[...]}}}
        inner = ((data.get("data") or {}).get("data")) or {}
        hs = (inner.get("hscodeInfo") or {})
        hscode = str(hs.get("hscode") or "").strip()
        if not hscode:
            return []  # 未识别出 HS 码 → 诚实返回空（不编造）
        rate = inner.get("tariffRate")
        formula = str(inner.get("tariffFormula") or "").strip()
        desc_en = str(hs.get("descriptionEn") or "").strip()
        content = (f"HS {hscode}｜关税 {rate}%｜{formula}"
                   f"｜{self.origin}→{dest}").strip("｜")
        out = [{
            "title": f"{product} HS {hscode}（{self.origin}→{dest} 关税 {rate}%）",
            "url": "https://www.accio.com/",
            "content": (content + ("｜" + desc_en if desc_en else ""))[:500],
            "score": 0.85,
        }]
        return out[:max(1, min(int(max_results or 5), 20))]


class HsCodeTariffProvider:
    """HS 码 → 六国关税明细 + 301 风险（accio-wow-api.vercel.app，公开端点，无需 key）。

    来源：Accio「HS 码查询」计算器页面内嵌的公开 API（M10-P4 §A1 实测；页面本身
    未在任何文档中标注该 API，属从公开前端代码中发现的零密钥端点）。

    与 `accio_tariff` 的分工（重要，勿混）：
    - `accio_tariff`（阿里的语义分类器）负责「商品名 → HS 码」，实测「陶瓷咖啡杯」准。
    - 本源负责「已知 HS 码 → 六国关税 + 风险标记」，精确查码可靠。
    两者组合 = 完整链路：商品名 → HS 码 → 各国关税/风险。

    ⚠️ 关键词搜索**质量不可靠**（实测 `ceramic mug` → 2844.10 铀矿、`steel water bottle`
    → 0301.11 活鱼），故本实现**只走精确码查询**（`/<code>`），不暴露模糊搜索。
    未命中码 → 诚实返回空，绝不返回模糊匹配的错码。

    query 约定：直接传 HS 码（支持 `6109.10` / `610910` / `6109` 前缀）。
    可选 `|国家A,国家B` 限定只输出指定市场（缺省全部六个）。
    """

    ENDPOINT = "https://accio-wow-api.vercel.app/api/hs-code"
    MARKETS = ("AU", "CA", "DE", "EU", "GB", "US")
    # 市场 → 可读名
    MARKET_LABEL = {"AU": "澳大利亚", "CA": "加拿大", "DE": "德国",
                    "EU": "欧盟", "GB": "英国", "US": "美国"}

    def __init__(self, timeout: int = 25):
        self.id = "hs_code_tariff"
        self.name = "HS 码关税明细"
        self.category = "电商"
        self.auth_type = "none"
        self.timeout = timeout

    @staticmethod
    def _normalize_code(s: str) -> str:
        """把用户输入归一为 `XXXX.XX` 形态（去空格/连字符/点后重排）。
        支持 6109.10 / 610910 / 6109；无法识别 → 空串。"""
        digits = re.sub(r"\D", "", s or "")
        if len(digits) < 4:
            return ""
        if len(digits) <= 6:
            return f"{digits[:4]}.{digits[4:]}" if len(digits) > 4 else digits
        return f"{digits[:4]}.{digits[4:6]}"

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        raw_code, _, mk = (query or "").partition("|")
        code = self._normalize_code(raw_code)
        if not code:
            return []  # 非码输入 → 空（不做模糊搜索，避免返回"铀矿"式错码）
        wanted = [m.strip().upper() for m in mk.split(",") if m.strip()] or list(self.MARKETS)
        data = _http_get_json(f"{self.ENDPOINT}/{urllib.parse.quote(code)}", self.timeout)
        if not isinstance(data, dict) or not data.get("hs_code"):
            return []
        hs = str(data.get("hs_code") or "").strip()
        desc = str(data.get("description") or "").strip()
        if not hs:
            return []
        tariffs = data.get("tariffs") or {}
        parts = []
        for m in wanted:
            t = tariffs.get(m)
            if isinstance(t, dict) and t.get("rate") is not None:
                parts.append(f"{self.MARKET_LABEL.get(m, m)} {t['rate']}%")
        risk = str(data.get("tariff_risk") or "").strip()
        notes = str(data.get("notes") or "").strip()
        hts = str(data.get("hts_code") or "").strip()
        content = "｜".join(x for x in [
            f"HS {hs}" + (f" (HTS {hts})" if hts else ""),
            "关税 " + "、".join(parts) if parts else "",
            f"风险 {risk}" if risk else "",
            notes,
        ] if x)
        out = [{
            "title": f"HS {hs}：{desc}（{'、'.join(parts)}）" if parts else f"HS {hs}：{desc}",
            "url": f"https://www.accio.com/wow/tool-hs-code-lookup.html?code={urllib.parse.quote(hs)}",
            "content": content[:500],
            "score": 0.88,
        }]
        return out[:max(1, min(int(max_results or 5), 20))]


# 常用中文城市 → Open-Meteo geocoding 英文名（离线别名表）。
# 实测（docker compose logs api，2026-09-15）：geocoding-api 对「厦门」等中文名常返回空，
# 但英文名（Xiamen）稳定命中；故 geocoding 落空时回退英文名重试，覆盖大陆主要城市 + 港澳。
_CN_CITY_ALIASES = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "天津": "Tianjin", "重庆": "Chongqing", "杭州": "Hangzhou", "南京": "Nanjing",
    "武汉": "Wuhan", "成都": "Chengdu", "西安": "Xi'an", "苏州": "Suzhou",
    "厦门": "Xiamen", "青岛": "Qingdao", "大连": "Dalian", "宁波": "Ningbo",
    "无锡": "Wuxi", "福州": "Fuzhou", "长沙": "Changsha", "郑州": "Zhengzhou",
    "济南": "Jinan", "合肥": "Hefei", "南昌": "Nanchang", "昆明": "Kunming",
    "贵阳": "Guiyang", "南宁": "Nanning", "海口": "Haikou", "三亚": "Sanya",
    "哈尔滨": "Harbin", "长春": "Changchun", "沈阳": "Shenyang",
    "石家庄": "Shijiazhuang", "太原": "Taiyuan", "兰州": "Lanzhou",
    "西宁": "Xining", "银川": "Yinchuan", "乌鲁木齐": "Urumqi",
    "呼和浩特": "Hohhot", "拉萨": "Lhasa", "香港": "Hong Kong", "澳门": "Macau",
}


class OpenMeteoProvider:
    """Open-Meteo 实时天气（公开 API，无需 key；geocoding + forecast 两步）。
    仅在 query 含天气意图且能解析出城市时返回数据；非天气查询返回空（不污染其它检索）。
    失败（城市解析不出 / 网络）一律返回空 → 由 SearchTool.search_many 降级，不冒充可用。"""

    INTENT = "weather"  # search_many 路由：天气意图专用源（聊天体启用路由时只它+web 兜底）
    GEO_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
    FC_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
    WMO = {
        0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
        45: "雾", 48: "雾凇",
        51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
        56: "冻毛毛雨", 57: "强冻毛毛雨",
        61: "小雨", 63: "中雨", 65: "大雨",
        66: "冻雨", 67: "强冻雨",
        71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
        80: "阵雨", 81: "强阵雨", 82: "暴雨",
        85: "阵雪", 86: "强阵雪",
        95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
    }

    def __init__(self, timeout: int = 20):
        self.id = "open_meteo"
        self.name = "Open-Meteo 天气"
        self.category = "综合检索"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        loc = self._extract_location(query or "")
        if not loc:
            logger.info("[open_meteo] 非天气查询或抽不出地名，跳过 query=%r", query)
            return []
        # geocoding：先按原地名（可能中文）查，zh/en 都不中再试英文别名表（离线，覆盖常用
        # 中文城市 → Open-Meteo 英文名，实测对「厦门」等中文名 geocoding 常返回空，但英文名
        # Xiamen 稳定命中）。网络失败换 name/lang 重试（不再直接 return []，否则中文城市永远
        # 落空）；整体最多 4 次 geocoding 调用，单次失败不内部重试（防越过 2 分钟线）。
        geo = None
        names = [loc]
        if loc in _CN_CITY_ALIASES:
            names.append(_CN_CITY_ALIASES[loc])
        for name in names:
            for lang in ("zh", "en"):
                try:
                    g = _http_get_json_retry(
                        f"{self.GEO_ENDPOINT}?name={urllib.parse.quote(name)}&count=1&language={lang}",
                        self.timeout,
                    )
                except ToolError as e:
                    # MAJOR-4：geocoding 端点故障（同一域名），换语言/别名重试毫无意义，
                    # 只会把最坏耗时翻倍并越过 /chat 2 分钟线。_http_get_json_retry 已内部
                    # 退避 3 次仍失败 → 直接返回空，由 search_many 降级，不冒充可用。
                    logger.warning("[open_meteo] geocoding 失败 loc=%r lang=%s query=%r: %s",
                                   name, lang, query, e)
                    return []
                if (g or {}).get("results"):
                    geo = g
                    break
            if geo:
                break
        if geo is None:
            logger.info("[open_meteo] geocoding 无匹配 loc=%r query=%r", loc, query)
            return []
        results = geo.get("results") or []
        if not results:
            return []
        r = results[0]
        lat, lon = r.get("latitude"), r.get("longitude")
        if lat is None or lon is None:
            return []
        qs = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "timezone": "auto",
        })
        try:
            fc = _http_get_json_retry(f"{self.FC_ENDPOINT}?{qs}", self.timeout)
        except ToolError as e:
            logger.warning("[open_meteo] forecast 失败 loc=%r: %s", loc, e)
            return []
        cur = fc.get("current") or {}
        if not cur:
            logger.info("[open_meteo] forecast 无 current 字段 loc=%r", loc)
            return []
        code = cur.get("weather_code")
        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        humidity = cur.get("relative_humidity_2m")
        wind = cur.get("wind_speed_10m")
        precip = cur.get("precipitation")
        name = r.get("name") or loc
        place = "，".join(x for x in [name, r.get("admin1"), r.get("country")] if x)
        wdesc = self.WMO.get(code, "") if isinstance(code, int) else ""
        content = "｜".join(filter(None, [
            f"实时气温 {temp}°C" + (f"（体感 {feels}°C）" if feels is not None else ""),
            f"天气 {wdesc}" if wdesc else "",
            f"湿度 {humidity}%" if humidity is not None else "",
            f"风速 {wind} km/h" if wind is not None else "",
            f"降水 {precip} mm" if precip is not None else "",
            f"观测时间 {cur.get('time')}" if cur.get("time") else "",
        ]))
        title = f"{place} 实时天气：{temp}°C {wdesc}".strip()
        url = f"https://open-meteo.com/zh/docs?latitude={lat}&longitude={lon}"
        return [{"title": title, "url": url, "content": content[:600], "score": 0.95}]

    @staticmethod
    def _extract_location(query: str) -> Optional[str]:
        # 【关键顺序】先在**整句**上去空格/数字/日期/时间词，再定位天气关键词。
        # 反序（先定位关键词再清洗左侧）会把时间词从中间劈开：
        # 「北京明天气温」的「天气」命中于 index 3 → before=「北京明」，
        # 「明天」被切成「明」+「天」，噪声表里的「明天」再也匹配不上 → geocoding 落空。
        # 这是 M12 审议 MAJOR-2 实测复现、且首轮修复仍未解决的一例。
        # 注意：整句阶段**不能**去空格 —— 英文分支（weather in X）依赖空格分词。
        # 空格只在中文本分支取左侧时再剥（「厦门 天气」→「厦门」）。
        q = query or ""
        # 日期/时间单位**只在紧跟数字时**才剥。无条件删「日」「周」会把地名砍坏：
        # 日本→本、周口→口、日照→照、日喀则→喀则（M12 审议 M-3，987048f 引入）。
        # 顺序要紧：必须在剥裸数字**之前**跑，否则「2025年」的数字先没了，单位就再无依靠。
        q = re.sub(r"\d+\s*(年|月|日|号|周|星期|时|点|分|秒钟?)", "", q)
        q = re.sub(r"星期[一二三四五六日天]", "", q)
        # 「下周三/周末」若不整块剥掉，会残留「三」「末」（NIT-13）。
        # 「周口」的「口」不在星期字符集里，安全。
        q = re.sub(r"(下|上|这|本)?周[一二三四五六日天末]", "", q)
        q = re.sub(r"[\d]+", "", q)
        q = re.sub(r"(今天|明天|昨天|后天|大后天|上午|下午|晚上|现在|此刻|当前|"
                   r"今日|昨日|最近|近期|本周|这周|上周|下周)", "", q)

        # 注意：**不要**把裸字「度」放进关键词表 —— 「印度天气」会在 index 1 命中「度」
        # → before=「印」→ geocoding 查「印」。只认「多少度/几度」这类量词短语即可
        # （「广州今天多少度」仍能命中），温度/气温本来就在表里。
        # 裸字「晴」「阴」已从关键词表移除（NIT-14）：有真实地名反例「晴隆县」。
        # 「雨」「雪」保留 —— 区县名（雨花区）影响面小，而「有雨吗」类问法依赖裸字召回。
        m = re.search(r"(天气|气温|温度|气象|下雨|下雪|雨|雪|多云|天气预报|会下|"
                      r"多少度|几度|weather|temperature|forecast)", q, re.IGNORECASE)
        if not m:
            return None
        kw = m.group(1).lower()
        if kw in ("weather", "temperature", "forecast"):
            # 英文："weather in Guangzhou" → Guangzhou；"Guangzhou temperature" → Guangzhou
            right = re.search(r"(?:weather|temperature|forecast)\s+(?:in|for|at|of)\s+([A-Za-z ]{2,40})", q, re.IGNORECASE)
            if right:
                return right.group(1).strip()
            left = re.search(r"([A-Za-z ]{2,40})\s+(?:weather|temperature|forecast)", q, re.IGNORECASE)
            if left:
                return left.group(1).strip()
            return None
        before = re.sub(r"\s+", "", q[: m.start()])  # 「厦门 天气」→「厦门」
        # 清洗（M11 修复）：LLM 生成的 query 常夹语气词/礼貌语，且位置不定。
        # 只剥离首尾会残留 —— 「帮我查一下深圳天气」→「查一下深圳」，geocoding 落空。
        # 改为整段移除噪声词（不分首尾），再取尾部中文串作为地名。
        # M12 审议 MAJOR-2：噪声表补否定词/疑问词/量词
        # （「厦门会不会下雨」→「厦门不」、「青岛没下雨吗」→「青岛没」同属落空）。
        # 长词必须排在单字前（分支按序匹配）。
        before = re.sub(
            r"(帮我|查一下|查询|请问|请|看看|告诉我|一下|"
            r"本地|这里|这儿|"
            r"会不会|有没有|是不是|"
            r"的|了|吗|呢|啊|吧|哦|呀|嘞|会|要|将|有|可能|应该|究竟|到底|在|"
            r"不|没|多少|怎样|怎么样|咋样|如何|几)",
            "", before)
        tail = re.search(r"([\u4e00-\u9fa5]{1,12})$", before)
        if not tail:
            return None
        return tail.group(1) or None


class RssNewsProvider:
    """官方媒体 RSS 新闻（keyless，只读；失败即单源降级，不冒充可用）。

    - 仅在 query 含新闻意图时返回；非新闻查询返回空，避免污染学术/百科等检索结果。
    - 单个 feed 拉取/解析失败只降级该 feed，不影响其它 feed（防御纵深，绝不让单源击穿聚合）。
    - feed 清单经沙箱实测可用（2026-09-15）：新华网时政、人民网时政/国际/社会、
      中新网滚动、IT之家科技。news.cn / cctv / rsshub 实测 404/502，故不纳入。
      注意：沙箱网络 ≠ 容器网络，容器侧可用性以 docker compose logs 实测为准。
    """

    INTENT = "news"  # search_many 路由：新闻意图专用源（聊天体启用路由时只它+web 兜底）
    FEEDS = [
        ("新华网·时政", "http://www.xinhuanet.com/politics/news_politics.xml"),
        ("人民网·时政", "http://www.people.com.cn/rss/politics.xml"),
        ("人民网·国际", "http://www.people.com.cn/rss/world.xml"),
        ("人民网·社会", "http://www.people.com.cn/rss/society.xml"),
        ("中新网·滚动", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
        ("IT之家·科技", "https://www.ithome.com/rss/"),
    ]
    # 只认「新闻名词」，不认「今天/最新」这类时间词 —— 否则「厦门今天天气怎么样」会被误判成
    # 新闻意图，天气检索里混进一堆新闻（冒烟测试抓出的真 bug）。宁可漏召回，不可污染。
    NEWS_RE = re.compile(
        r"(新闻|资讯|消息|热点|头条|快讯|动态|报道|news|headline)", re.IGNORECASE)
    # 主题词里要剥掉的噪声（时间词 + 礼貌语），避免「今天厦门新闻」抽出「今天厦门」，
    # 其中「天」是高频字，按单字匹配会把所有条目都判为命中。
    NOISE_RE = re.compile(
        r"(今天|明天|昨天|后天|今日|昨日|最新|最近|近期|有什么|有哪些|一下|帮我|查一下|请问|请)")

    # 单 feed 超时预算：6 feed 并行下墙钟 ≈ FEED_TIMEOUT × FEED_ATTEMPTS。
    # 【实测校准 2026-09-15】容器内首次拉新华网耗时 10.39s（第二次 0.35s，有缓存），
    # 故单 feed 预算必须 >10s，否则最慢的 feed 会被稳定丢弃 —— 上一版设 4s 就砍掉了它。
    # 取 12s × 1 次（不重试）：墙钟 12s，远低于原先 240s，又给冷启动留了余量。
    # 不重试的理由：超时说明该 feed 本来就慢，再试一次只是把墙钟翻倍，收益为零；
    # 且并行设计已把单 feed 慢的代价隔离掉了（不影响其余 5 个）。
    FEED_TIMEOUT = 12
    FEED_ATTEMPTS = 1

    def __init__(self, timeout: int = 20):
        self.id = "rss_news"
        self.name = "官方媒体 RSS 新闻"
        self.category = "新闻资讯"
        self.auth_type = "none"
        self.timeout = timeout

    def _fetch_feed(self, feed_name: str, url: str) -> list[dict]:
        """拉单个 feed 并解析成原始条目；任何失败都只降级该 feed（返回 []）。

        绝不向上抛：search_many 里一个源的异常不应拖垮整轮聚合。
        """
        try:
            body = _http_get_retry(url, self.FEED_TIMEOUT, attempts=self.FEED_ATTEMPTS)
        except ToolError as e:
            logger.warning("[rss_news] feed 拉取失败 %s: %s", feed_name, e)
            return []
        try:
            root = _parse_xml(body, url)
        except ToolError as e:
            logger.warning("[rss_news] feed 解析失败 %s: %s", feed_name, e)
            return []
        except Exception as e:  # noqa: BLE001 — 其它解析异常同样不得击穿整体
            logger.warning("[rss_news] feed 解析异常 %s: %s: %s",
                           feed_name, type(e).__name__, e)
            return []
        out: list[dict] = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            if not (title and link):
                continue
            out.append({
                "feed": feed_name,
                "url": link,
                "title": title,
                "desc": re.sub(r"<[^>]+>", "", (it.findtext("description") or "")).strip(),
                "pub": (it.findtext("pubDate") or "").strip(),
            })
        return out

    @staticmethod
    def _topic_of(query: str) -> str:
        """剥掉新闻意图词、时间/礼貌噪声与标点后剩下的主题词（用于给条目打分）。

        打分用**连续子串**匹配而非单字匹配 —— 单字（如「天」）会命中几乎所有中文条目，
        等于没打分。
        """
        t = RssNewsProvider.NEWS_RE.sub("", query or "")
        t = RssNewsProvider.NOISE_RE.sub("", t)
        return re.sub(r"[的了吗呢啊吧呀，。,\.!！?？\s]+", "", t)

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        if not self.NEWS_RE.search(query or ""):
            logger.info("[rss_news] 非新闻查询，跳过 query=%r", query)
            return []
        topic = self._topic_of(query)
        # M12 审议 MAJOR-1：原实现 6 个 feed **串行**拉取，单 feed timeout=20s × attempts=2
        # ≈ 最坏 240s，且 search_many 无整体 deadline —— 直接重演「/chat 两分钟不返回」。
        # 改为 feed 级并行 + 单 feed 超时压到 FEED_TIMEOUT，墙钟 ≈ 最慢单 feed。
        from concurrent.futures import ThreadPoolExecutor

        raw: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(self.FEEDS)))) as ex:
            futs = [ex.submit(self._fetch_feed, n, u) for n, u in self.FEEDS]
            for f in futs:
                try:
                    raw.extend(f.result())
                except Exception as e:  # noqa: BLE001 — 单 feed 任务异常不得击穿聚合
                    logger.warning("[rss_news] feed 任务异常: %s: %s",
                                   type(e).__name__, e)

        items: list[dict] = []
        for r in raw:
            desc = r["desc"]
            score = 0.5
            if topic and topic in (r["title"] + desc):
                score = 0.9  # 命中主题词（连续子串）的条目优先
            items.append({
                "url": r["url"],
                "title": f"[{r['feed']}] {r['title']}",
                "content": (f"{r['pub']} " if r["pub"] else "") + desc[:300],
                "score": score,
            })
        if topic:
            items.sort(key=lambda x: -x["score"])  # 稳定排序：同分保持 feed 顺序
        return items[:max(1, max_results)]


class DuckDuckGoProvider:
    """DuckDuckGo lite 通用网页检索（HTML，无需 key）。
    容器出口对该域可达性取决于网络（参考 gdelt TLS 不稳先例）→ 失败即单源降级，不冒充可用。"""

    INTENT = "web"  # search_many 路由：通用网页兜底（天气/新闻意图也调它）
    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout: int = 20):
        self.id = "duckduckgo"
        self.name = "DuckDuckGo 网页"
        self.category = "综合检索"
        self.auth_type = "none"
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """DDG lite 必须用 POST（data=q）提交，否则 GET /html/ 只返回空表单页（无 result__a）。
        浏览器 UA 是必须的——DDG 对非浏览器 UA（含默认 report-agent-team/1.0）返回空/拦截。
        kl=cn-zh 优先（中文区结果），为空再退一次无 kl，最大化出结果概率。"""
        last_err: Optional[Exception] = None
        for kl in ("cn-zh", ""):
            body = urllib.parse.urlencode(
                {"q": query, **({"kl": kl} if kl else {})}
            ).encode("utf-8")
            try:
                raw = _http_get(
                    self.ENDPOINT, self.timeout,
                    data=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        # 强制不压缩：urllib 不解密 brotli，Chrome UA 下 DDG 可能回 br 体，
                        # 导致解析不到 result__a → 一直 []。identity 保证返回明文 HTML。
                        "Accept-Encoding": "identity",
                        "Origin": "https://html.duckduckgo.com",
                    },
                )
            except ToolError as e:
                last_err = e
                continue
            text = html.unescape(raw.decode("utf-8", errors="replace"))
            logger.debug("DDG q=%r len=%d has_result__a=%s", query, len(raw), "result__a" in text)
            out = self._parse(text, max_results)
            if out:
                return out
        if last_err:
            logger.debug("DuckDuckGo 两次请求均失败: %s", last_err)
        return []

    def _parse(self, text: str, max_results: int) -> list[dict]:
        """鲁棒解析 DDG lite HTML：无论 class 与 href 谁在前，均提取 result__a 锚点。"""
        anchors = re.findall(r'<a\b[^>]*class="result__a"[^>]*>.*?</a>', text, re.S)
        out: list[dict] = []
        limit = max(1, min(int(max_results or 5), 20))
        for a in anchors[:limit]:
            href_m = re.search(r'href="([^"]*)"', a)
            if not href_m:
                continue
            uddg_m = re.search(r'uddg=([^&"]+)', href_m.group(1))
            if not uddg_m:
                continue
            url = html.unescape(urllib.parse.unquote(uddg_m.group(1)))
            title_m = re.search(r'>(.*?)</a>', a, re.S)
            title = html.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() if title_m else ""
            if not title or not url:
                continue
            out.append({
                "title": title,
                "url": url,
                "content": "",
                "score": round(0.82 - len(out) * 0.05, 2),
            })
        # 片段回填（best-effort）：与 anchors 顺序对应的 result__snippet
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
        for i, s in enumerate(snippets):
            if i < len(out):
                out[i]["content"] = html.unescape(re.sub(r"<[^>]+>", "", s)).strip()[:500]
        return out


# keyless 真源注册表（auth=none，无需密钥；build_search_tool 据此直连）
KEYLESS_PROVIDERS = {
    "rss_news": RssNewsProvider,   # M12 前哨：官方媒体 RSS 新闻（keyless，实测可用 feed 见类内注释）
    "arxiv": ArxivProvider,
    "semantic_scholar": SemanticScholarProvider,
    "crossref": CrossrefProvider,
    "wikipedia": WikipediaProvider,
    "openalex": OpenAlexProvider,
    "europe_pmc": EuropePMCProvider,
    "wikidata": WikidataProvider,
    "tencent_quote": TencentQuoteProvider,
    "gdelt": GDELTProvider,
    # M10-P3 电商 / 电商竞品分析（DESIGN_M10-P3.md）
    "taobao_suggest": TaobaoSuggestProvider,
    "amazon_suggest": AmazonSuggestProvider,
    "ebay_suggest": EbaySuggestProvider,
    "accio_tariff": AccioTariffProvider,
    # M10-P4 电商（DESIGN_M10-P4.md）：HS 码 → 六国关税 + 301 风险
    "hs_code_tariff": HsCodeTariffProvider,
    # M11：实时天气 / 通用网页（keyless 公开 API；失败即单源降级）
    "open_meteo": OpenMeteoProvider,
    "duckduckgo": DuckDuckGoProvider,
}


# provider 键 → 实现类（新增真源在此登记，不动态 import 运行时模块）
PROVIDER_REGISTRY = {
    "tavily": TavilyProvider,
    "mock": MockProvider,
    "coming_soon": ComingSoonProvider,
}


def _config_dir() -> Path:
    """按当前账号命名空间解析 config 目录（各自一套插件配置）。"""
    try:
        from server import tenancy
        return tenancy.tenant_path("config")
    except ImportError:
        return Path(__file__).resolve().parent.parent / "config"


def _secrets_file() -> Path:
    """按当前账号命名空间解析密钥文件（各自一套 key，绝不跨账号共享）。"""
    try:
        from server import tenancy
        return tenancy.secrets_path("plugins.env")
    except ImportError:
        return Path(__file__).resolve().parent.parent / ".secrets" / "plugins.env"


def load_secrets() -> dict:
    """读取数据源密钥：.secrets/plugins.env（KEY=VALUE）+ 进程 env。

    统一前缀 DS_<ID大写>_API_KEY；tavily 兼容旧 TAVILY_API_KEY。
    密钥不进 config/plugins.yaml（避免提交泄露）。
    """
    secrets: dict = {}
    for k, v in os.environ.items():
        if k.startswith("DS_") and k.endswith("_API_KEY") and v:
            secrets[k] = v
    if os.getenv("TAVILY_API_KEY"):
        secrets.setdefault("DS_TAVILY_API_KEY", os.getenv("TAVILY_API_KEY"))
    sec = _secrets_file()
    if sec.exists():
        for line in sec.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k.startswith("DS_") and k.endswith("_API_KEY") and v:
                secrets[k] = v
    return secrets


def _recompute_connected(spec: dict, secrets: dict) -> str:
    """重算插件连接状态（DESIGN §3.1 派生规则）：
    - provider=coming_soon → coming_soon（永远）
    - enabled=False → disconnected（控制器开关关闭，引擎不取数，UI 不得显示"已连接"）
    - auth_type=none → connected（无需密钥）
    - auth_type=api_key 且有 DS_<ID>_API_KEY → connected，否则 disconnected
    - 其它（oauth 等未实现）→ disconnected
    """
    if spec.get("provider") == "coming_soon":
        # oauth 类若已落盘会话 Cookie（has_credential 标记），诚实显示"凭证已保存·待接入"，
        # 仍不冒充 connected（引擎 build_search_tool 对 coming_soon 永远跳过）
        if spec.get("auth_type") == "oauth" and spec.get("has_credential"):
            return "credential_saved"
        return "coming_soon"
    if not spec.get("enabled", True):
        return "disconnected"  # 已实现但被控制器关闭：诚实反映"引擎不会用它"
    auth = spec.get("auth_type", "none")
    if auth == "none":
        return "connected"
    if auth == "api_key":
        return "connected" if secrets.get(f"DS_{spec['id'].upper()}_API_KEY") else "disconnected"
    return "disconnected"


def load_data_sources() -> list[dict]:
    """读取数据源插件注册表。缺省回落 legacy tools.yaml 单源（向后兼容）。"""
    p = _config_dir() / "plugins.yaml"
    secrets = load_secrets()
    if not p.exists():
        t = _config_dir() / "tools.yaml"
        if not t.exists():
            return []
        import yaml

        cfg = yaml.safe_load(t.read_text(encoding="utf-8")) or {}
        ws = cfg.get("web_search") or {}
        prov = (ws.get("provider") or "tavily").lower()
        # 通用化（2026-09-19）：不再硬编码 DS_TAVILY_API_KEY，与 _recompute_connected() 一致，
        # 否则换个 provider 这条回退分支会永远报 disconnected。
        status = ("connected" if (prov != "mock"
                                 and secrets.get(f"DS_{prov.upper()}_API_KEY"))
                  else "disconnected")
        return [{
            "id": "tavily", "name": "Tavily 搜索", "category": "数据源",
            "description": "（legacy）由 config/tools.yaml 驱动", "auth_type": "api_key",
            "provider": prov, "enabled": True, "status": status, "builtin": True,
        }]
    import yaml

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for s in (data.get("plugins") or []):
        if not isinstance(s, dict) or not s.get("id"):
            continue
        s = dict(s)
        s["status"] = _recompute_connected(s, secrets)
        out.append(s)
    return out


def _query_intent(q: str) -> str:
    """粗略判断查询意图，供 SearchTool.search_many 路由源。weather > news > general。
    仅在 route_by_intent=True（聊天体 ChatAgent）时启用，引擎研究检索默认不过滤。"""
    if OpenMeteoProvider._extract_location(q or ""):
        return "weather"
    if RssNewsProvider.NEWS_RE.search(q or ""):
        return "news"
    return "general"


class SearchTool:
    """多源聚合检索工具，接口对齐 WebSearchTool.search_many（orchestrator 调用零改动）。

    结果每条带 source 字段（= plugin id）；tool_status 条目**保留 tool="web_search"**
    （兼容既有回归测试硬断言），并新增独立 source 字段标识来源。
    """

    def __init__(self, providers: list, max_results: int = 5, timeout: int = 20,
                 using_mock_search: bool = False, degraded: list | None = None):
        # providers: list of (spec, provider_instance)
        self._providers = providers
        self.max_results = max_results
        self.timeout = timeout
        # True 仅当本次**显式**装载了 provider=mock 的源（离线/链路验证）。
        # 不再用 `not has_real` 推导——那会被「别的源是真的」中和，使降级警告永不触发。
        self.using_mock_search = using_mock_search
        # 本次未能装载的源：[{id, name, reason, fix}]。调用方（/chat、引擎摘要、控制台）
        # 必须据此对用户/模型如实说明「哪些源没参与」，而不是假装检索面完整。
        self.degraded: list[dict] = list(degraded or [])

    @property
    def providers(self):
        """本次已装载的 (spec, provider) 列表（只读）。供可观测性/验收脚本使用。"""
        return self._providers

    def search_many(self, queries: list[str], agent: str = "Researcher",
                    route_by_intent: bool = False):
        # 并行取数（M11 性能修复）：此前按「源 → query」双层串行，单源超时 20s × 16 源
        # 最坏 5 分钟，是 /chat 两分钟不返回的头号根因。改线程池并发后总耗时 ≈ 最慢单源。
        # jobs 按提交顺序收集，结果顺序与串行版一致（open_meteo 置顶仍最前）。
        from concurrent.futures import ThreadPoolExecutor

        # 按查询意图路由源（仅 ChatAgent 启用 route_by_intent）：天气意图→仅 open_meteo(+web
        # 兜底)，新闻意图→仅 rss_news(+web)，通用/研究→全部。减少 firehose / 提速 / 降限流噪声。
        # 引擎研究检索不传此标志 → 全部源照旧，零回归。
        intents = {q: _query_intent(q) for q in queries} if route_by_intent else {}
        jobs = []  # (sid, query, future)
        with ThreadPoolExecutor(
            max_workers=min(16, max(4, len(self._providers) * len(queries)))
        ) as ex:
            for spec, provider in self._providers:
                p_intent = getattr(provider, "INTENT", "research")
                for q in queries:
                    if route_by_intent:
                        qi = intents[q]
                        if qi == "weather" and p_intent not in ("weather", "web"):
                            continue
                        if qi == "news" and p_intent not in ("news", "web"):
                            continue
                    jobs.append((spec["id"], q,
                                  ex.submit(_search_with_retry, provider, q,
                                            self.max_results, self.timeout)))

        results: list[dict] = []
        status: list[dict] = []
        seen: set[str] = set()
        n = 0
        # 本次装载的占位源 id 集合（结果需带 is_mock 标记，见下方收集循环）
        mock_ids = {spec.get("id") for spec, p in self._providers
                    if isinstance(p, MockProvider)}
        # 降级源先登记：调用方据此知道「本次检索面不完整」，而不是把缺失源当成「没有相关内容」。
        for d in self.degraded:
            status.append({"agent": agent, "tool": "web_search", "source": d.get("id"),
                           "ok": False,
                           "error": "missing_key: 未配置密钥，本次未参与检索"
                           if d.get("reason") == "missing_key"
                           else f"{d.get('reason')}: 未能装载，本次未参与检索"})
        for sid, q, fut in jobs:
            try:
                raw = fut.result()
            except ToolError as e:
                status.append({"agent": agent, "tool": "web_search", "source": sid,
                               "ok": False, "error": f"{q} :: {e}"})
                logger.warning("[search_many] 源 %s 调用失败(agent=%s): %s", sid, agent, e)
                continue
            except Exception as e:  # noqa: BLE001 — 防御纵深：任何单源异常不得击穿整体聚合
                status.append({"agent": agent, "tool": "web_search", "source": sid,
                               "ok": False, "error": f"{q} :: 源异常({type(e).__name__}): {e}"})
                logger.warning("[search_many] 源 %s 未捕获异常(agent=%s): %s: %s",
                               sid, agent, type(e).__name__, e)
                continue
            if not raw:
                status.append({"agent": agent, "tool": "web_search", "source": sid,
                               "ok": False, "error": f"{q} :: 检索返回空结果"})
                # 单源静默降级曾是「天气凭空消失」根因：记录以便 docker compose logs api 排查
                logger.info("[search_many] 源 %s 返回空(agent=%s, query=%r)", sid, agent, q)
                continue
            status.append({"agent": agent, "tool": "web_search", "source": sid, "ok": True})
            # 可观测性（M11 教训）：此前成功分支完全沉默，导致「源到底有没有返回数据」只能靠猜，
            # 排查被迫绕大弯。命中即打点，含源 id/条数/query，docker compose logs api 可直接确认。
            logger.info("[search_many] 源 %s 命中 %d 条(agent=%s, query=%r)",
                        sid, len(raw), agent, q)
            for r in raw:
                url = (r.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                n += 1
                rec = {
                    "id": f"rec-{n}",
                    "url": url,
                    "title": (r.get("title") or url).strip(),
                    "snippet": (r.get("content") or "")[:500],
                    "credibility": score_to_credibility(r.get("score")),
                    "source": sid,
                    "_query": q,
                }
                # 占位数据必须自带标记：下游（sources 列表、日志「命中」计数、学习沉淀）
                # 一律不得把占位当证据。真源记录不带此键。
                if sid in mock_ids:
                    rec["is_mock"] = True
                results.append(rec)
        return results, status


def build_search_tool(specs: list[dict], secrets: dict, max_results: int = 5,
                      timeout: int = 20) -> SearchTool:
    """按 DESIGN §3.2 规则聚合 enabled 且非 coming_soon 的源：
    - coming_soon → 跳过（守卫，绝不冒充）
    - enabled=False → 跳过（控制器开关）
    - api_key 且 key 存在 → 真 Provider（TavilyProvider）
    - api_key 但 key 缺失 → **不注册**，登记进 degraded（reason=missing_key）
      —— 2026-09-19 修真缺陷：此前这里静默塞 MockProvider，于是「缺密钥」被伪装成
      「检索成功」，前端显示出 8 条假「来源」、日志把占位记成「命中 N 条」，
      而同时取回的真源结果被挤到展示位之外。立约「禁假配置」要求：不可用就明说不可用。
    - provider == mock → MockProvider（**仅显式声明时**；离线/链路验证用）
    - none + provider 在 KEYLESS_PROVIDERS → 真 Provider（M10-P1 公开 API 源，无需密钥）
    - 未知 provider → 跳过（当 coming_soon 处理）

    返回的 SearchTool 上 `.degraded` 列出本次**未能装载**的源及其原因与修复指引；
    `.using_mock_search` 仅在「显式 mock 源」时才为 True（不再由「有没有真源」推导，
    否则只要有一个 keyless 真源，全局降级警告就永远不触发）。
    """
    providers = []
    degraded: list[dict] = []
    has_explicit_mock = False
    for s in specs:
        if not s.get("enabled"):
            continue
        prov = (s.get("provider") or "coming_soon").lower()
        if prov == "coming_soon":
            continue
        sid = s["id"]
        name = s.get("name", sid)
        if prov == "tavily":
            env_key = f"DS_{sid.upper()}_API_KEY"
            key = secrets.get(env_key)
            if key:
                try:
                    providers.append((s, TavilyProvider(api_key=key, timeout=timeout)))
                    continue
                except ToolError as e:
                    degraded.append({
                        "id": sid, "name": name, "reason": "provider_init_failed",
                        "fix": f"检查「{name}」的接口地址/密钥格式后重试（{e}）",
                    })
                    continue
            degraded.append({
                "id": sid, "name": name, "reason": "missing_key",
                "fix": f"在「设置 → 数据源」中为「{name}」填写 API Key（环境变量名 {env_key}）",
            })
        elif prov == "mock":
            # 显式 mock：合法用途（离线开发 / 链路验证），但结果带 is_mock 标记，
            # 不得进「来源」列表冒充引用。
            providers.append((s, MockProvider(sid, name)))
            has_explicit_mock = True
        elif prov in KEYLESS_PROVIDERS:
            # M10-P1：keyless 真·公开 API 源（arxiv/semantic_scholar/crossref/wikipedia）
            providers.append((s, KEYLESS_PROVIDERS[prov](timeout=timeout)))
        else:
            # 未知 provider：当 coming_soon，跳过
            continue
    return SearchTool(providers, max_results=max_results, timeout=timeout,
                      using_mock_search=has_explicit_mock, degraded=degraded)
