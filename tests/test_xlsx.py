"""Excel 导出：只有标准库，但必须是能打开的 xlsx。"""

from __future__ import annotations

import io
import unittest
import zipfile

from signalwatch import xlsx


class XlsxTests(unittest.TestCase):
    def test_package_structure(self) -> None:
        blob = xlsx.build([("条目", [["时间", "标题"], [1, "甲"]], [20, 40])])
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                     "xl/worksheets/sheet1.xml", "xl/styles.xml"):
            self.assertIn(part, names)

    def test_two_sheets_get_two_parts(self) -> None:
        blob = xlsx.build([("按来源", [["a"]], None), ("按天", [["b"]], None)])
        z = zipfile.ZipFile(io.BytesIO(blob))
        self.assertIn("xl/worksheets/sheet2.xml", z.namelist())
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('name="按来源"', wb)
        self.assertIn('name="按天"', wb)

    def test_cells_escape_and_numbers(self) -> None:
        blob = xlsx.build([("s", [["标题", 42], ["a & <b>", 3.5]], None)])
        sheet = zipfile.ZipFile(io.BytesIO(blob)).read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("a &amp; &lt;b&gt;", sheet)
        self.assertIn("<v>42</v>", sheet)
        self.assertIn("<v>3.5</v>", sheet)

    def test_empty_cells_are_skipped(self) -> None:
        blob = xlsx.build([("s", [["a", "", None]], None)])
        sheet = zipfile.ZipFile(io.BytesIO(blob)).read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('r="A1"', sheet)
        self.assertNotIn('r="B1"', sheet)

    def test_column_reference_beyond_z(self) -> None:
        self.assertEqual(xlsx._col(0), "A")
        self.assertEqual(xlsx._col(25), "Z")
        self.assertEqual(xlsx._col(26), "AA")
        self.assertEqual(xlsx._col(27), "AB")


if __name__ == "__main__":
    unittest.main()
