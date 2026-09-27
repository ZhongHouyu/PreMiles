"""把文档里的公式文本（Unicode 数学记号）转成 **OMML**（Word 原生公式）。

为什么用 OMML
-------------
OMML 是 Word 2007 起的内置公式格式，也是 MathType 的官方互通格式：
装好 MathType 后执行「Convert Equations → Office Math」，即可把全文 OMML 公式一键转成
MathType 公式对象；不装 MathType 也能在 Word 里直接编辑公式。

转换规则
--------
* 数学斜体字符（𝐸、𝑣、𝜌…）→ 还原为 ASCII 字母：Word 数学区会自动把变量排成斜体。
* `X_y` → 下标；下标是多字母说明词（idle、roll、engine_est）时设为**正体**（m:sty="p"）。
* `X^y`、`X²`、`X⁻¹` → 上标；`X_y^z` → 上下标。
* `A / B`（两侧都是简单量）→ 分式 m:f；单位（MJ/kg、L/h）保持斜杠不转分式。
* `abs(x)` → 绝对值定界符；`( … )` → 圆括号定界符（m:d），因此 `A_f / (2m)` 能正确转分式。
* 其余符号（∑ ∫ ρ θ η Δ ≤ ≥ ⇒ ∝ ·）原样进入数学区。

用法
----
    from math_omml import omml_paragraph_xml, omml_inline_xml, to_omml
    to_omml("𝐸_chem = 𝐸_idle + 𝐸_trac")      # OMML 片段（m:oMath 内部）
    omml_paragraph_xml("...")                # 整段展示式（m:oMathPara）
    omml_inline_xml("...")                   # 行内公式（m:oMath）
"""

from __future__ import annotations

import re

# 必须是**标准** OMML 命名空间（ECMA-376 / Word 2007 正式版）。
# 早期版本这里误用了预发布命名空间 http://schemas.microsoft.com/office/2004/12/omml，
# 结果 Word 会把 oMath 当作未知元素、在打开时整段丢弃（OMaths.Count = 0，
# 排版上只留下空白），而 XML 本身仍然完全合法，极易误判为“构建失败”。
M_NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'

# ------------------------------------------------------------------ 字符映射
_ITALIC_MAP: dict[str, str] = {}
for _i in range(26):
    _ITALIC_MAP[chr(0x1D434 + _i)] = chr(ord("A") + _i)      # 𝐴..𝑍
    _ITALIC_MAP[chr(0x1D44E + _i)] = chr(ord("a") + _i)      # 𝑎..𝑧
_ITALIC_MAP["\u210E"] = "h"                                   # ℎ
_ITALIC_MAP["\U0001D455"] = "h"
# 数学斜体希腊字母 → 普通希腊字母（Word 数学区自动斜体）
_GREEK_IT = "αβγδεζηθικλμνξοπρςστυφχψω"
for _i, _ch in enumerate(_GREEK_IT):
    _ITALIC_MAP[chr(0x1D6FC + _i)] = _ch
_GREEK_IT_UP = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
for _i, _ch in enumerate(_GREEK_IT_UP):
    _ITALIC_MAP[chr(0x1D6E2 + _i)] = _ch

# 单位里的斜杠（m/s²、MJ/kg、L/100km）要保护起来，不当作分式
_UNIT_SLASH = re.compile(r"(?<![A-Za-z_])([A-Za-z]{1,3})/([0-9]{0,3}[A-Za-z]{1,6})(?![A-Za-z])")
_UNIT_LEFT = {"m", "s", "L", "h", "g", "kg", "t", "N", "W", "J", "V", "A", "kWh",
              "kW", "MJ", "km", "rpm", "rad", "Hz", "kPa", "bit"} 

GREEK = set("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψω")
UNIT_TOKENS = {
    "MJ", "kg", "kWh", "kW", "km", "cm", "mm", "ms", "L", "h", "s", "m", "g", "t",
    "N", "V", "A", "W", "J", "rad", "rpm", "Hz", "kPa", "MPa", "bit", "tkm",
}
SUP_MAP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
           "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "⁺": "+", "ⁿ": "n"}
SUB_MAP = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
           "₆": "6", "₇": "7", "₈": "8", "₉": "9"}
