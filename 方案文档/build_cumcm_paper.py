"""按「全国大学生数学建模竞赛（国赛）论文格式规范」把论文（06）单独排成 PDF。

规范要点（本脚本逐条落实）
--------------------------
1. 第一页：承诺书；第二页：编号专用页（由组委会填写）；第三页：摘要页（题目 + 摘要 + 关键词，不超过一页）。
2. 从摘要页之后为论文正文，**不设目录**。
3. 论文用白色 A4 纸，上下左右各留 **2.5 cm** 页边距，左侧装订。
4. 字体：论文题目 **三号黑体**；一级标题 **四号黑体、居中**；二级/三级标题 **小四号黑体、左对齐**；
   正文 **小四号宋体**、**1.5 倍行距**；西文用 Times New Roman；表格用五号。
5. 图题在图下方、表题在表上方，五号宋体居中，并连续编号（图 1、图 2…；表 1、表 2…）。
6. 参考文献按正文引用顺序编号，正文首次出现处标注 [n]（本脚本在首次出现处自动补标注）。
7. 页码从摘要页开始，标注在页脚中部；承诺书页与编号专用页不编码。
8. 全文不出现学校、姓名等身份信息。

说明
----
* 公式仍采用 Unicode 数学字形（数学斜体变量 + 正体下标/单位），由 Cambria Math 渲染——
  国赛允许公式编辑器排版，此处不依赖任何公式引擎，保证任何机器打开都是同样的字形。
* 摘要页使用**压缩版摘要**（内容全部来自论文自身摘要与正文，未引入新数字），
  以保证「摘要不超过一页」的规范要求；正文不再重复摘要。
* 参考文献表按引用顺序重排为 GB/T 7714 样式。

用法
----
    python build_cumcm_paper.py          # 生成 PDF（含页码与书签）
    python build_cumcm_paper.py html     # 只生成 HTML
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import build_doc as bd
import postprocess_pdf as pp

HERE = Path(__file__).resolve().parent
OUT = bd.OUT
SRC = HERE / "06_算法模型_数学建模论文格式.md"
PDF_NAME = "论文_整车能耗归因与降耗方向（数学建模国赛版）.pdf"

# ------------------------------------------------------------------ 文本素材
COMMITMENT = """
<p class="c-line">我们仔细阅读了中国大学生数学建模竞赛的竞赛规则。</p>
<p class="c-line">我们完全明白，在竞赛开始后参赛队员不能以任何方式（包括电话、电子邮件、网上咨询等）与队外的任何人（包括指导教师）研究、讨论与赛题有关的问题。</p>
<p class="c-line">我们知道，抄袭别人的成果是违反竞赛规则的，如果引用别人的成果或其他公开的资料（包括网上查到的资料），必须按照规定的参考文献的表述方式在正文引用处和参考文献中明确列出。</p>
<p class="c-line">我们郑重承诺，严格遵守竞赛规则，以保证竞赛的公正、公平性。如有违反竞赛规则的行为，我们将受到严肃处理。</p>
<p class="c-line">我们授权全国大学生数学建模竞赛组委会，可将我们的论文以任何形式进行公开展示（包括进行网上公示，在书籍、期刊和其他媒体进行正式或非正式发表等）。</p>
<div class="c-sign">
  <p>我们参赛选择的题号是（从 A/B/C/D/E 中选择一项填写）：<span class="fill"></span></p>
  <p>我们的参赛报名号为（如果赛区设置报名号的话）：<span class="fill"></span></p>
  <p>所属学校（请填写完整的全名）：<span class="fill"></span></p>
  <p>参赛队员（打印并签名）：1. <span class="fill2"></span> 2. <span class="fill2"></span> 3. <span class="fill2"></span></p>
  <p>指导教师或指导教师组负责人（打印并签名）：<span class="fill"></span></p>
  <p class="c-date">日期：<span class="fill3"></span>年<span class="fill3"></span>月<span class="fill3"></span>日</p>
