# tests/test_m12_review_fixes.py · M12 独立审议（REVIEW_M12_diag.md）两条 MAJOR 的回归锁
#
# 目的：把审议发现的两个缺陷钉死，防止回退。全部离线，不打真网、不依赖密钥。
#   - MAJOR-1：RssNewsProvider 曾串行拉 6 个 feed（20s × 2 次重试 ≈ 最坏 240s）
#             → 重演「/chat 两分钟不返回」。改为 feed 级并行 + 单 feed 超时预算。
#   - MAJOR-2：_extract_location 噪声表漏否定/疑问/量词，且清洗顺序会把时间词
#             从中间劈开 → 天气静默消失。
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tools import ToolError  # noqa: E402
import tools.data_sources as ds  # noqa: E402
from tools.data_sources import OpenMeteoProvider, RssNewsProvider  # noqa: E402


# ---------------- MAJOR-2：地名抽取 ----------------

LOCATION_CASES = [
    ("厦门今天天气怎么样", "厦门"),
    ("北京明天气温", "北京"),          # 关键词「天气」会把「明天」劈开，必须先在整句去时间词
    ("厦门会不会下雨", "厦门"),        # 否定/疑问词残留：「厦门不」
    ("青岛没下雨吗", "青岛"),          # 「青岛没」
    ("2025年3月厦门天气", "厦门"),     # 日期
    ("帮我查一下深圳天气", "深圳"),    # 礼貌语
    ("北京明天会下雪吗", "北京"),
    ("上海天气", "上海"),
    ("广州今天多少度", "广州"),        # 「多少度」需进关键词表，否则整个返回 None
    # 反例：曾为修上一条把裸字「度」加进关键词表，结果「印度天气」在 index 1 命中「度」
    # → 抽出「印」。只认「多少度/几度」，绝不认裸字「度」。
    ("印度天气", "印度"),
    ("印度今天天气", "印度"),
    ("今天印度多少度", "印度"),
    # 反例：日期单位「日」「周」曾被无条件剥离（987048f）→ 日本/周口/日照 全被砍坏。
    # 只在紧跟数字时才剥单位。
    ("日本天气", "日本"),
    ("周口天气", "周口"),
    ("日照天气", "日照"),
    ("日喀则天气", "日喀则"),
    ("本溪天气", "本溪"),
    ("2025年3月15日厦门天气", "厦门"),   # 带数字的日期单位仍须剥掉
    ("晴隆天气", "晴隆"),              # NIT-14：裸字「晴」已移出关键词表（晴隆县）
    ("下周三北京天气", "北京"),          # NIT-13：周+星期几须整块剥掉
    ("周末上海天气", "上海"),
    ("杭州气温度", "杭州"),
    ("哈尔滨现在气温多少", "哈尔滨"),
    ("成都最近天气如何", "成都"),      # 「最近」+「如何」在关键词右侧
    ("西安明天有没有雨", "西安"),
    ("厦门 天气", "厦门"),             # 空格
    ("weather in Guangzhou", "Guangzhou"),
    ("Guangzhou temperature", "Guangzhou"),
    ("今天天气", None),                # 无地名应诚实返回 None，不猜
]


@pytest.mark.parametrize("query,expected", LOCATION_CASES)
def test_extract_location(query, expected):
    assert OpenMeteoProvider._extract_location(query) == expected


# ---------------- MAJOR-1：RSS feed 并行 + 超时预算 ----------------

def test_rss_feeds_are_fetched_in_parallel(monkeypatch):
    """6 个 feed 若串行，慢 feed 会叠加成分钟级；并行后墙钟 ≈ 最慢单 feed。

    用「每个 feed 固定睡 0.4s 且必失败」打桩：
      串行 = 6 feed × 0.4s × 2 次重试 ≈ 4.8s
      并行 ≈ 0.8s
    断言 < 2.5s —— 既能抓住回退到串行，又留足 CI 抖动余量。
    """
    def slow_fail(url, timeout, attempts=1, **kw):
        # 按 attempts 重复耗时，真实模拟重试（否则打桩只算一次，测不出重试放大效应）
        for _ in range(max(1, attempts)):
            time.sleep(0.4)
        raise ToolError("stub: 故意失败")

    monkeypatch.setattr(ds, "_http_get_retry", slow_fail)

    t0 = time.time()
    items = RssNewsProvider().search("今天有什么新闻", max_results=5)
    elapsed = time.time() - t0

    assert items == []
    # 实测基线（本地）：串行 4.8s（6 feed × 0.4s × 2 次重试），并行 0.8s。
    # 阈值取 1.8s：并行留 2 倍余量，串行 4.8s 必红。
    assert elapsed < 1.8, (
        f"RSS 拉取耗时 {elapsed:.2f}s，疑似回退为串行（并行应 ≈0.8s，串行 ≈4.8s）")


