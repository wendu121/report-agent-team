"""M12-2 聊天智能体只读网页抓取原语（fetch_url）。

与 skill_importer 的区别与边界：
  - skill_importer.fetch_source 受 GitHub 域名白名单约束，并强制 CLASSIFY/INSTALL 闸门
    （只服务于「装 skill」）；
  - 本模块是**只读研究抓取**：fetch 任意可信 http/https URL 的正文，不落盘、不安装、不执行，
    专供 chat agent 「抓上网」去研究/分析外部链接（仓库、文档、博客），支撑「学习进步」。

安全边界（诚实声明，非 OS 硬隔离；容器跑在 appuser、无 sudo，且本函数只读 GET）：
  1. 仅 http/https；拒绝 file:///ftp/等本地/非 HTTP 协议。
  2. SSRF 基础防护（best-effort，不开 DNS-rebinding 窗口承诺）：解析后若是私有/环回/链路本地
     /保留地址（127.0.0.0/8、10/8、172.16/12、192.168/16、169.254/16、::1、fc00::/7、fe80::/10、
     localhost、*.local、*.internal）一律拒绝。云元数据端点 169.254.169.254 命中链路本地即拦，
     避免凭据泄露。
  3. 体积上限 MAX_BYTES；超时 FETCH_TIMEOUT；HTML 正文提炼后按 max_chars 截断回注。
"""
from __future__ import annotations

import base64
import html as _html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from tools.skill_importer import extract_main_text  # 复用正文提炼（保守去噪 + 质量校验）

UA = {"User-Agent": "report-agent-team-web-fetch/1.0"}
MAX_BYTES = 2_000_000       # 抓取整页上限（正文提炼后再按 max_chars 截断）
FETCH_TIMEOUT = 20


class WebFetchError(Exception):
    """抓取失败：给明确原因，不静默吞错。"""


# --------------------------------------------------------------------------
# SSRF 防护（best-effort）
# --------------------------------------------------------------------------
_BLOCKED_HOSTNAMES = ("localhost",)
_BLOCKED_SUFFIXES = (".local", ".internal", ".svc", ".cluster.local")


def _ip_blocked(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
    )


def _host_blocked(host: str) -> Tuple[bool, str]:
    """返回 (是否拦截, 归一化 host)。best-effort：DNS 解析失败时放行（真实请求会自然失败）。"""
    h = (host or "").strip().lower()
    if h in _BLOCKED_HOSTNAMES or any(h.endswith(s) for s in _BLOCKED_SUFFIXES):
        return True, h
    # IP 字面量
    try:
        return _ip_blocked(ipaddress.ip_address(h)), h
    except ValueError:
        pass
    # 主机名 → 解析后逐个校验（不承诺关闭 DNS rebinding 窗口，属已知边界）
    try:
        for info in socket.getaddrinfo(h, None):
            addr = info[4][0]
            try:
                if _ip_blocked(ipaddress.ip_address(addr)):
                    return True, h
            except ValueError:
                continue
    except Exception:  # noqa: BLE001 — 解析失败放行；best-effort 防护
        return False, h
    return False, h


def _host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


