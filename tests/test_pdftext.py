"""PDF 抠字与正文补全的测试。

PDF 部分是手搓一份最小 PDF（明文 CMap + 一条 `/FT8 Tf <0001>Tj`），
不依赖网络也不依赖第三方库；补全部分把 `fetch` 换成假的。
"""

import os
import tempfile
import unittest
from unittest import mock

from signalwatch import enrich, pdftext

MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Contents 4 0 R
   /Resources << /Font << /FT8 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /FT8 12 Tf 72 700 Td <000100020003> Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+SimSun /ToUnicode 6 0 R >>
endobj
6 0 obj
<< /Length 120 >>
stream
/CIDInit /ProcSet findresource begin
12 dict begin begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfchar
<0001> <516C>
endbfchar
1 beginbfrange
<0002> <0003> <53F8>
endbfrange
endcmap end end
endstream
endobj
trailer
<< /Root 1 0 R /Size 7 >>
%%EOF
"""


class PdfTextTests(unittest.TestCase):
    def test_extracts_chinese_from_minimal_pdf(self):
        # bfchar 把 0001 映射成「公」；bfrange 的 <0002><0003><53F8> 只有一个目标串，
        # 按规范只覆盖首个码 0002 -> 「司」，0003 因此不产出字符。
        text = pdftext.extract(MINIMAL_PDF)
        self.assertEqual(text, "公司")

    def test_garbage_returns_empty_not_raise(self):
        self.assertEqual(pdftext.extract(b"not a pdf"), "")

    def test_literal_escapes(self):
        self.assertEqual(pdftext._decode_literal(b"a\\(b\\)c"), b"a(b)c")
        self.assertEqual(pdftext._decode_literal(b"\\101"), b"A")


class EnrichTests(unittest.TestCase):
    def setUp(self):
        from signalwatch import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, "t.db")
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        with db.guard(self.conn), self.conn:
            self.conn.execute(
                "INSERT INTO items (source, external_id, title, summary, published_at,"
                " first_seen_at, last_seen_at, fingerprint, content_hash, kind, url)"
                " VALUES ('cninfo','x1','某公告','', 1, 1, 1, 'fp1', 'h1', '金融',"
                " 'http://example.com/a.PDF')")
            self.conn.execute(
                "INSERT INTO items (source, external_id, title, summary, published_at,"
                " first_seen_at, last_seen_at, fingerprint, content_hash, kind, url)"
                " VALUES ('cninfo','x2','空文字层','', 2, 2, 2, 'fp2', 'h2', '金融',"
                " 'http://example.com/b.PDF')")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_fills_body_and_summary(self):
        with mock.patch.object(enrich, "fetch", lambda url, timeout=30.0: MINIMAL_PDF):
            result = enrich.enrich(self.conn, limit=5, pause=0)
        self.assertEqual(result["ok"], 2)
        row = self.conn.execute(
            "SELECT body_text, summary FROM items WHERE external_id = 'x1'").fetchone()
        self.assertIn("公", row["body_text"])
        self.assertTrue(row["summary"].strip())

    def test_failure_is_counted_not_raised(self):
        def boom(url, timeout=30.0):
            raise OSError("网络断了")

        with mock.patch.object(enrich, "fetch", boom):
            result = enrich.enrich(self.conn, limit=1, pause=0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["ok"], 0)

    def test_does_not_touch_items_without_url(self):
        with self.db.guard(self.conn), self.conn:
            self.conn.execute("UPDATE items SET url = '' WHERE external_id = 'x1'")
        with mock.patch.object(enrich, "fetch", lambda url, timeout=30.0: MINIMAL_PDF):
            result = enrich.enrich(self.conn, limit=5, pause=0)
        self.assertEqual(result["tried"], 1)  # 只有 x2 还有 http 链接


if __name__ == "__main__":
    unittest.main()
