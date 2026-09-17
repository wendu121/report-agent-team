"""输出渲染真实产物冒烟（容器内运行）。

用途：证明 tools/doc_render.py 在真实运行环境（python:3.13-slim + python-docx /
python-pptx / reportlab）下，能把「引擎真实产出的中文研报 markdown」渲染成
docx / pptx / pdf，且 PDF 走的是 STSong-Light CID 字体（否则中文全是黑块）。

用法（容器内）：
    docker exec -w /app -i report-api python - < scripts/smoke_render_real.py
"""

import glob
import os

from tools.doc_render import parse_markdown, render_docx, render_pdf, render_pptx

files = sorted(glob.glob("/app/outputs/*.md"))
if not files:
    raise SystemExit("FAIL: /app/outputs 下没有 .md 真实产物，无法冒烟")

# 取最大的一份，内容最全（章节/表格/引用都更可能有）
src = max(files, key=os.path.getsize)
md = open(src, encoding="utf-8").read()
blocks = parse_markdown(md)
meta = {
    "title": os.path.basename(src)[:-3],
    "topic": "真实产物冒烟",
    "generated_at": "2026-09-17",
}

print(f"source      : {src}")
print(f"md bytes    : {len(md)}")
print(f"blocks      : {len(blocks)}")
kinds = {}
for b in blocks:
    kinds[b.kind] = kinds.get(b.kind, 0) + 1
print(f"block kinds : {kinds}")

ok = True
for ext, fn, magic in (
    ("docx", render_docx, b"PK"),
    ("pptx", render_pptx, b"PK"),
    ("pdf", render_pdf, b"%PDF"),
):
    data = fn(blocks, meta)
    head_ok = data[:4].startswith(magic) if ext == "pdf" else data[:2] == magic
    print(f"{ext:<11} : {len(data):>9} bytes  magic_ok={head_ok}")
    ok = ok and head_ok and len(data) > 1024
    if ext == "pdf":
        # 中文关键：字体必须是 CID 字体 STSong-Light，否则正文黑块
        has_font = b"STSong" in data
        print(f"{'':<11}   STSong-Light registered in pdf = {has_font}")
        ok = ok and has_font
    out = f"/tmp/smoke_real.{ext}"
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"{'':<11}   -> {out}")

print("RESULT:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
