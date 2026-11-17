"""正文补抓：网页与 PDF 两条路都要能抠出东西，且不许把脚本/导航混进来。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalwatch import db, enrich


class HtmlTextTests(unittest.TestCase):
    def test_article_wins_and_script_is_dropped(self) -> None:
        raw = ("<html><body><nav>导航条导航条</nav><script>var a=1;</script>"
               "<article><h1>标题</h1><p>第一段。</p><p>第二段。</p></article>"
               "<footer>版权</footer></body></html>")
        text = enrich.html_to_text(raw)
        self.assertIn("第一段。", text)
        self.assertIn("第二段。", text)
        self.assertNotIn("var a=1", text)
        self.assertNotIn("导航条导航条", text)

    def test_falls_back_to_whole_page(self) -> None:
        raw = "<div><p>没有 article 标签的一段话。</p></div>"
        self.assertIn("没有 article 标签的一段话。", enrich.html_to_text(raw))

    def test_entities_and_whitespace(self) -> None:
        raw = "<p>a &amp; b&nbsp;&nbsp;c</p>"
        text = enrich.html_to_text(raw)
        self.assertIn("a & b", text)
        self.assertNotIn("\xa0", text)

    def test_limit_is_respected(self) -> None:
        raw = "<p>" + "字" * 9000 + "</p>"
        self.assertEqual(len(enrich.html_to_text(raw, limit=100)), 100)

    def test_site_chrome_is_trimmed(self) -> None:
        raw = ("<div>首页 IT圈 最会买 设置 日夜间</div>"
               "<div>这是真正的一段正文，长度足够长，应该被保留下来而不是当成导航丢掉，"
               "所以这里多写一点字凑够六十个字符以上让启发式能认出它是正文。</div>")
        text = enrich.html_to_text(raw)
        self.assertNotIn("最会买", text)
        self.assertIn("真正的一段正文", text)

    def test_empty_input(self) -> None:
        self.assertEqual(enrich.html_to_text(""), "")


class DispatchTests(unittest.TestCase):
    def test_pdf_and_html_routes(self) -> None:
        with mock.patch.object(enrich, "fetch", return_value=b"%PDF-1.4 x") as f, \
                mock.patch.object(enrich, "text_of", return_value="PDF 正文"):
            text, size = enrich.text_of_url("http://x/a.pdf", "pdf")
            self.assertEqual(text, "PDF 正文")
            self.assertEqual(size, len(b"%PDF-1.4 x"))
            f.assert_called_once()

        with mock.patch.object(enrich, "http_get", return_value="<p>网页正文</p>") as g:
            text, size = enrich.text_of_url("http://x/a.html", "html")
            self.assertIn("网页正文", text)
            self.assertGreater(size, 0)
            g.assert_called_once()

    def test_skip_kind_does_nothing(self) -> None:
        self.assertEqual(enrich.text_of_url("http://x/a", "skip"), ("", 0))

    def test_source_kind_covers_every_default_source(self) -> None:
        from signalwatch.sources import default_sources

        names = {s.name for s in default_sources()}
        self.assertEqual(names - set(enrich.SOURCE_KIND), set())


class EnrichRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _item(self, source: str, url: str) -> int:
        with db.guard(self.conn), self.conn:
            cur = self.conn.execute(
                "INSERT INTO items(fingerprint, source, external_id, kind, title, url,"
                " content_hash, published_at, first_seen_at, last_seen_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"fp-{url}", source, "e1", "announcement", "标题", url, "h", 1, 1, 1))
            return int(cur.lastrowid)

    def test_enrich_writes_body_for_html_source(self) -> None:
        item_id = self._item("ithome", "http://example.com/a.html")
        with mock.patch.object(enrich, "http_get", return_value="<p>正文在此。</p>"):
            out = enrich.enrich(self.conn, limit=5, only=("ithome",), pause=0)
        self.assertEqual(out["ok"], 1)
        row = self.conn.execute("SELECT body_text, body_bytes FROM items WHERE id = ?",
                                (item_id,)).fetchone()
        self.assertIn("正文在此。", row["body_text"])
        self.assertGreater(row["body_bytes"], 0)

    def test_failure_is_counted_not_raised(self) -> None:
        self._item("ithome", "http://example.com/b.html")
        with mock.patch.object(enrich, "http_get", side_effect=OSError("boom")):
            out = enrich.enrich(self.conn, limit=5, only=("ithome",), pause=0)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["ok"], 0)

    def test_default_scope_skips_arxiv(self) -> None:
        self._item("arxiv", "http://example.com/c")
        with mock.patch.object(enrich, "http_get", return_value="<p>x</p>") as g:
            out = enrich.enrich(self.conn, limit=5, pause=0)
        self.assertEqual(out["tried"], 0)
        g.assert_not_called()