MATH_CHARS = set("∑∫√∂∝∞")
FUNCTIONS = {"max", "min", "clip", "median", "mean", "log", "ln", "exp", "sin",
             "cos", "tan", "argmin", "argmax", "sign", "std", "var", "sqrt"}

IDENT = re.compile(r"[A-Za-z][A-Za-z0-9]*|[0-9]+(?:\.[0-9]+)?")


def normalize(text: str) -> str:
    """数学斜体 → ASCII / 普通希腊字母；Unicode 上下标连写 → `^(...)` / `_(...)`。"""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _ITALIC_MAP:
            out.append(_ITALIC_MAP[ch])
            i += 1
            continue
        if ch in SUP_MAP:
            j, buf = i, ""
            while j < len(text) and text[j] in SUP_MAP:
                buf += SUP_MAP[text[j]]
                j += 1
            out.append(f"^({buf})")
            i = j
            continue
        if ch in SUB_MAP:
            j, buf = i, ""
            while j < len(text) and text[j] in SUB_MAP:
                buf += SUB_MAP[text[j]]
                j += 1
            out.append(f"_({buf})")
            i = j
            continue
        out.append(ch)
        i += 1
    text = "".join(out)

    # 保护单位斜杠：m/s、MJ/kg、L/100km → 用 ∕（U+2215）占位，避免被当作分式
    def _protect(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if left in _UNIT_LEFT or right[0].isdigit():
            return f"{left}∕{right}"
        return m.group(0)

    return _UNIT_SLASH.sub(_protect, text)


def tokenize(src: str) -> list[str]:
    toks: list[str] = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == " ":
            i += 1
            continue
        m = IDENT.match(src, i)
        if m:
            toks.append(m.group(0))
            i = m.end()
            continue
        toks.append(ch)
        i += 1
    return toks


class Node:
    def __init__(self, kind: str, **kw):
        self.kind = kind
        self.data = kw


def parse(toks: list[str]) -> list[Node]:
    nodes: list[Node] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        # 组合附加符号（m̂、θ̂、x̄）：并进前一个文本 run，不能单独成一个 run，
        # 否则 Word 会把帽子画成孤立的符号。
        if len(tok) == 1 and "\u0300" <= tok <= "\u036f":
            if nodes and nodes[-1].kind == "text":
                nodes[-1].data["value"] = str(nodes[-1].data["value"]) + tok
            elif nodes:
                nodes.append(Node("text", value=tok))
            i += 1
            continue
        if tok == "abs" and i + 1 < len(toks) and toks[i + 1] == "(":
            inner, i = _read_group(toks, i + 1)
            base = Node("delims", inner=parse(inner), beg="|", end="|")
        elif tok == "(":
            inner, i = _read_group(toks, i)
            base = Node("delims", inner=parse(inner), beg="(", end=")")
        elif tok == "[":
            inner, i = _read_bracket(toks, i)
            base = Node("delims", inner=parse(inner), beg="[", end="]")
        else:
            base = Node("text", value=tok)
            i += 1
        sub = sup = None
        if i < len(toks) and toks[i] == "_":
            sub, i = _read_script(toks, i + 1, allow_chain=True)
        if i < len(toks) and toks[i] == "^":
            sup, i = _read_script(toks, i + 1, allow_chain=False)
        if sub is None and sup is not None and i < len(toks) and toks[i] == "_":
            # 形如 R²_oof：上标写在下标前面，仍应组成 subsup
            sub, i = _read_script(toks, i + 1, allow_chain=True)
        if sub is not None and not sub:
            sub = None
        if sup is not None and not sup:
            sup = None
        if sub is not None and sup is not None:
            nodes.append(Node("subsup", base=base, sub=sub, sup=sup))
        elif sub is not None:
            nodes.append(Node("sub", base=base, sub=sub))
        elif sup is not None:
            nodes.append(Node("sup", base=base, sup=sup))
        else:
            nodes.append(base)
    return _fractions(_terms(nodes))


def _read_bracket(toks: list[str], i: int) -> tuple[list[str], int]:
    """读取 `[ ... ]` 内部（toks[i] == "["），返回 (内部 token, 下一位置)。"""
    depth = 0
    out: list[str] = []
    while i < len(toks):
        if toks[i] == "[":
            depth += 1
            if depth > 1:
                out.append(toks[i])
        elif toks[i] == "]":
            depth -= 1
            if depth == 0:
                return out, i + 1
            out.append(toks[i])
        else:
            out.append(toks[i])
        i += 1
    return out, i


def _read_group(toks: list[str], i: int) -> tuple[list[str], int]:
    depth = 0
    out: list[str] = []
    while i < len(toks):
        if toks[i] == "(":
            depth += 1
            if depth > 1:
                out.append(toks[i])
        elif toks[i] == ")":
            depth -= 1
            if depth == 0:
                return out, i + 1
            out.append(toks[i])
        else:
            out.append(toks[i])
        i += 1
    return out, i


def _read_script(toks: list[str], i: int, allow_chain: bool = True) -> tuple[list[Node], int]:
    """读取下标/上标内容。

    * allow_chain=True 时支持链式下标（`engine_est` 作为一个下标，保留下划线）；
    * 中文字符连续读取（`r_操作` 不能只吃一个字）；
    * 上标不参与链式（否则 `R²_oof` 的 `_oof` 会被吞进上标）。
    """
    nodes: list[Node] = []
    while i < len(toks):
        tok = toks[i]
        if tok == "(":
            inner, i = _read_group(toks, i)
            nodes.extend(parse(inner))
        elif tok in ("_", "^"):
            break
        elif len(tok) == 1 and "\u4e00" <= tok <= "\u9fff":
            buf = ""
            while i < len(toks) and len(toks[i]) == 1 and "\u4e00" <= toks[i] <= "\u9fff":
                buf += toks[i]
                i += 1
            nodes.append(Node("text", value=buf))
        else:
            nodes.extend(parse([tok]))
            i += 1
        if allow_chain and i < len(toks) and toks[i] == "_":
            nodes.append(Node("text", value="_"))
            i += 1
            continue
        break
    return nodes, i


def _is_simple(node: Node) -> bool:
    return node.kind in ("text", "sub", "sup", "subsup", "delims", "term")


def _plain(node: Node) -> str:
    if node.kind == "text":
        return str(node.data.get("value", ""))
    if node.kind == "term":
        return "".join(_plain(n) for n in node.data["items"])
    return ""


OP_TEXT = set("+-−=≠≤≥≈⇒→·×÷/,;:%<>~")
# 全角/中文标点与集合符号同样是"分隔符"，绝不能与两侧合并成一个"项"，
# 否则会出现 `(𝑋−μ) / σ；𝑑_ij` 这种分母越过语句分隔符的错误分式。
OP_TEXT |= set("；，、：（）｛｝［］｜？！")
OP_TEXT |= set("∈⊆⊂∪∩∖∀∃⟺↔∎")


def _is_operand(node: Node) -> bool:
    """是否是"项"的一部分：不是单字符运算符即可。"""
    if node.kind != "text":
        return True
    v = str(node.data["value"])
    return not (len(v) == 1 and v in OP_TEXT)


def _terms(nodes: list[Node]) -> list[Node]:
    """把相邻的运算对象合并成"项"。

    这样 `Δt / 2` 的分子才会是整个 `Δt`（而不是只剩 `t`），
    `∑ r² / ∑ (y−ȳ)²` 的分子才会是 `∑r²`。
    """
    out: list[Node] = []
    for n in nodes:
        if _is_operand(n) and out and _is_operand(out[-1]):
            if out[-1].kind == "term":
                out[-1].data["items"].append(n)
            else:
                out[-1] = Node("term", items=[out[-1], n])
        else:
            out.append(n)
    return out


def _fractions(nodes: list[Node]) -> list[Node]:
    out: list[Node] = []
    i = 0
    while i < len(nodes):
        if (i + 2 < len(nodes) and nodes[i + 1].kind == "text"
                and _plain(nodes[i + 1]) == "/"
                and _is_simple(nodes[i]) and _is_simple(nodes[i + 2])
                and _frac_ok(nodes[i], nodes[i + 2])):
            out.append(Node("frac", num=[nodes[i]], den=[nodes[i + 2]]))
            i += 3
            continue
        out.append(nodes[i])
        i += 1
    return out


def _frac_ok(a: Node, b: Node) -> bool:
    """判断两侧是否适合做成"竖式分式"。

    排除：单位与单位（MJ/kg）、缩写（BSFC/1000、19/FP）、含中文的一侧、
    以及一侧里混进了语句分隔符（说明分式跨过了 `；`，一定是错的分组）。
    """
    pa, pb = _plain(a), _plain(b)
    if pa in UNIT_TOKENS and pb in UNIT_TOKENS:
        return False
    for p in (pa, pb):
        if not p:
            continue
        if len(p) > 1 and p.isalpha() and p.isupper():     # 缩写：BSFC、FP、MAE
            return False
        if re.search(r"[\u4e00-\u9fff]", p):                # 中文不放进分式
            return False
        if re.search(r"[；;，,、：:]", p):                    # 跨过语句分隔符 → 拒绝
            return False
    return True


# ------------------------------------------------------------------ OMML 生成
def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run(text: str, upright: bool = False) -> str:
    style = '<m:rPr><m:sty m:val="p"/></m:rPr>' if upright else ""
    return f"<m:r>{style}<m:t>{_esc(text.replace('∕', '/'))}</m:t></m:r>"


def _needs_upright(nodes: list[Node]) -> bool:
    txt = "".join(_plain(n) for n in nodes if n.kind == "text")
    return len(txt) > 1 and txt.replace("_", "").isalpha()


def emit(nodes: list[Node], upright: bool = False) -> str:
    parts: list[str] = []
    for n in nodes:
        k = n.kind
        if k == "text":
            value = str(n.data["value"])
            # 函数名保持正体（Word 数学区默认把拉丁字母排成斜体）
            parts.append(_run(value, upright or value in FUNCTIONS))
        elif k == "sub":
            parts.append("<m:sSub><m:e>" + emit([n.data["base"]]) + "</m:e><m:sub>"
                         + emit(n.data["sub"], _needs_upright(n.data["sub"]))
                         + "</m:sub></m:sSub>")
        elif k == "sup":
            parts.append("<m:sSup><m:e>" + emit([n.data["base"]]) + "</m:e><m:sup>"
                         + emit(n.data["sup"], True) + "</m:sup></m:sSup>")
        elif k == "subsup":
            parts.append("<m:sSubSup><m:e>" + emit([n.data["base"]]) + "</m:e><m:sub>"
                         + emit(n.data["sub"], _needs_upright(n.data["sub"]))
                         + "</m:sub><m:sup>" + emit(n.data["sup"], True)
                         + "</m:sup></m:sSubSup>")
        elif k == "frac":
            parts.append("<m:f><m:num>" + emit(n.data["num"]) + "</m:num><m:den>"
                         + emit(n.data["den"]) + "</m:den></m:f>")
        elif k == "delims":
            parts.append(f'<m:d><m:dPr><m:begChr m:val="{n.data["beg"]}"/>'
                         f'<m:endChr m:val="{n.data["end"]}"/></m:dPr><m:e>'
                         + emit(n.data["inner"]) + "</m:e></m:d>")
        elif k == "term":
            parts.append(emit(n.data["items"]))
    return "".join(parts)


def to_omml(formula: str) -> str:
    """公式文本 → OMML 片段（不含 <m:oMath> 外层）。"""
    return emit(parse(tokenize(normalize(formula))))


# ------------------------------------------------------------- 线性语法（Word）
def _wrap(nodes: list[Node]) -> str:
    """下/上标内容：单 token 直接写，多 token 加括号（Word 线性格式要求）。"""
    txt = "".join(_linear([n]) for n in nodes)
    return txt if len(txt) <= 1 or txt.startswith("(") else f"({txt})"


def _linear(nodes: list[Node]) -> str:
    out: list[str] = []
    for n in nodes:
        k = n.kind
        if k == "text":
            # 注意：单位里的斜杠保留为 ∕（U+2215），避免 Word 的 BuildUp 把它掰成分式
            out.append(str(n.data["value"]))
        elif k == "sub":
            out.append(_linear([n.data["base"]]) + "_" + _wrap(n.data["sub"]))
        elif k == "sup":
            out.append(_linear([n.data["base"]]) + "^" + _wrap(n.data["sup"]))
        elif k == "subsup":
            out.append(_linear([n.data["base"]]) + "_" + _wrap(n.data["sub"])
                       + "^" + _wrap(n.data["sup"]))
        elif k == "frac":
            out.append(_linear(n.data["num"]) + "/" + _linear(n.data["den"]))
        elif k == "delims":
            out.append(str(n.data["beg"]) + _linear(n.data["inner"]) + str(n.data["end"]))
        elif k == "term":
            out.append(_linear(n.data["items"]))
    return "".join(out)


def to_linear(formula: str) -> str:
    """公式文本 → Word 线性公式语法（配合 OMaths.Add + BuildUp 生成原生公式）。"""
    text = _linear(parse(tokenize(normalize(formula))))
    return re.sub(r"\s+", " ", text).strip()


def omml_paragraph_xml(formula: str) -> str:
    """整段展示式（居中）：<m:oMathPara>。"""
    return f'<m:oMathPara {M_NS}><m:oMath>{to_omml(formula)}</m:oMath></m:oMathPara>'


def omml_inline_xml(formula: str) -> str:
    """行内公式：<m:oMath>。"""
    return f'<m:oMath {M_NS}>{to_omml(formula)}</m:oMath>'


# --------------------------------------------------------------- docx 收尾处理
def fix_docx_namespaces(path) -> None:
    """把 m: 命名空间声明搬回文档根元素，并统一为标准 OMML 命名空间。

    python-docx 的 parse_xml 要求片段自带前缀声明，所以每个公式片段上都会出现
    xmlns:m；但 Word 对**元素级** m: 声明所在的命名空间非常敏感：只有
    http://schemas.openxmlformats.org/officeDocument/2006/math（标准）才会被识别成公式，
    预发布命名空间 .../2004/12/omml 会被当作未知元素整段丢弃（表现为公式全部变成空白）。

    做法：删掉文档里所有 xmlns:m 声明，再在 <w:document> 上补一条标准声明。
    """
    import re as _re
    import shutil
    import zipfile
    from pathlib import Path as _Path

    from lxml import etree

    std_uri = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    assert M_NS == 'xmlns:m="%s"' % std_uri, "M_NS 必须是标准 OMML 命名空间"

    path = _Path(path)
    tmp = path.with_suffix(".tmpdocx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                s = data.decode("utf-8")
                # 删掉所有 m: 声明（元素级可能前面是换行而不是空格，必须用正则）
                s = _re.sub(r'\s+xmlns:m="[^"]*"', "", s)
                m = _re.search(r"<w:document\b[^>]*>", s)   # 文档前面有 <?xml?>，不能用 match
                if not m:
                    raise ValueError("找不到 <w:document> 根元素")
                tag = m.group(0)
                s = s.replace(tag, tag[:-1] + " " + M_NS + ">", 1)
                if s.count("xmlns:m=") != 1:
                    raise ValueError(f"xmlns:m 声明数量异常：{s.count('xmlns:m=')}")
                if "2004/12/omml" in s:
                    raise ValueError("文档中仍残留预发布 OMML 命名空间 2004/12/omml")
                etree.fromstring(s.encode("utf-8"))          # 必须仍是合法 XML
                data = s.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(str(tmp), str(path))


if __name__ == "__main__":
    tests = [
        "𝐸_chem = 𝐸_idle + 𝐸_dt_loss + 𝐸_trac",
        "𝜀_floor = q / Δ",
        "𝜂_engine_est = (𝐸_trac / η_dt) / (𝐸_chem − 𝐸_idle)",
        "𝜌_air · 𝐶_d · 𝐴_f / (2m) · 𝑣²",
        "abs(a) > 0.3 m/s²",
        "ρ = 1/[1 + (k − 1)w]",
        "𝑅² = 0.894，MAE 1.71 L/100km，MAPE 4.61%",
        "𝑛 ≈ 7.85 · σ_d² / Δ²",
        "𝑃_trac / 𝑣 = 𝑚 · 𝑔 · 𝐶_rr + ½ · ρ_air · 𝐶_d · 𝐴_f · 𝑣²",
    ]
    for f in tests:
        print(f)
        print("  linear:", to_linear(f))