# --------------------------------------------------------------------------
# HTTP GET（自带 SSRF 前置校验 + 重定向逐跳校验）
# --------------------------------------------------------------------------
class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随重定向时逐跳校验目标 host，防止重定向穿透到内网/云元数据地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            host = _host_of(newurl)
            if host:
                blocked, _ = _host_blocked(host)
                if blocked:
                    raise WebFetchError(
                        f"重定向目标 {host!r} 命中 SSRF 防护，拒绝跟随（原请求 {req.get_full_url()!r}）"
                    )
        except WebFetchError:
            raise
        except Exception:  # noqa: BLE001 — 解析异常放行，由真实请求兜底
            pass
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get(url: str, timeout: int = FETCH_TIMEOUT) -> Tuple[bytes, str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError(f"仅支持 http/https，收到 {parsed.scheme!r}")
    host = _host_of(url)
    if not host:
        raise WebFetchError("URL 无法解析主机名")
    blocked, _ = _host_blocked(host)
    if blocked:
        raise WebFetchError(
            f"主机 {host!r} 命中 SSRF 防护（私有/环回/链路本地地址，拒绝访问）"
        )
    req = urllib.request.Request(url, headers=UA)
    try:
        # 自定义 opener：重定向逐跳过 SSRF 校验（默认 urlopen 会盲目跟随到内网/元数据）
        opener = urllib.request.build_opener(_SSRFRedirectHandler)
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final = resp.geturl()
    except urllib.error.HTTPError as e:
        raise WebFetchError(f"HTTP {e.code} 抓取失败: {url}")
    except Exception as e:  # noqa: BLE001
        raise WebFetchError(f"抓取失败 {url}: {type(e).__name__}: {e}")
    if len(raw) > MAX_BYTES:
        raise WebFetchError(f"内容超过 {MAX_BYTES} 字节上限，拒绝（防超大/恶意输入）")
    return raw, ctype, final


# --------------------------------------------------------------------------
# GitHub 仓库/文件候选定位
# --------------------------------------------------------------------------
def _github_candidates(url: str) -> list[str]:
    """GitHub 链接 → 可能的 raw 直链（按序尝试）。

    - blob/tree/<branch>/<path>  → 定位具体文件（.md/.txt/.rst 才值得抓，否则抓该目录 README）
    - 仓库根（owner/repo[.git]）  → 依次尝试常见 README 变体（HEAD/main/master）
    """
    p = urllib.parse.urlparse(url)
    path = p.path
    if path.endswith(".git"):
        path = path[:-4]
    parts = [x for x in path.split("/") if x]
    if len(parts) < 2:
        return []
    owner, repo = parts[0], parts[1]
    if len(parts) >= 5 and parts[2] in ("blob", "tree"):
        branch, rest = parts[3], "/".join(parts[4:])
        if rest.lower().endswith((".md", ".markdown", ".txt", ".rst")):
            return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"]
        # 目录 → 该目录下的 README
        return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest.rstrip('/')}/README.md"]
    base = f"https://raw.githubusercontent.com/{owner}/{repo}"
    cands: list[str] = []
    for b in ("HEAD", "main", "master"):
        for name in ("README.md", "readme.md", "README.rst", "README.txt", "docs/README.md"):
            cands.append(f"{base}/{b}/{name}")
    return cands


_GITHUB_PER_TIMEOUT = 10          # 单候选超时（原 20s，压缩以加快失败回退）
_GITHUB_TOTAL_BUDGET = 45.0       # 整个 GitHub 抓取总预算，杜绝 15 候选×20s=300s 拖垮 ChatAgent step


def _github_blob_targets(url: str) -> Tuple[str, str, list]:
    """GitHub URL → (owner, repo, [(branch, path)])：contents API 的取文件目标。

    与 _github_candidates 同源逻辑（.git 剥离、blob/tree 定位、仓库根退 README），
    但输出的是「分支 + 文件路径」而不是 raw 直链 —— 供 contents API 降级通道用。
    """
    p = urllib.parse.urlparse(url)
    path = p.path[:-4] if p.path.endswith(".git") else p.path
    parts = [x for x in path.split("/") if x]
    if len(parts) < 2:
        return "", "", []
    owner, repo = parts[0], parts[1]
    if len(parts) >= 5 and parts[2] in ("blob", "tree"):
        branch, rest = parts[3], "/".join(parts[4:])
        if rest.lower().endswith((".md", ".markdown", ".txt", ".rst")):
            return owner, repo, [(branch, rest)]
        return owner, repo, [(branch, rest.rstrip("/") + "/README.md")]
    return owner, repo, [(b, "README.md") for b in ("HEAD", "main", "master")]


def _github_contents(owner: str, repo: str, fpath: str, branch: str,
                     max_chars: int, deadline: float) -> Tuple[str, str, str]:
    """经 GitHub REST contents API 取同一文件（base64 解码）。

    存在理由（2026-09-15 ChatAgent 真 e2e 实测）：受限网络/代理对 raw.githubusercontent.com
    的 CONNECT 一律 502 或 TLS 抖动（UNEXPECTED_EOF），agent 连撞 3 次 fetch_url 全败后
    只能放弃；而 api.github.com 是**另一条通道**，同一文件 0.7s 即达。
    contents API 单次失败若带 SSL 特征则立即重试一次（与 skill_importer 同款判定）。
    """
    api = (f"https://api.github.com/repos/{owner}/{repo}/contents/"
           + urllib.parse.quote(fpath) + "?ref=" + urllib.parse.quote(branch))
    last: Optional[WebFetchError] = None
    for attempt in range(2):
        if time.monotonic() > deadline:
            break
        try:
            raw, _ctype, final = _http_get(api, timeout=_GITHUB_PER_TIMEOUT)
            data = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("content"):
                text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                return text[:max_chars], "github_file", api
            if isinstance(data, list):
                raise WebFetchError(f"{fpath} 是目录而非文件")
            raise WebFetchError(
                f"contents API 未返回正文（{str(data.get('message'))[:80]}）")
        except WebFetchError as e:
            last = e
            if attempt == 0 and "SSL" in str(e):
                continue  # 瞬时 TLS 抖动：换不来别的，立即重试一次
            break
        except Exception as e:  # noqa: BLE001
            raise WebFetchError(f"contents API 解析失败: {type(e).__name__}: {e}")
    if last:
        raise last
    raise WebFetchError("contents API 在预算内未成功")


