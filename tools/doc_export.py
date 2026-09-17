# tools/doc_export.py · Markdown 导出与引用章节校验（M4，Writer 工具）
#
# 职责：
#   1. 由 draft_segments 确定性拼装正文（章节顺序由段序决定，不靠 LLM 自由发挥）；
#   2. 生成完整「引用 / 来源」章节（从 retrieval_records 导出，保证可回溯）；
#   3. 校验 LLM 产出的 report_markdown 是否已含引用章节，缺失则补齐；
#   4. 可选落盘到 export_dir。
#
# 之所以由代码而非 LLM 生成引用章节：GateC 的"引用完整"是硬校验项，
# 交给 LLM 写有漏写风险；确定性拼装可保证 100% 覆盖。

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Optional

from . import ToolError

_CITATION_HEADING = "## 引用 / 来源"
_HEADING_RE = re.compile(r"^\s*##\s*(引用|参考|来源)", re.MULTILINE)


def build_citation_section(retrieval_records: list[dict]) -> str:
    lines = [_CITATION_HEADING, ""]
    for r in retrieval_records:
        cred = r.get("credibility", "unknown")
        title = (r.get("title") or r.get("url") or "").strip()
        lines.append(f"- [{r.get('id')}]({r.get('url')}) — {title}（可信度: {cred}）")
    return "\n".join(lines)


def build_body(draft_segments: list[dict]) -> str:
    parts = []
    for s in draft_segments:
        section = (s.get("section") or "").strip() or s.get("id", "")
        content = (s.get("content") or "").strip()
        parts.append(f"## {section}\n\n{content}\n")
    return "\n".join(parts)


def assemble_report(title: str, draft_segments: list[dict], retrieval_records: list[dict]) -> str:
    head = f"# {title}\n"
    body = build_body(draft_segments)
    cite = build_citation_section(retrieval_records)
    return f"{head}\n{body}\n{cite}\n"


def _citation_incomplete(retrieval_records: list[dict], section: str) -> list[str]:
    """判定引用章节是否真正完整：每条记录须同时出现 id 与 url（仅裸 id 不算完整）。

    早期实现只查 id 文本是否出现，导致 LLM 输出的裸 id 列表（如 '- rec-6'）被误判为
    已完整而不重建 —— 最终引用章节缺 url/标题，无法点击回溯。现要求 url 也须出现。
    """
    missing = []
    for r in retrieval_records:
        rid = r.get("id")
        if not rid:
            continue
        if rid not in section:
            missing.append(rid)
        elif r.get("url") and r.get("url") not in section:
            missing.append(f"{rid}(缺url)")
    return missing


class DocExportTool:
    def __init__(self, enabled: bool = True, export_dir: Optional[Path] = None):
        self.enabled = enabled
        self.export_dir = Path(export_dir) if export_dir else None

    def ensure_citation_section(self, report_markdown: str, retrieval_records: list[dict]):
        """确保终稿的引用章节**真实对应** retrieval_records，否则确定性重建。

        返回 (markdown, regenerated: bool, tool_status_entry)

        仅检查"有没有引用章节标题"是不够的：LLM 可能写出带**编造 url** 的引用章节
        （如硬编码 example.com），形式上齐全但内容全假。故须同时校验：
          ① 所有 rec id 与其 url 均出现；② 章节内不含 retrieval_records 之外的 url。
        任一不满足即整体重建，从构造上保证引用可回溯。
        """
        if not self.enabled:
            return report_markdown, False, {"agent": "Writer", "tool": "doc_export",
                                            "ok": False, "error": "doc_export 已禁用"}
        md = report_markdown or ""
        m = _HEADING_RE.search(md)
        if m:
            section = md[m.start():]
            rec_urls = {r.get("url") for r in retrieval_records if r.get("url")}
            missing = _citation_incomplete(retrieval_records, section)
            foreign = set(re.findall(r"\((https?://[^)\s]+)\)", section)) - rec_urls
            if not missing and not foreign:
                return md, False, {"agent": "Writer", "tool": "doc_export", "ok": True}
        body = md[:m.start()].rstrip() if m else md.rstrip()
        return body + "\n\n" + build_citation_section(retrieval_records) + "\n", True, \
            {"agent": "Writer", "tool": "doc_export", "ok": True}

    def export(self, markdown: str, topic: str = "report") -> Optional[str]:
        """落盘到 export_dir，返回文件路径；未配置目录则返回 None。"""
        if not self.enabled or self.export_dir is None:
            return None
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", topic)[:40] or "report"
            path = self.export_dir / f"{safe}-{stamp}.md"
            path.write_text(markdown, encoding="utf-8")
            return str(path)
        except OSError as e:
            raise ToolError(f"导出失败: {e}")