</div>
"""

NUMBER_PAGE = """
<p class="n-line">赛区评阅编号（由赛区组委会评阅前进行编号）：</p>
<div class="n-box"></div>
<p class="n-line">全国评阅编号（由全国组委会评阅前进行编号）：</p>
<div class="n-box"></div>
"""

# 压缩版摘要：全部数字与结论均取自论文自身摘要与正文（未引入新数字）
ABSTRACT = """
<p>本文针对整车能耗开发中「能耗账目可算、降耗方向难定」的问题，以随车 CAN/J1939 报文（含台架、转鼓、
路试与量产回传数据）为输入，建立了<strong>物理模型与统计学习混合</strong>的能耗归因模型，并按
<strong>拆—比—反—敏—指—验</strong>六步给出可排序、可标定、可回归验证的降耗方向。</p>

<p><strong>拆</strong>：由纵向动力学与能量守恒把总能耗分解为怠速与附件、传动损失、热效率与部分负荷损失，
以及滚阻、风阻、坡道、动能、制动耗散等分项。<strong>比</strong>：以载荷、地形、环境、车辆属性为特征建立
梯度提升基线，用折外残差 r = 实际 − 应达 构造「公平比较器」，并配以不依赖模型的近邻中位数基线。
<strong>反</strong>：从长期实车数据反标定等效滚阻、C_d·A、传动效率、附件功率与电池内阻。
<strong>敏</strong>：利用能量项的一次齐次性证明相对灵敏度等于该分项的能量份额，使方向可被<strong>排序</strong>
而非罗列。<strong>指</strong>：把偏差映射为设计参数、VCU 策略与标定阈值三类可干预量，逐条附预期收益口径、
置信度与验证方式。<strong>验</strong>：以配对设计与功效分析确定验证样本量，用工况回放做台架、HIL 或实车 A/B 对比。</p>

<p>模型给出四个可用于边界判断的结果：<strong>①</strong> 能量瀑布的「分项之和等于总量」是按定义闭合的
<strong>恒等式</strong>，鉴别力为零，不能作为物理验证；<strong>②</strong> 灵敏度定理 S_p = w_i，并由审计实验
两次独立扰动反解出同一份额（27.0% 与 26.8%）予以验证；<strong>③</strong> 参数可辨识性退化关系
ρ = 1/[1 + (k − 1)w]，说明「滚阻升高 35%」与「动力系统效率下降 8.9%」在一维观测量下完全等价；
<strong>④</strong> 量化下限 ε_floor = q/Δ 与配对验证样本量 n ≈ 7.85σ_d²/Δ²，后者表明验证 1% 量级的改善
需要约 32 组同工况配对车次。</p>

<p>在 80 车次、235 711 帧的带真值仿真数据上：能耗账目与真值偏差 0.00%~0.03%；与外部对账基准偏差
1.08%~3.57%（目标 &lt; 5%）；三方里程互差 0.01%~0.03%；基线模型样本外 R² = 0.894、MAPE = 4.61%；
异常检测查准率 100%、查全率 79.2%（折半交叉验证 70.8%）；参数扰动使牵引能耗变化 +16.2%、
+13.4%、+56.5%，而上述账目自检<strong>全部通过</strong>，从实验上印证了结论 ①。</p>

<p><strong>结论</strong>：模型在带真值的仿真数据上通过了对抗性检验，算法自洽且账目算得准，
<strong>但不能替代真车与台架验证</strong>；由结论 ③ 可知一维观测量只能识别一个参数组合，
因此参数反标定必须与独立观测量（称重、滑行试验、长巡航段、台架）配合使用。</p>

