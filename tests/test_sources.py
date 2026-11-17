"""源解析：用固定样本验证，不打网络。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch.sources.arxiv import ArxivSource  # noqa: E402
from signalwatch.sources.cninfo import CninfoSource  # noqa: E402
from signalwatch.sources.rss import RssSource, _parse_date  # noqa: E402

CNINFO_SAMPLE = {
    "announcements": [
        {
            "secCode": "688506",
            "secName": "百利天恒",
            "announcementId": "1225672396",
            "announcementTitle": "关于纳入医保目录的公告",
            "announcementTime": 1794758400000,
            "adjunctUrl": "finalpage/2026-11-16/1225672396.PDF",
            "pageColumn": "SHKCB",
        },
        {
            "secCode": None,
            "announcementId": "",
            "announcementTitle": "坏数据：没有 id",
            "announcementTime": 1794758400000,
        },
    ]
}

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>芯片公司发布新架构</title><link>https://example.com/a</link>
<guid>https://example.com/a</guid><pubDate>Mon, 10 Nov 2026 08:00:00 +0800</pubDate>
<description>一句话摘要</description></item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2611.00001v1</id><title>On Efficient
  Attention</title><published>2026-11-10T12:00:00Z</published>
  <summary>We study attention.</summary>
  <link href="http://arxiv.org/abs/2611.00001v1"/></entry>
</feed>"""


class CninfoParseTests(unittest.TestCase):
    def test_parse_fields(self):
        src = CninfoSource(columns=("szse",))
        rows = [src._to_record(x) for x in CNINFO_SAMPLE["announcements"]]
        good = [r for r in rows if r is not None]
        self.assertEqual(len(good), 1)
        r = good[0]
        self.assertEqual(r.external_id, "1225672396")
        self.assertEqual(r.sec_code, "688506")
        self.assertTrue(r.url.startswith("http://static.cninfo.com.cn/finalpage/"))
        self.assertEqual(r.published_at, 1794758400000)

    def test_bad_item_skipped(self):
        src = CninfoSource()
        self.assertIsNone(src._to_record(CNINFO_SAMPLE["announcements"][1]))


class RssParseTests(unittest.TestCase):
    def test_control_chars_are_cleaned(self):
        from signalwatch.sources.rss import _clean

        self.assertEqual(_clean("a\x00b\x0cc\ufeffd"), "abcd")

    def test_website_instead_of_feed_is_an_error_not_empty_success(self):
        from signalwatch.sources import rss

        src = rss.RssSource(name="bad", feed_url="http://example.invalid")
        orig = rss.http_get

        rss.http_get = lambda *a, **k: "<!DOCTYPE html><html><body>不是 feed</body></html>"
        try:
            with self.assertRaises(ValueError):
                src.fetch(None, 10)
        finally:
            rss.http_get = orig

    def test_date_parsing(self):
        self.assertGreater(_parse_date("Mon, 10 Nov 2026 08:00:00 +0800"), 0)
        self.assertGreater(_parse_date("2026-11-10T12:00:00Z"), 0)
        self.assertEqual(_parse_date(""), 0)

    def test_symbol_present(self):
        self.assertTrue(hasattr(RssSource, "fetch"))


class HtmlListParseTests(unittest.TestCase):
    """新增的两个政府源用的是同一套 href 解析，这里用固定 HTML 验证。"""

    HTML = """
    <html><body>
      <a href="./202611/t20261115_123456.html">2026年10月份国民经济运行情况发布</a>
      <a href="/sj/zxfb/202611/t20261001_999.html">9月份工业生产者出厂价格变动情况</a>
      <a href="/about.html">研究所简介</a>
      <a href="/202611/t20261120_777.shtml">短</a>
    </body></html>
    """

    def test_parse_extracts_title_date_and_url(self):
        from signalwatch.sources.html_list import HtmlListSource

        src = HtmlListSource(name="stats", list_url="https://www.stats.gov.cn/sj/zxfb/")
        rows = src.parse(self.HTML)
        titles = [r.title for r in rows]
        self.assertIn("2026年10月份国民经济运行情况发布", titles)
        self.assertIn("9月份工业生产者出厂价格变动情况", titles)
        # 非文章链接和过短标题要丢掉
        self.assertNotIn("研究所简介", titles)
        self.assertEqual(len(rows), 2)

    def test_url_is_absolute_and_time_from_filename(self):
        from signalwatch.sources.html_list import HtmlListSource

        src = HtmlListSource(name="stats", list_url="https://www.stats.gov.cn/sj/zxfb/")
        row = src.parse(self.HTML)[0]
        self.assertTrue(row.url.startswith("https://www.stats.gov.cn/"))
        self.assertGreater(row.published_at, 0)

    def test_page_too_short_is_an_error(self):
        from signalwatch.sources import html_list

        src = html_list.HtmlListSource(name="x", list_url="http://example.invalid")
        orig = html_list.http_get
        html_list.http_get = lambda *a, **k: "<html></html>"
        try:
            with self.assertRaises(ValueError):
                src.fetch(None, 10)
        finally:
            html_list.http_get = orig


class ApiContractTests(unittest.TestCase):
    def test_app_factory_exposes_routes(self):
        from signalwatch.api import create_app
        import tempfile

        from signalwatch.config import Settings

        tmp = Path(tempfile.mkdtemp())
        app = create_app(Settings(home=tmp, db_path=tmp / "a.db"), autostart=False)
        paths = {r.path for r in app.routes}
        for p in ("/", "/api/health", "/api/items", "/api/stats", "/api/sources", "/api/crawl"):
            self.assertIn(p, paths)
        self.assertIn("/manifest.webmanifest", paths)
        self.assertIn("/sw.js", paths)

    def test_static_assets_exist(self):
        web = Path(__file__).resolve().parents[1] / "src" / "signalwatch" / "web"
        for name in ("index.html", "manifest.webmanifest", "sw.js"):
            self.assertTrue((web / name).is_file(), name)
        manifest = json.loads((web / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(manifest["icons"])


if __name__ == "__main__":
    unittest.main()
