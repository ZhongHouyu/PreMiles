"""把方案文档的 Markdown 合集成一份可打印的 HTML，再用 Edge 无头模式导出 PDF。

为什么不用 pandoc / LaTeX
-------------------------
本机没有 pandoc、没有 TeX、也没有 Word，但有 Microsoft Edge。
因此链路是：Markdown --(本脚本)--> HTML(内嵌 CSS/图片) --(Edge --headless --print-to-pdf)--> PDF。

排版要点
--------
* A4、页边距 20mm，正文 宋体 + Times New Roman，标题 微软雅黑；
  公式里的数学斜体字形（𝐸、𝑣、𝜌…）由 Cambria Math 提供，浏览器自动按字符回退。
* 每章从新页开始；表格、图片、引用块不跨页断开。
* 图片以 base64 data URI 内嵌，避免 file:// 子资源被拦。
* 生成封面页 + 目录页。

用法
----
    python build_doc.py html      # 只生成 HTML（便于用 Edge 截图检查）
    python build_doc.py pdf       # 生成 HTML 并用 Edge 导出 PDF
"""

from __future__ import annotations

import base64
import html as html_mod
import mimetypes
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # …/方案文档
OUT = HERE / "out"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

DOCS = [
    ("01_AI应用方案_整车能耗归因与降耗方向.md", "主方案"),
    ("02_PPT大纲与现场演示脚本.md", "路演 PPT 大纲与演示脚本"),
    ("03_一页纸速览.md", "一页纸速览"),
    ("04_独立复现与技术审计报告.md", "独立复现与技术审计报告"),
    ("05_面向整车厂与VCU的降耗方向专项设计.md", "面向整车厂与 VCU 的降耗方向专项设计"),
    ("06_算法模型_数学建模论文格式.md", "算法模型（数学建模论文格式）"),
]
PDF_NAME = "整车能耗归因与降耗方向_方案文档合集.pdf"

