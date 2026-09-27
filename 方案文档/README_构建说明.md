# 方案文档与论文的构建说明

本目录的交付物全部由脚本生成，**不要手工改 out/ 里的文件**（会被下一次构建覆盖）。

## 1. 产物清单（`out/`）

| 文件 | 说明 |
| --- | --- |
| `整车能耗归因与降耗方向_方案文档合集.pdf` | 01/02/03/04/05 五份文档合集，147 页，带页码与书签 |
| `论文_整车能耗归因与降耗方向（数学建模国赛版）.docx` | 国赛格式论文，**公式为 Word 原生公式对象（OMML）**，Word 里可直接编辑、MathType 可一键转成 Office Math |
| `论文_整车能耗归因与降耗方向（数学建模国赛版）.pdf` | 同内容的 PDF 版（Edge 无头打印 + 页码/书签后处理） |
| `论文_整车能耗归因与降耗方向（数学建模国赛版·Word公式版）.pdf` | 由 Word 导出，用于核对公式在 Word 里的真实排版 |

> 目标文件被 WPS / Word 打开时会写入失败，构建脚本会自动另存为同名 `·修正版` 文件。

## 2. 构建命令

```powershell
cd PreMiles/方案文档
python build_doc.py            # 方案文档合集 PDF
python build_cumcm_paper.py    # 国赛版论文 PDF（HTML → Edge 打印 → 页码书签）
python build_cumcm_docx.py     # 国赛版论文 Word（公式 OMML）
```

三份产物同源：都从 `0X_*.md` 读取内容，`build_cumcm_paper.split_source()` 负责切分摘要/正文/参考文献，
因此改一处 Markdown 即可同步三份产物。

## 3. 校验命令（改完必跑）

```powershell
python ../audit_omml.py "out/论文_....docx"       # 公式结构审计：空上下标、命名空间、可疑分组
python verify_formula_content.py                  # 内容比对：源文公式 vs 生成 OMML，字符级一致
powershell -File check_docx_equations.ps1 -Path "out/论文_....docx" -LogPath out/_w.log
```

判定标准：`audit_omml.py` 报告的公式数 **等于** Word 里的 `OMaths` 数（可用 `check_docx_equations.ps1` 读到），
且空上下标为 0、`verify_formula_content.py` 可疑条目为 0。三者同时满足才算公式没问题。

## 4. 公式管线（OMML）踩过的坑

1. **命名空间必须是标准 OMML**：`http://schemas.openxmlformats.org/officeDocument/2006/math`。
   早期误用预发布命名空间 `http://schemas.microsoft.com/office/2004/12/omml`，Word（12.0）会把
   `m:oMath` 当作未知元素、**打开时整段丢弃**：公式位置只剩空白、`OMaths.Count = 0`、
   `doc.Content.Text` 变短、页数减少。此时 XML 依然完全合法，`lxml` 也能正常解析，
   极易被误判成"构建失败"或"Word 版本问题"。审计脚本会显式检查 `2004/12/omml` 残留。
2. **`xmlns:m` 只保留根元素上的一条**：`math_omml.fix_docx_namespaces()` 负责删除所有元素级声明
   并在 `<w:document>` 上补一条标准声明，同时校验 XML 仍然合法。
3. **公式末尾的括号不一定是注释**：只有"公式编号 `(6)`"或"含中文的说明"才算注释，
   否则 `abs(𝑟 / 𝑦)` 会被拆成 `abs` + 正文 `(𝑟 / 𝑦)`（内容丢失）。判定见 `_is_note()`。
4. **Markdown 残留会进公式**：`<br>`、`**` 必须在 `clean_formula_text()` 里清掉，
   否则 Word 会把它们当字面字符排版出来；带 `**` 的行（如 `**① 物理规则**：…`）不能判为展示式。
5. **全角分隔符要算"非操作数"**：`；，、：`（以及 `∈⊆∪∖` 等）必须在 `OP_TEXT` 里，
   否则 `𝑑_ij = ‖𝑍_i − 𝑍_j‖₂；base_i = …` 会被合并成一个"项"，
   出现 `(𝑋−μ)/σ；𝑑_ij` 这种分母越过语句分隔符的错误分式。
6. **粗体片段内部同样要转公式**：`render_inline()` 的粗体分支要走 `_render_plain(..., bold=True)`，
   否则 `**查准率 𝑃 = 1.000**` 里的 `𝑃 = 1.000` 只是一段粗体文本而不是公式对象。

## 5. 图片

`figures/make_figures.py` 生成 01/06 号文档里嵌入的全部插图，数据一律取自
`prototype/outputs/` 的真实产物与 `../_verify/` 的独立复现结果；改图后需重跑
`python build_doc.py` 与 `python build_cumcm_docx.py`。
