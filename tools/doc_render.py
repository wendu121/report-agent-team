# tools/doc_render.py · 研报输出渲染（docx / pptx / pdf）
#
# 设计依据：DESIGN_OUTPUT_RENDERING.md v1.0（服务端按需渲染）
# 职责边界：
#   - SoT 仍是 report_markdown（Markdown）；本模块只做**派生视图**，不回写、不改引擎。
#   - 输入是自家管线确定性生成的受控 md（`# 标题` + `## 章节` + `## 引用 / 来源`），
#     解析只覆盖该子集；未知行一律降级为段落，宁简不崩。
#   - 三个渲染器各自 import-guard：缺库抛 RendererUnavailable（API 层转 503），
#     绝不静默降级成空文件（诚实边界）。

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Optional

from . import ToolError

__all__ = [
    "RendererUnavailable", "Block", "parse_markdown",
    "render_docx", "render_pptx", "render_pdf",
]

# [text](url) —— 报告引用章节的既有形态（tools/doc_export.py:30）
# URL 允许一层成对括号（维基百科等 `.../Foo_(bar)` 直链），否则会被截断成 `.../Foo_(bar` 。
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^()\s]+(?:\([^()\s]*\)[^()\s]*)*)\)")
_CITE_HEADING_RE = re.compile(r"引用|来源|参考", re.I)


class RendererUnavailable(ToolError):
    """渲染库缺失（容器内未安装/未重建镜像）。绝不静默降级成空文件。"""


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------
@dataclass
class Block:
    """受控 md 的结构块。text/items 内保留 [text](url) 原样，由渲染器拆链接。"""
    kind: str                      # heading | paragraph | bullets | table
    level: int = 0                 # heading 层级（1..6）
    text: str = ""                 # heading / paragraph 文本
    items: list = field(default_factory=list)   # bullets：每条一个字符串
    rows: list = field(default_factory=list)    # table：每行一个字符串列表


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and set(s.replace("|", "").replace(":", "").replace("-", "").strip()) == set()


def _split_row(line: str) -> list:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_markdown(md: str) -> list:
    """把 report_markdown 解析成结构块列表。

    覆盖：#/##/### 标题、段落、`-`/`*` 要点组、`|` 管道表。
    未知行并入当前段落（宁简不崩）。
    """
    blocks: list = []
    if not md:
        return blocks

    buf: list = []          # 待落段落缓冲
    bullets: Optional[Block] = None
    table: Optional[Block] = None

    def flush_paragraph():
        nonlocal buf
        if buf:
            blocks.append(Block(kind="paragraph", text="\n".join(buf).strip()))
            buf = []

    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.strip()

        if not s:
            flush_paragraph()
            bullets = None
            table = None
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_paragraph()
            bullets = None
            table = None
            blocks.append(Block(kind="heading", level=len(m.group(1)), text=m.group(2).strip()))
            continue

        if re.match(r"^[-*]\s+", s):
            flush_paragraph()
            table = None
            if bullets is None:
                bullets = Block(kind="bullets")
                blocks.append(bullets)
            bullets.items.append(re.sub(r"^[-*]\s+", "", s))
            continue

        if s.startswith("|"):
            flush_paragraph()
            bullets = None
            if _is_table_sep(s):
                continue
            if table is None:
                table = Block(kind="table")
                blocks.append(table)
            table.rows.append(_split_row(s))
            continue

        # 未知行：并入段落（保持顺序，不抛错）
        bullets = None
        table = None
        buf.append(s)

    flush_paragraph()
    return blocks


def _plain(text: str) -> str:
    """去掉链接语法，只留可见文本（pptx 等不支持富链的场景）。"""
    return _LINK_RE.sub(r"\1", text or "").strip()


def _segments(text: str) -> list:
    """把文本切成 [(kind, payload)]：('text', s) 或 ('link', (显示文本, url))。"""
    out = []
    pos = 0
    for m in _LINK_RE.finditer(text or ""):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("link", (m.group(1), m.group(2))))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("text", text[pos:]))
    return out