def test_rss_single_feed_failure_degrades_only_that_feed(monkeypatch):
    """单 feed 失败只降级该 feed：其余 feed 仍返回结果，且不向上抛。"""
    real = ds._http_get_retry
    calls = []

    def flaky(url, timeout, attempts=1, **kw):
        calls.append(url)
        if "xinhuanet" in url:
            raise ToolError("stub: 新华网挂了")
        return real(url, min(timeout, 4), attempts=1, **kw)

    monkeypatch.setattr(ds, "_http_get_retry", flaky)
    # 只用两个 feed 缩短用例；真实网络不可用时 real() 也会失败 → 结果可为空但不得抛异常
    monkeypatch.setattr(RssNewsProvider, "FEEDS", RssNewsProvider.FEEDS[:2])

    out = RssNewsProvider().search("新闻", max_results=3)
    assert isinstance(out, list)
    assert any("xinhuanet" in u for u in calls)


def test_rss_ignores_non_news_query():
    """非新闻查询必须返回空 —— 否则天气/学术检索会被新闻污染。"""
    assert RssNewsProvider().search("量子计算的最新论文", max_results=5) == []


def test_rss_weather_query_is_not_news():
    """「厦门今天天气怎么样」不得被判成新闻意图（NEWS_RE 曾含「今天」导致误判）。"""
    assert RssNewsProvider.NEWS_RE.search("厦门今天天气怎么样") is None


# ---------------- MAJOR-4：open_meteo 网络失败不得换语言重试 ----------------

def test_open_meteo_network_failure_does_not_retry_other_language(monkeypatch):
    """网络/HTTP 失败时换语言再试毫无意义（同一域名同一错误），只会把最坏耗时翻倍。

    修前：zh 失败 -> continue -> en 再失败，最坏耗时翻倍并越过 2 分钟线
    （RSS 修好后这是 /chat 的头号耗时源）。
    """
    calls = []

    def fail(url, timeout, **kw):
        calls.append(url)
        raise ToolError("stub: 网络失败")

    monkeypatch.setattr(ds, "_http_get_json_retry", fail)
    assert OpenMeteoProvider().search("厦门天气", max_results=5) == []
    assert len(calls) == 1, f"网络失败后仍换语言重试，共调用 {len(calls)} 次（应为 1）"


def test_rss_feed_timeout_is_bounded():
    """NIT-15：给单 feed 超时预算上锁，防止有人把 FEED_TIMEOUT 调回 20s。

    注意这条**不能替代**上面的 wall-clock 断言 —— 它锁的是配置值，
    抓不到「并行被改回串行」；两者叠加才完整。
    """
    # 双向锁：上界防回到 240s，下界防把慢 feed（新华网实测冷启动 10.39s）压超时丢掉。
    wall = RssNewsProvider.FEED_TIMEOUT * RssNewsProvider.FEED_ATTEMPTS
    assert wall <= 16, f"单源墙钟预算 {wall}s 过大（并行下 = 最慢单 feed）"
    assert RssNewsProvider.FEED_TIMEOUT >= 10, (
        f"单 feed 超时 {RssNewsProvider.FEED_TIMEOUT}s 过短，实测新华网冷启动需 10.39s，会被丢弃")


def test_rss_feeds_actually_run_concurrently(monkeypatch):
    """NIT-15：用并发峰值断言并行（不读时钟，避免慢 CI 抖动）。"""
    import threading

    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def slow(url, timeout, attempts=1, **kw):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        try:
            time.sleep(0.3)
        finally:
            with lock:
                state["cur"] -= 1
        raise ToolError("stub")

    monkeypatch.setattr(ds, "_http_get_retry", slow)
    RssNewsProvider().search("新闻", max_results=5)
    assert state["peak"] >= 2, f"feed 未并发执行（并发峰值 {state['peak']}，串行时为 1）"


def test_open_meteo_retries_other_language_when_no_match(monkeypatch):
    """对照用例：请求成功但无匹配，才值得换语言再试（zh -> en 共两次）。

    用非别名城市「伦敦」（不在 _CN_CITY_ALIASES），避免别名扩展把调用数从 2 变 4；
    别名扩展本身由 T40 的 geocoding 测试覆盖（厦门→Xiamen）。"""
    calls = []

    def empty(url, timeout, **kw):
        calls.append(url)
        return {}

    monkeypatch.setattr(ds, "_http_get_json_retry", empty)
    assert OpenMeteoProvider().search("伦敦天气", max_results=5) == []
    assert len(calls) == 2, f"无匹配时应换语言重试一次，实际调用 {len(calls)} 次"