<p class="kw"><strong>关键词</strong>：整车能耗归因；能量瀑布；纵向动力学；梯度提升回归；孤立森林；可辨识性；参数反标定</p>
"""

# 参考文献：按正文引用顺序重排（GB/T 7714 样式）
REFERENCES = """
<p>[1] SAE International. J1939: Serial control and communications heavy duty vehicle network[S].
Warrendale: SAE International.</p>
<p>[2] GILLESPIE T D. Fundamentals of vehicle dynamics[M]. Warrendale: SAE International, 1992.</p>
<p>[3] FRIEDMAN J H. Greedy function approximation: a gradient boosting machine[J].
Annals of Statistics, 2001, 29(5): 1189-1232.</p>
<p>[4] PEDREGOSA F, VAROQUAUX G, GRAMFORT A, et al. Scikit-learn: machine learning in Python[J].
Journal of Machine Learning Research, 2011, 12: 2825-2830.</p>
<p>[5] LIU F T, TING K M, ZHOU Z H. Isolation forest[C]//Proceedings of the 8th IEEE International
Conference on Data Mining. Pisa: IEEE, 2008: 413-422.</p>
<p>[6] PAGE E S. Continuous inspection schemes[J]. Biometrika, 1954, 41(1/2): 100-115.</p>
<p>[7] LUNDBERG S M, LEE S I. A unified approach to interpreting model predictions[C]//Advances in
Neural Information Processing Systems. Long Beach: NeurIPS, 2017: 4765-4774.</p>
<p>[8] BREIMAN L. Random forests[J]. Machine Learning, 2001, 45(1): 5-32.</p>
<p>[9] SAE International. J1263: Road load measurement and dynamometer simulation using coastdown
techniques[S]. Warrendale: SAE International.</p>
<p>[10] ASAM. Measurement data format (MDF) v4[S]. Munich: ASAM e.V.</p>
<p>[11] 本项目组. 独立复现与技术审计报告：第三方复现与对抗性验证[R]. 内部技术报告, 2026.</p>
<p>[12] 全国汽车标准化技术委员会. 中国汽车行驶工况：GB/T 38146[S]. 北京: 中国标准出版社.</p>
<p class="ref-note">注：所引标准的具体版本号以现行有效版本为准。</p>
"""

# 正文首次出现处补引用标注（国赛要求参考文献在正文中标注）
CITATIONS: list[tuple[str, str]] = [
    ("商用车的车联网终端以 10 ~ 100 Hz 采集整车 CAN / SAE J1939 报文",
     "商用车的车联网终端以 10 ~ 100 Hz 采集整车 CAN / SAE J1939 报文[1]"),
    ("车轮侧各分项的功率表达式为：", "车轮侧各分项的功率表达式为[2]："),
    ("取平方损失 𝐿(𝑦, 𝐹) = ½ (𝑦 − 𝐹)²，则伪残差即为 **𝑦 − 𝐹**。",
     "取平方损失 𝐿(𝑦, 𝐹) = ½ (𝑦 − 𝐹)²，则伪残差即为 **𝑦 − 𝐹**。[3-4]"),
    ("**③ 无监督**：异常分数", "**③ 无监督**[5]：异常分数"),
    ("𝑆_i = max( 0 , 𝑆_(𝑖−1) + 𝑟_i − 𝑘 )，𝑘 = 3（%），ℎ = 12",
     "𝑆_i = max( 0 , 𝑆_(𝑖−1) + 𝑟_i − 𝑘 )，𝑘 = 3（%），ℎ = 12[6]"),
    ("𝑐_j = 𝑓(𝑥) − 𝑓(𝑥^(𝑗))，其中 𝑥^(𝑗) 表示仅将第 𝑗 个特征替换为车队中位数",
     "𝑐_j = 𝑓(𝑥) − 𝑓(𝑥^(𝑗))，其中 𝑥^(𝑗) 表示仅将第 𝑗 个特征替换为车队中位数[7]"),
    ("置换重要度用训练集自证", "置换重要度用训练集自证[8]"),
    ("（SAE J1263 / J2263 类方法）", "（SAE J1263 / J2263 类方法）[9]"),
    (" 1  解码: DBC/A2L → 物理信号", " 1  解码: DBC/A2L[10] → 物理信号"),
    ("### 7.1 数据与场景", "### 7.1 数据与场景[11]"),
    ("4. **建工况库**", "4. **建工况库**[12]"),
]

# ------------------------------------------------------------------ CSS（国赛规范）
CSS = """
@page { size: A4; margin: 2.5cm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Times New Roman", "Cambria Math", SimSun, "宋体", serif;
  font-size: 12pt;            /* 小四 */
  line-height: 1.5;           /* 1.5 倍行距 */
  color: #000; margin: 0; text-align: justify;
}
/* 论文题目：三号黑体，居中 */
h1.paper-title { font-family: SimHei, "黑体", sans-serif; font-size: 16pt; text-align: center;
                 margin: 0 0 4pt; line-height: 1.4; }
p.paper-sub { font-family: SimHei, "黑体", sans-serif; font-size: 12pt; text-align: center;
              margin: 0 0 18pt; }
/* 一级标题：四号黑体，居中 */
h1 { font-family: SimHei, "黑体", sans-serif; font-size: 14pt; text-align: center;
     margin: 16pt 0 10pt; page-break-after: avoid; }
