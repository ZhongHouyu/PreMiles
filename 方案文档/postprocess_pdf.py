"""PDF 后处理：加页脚页码 + 书签（Edge 导出的 PDF 两者都没有）。

做法
----
1. 用 pypdf 逐页抽取文本，把 Markdown 里的一级/二级标题映射到 PDF 页码；
2. 用 reportlab 生成"页脚叠加层"（分隔线 + 文档名 + 第 N / M 页），中文用微软雅黑；
3. 逐页合并叠加层并写出新 PDF，同时写入书签（章 = 一级，节 = 二级）。

用法
----
    python postprocess_pdf.py <输入.pdf> [输出.pdf]
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import build_doc as bd

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\simsun.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", None),
]
FOOTER_TITLE = "整车能耗归因与降耗方向 · 方案文档合集"


def register_font() -> str:
    for path, idx in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        name = "CJK" + Path(path).stem
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont(name, path))
            else:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
            return name
        except Exception as exc:              # noqa: BLE001
            print(f"  [warn] 字体 {path} 注册失败: {exc}")
    raise SystemExit("没有可用的中文字体")


def headings() -> list[tuple[int, str]]:
    """从 Markdown 收集 (层级, 标题) —— 层级 1 = 章，2 = 节。"""
    out: list[tuple[int, str]] = []
    for idx, (filename, _short) in enumerate(bd.DOCS, start=1):
        lines = (bd.HERE / filename).read_text(encoding="utf-8").split("\n")
        title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else filename
        out.append((1, f"第 {idx} 部分 · {title}"))
        for ln in lines:
            m = re.match(r"^##\s+(.*?)\s*#*\s*$", ln)
            if m:
                out.append((2, m.group(1).strip()))
    return out


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def process(src: Path, dst: Path, skip: int = 0, start: int = 1,
            heads: list[tuple[int, str]] | None = None) -> Path:
    """给 PDF 加页脚页码与书签，返回输出路径。

    skip  : 前 skip 页不加页脚、不参与页码计数（如国赛的承诺书页与编号专用页）
    start : 第一个参与编码的页面显示的页码
    """
    font = register_font()
    reader = PdfReader(str(src))
    total = len(reader.pages)
    numbered = total - skip
    print(f"输入: {src.name}  {total} 页（其中 {skip} 页不编码）")

    page_text = [norm(p.extract_text() or "") for p in reader.pages]
    sample = page_text[3][:60] if len(page_text) > 3 else ""
    print(f"  文本抽取抽样: {sample!r}")
    if not sample:
        print("  [warn] 文本抽取为空 —— 书签将只能按章粗略定位")

    # 标题 -> 页码
    marks: list[tuple[int, str, int]] = []
    cursor = 0
    head_list = heads if heads is not None else headings()
    for level, title in head_list:
        key = norm(title)
        probe = key[:14]
        found = None
        for i in range(cursor, total):
            if probe and probe in page_text[i]:
                found = i
                break
        if found is None:                      # 回退：全文档再找一次
            for i in range(total):
                if probe and probe in page_text[i]:
                    found = i
                    break
        if found is not None:
            marks.append((level, title, found))
            cursor = found
    print(f"  定位到 {len(marks)} / {len(head_list)} 个标题")

    # 页脚叠加
    w = float(reader.pages[0].mediabox.width)
    h = float(reader.pages[0].mediabox.height)

    def overlay(index: int):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        if index >= skip:                      # 前 skip 页不加页脚
            c.setFont(font, 9)
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.drawCentredString(w / 2, 26, f"{index - skip + start}")
        c.showPage()          # 必须显式收页，否则生成的是空 PDF
        c.save()
        buf.seek(0)
        return PdfReader(buf).pages[0]

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        page.merge_page(overlay(i))
        writer.add_page(page)

    # 书签（跳过不编码的前置页）
    parents: dict[int, object] = {}
    for level, title, page in marks:
        if page < skip:
            continue
        parent = parents.get(level - 1) if level > 1 else None
        item = writer.add_outline_item(title, page, parent=parent)
        parents[level] = item
        for lv in list(parents):
            if lv > level:
                parents.pop(lv)

    writer.add_metadata({
        "/Title": "整车能耗归因与降耗方向 · 方案文档合集",
        "/Subject": "创科升 AI 应用方案大赛 · 面向整车开发商与 VCU 开发厂家",
        "/Creator": "build_doc.py + Edge headless print + postprocess_pdf.py",
    })
    for page in writer.pages:                 # 合并叠加层后压缩内容流
        try:
            page.compress_content_streams()
        except Exception:                     # noqa: BLE001
            pass
    writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    try:
        with dst.open("wb") as fh:
            writer.write(fh)
    except PermissionError:
        # 目标 PDF 正在阅读器（WPS/Acrobat）里打开时无法覆盖：另存为修正版，避免白跑一遍
        alt = dst.with_name(dst.stem + "·修正版" + dst.suffix)
        with alt.open("wb") as fh:
            writer.write(fh)
        print("[warn] 目标 PDF 被占用（可能正在阅读器中打开），已另存为 " + alt.name)
        dst = alt
    print(f"[OK] {dst}  ({dst.stat().st_size/1024:.0f} KB)")
    return dst


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else bd.OUT / bd.PDF_NAME
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + "_带页码书签.pdf")
    process(src, dst)


if __name__ == "__main__":
    main()
