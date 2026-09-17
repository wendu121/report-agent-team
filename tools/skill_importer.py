"""M12-1 Skill 自主获取 Importer 内核。

目标（DESIGN_M12 §1 G1）：给定任意 URL，自动完成
    FETCH → PARSE → CLASSIFY → ADAPT → VALIDATE → GATE → INSTALL → VERIFY
无需人手改配置，**且 agent 不得修改引擎自身代码**（N1）—— 自主止步于
「生成装配方案 + 审批后落盘配置/技能/专家包」。

安全边界（DESIGN_M12 §6，不可协商）：
  1. 网络白名单：默认仅 GitHub 系域；其它域名须显式入 `config/skill_import_allowlist.yaml`。
  2. 路径白名单：只写 `skills/`、`config/skills.yaml`、`.audit/`。严禁写 tools/、orchestrator.py、server/。
  3. 禁止执行外部脚本：L2 直接拒绝。
  4. 强 schema + 白名单占位符/受限 JSONPath。
  5. 先备份后写，可回滚。
  6. 幂等：同 (source_url, sha256) 不重复落盘。
  7. 全动作进 `.audit`。

依赖：仅标准库 + ruamel.yaml（写注册表保注释；缺失则 fail loud，不降级 PyYAML）。
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent.parent


def _tenant_root() -> Path:
    """按当前账号命名空间解析资源根（各自一套技能/审计）。

    严格路径：server 可导入时一律走 tenancy.account_root()（无上下文抛 TenancyError，
    绝不静默回落全局 = 假隔离）；仅当 server 包本身不可导入（独立 CLI 调试）才回落 BASE。
    """
    try:
        from server import tenancy
        return tenancy.account_root()
    except ImportError:
        return BASE


def _config_dir() -> Path:
    return _tenant_root() / "config"


def _skills_yaml() -> Path:
    return _config_dir() / "skills.yaml"


def _skills_dir() -> Path:
    return _tenant_root() / "skills"


def _audit_dir() -> Path:
    return _tenant_root() / ".audit"


def _proposal_dir() -> Path:
    return _audit_dir() / "skill_proposals"


def _backup_dir() -> Path:
    return _audit_dir() / "skill_backups"


def _import_log() -> Path:
    return _audit_dir() / "skill_imports.jsonl"


def _allowlist_path() -> Path:
    return _config_dir() / "skill_import_allowlist.yaml"

# 抓取预算（DESIGN_M12 §4）
MAX_BYTES = 1_000_000
FETCH_TIMEOUT = 15

# 默认网络白名单：GitHub 系（DESIGN_M12 §6.1）
# api.github.com：仅供 discover_candidates() 只读列目录（GET /repos/{o}/{r}/git/trees），
# 用于仓库根无 SKILL.md 时给可安装候选；不改变「非白名单一律拒绝」的默认立场。
DEFAULT_ALLOWED_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "gist.githubusercontent.com",
    "api.github.com",
    "gitee.com",
)

# 允许的占位符（DESIGN_M12 §4.1：杜绝 LLM 生成可执行片段）
ALLOWED_PLACEHOLDERS = {"query", "topic", "task", "input", "term"}
# 受限 JSONPath：只允许 $.a.b[0] 形态，禁 `..`、禁函数调用、禁通配符
JSONPATH_RE = re.compile(r"^\$(\.[A-Za-z0-9_-]+|\[\d+\])+$")

SKILL_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
VALID_ROLES = ("researcher", "analyst", "writer")
VALID_TARGETS = ("skill", "tool", "datasource", "expert")

UA = {"User-Agent": "report-agent-team-skill-importer/1.0"}


class SkillImportError(Exception):
    """Importer 异常：任何失败都必须给出**明确原因**，不静默吞错。"""


# --------------------------------------------------------------------------
# 白名单
# --------------------------------------------------------------------------
def _load_allowlist() -> Tuple[str, ...]:
    """读域名白名单；文件不存在则用默认（仅 GitHub 系）。

    白名单文件缺失**不**放宽为「全部放行」—— 那样等于没有白名单（V5 要求非白名单域名拒绝）。
    """
    if not _allowlist_path().exists():
        return DEFAULT_ALLOWED_HOSTS
    try:
        import yaml

        data = yaml.safe_load(_allowlist_path().read_text(encoding="utf-8")) or {}
        hosts = data.get("allowed_hosts") or []
        return tuple(h.strip().lower() for h in hosts if isinstance(h, str)) or DEFAULT_ALLOWED_HOSTS
    except Exception as e:  # noqa: BLE001
        raise SkillImportError(f"域名白名单读取失败，拒绝放宽为全放行: {e}")


def _host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _check_host(url: str) -> None:
    host = _host_of(url)
    if not host:
        raise SkillImportError(f"URL 无法解析主机名: {url!r}")
    allowed = _load_allowlist()
    if host not in allowed and not any(host.endswith("." + a) for a in allowed):
        raise SkillImportError(
            f"域名 {host!r} 不在白名单内（当前允许：{', '.join(allowed)}）。"
            f"确认可信后把该域名加进 {_allowlist_path().name} 的 allowed_hosts 再重试。"
        )


# --------------------------------------------------------------------------
# FETCH
# --------------------------------------------------------------------------
def _http_get(url: str, timeout: int = FETCH_TIMEOUT) -> Tuple[bytes, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        raise SkillImportError(f"HTTP {e.code} 拉取失败: {url}")
    except Exception as e:  # noqa: BLE001
        raise SkillImportError(f"拉取失败 {url}: {type(e).__name__}: {e}")
    if len(raw) > MAX_BYTES:
        raise SkillImportError(f"内容超过 {MAX_BYTES} 字节上限，拒绝（防超大/恶意输入）")
    return raw, ctype


def _github_repo(url: str) -> Tuple[Optional[str], Optional[str]]:
    """GitHub URL → (owner, repo)；非 GitHub 形态 → (None, None)。

    bugfix：用户常贴带 .git 后缀的 clone 地址（如 https://github.com/o/r.git）。
    不剥离会把 raw 域名拼成 raw.githubusercontent.com/o/r.git/... —— 仓库真实名不含 .git，
    于是所有候选必然 404（boss 实测：agency-agents.git/HEAD/SKILL.md 全 404）。
    """
    parts = [x for x in urllib.parse.urlparse(url).path.split("/") if x]
    if len(parts) < 2:
        return None, None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


# 探路重试次数：容器出网存在**瞬时** TLS 抖动（实测 [SSL: UNEXPECTED_EOF_WHILE_READING]，
# 同一 URL 立刻重来一次往往就 200 —— 与样式 lookmalink 的第 15 坑同源）。
# 单次失败绝不可当作「网络不通」的判据，故这里短退避重试。
_DISCOVER_RETRIES = 3
_GITHUB_BRANCHES = ("HEAD", "main", "master")
# 目录/元信息类文件：不是可安装的技能，列出候选时跳过（按**前缀**匹配，
# 连 CONTRIBUTING_zh-CN.md、README_pt-BR.md 这类本地化副本一并滤掉）
_NON_SKILL_MD_PREFIX = ("readme", "contributing", "license", "licence",
                        "changelog", "security", "code_of_conduct", "authors",
                        "support", "conduct")


def _github_raw_candidates(url: str) -> List[str]:
    """GitHub 仓库/页面 URL → 可能的 raw SKILL.md 直链（按序尝试）。"""
    owner, repo = _github_repo(url)
    if not owner or not repo:
        return []
    p = urllib.parse.urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    # blob/tree/<branch>/<path...> → raw
    if len(parts) >= 5 and parts[2] in ("blob", "tree"):
        branch, rest = parts[3], "/".join(parts[4:])
        target = rest if rest.endswith(".md") else (rest.rstrip("/") + "/SKILL.md")
        return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{target}"]
    # 仓库根：按 HEAD/main/master 依次尝试
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/{b}/SKILL.md"
        for b in _GITHUB_BRANCHES
    ]


def discover_candidates(url: str, limit: int = 20) -> List[Dict[str, str]]:
    """仓库根没有 SKILL.md 时的兜底：列出该仓库内可安装的 .md 文件。

    针对「多文件 agent 仓库」形态（典型：msitarzewski/agency-agents —— 230+ 个
    单 agent .md，根上没有 SKILL.md）。此时让用户/LLM 盲猜路径不可靠，所以这里用
    GitHub Trees API（只读 GET /repos/{o}/{r}/git/trees/{branch}?recursive=1）
    拿到确定的文件清单，供出错信息或调用方挑选。

    返回 [{path, branch, raw_url, html_url}]，按 path 排序去重，最多 limit 条。
    失败/被限流/结果被截断一律返回 []——它是「提示」而非硬依赖，绝不允许把
    原始失败原因吞掉（V5）。
    """
    owner, repo = _github_repo(url)
    if not owner or not repo:
        return []
    found: List[Dict[str, str]] = []
    for br in _GITHUB_BRANCHES:
        api = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{br}?recursive=1"
        data = None
        last_err: Optional[str] = None
        for attempt in range(_DISCOVER_RETRIES):
            try:
                raw, _ctype = _http_get(api)
                data = json.loads(raw.decode("utf-8", errors="replace"))
                break
            except Exception as e:  # noqa: BLE001  抖动/限流/非 JSON：退避后再试
                last_err = f"{type(e).__name__}: {str(e)[:80]}"
                if attempt < _DISCOVER_RETRIES - 1:
                    time.sleep(0.4 * (attempt + 1))
        if not isinstance(data, dict):
            continue  # 三个等级都拿不到（含被限流/不可达）→ 视为无提示
        if data.get("truncated"):
            continue  # 清单不完整 → 不给一半的真相，直接当作没发现
        for ent in data.get("tree") or []:
            if not isinstance(ent, dict) or ent.get("type") != "blob":
                continue
            path = str(ent.get("path") or "")
            low = path.lower()
            if not low.endswith(".md"):
                continue
            base = low.split("/")[-1]
            if low.startswith(".github/") or base.startswith(_NON_SKILL_MD_PREFIX):
                continue
            found.append({
                "path": path,
                "branch": br,
                "raw_url": f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{path}",
                "html_url": f"https://github.com/{owner}/{repo}/blob/{br}/{path}",
            })
        if found:
            break
    out: List[Dict[str, str]] = []
    seen = set()
    for c in sorted(found, key=lambda x: x["path"]):
        if c["path"] in seen:
            continue
        seen.add(c["path"])
        out.append(c)
    return out[:limit]


def _github_file_targets(url: str) -> List[Tuple[str, str]]:
    """GitHub URL → 待取的 [(branch, path)] 候选。blob/tree 直链 → 唯一；(仓库根) → 逐分支试 SKILL.md。"""
    parts = [x for x in urllib.parse.urlparse(url).path.split("/") if x]
    if len(parts) >= 5 and parts[2] in ("blob", "tree"):
        return [(parts[3], "/".join(parts[4:]))]
    return [(b, "SKILL.md") for b in _GITHUB_BRANCHES]


def _github_contents(owner: str, repo: str, path: str, ref: str) -> Dict[str, Any]:
    """经 GitHub REST contents API 取单个文件正文（base64），失败抛 SkillImportError。

    存在的理由（2026-09-15 实测，勿以为是冗余通道）：受限网络/代理环境常常**只放行
    api.github.com**，对 raw.githubusercontent.com 的 CONNECT 一律 502 Bad Gateway
    （本沙箱:34177 「Tunnel connection failed: 502」，四次全败且稳定复现）。
    此时 raw 直链全废，而 contents API 可在 0.7s 内拿到同一文件的真实正文。
    两条路取的是同一个文件，内容一致，不涉及任何格式猜测。
    """
    api = (f"https://api.github.com/repos/{owner}/{repo}/contents/"
           + urllib.parse.quote(path) + "?ref=" + urllib.parse.quote(ref))
    raw, _ctype = _http_get(api)
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise SkillImportError(f"contents API 返回非 JSON: {e}")
    if isinstance(data, list):
        raise SkillImportError(f"{path} 是目录而非文件，请给出具体的 .md 文件路径")
    if data.get("encoding") == "base64" and data.get("content"):
        text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    elif isinstance(data.get("content"), str):
        text = data["content"]
    else:
        raise SkillImportError(
            f"contents API 未返回可用正文（message={str(data.get('message'))[:80]}）")
    return _pack(text, "raw_md", api)


def fetch_source(url: str) -> Dict[str, Any]:
    """按来源类型取正文（DESIGN_M12 §3.1）。返回 {text, source_type, final_url, sha256}。

    - 直链 .md / content-type markdown → 直接用
    - GitHub → 定位 SKILL.md 的 raw 直链
    - 其它网页 → HTML 正文提取
    """
    _check_host(url)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SkillImportError(f"只支持 http/https，收到 {parsed.scheme!r}")

    errors: List[str] = []
    host = _host_of(url)

    if host.endswith("github.com") and not host.startswith("raw."):
        cands = _github_raw_candidates(url)
        for cand in cands:
            try:
                raw, ctype = _http_get(cand)
                text = raw.decode("utf-8", errors="replace")
            except SkillImportError as e:
                errors.append(str(e))
                continue
            return _pack(text, "github", cand)
        # raw 全线失败 → 降级走 contents API（见 _github_contents 文档字符串）
        owner, repo = _github_repo(url)
        for br, path in _github_file_targets(url):
            if not owner or not repo:
                break
            try:
                return _github_contents(owner, repo, path, br)
            except SkillImportError as e:
                errors.append(f"contents API({br}/{path}): {e}")
                continue
        msg = ("在 GitHub 仓库里没定位到 SKILL.md（已尝试 " + "; ".join(cands)
               + f"）。原始错误：{' | '.join(errors) or '无'}")
        # 兜底：列仓库里的 .md 候选，给出可执行下一步（失败不得吞掉上面的原始错误）
        try:
            found = discover_candidates(url)
        except Exception:  # noqa: BLE001
            found = []
        if found:
            shown = found[:20]
            listing = "\n".join(f"  - {c['path']}  →  {c['html_url']}" for c in shown)
            msg += (
                f"\n\n该仓库根没有 SKILL.md，但仓库内发现 {len(found)} 个 .md 候选"
                f"（列出前 {len(shown)} 个）。这是「多文件 agent 仓库」的典型形态 —— "
                "挑一个想要的，把该文件的 URL 交给 install_skill 即可安装：\n" + listing
            )
        else:
            msg += "\n\n（未能列出仓库内 .md 候选：GitHub Trees API 不可用/受限/为空）"
        raise SkillImportError(msg)

    raw, ctype = _http_get(url)
    text = raw.decode("utf-8", errors="replace")
    if url.lower().endswith(".md") or "markdown" in ctype or "text/plain" in ctype:
        return _pack(text, "raw_md", url)
    if "<html" in text[:2000].lower() or "text/html" in ctype:
        extracted = extract_main_text(text)
        if not extracted:
            raise SkillImportError("网页正文提取为空，拒绝猜测（请改用直链 .md 或人工粘贴）")
        if len(extracted) > 100_000:  # DESIGN §3.1：超 100KB 截断
            extracted = extracted[:100_000]
        return _pack(extracted, "webpage", url)
    # 非 HTML 也非 md：当纯文本收下，但标记为 webpage 走 L1 审批
    return _pack(text, "webpage", url)


def _pack(text: str, source_type: str, final_url: str) -> Dict[str, Any]:
    return {
        "text": text,
        "source_type": source_type,
        "final_url": final_url,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


# --------------------------------------------------------------------------
# 网页正文提取（保守：去噪 + 质量校验）
# --------------------------------------------------------------------------
def extract_main_text(html_text: str) -> str:
    """剥离 script/style/nav/footer/header/aside/form，保留主内容文本。

    不做「智能识别正文算法」—— 页面结构千差万别，猜错代价高。
    策略：优先取 <article>/<main>，否则取 <body>；去标签后按空白折叠。
    质量不达标（过短/链接占比过高）返回空串 → 由调用方拒绝并提示人工粘贴。
    """
    s = html_text
    for tag in ("script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"):
        s = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", s, flags=re.S | re.I)
    m = re.search(r"<article\b[^>]*>(.*?)</article>", s, flags=re.S | re.I)
    if not m:
        m = re.search(r"<main\b[^>]*>(.*?)</main>", s, flags=re.S | re.I)
    if not m:
        m = re.search(r"<body\b[^>]*>(.*?)</body>", s, flags=re.S | re.I)
    body = m.group(1) if m else s

    # 块级标签 → 换行，其余标签直接删
    body = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    text = _html.unescape(body)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()

    if len(text) < 200:
        return ""
    link_chars = sum(len(x) for x in re.findall(r"https?://\S+", text))
    if link_chars > len(text) * 0.3:
        return ""  # 像导航页/目录页，不猜
    return text


# --------------------------------------------------------------------------
# PARSE：frontmatter + 正文
# --------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


# --------------------------------------------------------------------------
# CLASSIFY：L0 / L1 / L2（保守优先）
# --------------------------------------------------------------------------
# L2 = 即便本引擎「当前」不提供执行面、也明确携带执行意图的**声明**：可执行工具声明、
# 引用 scripts/ 目录、shebang、动态执行/求值调用、要求运行脚本。命中即拒（DESIGN §6.3）。
_L2_SIGNALS = (
    (r"allowed-tools\s*:", "声明 allowed-tools（可执行工具）"),
    (r"(?m)^\s*#!\s*/", "shebang（可执行脚本）"),
    (r"\b(subprocess|os\.system|popen)\s*\(", "动态执行调用"),
    (r"\b(eval|exec)\s*\(", "动态求值调用"),
    (r"scripts/\s*$", "引用 scripts/ 目录"),
    (r"\b(run|execute)\s+this\s+script\b", "要求执行脚本"),
)
# 「代码块 / 命令行」一类信号 —— 2026-09-15 实测修正：**从 L2 降到 L1**，勿再改回。
#
# 理由（安全事实，非妥协）：本引擎对技能 md 的用法**只有一种** —— tools/skills.py 的
# build_skill_context() 把文件全文原样读出、拼进对应 role 的 system prompt
# （【已启用技能：X】…【技能结束】），**从不执行其中的任何代码**。因此 md 里带
# ```bash / ```python 示例、或行首写成 `bash ./setup.sh`，对运行时是**惰性**的。
# 真正拦住风险的闸门是「安装前人工审批」，而不是拒收。
#
# 旧策略把它们一律判 L2 硬拒的实际后果：现实世界里几乎所有第三方 agent 定义
# （如 agency-agents 的单 agent .md，正文必定含代码示例）**一概装不进来** ——
# 这就是 boss 反馈的「功能还是缺失」。降至 L1 后：仍须人工确认才落盘，但能力不再被堵死。
_L1_CODE_HINT_SIGNALS = (
    (r"```(bash|sh|python|py|js|javascript|shell|powershell|ps1)",
     "含代码块（本引擎不执行外部代码，仅作提示词文本，须人工确认）"),
    (r"(?m)^\s*(python|python3|bash|sh|pwsh|powershell|node|npm|pip|pip3)\s+[\w\-./\\]",
     "行首命令式代码（本引擎不执行外部代码，仅作提示词文本，须人工确认）"),
)
# L1 = 不执行但需人来确认：外部端点声明、非结构化网页来源、专家包 manifest。
_L1_SIGNALS = (
    (r"https?://[^\s)\"']+", "含外部 URL"),
    (r"\b(openapi|swagger|request_template|response_map|endpoint)\b", "声明式外部调用"),
    (r"\b(plugin\.json|expertType)\b", "专家包 manifest"),
)


def classify(text: str, source_type: str) -> Tuple[str, List[str]]:
    """返回 (等级, 命中原因)。无法确定一律按更高等级（DESIGN_M12 §3.2）。

    判定顺序（保守优先）：真实执行意图 → L2；网页来源 / 外链 / 专家包 / 代码块 → L1；其余 L0。
    """
    reasons: List[str] = []
    for pat, why in _L2_SIGNALS:
        if re.search(pat, text, re.I):
            reasons.append(why)
    if reasons:
        return "L2", reasons
    if source_type == "webpage":
        reasons.append("来源为非结构化网页（正文提炼产物须人工确认）")
    for pat, why in _L1_SIGNALS:
        if re.search(pat, text, re.I) and why not in reasons:
            reasons.append(why)
    for pat, why in _L1_CODE_HINT_SIGNALS:
        if re.search(pat, text, re.I) and why not in reasons:
            reasons.append(why)
    if reasons:
        return "L1", reasons
    return "L0", ["纯指令文本，无脚本、无外部调用"]


# --------------------------------------------------------------------------
# ADAPT + VALIDATE：AssemblySpec 强 schema
# --------------------------------------------------------------------------
def validate_spec(spec: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    sid = spec.get("id")
    if not isinstance(sid, str) or not SKILL_ID_RE.match(sid or ""):
        errs.append(f"非法 id：{sid!r}（须 kebab-case 小写）")
    if spec.get("level") not in ("L0", "L1"):
        errs.append(f"level 须为 L0/L1，收到 {spec.get('level')!r}")
    if spec.get("target") not in VALID_TARGETS:
        errs.append(f"target 须为 {'/'.join(VALID_TARGETS)}，收到 {spec.get('target')!r}")
    roles = spec.get("target_roles") or []
    if not isinstance(roles, list) or not roles:
        errs.append("target_roles 不能为空")
    else:
        bad = [r for r in roles if r not in VALID_ROLES]
        if bad:
            errs.append(f"不支持的 target_roles：{bad}（可选：{list(VALID_ROLES)}）")
    if not (spec.get("name") or "").strip():
        errs.append("name 不能为空")

    payload = spec.get("payload") or {}
    if not isinstance(payload, dict):
        errs.append("payload 须为对象")
        return errs

    if spec.get("target") == "skill":
        if not (payload.get("markdown") or "").strip():
            errs.append("target=skill 时 payload.markdown 不能为空")
    elif spec.get("target") == "tool":
        ep = payload.get("endpoint")
        if not isinstance(ep, str) or not ep.startswith(("http://", "https://")):
            errs.append("target=tool 时 payload.endpoint 须为 http(s) URL")
        if (payload.get("method") or "GET").upper() not in ("GET", "POST"):
            errs.append("payload.method 仅支持 GET/POST")
        errs.extend(_validate_template(payload.get("request_template")))
        errs.extend(_validate_response_map(payload.get("response_map")))
    elif spec.get("target") == "expert":
        if not (payload.get("persona") or "").strip():
            errs.append("target=expert 时 payload.persona 不能为空")
    return errs


def _validate_template(tpl: Any) -> List[str]:
    if tpl is None:
        return []
    if not isinstance(tpl, dict):
        return ["request_template 须为对象"]
    errs = []
    for k, v in tpl.items():
        for ph in re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", str(v)):
            if ph not in ALLOWED_PLACEHOLDERS:
                errs.append(f"占位符 {{{{{ph}}}}} 不在白名单 {sorted(ALLOWED_PLACEHOLDERS)}")
    return errs


def _validate_response_map(rmap: Any) -> List[str]:
    if rmap is None:
        return []
    if not isinstance(rmap, dict):
        return ["response_map 须为对象"]
    errs = []
    for k, v in rmap.items():
        if not isinstance(v, str) or not JSONPATH_RE.match(v.strip()):
            errs.append(f"response_map[{k!r}] 不是受限 JSONPath（只允许 $.a.b[0]）")
    return errs


def build_spec(source: Dict[str, Any], level: str, reasons: List[str],
               *, sid: Optional[str] = None, name: Optional[str] = None,
               target_roles: Optional[List[str]] = None) -> Dict[str, Any]:
    """把抓取到的正文适配成本引擎的 AssemblySpec（DESIGN_M12 §4.1）。"""
    fm, body = parse_frontmatter(source["text"])
    auto_id = _norm_id(sid) if sid else _slugify(
        fm.get("name") or name or _first_heading(body) or "imported-skill")
    spec: Dict[str, Any] = {
        "id": auto_id,
        "level": level,
        "name": (fm.get("name") or name or _first_heading(body) or auto_id).strip(),
        "description": (fm.get("description") or _first_para(body))[:200],
        "target": "skill",
        "target_roles": target_roles or ["researcher"],
        "payload": {"markdown": source["text"].strip()},
        "provenance": {
            "source_url": source["final_url"],
            "source_type": source["source_type"],
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sha256": source["sha256"],
            "license": fm.get("license", ""),
        },
        "reasons": reasons,
    }
    return spec


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", s.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "imported-skill"


def _norm_id(raw: Optional[str]) -> str:
    """把任意来源 id 规范化为白名单内安全文件名（剥离 / . .. 等路径片段）。

    防路径遍历（DESIGN §6.2）：用户可控 id 必须经此后再拼文件路径。
    """
    return _slugify(raw or "") or "imported-skill"


def _first_heading(text: str) -> str:
    m = re.search(r"(?m)^#\s+(.+)$", text or "")
    return m.group(1).strip() if m else ""


def _first_para(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line
    return ""


# --------------------------------------------------------------------------
# 安装：备份 → 写文件 → 写注册表 → 回读校验
# --------------------------------------------------------------------------
def _load_yaml_rt(path: Path):
    try:
        from ruamel.yaml import YAML
    except ImportError as e:  # pragma: no cover
        raise SkillImportError(f"ruamel.yaml 未安装，拒绝降级写配置（缺失须 fail loud）: {e}")
    y = YAML()
    y.preserve_quotes = True
    data = y.load(path.read_text(encoding="utf-8")) if path.exists() else None
    return y, data


def _dump(y, data) -> str:
    import io

    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _audit(action: str, payload: Dict[str, Any]) -> None:
    _audit_dir().mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, **payload}
    with _import_log().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _backup() -> Optional[str]:
    if not _skills_yaml().exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = _backup_dir() / stamp
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "skills.yaml").write_text(_skills_yaml().read_text(encoding="utf-8"), encoding="utf-8")
    return str(dest.relative_to(BASE))


def _existing_index(data) -> Dict[str, Any]:
    out = {}
    for i, s in enumerate(data.get("skills") or []):
        if isinstance(s, dict) and s.get("id"):
            out[s["id"]] = i
    return out


def _idempotent(spec: Dict[str, Any]) -> bool:
    """同 (source_url, sha256) 已存在 → True（不重复落盘，V6）。"""
    if not _skills_yaml().exists():
        return False
    try:
        import yaml

        data = yaml.safe_load(_skills_yaml().read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return False
    url = (spec.get("provenance") or {}).get("source_url")
    sha = (spec.get("provenance") or {}).get("sha256")
    for s in data.get("skills") or []:
        prov = (s or {}).get("provenance") or {}
        if prov.get("source_url") == url and prov.get("sha256") == sha:
            return True
    return False


def install_spec(spec: Dict[str, Any], operator: str = "system") -> Dict[str, Any]:
    """落盘：先备份 → 写 md → 写注册表 → 回读校验。任一步失败即回滚。"""
    errs = validate_spec(spec)
    if errs:
        _audit("install_rejected", {"id": spec.get("id"), "errors": errs, "operator": operator})
        raise SkillImportError("schema 校验未通过，零落盘：" + "; ".join(errs))

    if _idempotent(spec):
        _audit("install_skipped_idempotent", {"id": spec["id"], "operator": operator})
        return {"status": "unchanged", "id": spec["id"], "message": "同来源同内容已存在，跳过"}

    sid = _norm_id(spec["id"])  # 防路径遍历：用户可控 id 先规范化再拼路径
    backup = _backup()
    md_path = _skills_dir() / f"{sid}.md"
    before = _skills_yaml().read_text(encoding="utf-8") if _skills_yaml().exists() else None
    md_before = md_path.read_text(encoding="utf-8") if md_path.exists() else None
    try:
        y, data = _load_yaml_rt(_skills_yaml())
        if data is None:
            from ruamel.yaml.comments import CommentedMap, CommentedSeq

            data = CommentedMap()
            data["skills"] = CommentedSeq()
        if data.get("skills") is None:
            from ruamel.yaml.comments import CommentedSeq

            data["skills"] = CommentedSeq()

        from ruamel.yaml.comments import CommentedMap

        entry = CommentedMap()
        entry["id"] = sid
        entry["name"] = spec["name"]
        entry["icon"] = ""
        entry["category"] = "导入技能"
        entry["description"] = spec.get("description", "")
        entry["target_roles"] = list(spec["target_roles"])
        entry["prompt_file"] = f"skills/{sid}.md"
        entry["enabled"] = True
        entry["installed"] = True
        entry["builtin"] = False
        entry["status"] = "active"
        entry["level"] = spec["level"]
        entry["provenance"] = spec.get("provenance", {})

        idx = _existing_index(data)
        if sid in idx:
            data["skills"][idx[sid]] = entry
        else:
            data["skills"].append(entry)

        _skills_dir().mkdir(parents=True, exist_ok=True)
        _atomic_write(md_path, spec["payload"]["markdown"])
        _atomic_write(_skills_yaml(), _dump(y, data))
    except Exception as e:  # noqa: BLE001
        if before is not None:
            _atomic_write(_skills_yaml(), before)
        if md_before is not None:
            _atomic_write(md_path, md_before)
        elif md_path.exists():
            try:
                md_path.unlink()
            except OSError:
                pass
        _audit("install_failed", {"id": sid, "error": f"{type(e).__name__}: {e}",
                                  "backup": backup, "operator": operator})
        raise SkillImportError(f"安装失败已回滚：{type(e).__name__}: {e}")

    verified = verify_installed(sid)
    _audit("install", {"id": sid, "level": spec["level"], "verified": verified,
                       "backup": backup, "operator": operator,
                       "source_url": (spec.get("provenance") or {}).get("source_url")})
    return {"status": "installed", "id": sid, "level": spec["level"],
            "verified": verified, "backup": backup}


def verify_installed(sid: str) -> bool:
    """V1 要求：不是「文件存在」就算数，必须能被 build_skill_context 真实读到。"""
    try:
        from tools.skills import build_skill_context, load_skills
    except Exception:  # noqa: BLE001
        return False
    specs = load_skills()
    if not any(s.get("id") == sid and s.get("enabled") for s in specs):
        return False
    roles = []
    for s in specs:
        if s.get("id") == sid:
            roles = [r.lower() for r in (s.get("target_roles") or [])]
    if not roles:
        return False
    return all(f"【已启用技能" in build_skill_context(r) for r in roles)


def rollback(sid: str, operator: str = "system") -> Dict[str, Any]:
    """V4：一键回滚 —— 从注册表移除 + 删除 md，恢复后 prompt 里不再出现。"""
    sid = _norm_id(sid)
    if not _skills_yaml().exists():
        raise SkillImportError("skills.yaml 不存在，无可回滚")
    backup = _backup()
    y, data = _load_yaml_rt(_skills_yaml())
    idx = _existing_index(data)
    if sid not in idx:
        raise SkillImportError(f"技能 {sid!r} 不在注册表中")
    removed = data["skills"].pop(idx[sid])
    _atomic_write(_skills_yaml(), _dump(y, data))
    md_path = _skills_dir() / f"{sid}.md"
    md_removed = False
    if md_path.exists():
        md_path.unlink()
        md_removed = True
    still = verify_installed(sid)
    _audit("rollback", {"id": sid, "md_removed": md_removed, "backup": backup,
                        "still_visible": still, "operator": operator})
    return {"status": "rolled_back", "id": sid, "md_removed": md_removed,
            "still_visible": still, "backup": backup, "removed": dict(removed)}


# --------------------------------------------------------------------------
# 提案（L1 闸门）
# --------------------------------------------------------------------------
def _proposal_path(pid: str) -> Path:
    return _proposal_dir() / f"{_norm_id(pid)}.json"  # 防路径遍历（DESIGN §6.2）


def save_proposal(spec: Dict[str, Any]) -> str:
    _proposal_dir().mkdir(parents=True, exist_ok=True)
    pid = _norm_id(spec.get("id") or "imported-skill")
    _proposal_path(pid).write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    _audit("proposal_created", {"id": pid, "level": spec.get("level"),
                                "source_url": (spec.get("provenance") or {}).get("source_url")})
    return pid


def list_proposals() -> List[Dict[str, Any]]:
    if not _proposal_dir().exists():
        return []
    out = []
    for p in sorted(_proposal_dir().glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def get_proposal(pid: str) -> Optional[Dict[str, Any]]:
    p = _proposal_path(pid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def accept_proposal(pid: str, operator: str = "system") -> Dict[str, Any]:
    spec = get_proposal(pid)
    if spec is None:
        raise SkillImportError(f"提案不存在：{pid!r}")
    res = install_spec(spec, operator=operator)
    try:
        _proposal_path(pid).unlink()
    except OSError:
        pass
    _audit("proposal_accepted", {"id": pid, "operator": operator, "result": res.get("status")})
    return res


def reject_proposal(pid: str, operator: str = "system") -> Dict[str, Any]:
    spec = get_proposal(pid)
    if spec is None:
        raise SkillImportError(f"提案不存在：{pid!r}")
    _proposal_path(pid).unlink()
    _audit("proposal_rejected", {"id": pid, "operator": operator})
    return {"status": "rejected", "id": pid}


# --------------------------------------------------------------------------
# 顶层入口
# --------------------------------------------------------------------------
def import_skill(url: str, operator: str = "system",
                 target_roles: Optional[List[str]] = None,
                 *, sid: Optional[str] = None, name: Optional[str] = None,
                 auto_install_l0: bool = True) -> Dict[str, Any]:
    """主流程。返回结构化结果，任何分支都给明确原因（V5）。

    L0 → 直接安装；L1 → 落提案等审批；L2 → 拒绝（零落盘）。
    """
    try:
        source = fetch_source(url)
    except SkillImportError as e:
        _audit("import_rejected", {"url": url, "reason": str(e), "operator": operator})
        return {"status": "rejected", "url": url, "reason": str(e)}

    level, reasons = classify(source["text"], source["source_type"])
    try:
        spec = build_spec(source, level, reasons, sid=sid, name=name,
                          target_roles=target_roles)
    except Exception as e:  # noqa: BLE001
        _audit("import_rejected", {"url": url, "reason": f"适配失败: {e}", "operator": operator})
        return {"status": "rejected", "url": url, "reason": f"适配失败: {e}"}

    if level == "L2":
        _audit("import_rejected", {"url": url, "level": "L2", "reasons": reasons,
                                   "operator": operator})
        return {"status": "rejected", "url": url, "level": "L2", "reason":
                "命中代码型信号，首批拒绝执行外部代码：" + "; ".join(reasons)}

    if level == "L1" or not auto_install_l0:
        pid = save_proposal(spec)
        return {"status": "pending_approval", "id": pid, "level": level,
                "reasons": reasons, "spec": spec}

    try:
        res = install_spec(spec, operator=operator)
    except SkillImportError as e:
        return {"status": "rejected", "url": url, "level": level, "reason": str(e)}
    res["level"] = level
    res["reasons"] = reasons
    return res