/* 二级、三级标题：小四黑体，左对齐 */
h2, h3, h4, h5, h6 { font-family: SimHei, "黑体", sans-serif; font-size: 12pt; text-align: left;
     margin: 12pt 0 6pt; page-break-after: avoid; }
h3 { margin-top: 10pt; }
h4 { margin-top: 8pt; }
p { margin: 0 0 6pt; text-indent: 0; }
ul, ol { margin: 4pt 0 6pt; padding-left: 22pt; }
li { margin: 2pt 0; }
a { color: #000; text-decoration: none; }
code { font-family: "Times New Roman", Consolas, monospace; font-size: 10.5pt; }
pre { font-family: Consolas, "Courier New", monospace; font-size: 9pt; line-height: 1.35;
      border: 0.5pt solid #999; padding: 5pt 7pt; margin: 6pt 0; white-space: pre-wrap;
      page-break-inside: avoid; }
blockquote { margin: 6pt 0; padding: 3pt 0 3pt 9pt; border-left: 2pt solid #999; page-break-inside: avoid; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; font-size: 10.5pt;   /* 五号 */
        page-break-inside: avoid; }
th, td { border: 0.5pt solid #666; padding: 3pt 4pt; vertical-align: top; text-align: left; }
th { font-family: SimHei, "黑体", sans-serif; font-weight: normal; background: #f2f2f2; }
img { max-width: 96%; height: auto; display: block; margin: 4pt auto 2pt; }
figure { margin: 8pt 0; page-break-inside: avoid; }
.tbl-wrap { page-break-inside: avoid; }
figcaption, .tbl-cap { font-family: SimSun, "宋体", serif; font-size: 10.5pt; text-align: center;
                       margin: 2pt 0 6pt; }
hr { border: none; border-top: 0.5pt solid #999; margin: 10pt 0; }
strong { font-weight: bold; }
/* 承诺书 / 编号页 */
.page-break { page-break-after: always; }
.c-title { font-family: SimHei, "黑体", sans-serif; font-size: 16pt; text-align: center;
           margin: 60pt 0 24pt; letter-spacing: 6pt; }
.c-line { font-size: 12pt; line-height: 1.9; margin: 0 0 10pt; text-indent: 0; }
.c-sign { margin-top: 30pt; font-size: 12pt; line-height: 2.1; }
.c-sign p { margin: 0 0 6pt; text-align: left; }
.fill { display: inline-block; width: 180pt; border-bottom: 0.5pt solid #000; }
.fill2 { display: inline-block; width: 96pt; border-bottom: 0.5pt solid #000; }
.fill3 { display: inline-block; width: 40pt; border-bottom: 0.5pt solid #000; }
.c-date { margin-top: 14pt; text-align: right; }
.n-title { font-family: SimHei, "黑体", sans-serif; font-size: 14pt; text-align: center; margin: 60pt 0 40pt; }
.n-line { font-size: 12pt; margin: 26pt 0 6pt; }
.n-box { height: 54pt; border: 0.5pt solid #000; }
/* 摘要页 */
.a-title { font-family: SimHei, "黑体", sans-serif; font-size: 16pt; text-align: center;
           margin: 0 0 4pt; }
.a-sub { font-family: SimHei, "黑体", sans-serif; font-size: 12pt; text-align: center; margin: 0 0 16pt; }
.a-head { font-family: SimHei, "黑体", sans-serif; font-size: 14pt; text-align: center;
          letter-spacing: 8pt; margin: 0 0 10pt; }
.a-body { font-size: 12pt; line-height: 1.5; }
.a-body p { margin: 0 0 6pt; }
.kw { margin-top: 10pt; }
.ref-note { font-size: 10.5pt; color: #333; }
"""


def split_source() -> dict:
    lines = SRC.read_text(encoding="utf-8").split("\n")
    text = "\n".join(lines)
    title = lines[0][2:].strip()
    sub = ""
    m = re.search(r"^##\s+——\s*(.+)$", text, re.M)
    if m:
        sub = "—— " + m.group(1).strip()
    body_start = text.index("## 一、问题重述")
    ref_start = text.index("## 参考文献")
    app_start = text.index("## 附录")
    body = text[body_start:ref_start].rstrip()
    appendix = text[app_start:].rstrip()
    return {"title": title, "sub": sub, "body": body, "appendix": appendix}


def add_citations(md: str) -> str:
    for old, new in CITATIONS:
        if old in md:
            md = md.replace(old, new, 1)
        else:
            print(f"  [warn] 引用锚点未命中: {old[:34]}")
    return md


def decorate(html: str) -> str:
    """给图片加 <figure>+图题，给表格加表题；编号连续。"""
    fig_no = 0

    def fig_repl(m: re.Match) -> str:
        nonlocal fig_no
        fig_no += 1
        alt = m.group(1).strip() or "图"
        return (f'<figure>{m.group(0)}'
                f'<figcaption>图 {fig_no}　{alt}</figcaption></figure>')

    html = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', fig_repl, html)

    # 表题与表格包在同一个容器里，避免分页时被拆散
    out = []
    tbl_no = 0
    last_head = ""
    pos = 0
    for m in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>|<table>.*?</table>", html, re.S):
        out.append(html[pos:m.start()])
        token = m.group(0)
        if token.startswith("<table"):
            tbl_no += 1
            subject = re.sub(r"<[^>]+>", "", last_head).strip()
            subject = re.sub(r"^[0-9.、\s]+", "", subject)[:34]
            cap = f"表 {tbl_no}　{subject}" if subject else f"表 {tbl_no}"
            out.append(f'<div class="tbl-wrap"><div class="tbl-cap">{cap}</div>{token}</div>')
        else:
            last_head = m.group(1)
            out.append(token)
        pos = m.end()
    out.append(html[pos:])
    return "".join(out)


def head_list(title: str, md: str) -> list[tuple[int, str]]:
    """论文书签：一级 = 题目与一/二级大标题，二级 = 三级小节标题。"""
    out: list[tuple[int, str]] = [(1, title)]
    for ln in md.split("\n"):
        m2 = re.match(r"^##\s+(.*?)\s*#*\s*$", ln)
        if m2:
            t = m2.group(1).strip()
            if t != "摘要":
                out.append((1, t))
            continue
        m3 = re.match(r"^###\s+(.*?)\s*#*\s*$", ln)
        if m3:
            out.append((2, m3.group(1).strip()))
    return out


def to_pdf(html_path: Path, heads: list[tuple[int, str]]) -> Path:
    raw = OUT / "_paper_raw.pdf"
    if raw.exists():
        raw.unlink()
    import subprocess
    cmd = [str(bd.EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
           "--no-pdf-header-footer", "--print-to-pdf-no-header",
           f"--print-to-pdf={raw}", html_path.as_uri()]
    print("  运行 Edge 无头打印 …")
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=600)
    if not raw.exists():
        raise SystemExit("Edge 未生成 PDF")
    final = OUT / PDF_NAME
    # 前两页（承诺书、编号专用页）不编码，页码从摘要页 = 1 开始
    pp.process(raw, final, skip=2, start=1, heads=heads)
    raw.unlink()
    print(f"[OK] PDF: {final}  ({final.stat().st_size/1024:.0f} KB)")
    return final


def main() -> None:
    if (sys.argv[1] if len(sys.argv) > 1 else "pdf") == "pdf":
        html_path, heads = build_and_report()
        to_pdf(html_path, heads)
    else:
        build_and_report()


def build_and_report() -> tuple[Path, list[tuple[int, str]]]:
    OUT.mkdir(exist_ok=True)
    src = split_source()
    md = add_citations(src["body"] + "\n\n" + src["appendix"])
    html_body = decorate(bd.render_blocks(md.split("\n"), {}))
    heads = head_list(src["title"], md) + [(1, "参考文献")]

    doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{src['title']}</title><style>{CSS}</style></head><body>
<section class="page-break">
  <div class="c-title">承 诺 书</div>
  {COMMITMENT}
</section>
<section class="page-break">
  <div class="n-title">编 号 专 用 页</div>
  {NUMBER_PAGE}
</section>
<section class="page-break">
  <div class="a-title">{src['title']}</div>
  <div class="a-sub">{src['sub']}</div>
  <div class="a-head">摘 要</div>
  <div class="a-body">{ABSTRACT}</div>
</section>
<section>
  {html_body}
  <h1>参考文献</h1>
  {REFERENCES}
</section>
</body></html>"""
    path = OUT / "paper.html"
    path.write_text(doc, encoding="utf-8")
    print(f"[OK] HTML: {path}  ({path.stat().st_size/1024:.0f} KB)")
    return path, heads


if __name__ == "__main__":
    main()
