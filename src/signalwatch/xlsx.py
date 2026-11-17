"""最小的 xlsx 写出器：只用标准库，不引第三方。

为什么手写：这台机器装不了包（pip 不通 PyPI），而「导出 Excel」是报表的基本要求。
xlsx 本质是一个 zip，里面几份 XML；这里只覆盖我们真正要用的东西：
多工作表、表头加粗、列宽、数字与文本两种单元格。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
)
_TAIL = "</worksheet>"


def _col(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(row: int, col: int, value) -> str:
    ref = f"{_col(col)}{row}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = "" if value is None else str(value)
    if text == "":
        return ""
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(rows: list[list], widths: list[int] | None = None) -> str:
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)
        ) + "</cols>"
    body = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(_cell(r, c, v) for c, v in enumerate(row))
        style = ' s="1"' if r == 1 else ""
        body.append(f'<row r="{r}"{style}>{cells}</row>')
    return _HEAD + cols + "<sheetData>" + "".join(body) + "</sheetData>" + _TAIL


def build(sheets: list[tuple[str, list[list], list[int] | None]]) -> bytes:
    """sheets = [(名字, 行, 列宽列表)]，返回 xlsx 字节。"""
    buf = io.BytesIO()
    names = [s[0] for s in sheets]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(
                       f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml"'
                       ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       for i in range(len(sheets))
                   )
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                   ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   "<sheets>" + "".join(
                       f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                       for i, n in enumerate(names)
                   ) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(
                       f'<Relationship Id="rId{i + 1}"'
                       ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
                       f' Target="worksheets/sheet{i + 1}.xml"/>'
                       for i in range(len(sheets))
                   )
                   + f'<Relationship Id="rId{len(sheets) + 1}"'
                   ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
                   ' Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                   "</styleSheet>")
        for i, (_, rows, widths) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(rows, widths))
    return buf.getvalue()