# ---------------------------------------------------------------------------
# DOCX（python-docx，纯 Python）
# ---------------------------------------------------------------------------
def render_docx(blocks: list, meta: dict) -> bytes:
    try:
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
    except ImportError as e:  # 缺库即响亮失败，不静默返空
        raise RendererUnavailable(f"docx 渲染不可用（缺 python-docx：{e}）")

    def add_hyperlink(paragraph, url: str, text: str):
        r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        rPr.append(color)
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        rPr.append(underline)
        run.append(rPr)
        t = OxmlElement("w:t")
        t.text = text
        run.append(t)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)

    def add_rich(paragraph, text: str):
        for kind, payload in _segments(text):
            if kind == "text":
                if payload:
                    paragraph.add_run(payload)
            else:
                add_hyperlink(paragraph, payload[1], payload[0])

    doc = Document()
    doc.add_heading(meta.get("title") or "研报", level=0)
    sub = meta.get("topic")
    if sub:
        doc.add_paragraph(f"主题：{sub}　生成时间：{meta.get('generated_at', '')}".rstrip())

    for b in blocks:
        if b.kind == "heading":
            if b.level <= 1 and b.text == (meta.get("title") or ""):
                continue  # 与文档标题重复，跳过
            doc.add_heading(_plain(b.text), level=max(1, min(b.level, 9)))
        elif b.kind == "paragraph":
            p = doc.add_paragraph()
            add_rich(p, b.text)
        elif b.kind == "bullets":
            for item in b.items:
                p = doc.add_paragraph(style="List Bullet")
                add_rich(p, item)
        elif b.kind == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            tb = doc.add_table(rows=0, cols=cols)
            for r in b.rows:
                cells = tb.add_row().cells
                for i in range(cols):
                    cells[i].text = _plain(r[i]) if i < len(r) else ""

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PPTX（python-pptx，纯 Python）
# ---------------------------------------------------------------------------
def _sentences(text: str) -> list:
    """段落按句切成要点（中文句号/问号/叹号/分号/换行）。"""
    parts = re.split(r"(?<=[。！？；!?;])\s*|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def render_pptx(blocks: list, meta: dict) -> bytes:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as e:
        raise RendererUnavailable(f"pptx 渲染不可用（缺 python-pptx：{e}）")

    prs = Presentation()

    # 1) 封面
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = meta.get("title") or "研报"
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = f"{meta.get('topic', '')}\n{meta.get('generated_at', '')}".strip()

    # 2) 按章节成页
    sections = []          # [{title, bullets:[], tables:[], is_cite:bool}]
    cur = None
    for b in blocks:
        if b.kind == "heading":
            if b.level <= 1 and not sections and b.text == (meta.get("title") or ""):
                continue  # 文档标题已在封面
            cur = {"title": _plain(b.text), "bullets": [], "tables": [],
                   "is_cite": bool(_CITE_HEADING_RE.search(b.text))}
            sections.append(cur)
            continue
        if cur is None:
            cur = {"title": meta.get("title") or "研报", "bullets": [], "tables": [], "is_cite": False}
            sections.append(cur)
        if b.kind == "bullets":
            cur["bullets"].extend(_plain(i) for i in b.items)
        elif b.kind == "paragraph":
            cur["bullets"].extend(_sentences(b.text))
        elif b.kind == "table" and b.rows:
            cur["tables"].append(b.rows)

    def add_bullet_slide(title: str, items: list):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.word_wrap = True
        for i, it in enumerate(items):
            p = body.paragraphs[0] if i == 0 else body.add_paragraph()
            p.text = it
            p.level = 0

    def add_table_slide(title: str, rows: list):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = title
        cols = max(len(r) for r in rows)
        shape = slide.shapes.add_table(len(rows), cols, Inches(0.6), Inches(1.6),
                                       Inches(8.8), Inches(0.4 * len(rows)))
        tb = shape.table
        for ri, r in enumerate(rows):
            for ci in range(cols):
                cell = tb.cell(ri, ci)
                cell.text = _plain(r[ci]) if ci < len(r) else ""
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(12)

    for sec in sections:
        cap = 10 if sec["is_cite"] else 6
        items = sec["bullets"]
        title = sec["title"]
        if not items:
            # 无要点但有表的章节：只出表页
            for rows in sec["tables"]:
                add_table_slide(title, rows)
            continue
        for start in range(0, len(items), cap):
            chunk = items[start:start + cap]
            add_bullet_slide(title if start == 0 else f"{title}（续）", chunk)
        for rows in sec["tables"]:
            add_table_slide(f"{title}（表）", rows)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF（reportlab，纯 Python）
# ---------------------------------------------------------------------------
def _xml_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_markup(text: str) -> str:
    """转 reportlab Paragraph 的富文本：转义 + [t](url) → <a href>。"""
    out = []
    for kind, payload in _segments(text):
        if kind == "text":
            out.append(_xml_escape(payload))
        else:
            label, url = payload
            out.append(f'<a href="{_xml_escape(url)}" color="blue"><u>{_xml_escape(label)}</u></a>')
    return "".join(out)


def render_pdf(blocks: list, meta: dict) -> bytes:
    try:
        # 必须先导入顶层包：只 from reportlab.lib... 会命中子模块缓存，
        # 使缺库场景捕获不到 ImportError（import-guard 形同虚设）。
        import reportlab  # noqa: F401
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (ListFlowable, ListItem, Paragraph,
                                        SimpleDocTemplate, Spacer, Table, TableStyle)
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError as e:
        raise RendererUnavailable(f"pdf 渲染不可用（缺 reportlab：{e}）")

    # 中文关键是字体：默认 Helvetica 不含 CJK → 全黑块。
    # UnicodeCIDFont('STSong-Light') 是 reportlab 内置 CID 字体，无需任何系统字体文件。
    cjk = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(cjk))
    except Exception as e:  # 字体注册失败要响亮，不能默默出黑块
        raise RendererUnavailable(f"pdf 中文字体注册失败：{e}")

    ss = getSampleStyleSheet()
    st_title = ParagraphStyle("CJKTitle", parent=ss["Title"], fontName=cjk, fontSize=20, leading=26)
    st_h = ParagraphStyle("CJKH", parent=ss["Heading2"], fontName=cjk, fontSize=14, leading=19,
                          spaceBefore=10, spaceAfter=5)
    st_p = ParagraphStyle("CJKP", parent=ss["BodyText"], fontName=cjk, fontSize=10.5, leading=15)

    story = [Paragraph(_xml_escape(meta.get("title") or "研报"), st_title), Spacer(1, 4 * mm)]
    if meta.get("topic"):
        story.append(Paragraph(_xml_escape(f"主题：{meta['topic']}　{meta.get('generated_at', '')}"), st_p))
        story.append(Spacer(1, 3 * mm))

    for b in blocks:
        if b.kind == "heading":
            if b.level <= 1 and b.text == (meta.get("title") or ""):
                continue
            story.append(Paragraph(_xml_escape(_plain(b.text)), st_h))
        elif b.kind == "paragraph":
            story.append(Paragraph(_pdf_markup(b.text), st_p))
        elif b.kind == "bullets":
            story.append(ListFlowable(
                [ListItem(Paragraph(_pdf_markup(i), st_p)) for i in b.items],
                bulletType="bullet", start="•", leftIndent=12))
            story.append(Spacer(1, 2 * mm))
        elif b.kind == "table" and b.rows:
            data = [[Paragraph(_xml_escape(_plain(c)), st_p) for c in r] for r in b.rows]
            tb = Table(data, hAlign="LEFT")
            tb.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, "#999999"),
                ("BACKGROUND", (0, 0), (-1, 0), "#F2F2F2"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(tb)
            story.append(Spacer(1, 3 * mm))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, title=meta.get("title") or "研报",
                      leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    return buf.getvalue()