def _fetch_github(url: str, max_chars: int) -> Tuple[str, str, str]:
    """先抓 README/raw 直链；全部失败则降级 contents API，再不行退回仓库主页 HTML 提炼。

    带总预算 + 单候选超时：raw.githubusercontent 在受限/防火墙网络下可能「连得上但不回包」，
    导致每个候选挂满超时；候选最多 15 个 × 原 20s = 300s，正好把 ChatAgent step 拖到 300s 超时。
    这里单候选压到 10s、总预算 45s，确保即使全挂也远早于 step 超时返回，并给出明确错误。
    """
    deadline = time.monotonic() + _GITHUB_TOTAL_BUDGET
    last_err: Optional[WebFetchError] = None
    for cand in _github_candidates(url):
        if time.monotonic() > deadline:
            break
        try:
            raw, _ctype, final = _http_get(cand, timeout=_GITHUB_PER_TIMEOUT)
        except WebFetchError as e:
            last_err = e
            continue
        text = raw.decode("utf-8", errors="replace")
        st = "github_readme" if "readme" in cand.lower() else "github_file"
        return text[:max_chars], st, final
    # raw 全废 → contents API（同一文件的另一条通道，见 _github_contents 文档）
    owner, repo, targets = _github_blob_targets(url)
    if owner and repo:
        for branch, fpath in targets:
            if time.monotonic() > deadline:
                break
            try:
                return _github_contents(owner, repo, fpath, branch, max_chars, deadline)
            except WebFetchError as e:
                last_err = e
                continue
    # README 都没命中 → 抓仓库主页，提炼简介/文件列表（同样受预算约束）
    if time.monotonic() <= deadline:
        try:
            raw, _ctype, final = _http_get(url, timeout=_GITHUB_PER_TIMEOUT)
            text = raw.decode("utf-8", errors="replace")
            title = _extract_title(text)
            extracted = extract_main_text(text)
            return (extracted or text)[:max_chars], "webpage", final
        except WebFetchError as e:
            last_err = e
    if last_err is not None:
        raise last_err
    raise WebFetchError("GitHub 仓库未取到 README，且所有候选在预算内超时/失败")


# --------------------------------------------------------------------------
# HTML <title> 提取
# --------------------------------------------------------------------------
def _extract_title(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.S | re.I)
    if m:
        return _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:200]
    return ""


# --------------------------------------------------------------------------
# 顶层入口
# --------------------------------------------------------------------------
def fetch_url(url: str, *, max_chars: int = 120_000) -> Dict[str, Any]:
    """只读抓取任意 http/https URL 正文（研究/学习用，不落盘不执行）。

    返回 {ok, text, source_type, final_url, title, error}。
      source_type ∈ {github_readme, github_file, webpage, raw_text}
    """
    empty = {"ok": False, "text": "", "source_type": "",
             "final_url": "", "title": "", "error": ""}
    if not isinstance(url, str) or not url.strip():
        return {**empty, "error": "缺少 url 参数"}
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {**empty, "error": f"仅支持 http/https，收到 {parsed.scheme!r}"}
    host = _host_of(url)
    if not host:
        return {**empty, "error": "URL 无法解析主机名"}
    blocked, _ = _host_blocked(host)
    if blocked:
        return {**empty, "error": f"主机 {host!r} 命中 SSRF 防护，拒绝访问"}

    cap = max(1, min(int(max_chars) or 120_000, 300_000))
    try:
        if host == "github.com" or host.endswith(".github.com"):
            text, source_type, final = _fetch_github(url, cap)
            return {"ok": True, "text": text, "source_type": source_type,
                    "final_url": final, "title": "", "error": ""}
        raw, ctype, final = _http_get(url)
        text = raw.decode("utf-8", errors="replace")
        if "<html" in text[:2000].lower() or "text/html" in ctype:
            title = _extract_title(text)
            body = extract_main_text(text) or text
            return {"ok": True, "text": body[:cap], "source_type": "webpage",
                    "final_url": final, "title": title, "error": ""}
        return {"ok": True, "text": text[:cap], "source_type": "raw_text",
                "final_url": final, "title": "", "error": ""}
    except WebFetchError as e:
        return {**empty, "error": str(e)}
