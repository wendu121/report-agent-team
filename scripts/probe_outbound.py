#!/usr/bin/env python
"""出网连通性探针（只读诊断，不写任何文件、不改任何配置）。

用途：判定容器内「境外 HTTPS 是否可达」以及「宿主代理能否救回来」。

用法（在宿主 PowerShell 执行）：

    # 1) 当前环境（直连）
    docker compose exec api python probe_outbound.py

    # 2) 临时挂代理试试（不动 .env），10808 换成你宿主代理端口
    docker compose exec -e HTTP_PROXY=http://host.docker.internal:10808 \
                        -e HTTPS_PROXY=http://host.docker.internal:10808 \
                        api python probe_outbound.py

判定口径：
    OK   = TLS 握手 + HTTP 响应都成功
    FAIL = 记录异常类型。SSL: UNEXPECTED_EOF_WHILE_READING = 握手被中间设备掐断（拦截/重置）
"""

import os
import socket
import ssl
import time
import urllib.error
import urllib.request

# (分组, 名称, URL)
TARGETS = [
    ("境外", "open_meteo(天气)", "https://api.open-meteo.com/v1/forecast?latitude=24.48&longitude=118.09&current=temperature_2m"),
    ("境外", "duckduckgo", "https://api.duckduckgo.com/?q=xiamen&format=json"),
    ("境外", "wikipedia", "https://zh.wikipedia.org/api/rest_v1/page/summary/%E5%8E%A6%E9%97%A8"),
    ("境外", "arxiv", "http://export.arxiv.org/api/query?search_query=all:electron&max_results=1"),
    ("境外", "tavily", "https://api.tavily.com/"),
    ("境内", "rss_news·新华网", "http://www.xinhuanet.com/politics/news_politics.xml"),
    ("境内", "rss_news·中新网", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ("境内", "rss_news·IT之家", "https://www.ithome.com/rss/"),
    ("境内", "tencent_quote", "https://smartbox.gtimg.cn/s3/?q=sh600519&t=all"),
    ("境内", "baidu(基线)", "https://www.baidu.com"),
]

TIMEOUT = 8.0


def show_env():
    print("=" * 72)
    print("环境变量（代理相关）")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "no_proxy"):
        v = os.getenv(k)
        if v:
            print(f"  {k} = {v}")
    if not any(os.getenv(k) for k in ("HTTP_PROXY", "http_proxy")):
        print("  (无代理环境变量 -> 直连)")


def probe_host_proxy():
    """宿主代理是否从容器内可达（决定 CHAT_PROXY 有没有意义）。"""
    print("=" * 72)
    print("宿主代理可达性（host.docker.internal 常见端口）")
    try:
        ip = socket.gethostbyname("host.docker.internal")
        print(f"  host.docker.internal -> {ip}")
    except Exception as e:
        print(f"  host.docker.internal 解析失败: {e}")
        return
    for port in (10808, 10809, 7890, 8888, 1080, 20171):
        try:
            t0 = time.time()
            with socket.create_connection(("host.docker.internal", port), timeout=2.0):
                print(f"  端口 {port}: 可达 ({time.time() - t0:.2f}s)")
        except Exception as e:
            print(f"  端口 {port}: 不可达 ({type(e).__name__}: {e})")


def probe_urls():
    print("=" * 72)
    print(f"出网探测（timeout={TIMEOUT}s）")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    ua = {"User-Agent": "Mozilla/5.0 (probe-outbound)"}

    for group, name, url in TARGETS:
        req = urllib.request.Request(url, headers=ua)
        t0 = time.time()
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                body = resp.read(2048)
                print(f"  [{group}] {name:<22} OK   HTTP {resp.status:<3} "
                      f"{len(body):>6}B  {time.time() - t0:.2f}s")
        except Exception as e:
            msg = str(e)
            if len(msg) > 90:
                msg = msg[:90] + "…"
            print(f"  [{group}] {name:<22} FAIL {type(e).__name__}: {msg}  "
                  f"{time.time() - t0:.2f}s")


def verdict():
    print("=" * 72)
    print("读法：")
    print("  · 全 OK（含境外）-> 出网正常，**不要**配 CHAT_PROXY；")
    print("    此前偶发的 SSL UNEXPECTED_EOF 属瞬时抖动，不是持续性拦截，重试即可。")
    print("  · 境外 FAIL、境内 OK，且宿主代理端口「可达」-> 在 .env 写")
    print("    CHAT_PROXY=http://host.docker.internal:<端口> 并 up -d 重建 api。")
    print("  · 宿主代理端口「不可达」-> 容器到不了宿主代理（常见：代理只监听 127.0.0.1")
    print("    或未开 Allow LAN）。此时配 CHAT_PROXY 只会让请求全部失败，绝对不要配。")
    print("    出网问题只能从容器网络/DNS 侧解决，与本项目代码无关。")
    print("  · 某个 feed 明显慢（>5s）-> 留意 RssNewsProvider.FEED_TIMEOUT，")
    print("    预算低于该耗时会把这个 feed 稳定丢弃（单源降级，不报错但少一路数据）。")
    print("=" * 72)


if __name__ == "__main__":
    show_env()
    probe_host_proxy()
    probe_urls()
    verdict()
