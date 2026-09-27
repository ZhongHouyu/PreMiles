"""全量比对：源文公式 vs 生成 OMML 的内容字符集合。

目的：发现“内容丢失/重复”这类上下标与分组 bug（例如 abs(𝑟/𝑦) 只剩 abs）。
两边都做归一化：数学斜体→ASCII、上下标数字→普通数字、去掉 OMML 结构关键字。
"""

import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, '.')
sys.path.insert(0, r'..\..')

from lxml import etree

import audit_omml as A
import build_cumcm_docx as B
import math_omml as mo

MD = Path('06_算法模型_数学建模论文格式.md')

KEYS = ('subsup', 'sub', 'sup', 'frac', 'delim', 'term')
SUPSUB = {'²': '2', '³': '3', '¹': '1', '⁰': '0', '⁻': '-',
          '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4', '₅': '5',
          '₆': '6', '₇': '7', '₈': '8', '₉': '9'}
IT_MAP = {}
for _i in range(26):
    IT_MAP[chr(0x1D434 + _i)] = chr(ord('A') + _i)
    IT_MAP[chr(0x1D44E + _i)] = chr(ord('a') + _i)
IT_MAP['\u210e'] = 'h'


def norm(s: str, recon: bool = False) -> Counter:
    for k, v in IT_MAP.items():
        s = s.replace(k, v)
    for k, v in SUPSUB.items():
        s = s.replace(k, v)
    s = s.replace('abs', '')
    if recon:
        for k in KEYS:
            s = s.replace(k, '')
    return Counter(ch for ch in s if ch.isalnum() or '\u4e00' <= ch <= '\u9fff')


def recon(xml: str) -> str:
    root = etree.fromstring(xml.encode('utf-8'))
    return ''.join(A.linear(c) for c in root)


bad = []


def check(src_formula: str, label: str) -> None:
    if not re.search(r'[\w\u4e00-\u9fff]', src_formula):
        return
    try:
        xml = (mo.omml_paragraph_xml(src_formula) if label.startswith('display')
               else mo.omml_inline_xml(src_formula))
        text = recon(xml)
    except Exception as e:                                   # noqa: BLE001
        bad.append((label, src_formula, '构建异常: %s' % e))
        return
    a, b = norm(src_formula), norm(text, recon=True)
    missing, extra = a - b, b - a
    if missing or extra:
        bad.append((label, src_formula, '缺=%s 多=%s' % (dict(missing), dict(extra))))


def inline_tokens(text: str):
    i = 0
    while i < len(text):
        if not B._is_math_start(text, i):
            i += 1
            continue
        end = B._extend_math(text, i)
        if end <= i:
            i += 1
            continue
        if end - i > 1:
            yield text[i:end]
        i = end


lines = MD.read_text(encoding='utf-8').splitlines()
for ln, raw in enumerate(lines, 1):
    line = raw.strip()
    if not line or line.startswith(('#', '>', '|', '```')):
        continue
    if B.is_display_formula(line):
        formula, _n = B.split_trailing_note(B.clean_formula_text(line))
        if formula:
            check(formula, 'display L%d' % ln)
        continue
    for tok in inline_tokens(re.sub(r'\*\*', '', line)):
        check(tok, 'inline  L%d' % ln)

print('比对完成，可疑条目 %d 条' % len(bad))
for label, src, why in bad:
    print('%-13s %-46s %s' % (label, src[:46], why))