# ----------------------------------------------------------------- CSS
CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Times New Roman", Cambria, SimSun, "Microsoft YaHei", "Cambria Math", serif;
  font-size: 10.5pt; line-height: 1.75; color: #1a1a1a; margin: 0;
}
h1, h2, h3, h4, h5, h6 {
  font-family: "Microsoft YaHei", "Segoe UI", SimHei, sans-serif;
  line-height: 1.4; page-break-after: avoid; break-after: avoid;
}
h1 { font-size: 19pt; margin: 0 0 10pt; padding-bottom: 6pt; border-bottom: 2px solid #0E7C86; color: #0E7C86; }
h2 { font-size: 14.5pt; margin: 16pt 0 7pt; color: #11707A; }
h3 { font-size: 12.5pt; margin: 13pt 0 6pt; color: #1B6E8C; }
h4 { font-size: 11pt; margin: 11pt 0 5pt; color: #333; }
h5, h6 { font-size: 10.5pt; margin: 9pt 0 4pt; color: #444; }
p { margin: 0 0 7pt; text-align: justify; }
ul, ol { margin: 5pt 0 8pt; padding-left: 20pt; }
li { margin: 2pt 0; }
a { color: #11707A; text-decoration: none; }
code {
  font-family: Consolas, "Courier New", monospace; font-size: 9.5pt;
  background: #f2f4f5; padding: 0.5pt 3pt; border-radius: 3px;
}
pre {
  font-family: Consolas, "Courier New", monospace; font-size: 8.6pt; line-height: 1.5;
  background: #f6f8f9; border: 0.5pt solid #dfe4e6; border-radius: 4px;
  padding: 7pt 9pt; margin: 7pt 0; white-space: pre-wrap; word-break: break-word;
  page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8.6pt; }
blockquote {
  margin: 7pt 0; padding: 4pt 0 4pt 10pt; border-left: 2.5pt solid #b9c6c9;
  color: #444; background: #fafbfb; page-break-inside: avoid;
}
blockquote p:last-child { margin-bottom: 0; }
table {
  border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.2pt;
  page-break-inside: avoid;
}
th, td { border: 0.5pt solid #cfd6d8; padding: 3.5pt 5pt; vertical-align: top; text-align: left; }
th { background: #eef3f4; font-family: "Microsoft YaHei", sans-serif; font-weight: 600; }
tr:nth-child(even) td { background: #fbfcfc; }
img { max-width: 100%; height: auto; display: block; margin: 8pt auto; page-break-inside: avoid; }
hr { border: none; border-top: 0.5pt solid #d8dee0; margin: 12pt 0; }
strong { font-weight: 700; }
/* 封面 */
.cover { height: 247mm; display: flex; flex-direction: column; justify-content: center;
         page-break-after: always; text-align: center; }
.cover .kicker { font-family: "Microsoft YaHei", sans-serif; font-size: 11pt; color: #11707A; letter-spacing: 2pt; }
.cover h1 { border: none; font-size: 27pt; margin: 12pt 0 6pt; color: #0E7C86; }
.cover .sub { font-size: 13pt; color: #444; margin-bottom: 26pt; }
.cover .meta { font-size: 10pt; color: #555; line-height: 2; }
.cover .rule { width: 46mm; height: 2.5pt; background: #E8A33D; margin: 16pt auto; }
.cover .note { margin-top: 30pt; font-size: 9pt; color: #777; text-align: left;
               border: 0.5pt solid #dfe4e6; border-radius: 4px; padding: 9pt 12pt; background: #fafbfb; }
/* 目录 */
.toc { page-break-after: always; }
.toc h1 { font-size: 17pt; }
.toc ol { list-style: none; padding-left: 0; }
.toc li { margin: 3pt 0; font-size: 10pt; }
.toc .lvl2 { padding-left: 16pt; color: #555; font-size: 9.4pt; }
.chapter { page-break-before: always; }
.chapter-head { font-family: "Microsoft YaHei", sans-serif; font-size: 9.5pt;
                color: #E8A33D; letter-spacing: 1pt; margin-bottom: 4pt; }
"""

# ----------------------------------------------------------------- inline
def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def inline(text: str) -> str:
    """行内元素：代码、加粗、斜体、删除线、图片、链接、<br>。"""
    placeholders: list[str] = []

    def stash(html: str) -> str:
        placeholders.append(html)
        return f"\x00{len(placeholders) - 1}\x00"

    # 1) 先摘出行内代码（内部不再解析）
    def code_repl(m: re.Match) -> str:
        return stash(f"<code>{esc(m.group(1))}</code>")

    text = re.sub(r"`([^`\n]+)`", code_repl, text)
    # 2) 图片与链接（src/href 里的 & 要转义）
    def img_repl(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        return stash(f'<img alt="{esc(alt)}" src="{resolve_image(src)}">')

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", img_repl, text)

    def link_repl(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        return stash(f'<a href="{esc(url)}">{inline(label)}</a>')

    text = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", link_repl, text)
    # 3) 转义其余文本
    text = esc(text)
    # 4) 行内标记
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~\n]+?)~~", r"<del>\1</del>", text)
    text = re.sub(r"&lt;br\s*/?&gt;", "<br>", text)
    # 5) 还原占位
    def restore(m: re.Match) -> str:
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, text)


_IMAGE_CACHE: dict[str, str] = {}


def resolve_image(src: str) -> str:
    """把文档内的相对图片路径换成 base64 data URI（避免 file:// 子资源被拦）。"""
    if src.startswith(("http://", "https://", "data:")):
        return src
    if src in _IMAGE_CACHE:
        return _IMAGE_CACHE[src]
    path = (HERE / src).resolve()
    if not path.exists():
        print(f"  [warn] 图片不存在: {src}")
        _IMAGE_CACHE[src] = ""
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    uri = f"data:{mime};base64,{data}"
    _IMAGE_CACHE[src] = uri
    return uri


# ----------------------------------------------------------------- blocks
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def aligns_of(line: str) -> list[str]:
    out = []
    for c in split_row(line):
        left, right = c.startswith(":"), c.endswith(":")
        out.append("center" if (left and right) else "right" if right else "left")
    return out


def render_list(lines: list[str], start: int) -> tuple[str, int]:
    first = LIST_RE.match(lines[start])
    base = len(first.group(1))
    ordered = bool(re.match(r"\d", first.group(2)))
    items: list[tuple[str, str]] = []
    i = start
    while i < len(lines):
        m = LIST_RE.match(lines[i])
        if not m or len(m.group(1)) < base:
            break
        if len(m.group(1)) > base:
            sub, nxt = render_list(lines, i)
            if items:
                items[-1] = (items[-1][0], items[-1][1] + sub)
            i = nxt
            continue
        if bool(re.match(r"\d", m.group(2))) != ordered:
            break
        body = m.group(3)
        task = re.match(r"^\[([ xX])\]\s+(.*)$", body)
        if task:
            box = "☑" if task.group(1).lower() == "x" else "☐"
            body = f"{box} {task.group(2)}"
        items.append((body, ""))
        i += 1
    tag = "ol" if ordered else "ul"
    parts = [f"<li>{inline(b)}{extra}</li>" for b, extra in items]
    return f"<{tag}>" + "".join(parts) + f"</{tag}>", i


def render_blocks(lines: list[str], ids: dict) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # fenced code
        if re.match(r"^\s*```", line):
            i += 1
            buf: list[str] = []
            while i < len(lines) and not re.match(r"^\s*```", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + esc("\n".join(buf)) + "</code></pre>")
            continue
        # heading
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            lvl = len(m.group(1))
            txt = inline(m.group(2))
            anchor = ids.get(("h", lvl, m.group(2).strip()))
            aid = f' id="{anchor}"' if anchor else ""
            out.append(f"<h{lvl}{aid}>{txt}</h{lvl}>")
            i += 1
            continue
        # hr
        if re.match(r"^\s*(?:[-*_]\s*){3,}$", line):
            out.append("<hr>")
            i += 1
            continue
        # table
        if "|" in line and i + 1 < len(lines) and SEP_RE.match(lines[i + 1]) and "|" in lines[i + 1]:
            header = split_row(line)
            aligns = aligns_of(lines[i + 1])
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i]))
                i += 1
            th = "".join(
                f'<th style="text-align:{aligns[k] if k < len(aligns) else "left"}">{inline(c)}</th>'
                for k, c in enumerate(header))
            body = ""
            for r in rows:
                tds = "".join(
                    f'<td style="text-align:{aligns[k] if k < len(aligns) else "left"}">'
                    f'{inline(r[k] if k < len(r) else "")}</td>'
                    for k in range(len(header)))
                body += f"<tr>{tds}</tr>"
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            continue
        # blockquote
        if re.match(r"^\s*>", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*>", lines[i]):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + render_blocks(buf, ids) + "</blockquote>")
            continue
        # list
        if LIST_RE.match(line):
            node, i = render_list(lines, i)
            out.append(node)
            continue
        # paragraph（软换行：中文之间不加空格，其余加空格）
        buf = [line]
        i += 1
        while (i < len(lines) and lines[i].strip()
               and not re.match(r"^\s*(```|#{1,6}\s|>)", lines[i])
               and not LIST_RE.match(lines[i])
               and not re.match(r"^\s*(?:[-*_]\s*){3,}$", lines[i])
               and not ("|" in lines[i] and i + 1 < len(lines) and SEP_RE.match(lines[i + 1]))):
            buf.append(lines[i])
            i += 1
        text = buf[0]
        for nxt in buf[1:]:
            if is_cjk(text[-1:]) or is_cjk(nxt[:1]):
                text += nxt
            else:
                text += " " + nxt
        out.append(f"<p>{inline(text)}</p>")
    return "".join(out)


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch[0])
    return 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFF60


# ----------------------------------------------------------------- build
def build() -> Path:
    OUT.mkdir(exist_ok=True)
    ids: dict = {}
    chapters: list[str] = []
    toc: list[str] = []

    for idx, (filename, short) in enumerate(DOCS, start=1):
        path = HERE / filename
        lines = path.read_text(encoding="utf-8").split("\n")
        # 标题（首行 # …）
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            lines = lines[1:]
        else:
            title = filename
        ch_id = f"ch{idx}"
        # 预登记锚点，供目录跳转
        toc.append(f'<li><a href="#{ch_id}"><strong>第 {idx} 部分 · {esc(title)}</strong></a></li>')
        sub = 0
        for ln in lines:
            m = re.match(r"^##\s+(.*?)\s*#*\s*$", ln)
            if m:
                sub += 1
                aid = f"{ch_id}-s{sub}"
                ids[("h", 2, m.group(1).strip())] = aid
                toc.append(f'<li class="lvl2"><a href="#{aid}">{esc(m.group(1).strip())}</a></li>')
        body = render_blocks(lines, ids)
        chapters.append(
            f'<section class="chapter"><div class="chapter-head">第 {idx} 部分 · {esc(short)}</div>'
            f'<h1 id="{ch_id}">{esc(title)}</h1>{body}</section>')

    cover = f"""
<div class="cover">
  <div class="kicker">创科升「AI 应用方案大赛」 · 参赛交付</div>
  <h1>整车能耗归因与降耗方向</h1>
  <div class="sub">基于随车 CAN 报文的「物理—统计」混合建模<br>面向整车开发商与 VCU 开发厂家</div>
  <div class="rule"></div>
  <div class="meta">
    交付内容：6 份文档 + 16 张配图 + 可运行原型（<code>prototype/</code>）+ 独立审计复算脚本（<code>audit/</code>）<br>
    一句话方案：把「能耗高」从一句抱怨，变成一张按性价比排序、可标定、可回归验证的改进清单<br>
    证据等级：【L1】原型实测可复现 ｜【L2】第三方审计独立复算 ｜【L3】设计明确但未实现 ｜【L4】测算假设
  </div>
  <div class="note">
    <strong>阅读提示</strong><br>
    ① 全文数字均带证据等级标注；凡 L3/L4 项不得作为已实现能力或已达成收益对外陈述。<br>
    ② 精度指标均在<strong>带真值的仿真数据</strong>上取得（80 车次 / 235,711 帧），<strong>不能替代真车与台架验证</strong>。<br>
    ③ 公式采用 Unicode 数学字形：变量为数学斜体（𝐸、𝑣、𝜌），下标说明、单位与标识符保持正体。
  </div>
</div>
<div class="toc">
  <h1>目录</h1>
  <ol>{''.join(toc)}</ol>
</div>
"""

    html_doc = (f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                f"<title>整车能耗归因与降耗方向 · 方案文档合集</title>"
                f"<style>{CSS}</style></head><body>{cover}{''.join(chapters)}</body></html>")
    html_path = OUT / "doc.html"
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"[OK] HTML: {html_path}  ({html_path.stat().st_size/1024:.0f} KB)")
    return html_path


def to_pdf(html_path: Path, final_name: str | None = None) -> Path:
    """Edge 无头打印 PDF；随后调用 postprocess 加页码与书签（若脚本存在）。"""
    raw = OUT / "_raw.pdf"
    if raw.exists():
        raw.unlink()
    cmd = [str(EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
           "--no-pdf-header-footer", "--print-to-pdf-no-header",
           f"--print-to-pdf={raw}", html_path.as_uri()]
    print("  运行 Edge 无头打印 …")
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600)
    if not raw.exists():
        raise SystemExit(f"Edge 未生成 PDF\nstdout={res.stdout[-800:]}\nstderr={res.stderr[-800:]}")

    final = OUT / (final_name or PDF_NAME)
    try:
        import postprocess_pdf as pp          # 加页脚页码 + 书签
        pp.process(raw, final)
        raw.unlink()
    except Exception as exc:                  # noqa: BLE001
        print(f"  [warn] 后处理失败（{exc}），输出未加页码的原始 PDF")
        raw.replace(final)
    print(f"[OK] PDF: {final}  ({final.stat().st_size/1024:.0f} KB)")
    return final


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pdf"
    html_path = build()
    if mode == "pdf":
        pdf = to_pdf(html_path)
        data = pdf.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
        print(f"     共 {pages} 页")


if __name__ == "__main__":
    main()
