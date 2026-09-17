# tools/web_search.py · 联网检索（M4）
#
# 抽象层：上层只消费字段契约 {id, url, title, snippet, credibility}，
# 不出现任何服务商专有名词（DESIGN §11-2）。未来替换 MCP 搜索只需新增 Provider。
#
# 失败语义：单条 query 失败不影响其余（失败隔离）；失败 → tool_status.ok=false + error，
# 由 Gate 判定 rework / escalate，不静默崩溃（DESIGN §10）。

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from . import ToolError

# score（相似度/质量分）→ credibility 评级映射；阈值属抽象层策略，不进 prompt。
_CRED_HIGH = 0.7
_CRED_MEDIUM = 0.4


def score_to_credibility(score) -> str:
    """服务商分值 → 契约字段 credibility。无分值时保守取 medium。"""
    if score is None:
        return "medium"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "medium"
    if s >= _CRED_HIGH:
        return "high"
    if s >= _CRED_MEDIUM:
        return "medium"
    return "low"


class WebSearchProvider(Protocol):
    """检索服务商接口。实现须返回列表，每项含 title/url/content，可选 score。"""

    def search(self, query: str, max_results: int) -> list[dict]:
        ...


class TavilyProvider:
    """Tavily 检索。api_key 由环境变量注入，不落配置文件。"""

    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout: int = 20):
        if not api_key:
            raise ToolError("TAVILY_API_KEY 未配置")
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        payload = json.dumps({
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.ENDPOINT, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            raise ToolError(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise ToolError(f"请求失败: {e}")

        raw = data.get("results") or []
        return [
            {
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "content": r.get("content") or "",
                "score": r.get("score"),
            }
            for r in raw
        ]


class MockProvider:
    """离线 / 无密钥时的占位实现。输出带 [MOCK] 前缀，可被审计一眼识别。"""

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        seed = abs(hash(query)) % 10000
        return [
            {
                "title": f"[MOCK] {query} · 结果 {i + 1}",
                "url": f"https://mock.local/{seed}/{i + 1}",
                "content": f"[MOCK] 这是关于「{query}」的占位内容，非真实检索结果。",
                "score": 0.6,
            }
            for i in range(max_results)
        ]


class WebSearchTool:
    """面向引擎的检索工具：批量查询 + 失败隔离 + 输出契约化。"""

    def __init__(self, provider: WebSearchProvider, max_results: int = 5):
        self.provider = provider
        self.max_results = max_results

    def search_many(self, queries: list[str], agent: str = "Researcher"):
        """逐条检索，返回 (contract_results, tool_status_entries)。

        单条 query 失败 → 该条 ok=false，其余继续（失败隔离）。
        结果按 url 去重，并统一编号 rec-n（供引用链使用）。
        """
        results: list[dict] = []
        status: list[dict] = []
        seen: set[str] = set()
        n = 0

        for q in queries:
            try:
                raw = self.provider.search(q, self.max_results)
            except ToolError as e:
                status.append({"agent": agent, "tool": "web_search", "ok": False, "error": f"{q} :: {e}"})
                continue
            if not raw:
                status.append({"agent": agent, "tool": "web_search", "ok": False,
                               "error": f"{q} :: 检索返回空结果"})
                continue
            status.append({"agent": agent, "tool": "web_search", "ok": True})
            for r in raw:
                url = (r.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                n += 1
                results.append({
                    "id": f"rec-{n}",
                    "url": url,
                    "title": (r.get("title") or url).strip(),
                    "snippet": (r.get("content") or "")[:500],
                    "credibility": score_to_credibility(r.get("score")),
                    "_query": q,   # 审计用：该条来源自哪次检索
                })
        return results, status
