"""告警与自动补抓正文：两条「在后台自己发生的事」的用例。

这两条的共同点是「用户不在场」，所以测试里只验证「该触发时触发、不该触发时
一次都不触发」，不验证邮件服务器和 PDF 下载（那是另外两个模块的事）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalwatch import admin, notify
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.scheduler import Crawler, TickResult


class FakeSource:
    name = "cninfo"
    kind = "announcement"

    def fetch(self, cursor, limit):  # pragma: no cover - 用例里不真的抓
        raise RuntimeError("should not be called")


def make_crawler(tmp: str) -> Crawler:
    settings = Settings(home=Path(tmp), min_interval=0.0)
    conn = connect(settings.db_path)
    init_db(conn)
    crawler = Crawler.__new__(Crawler)  # 不跑 __init__，避开真实源与网络
    crawler.settings = settings
    crawler.conn = conn
    crawler.sources = {"cninfo": FakeSource()}
    crawler._paused = set()
    crawler._paused_sources = {}
    from signalwatch.sources.base import RateLimiter

    crawler.limiter = RateLimiter(0.0)
    return crawler


class AlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.crawler = make_crawler(self.tmp.name)
        self.conn = self.crawler.conn

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _ready(self):
        return mock.patch.object(notify, "is_ready", return_value=True)

    def test_no_mail_when_threshold_not_reached(self) -> None:
        with self._ready() as ready, mock.patch.object(notify, "send") as send:
            self.crawler._alert_failure("cninfo", 1, "boom")
        send.assert_not_called()
        ready.assert_not_called()

    def test_mail_sent_once_per_source_per_window(self) -> None:
        with self._ready(), mock.patch.object(notify, "send") as send:
            self.crawler._alert_failure("cninfo", 3, "boom")
            self.crawler._alert_failure("cninfo", 4, "boom again")
        self.assertEqual(send.call_count, 1)
        self.assertIn("cninfo", send.call_args[0][1])
        self.assertEqual(admin.get_setting(self.conn, "alert.last.cninfo", ""), 
                         admin.get_setting(self.conn, "alert.last.cninfo", "0"))

    def test_alert_can_be_switched_off(self) -> None:
        admin.set_setting(self.conn, "notify.alert", "0")
        with self._ready(), mock.patch.object(notify, "send") as send:
            self.crawler._alert_failure("cninfo", 5, "boom")
        send.assert_not_called()

    def test_no_mail_when_smtp_not_configured(self) -> None:
        with mock.patch.object(notify, "is_ready", return_value=False), \
                mock.patch.object(notify, "send") as send:
            self.crawler._alert_failure("cninfo", 5, "boom")
        send.assert_not_called()


class AutoEnrichTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.crawler = make_crawler(self.tmp.name)

    def tearDown(self) -> None:
        self.crawler.conn.close()
        self.tmp.cleanup()

    def test_enrich_runs_when_new_items_arrive(self) -> None:
        with mock.patch("signalwatch.enrich.enrich", return_value={}) as enrich:
            self.crawler._auto_enrich([TickResult("cninfo", True, 5, 3, 0, None)])
        enrich.assert_called_once()

    def test_enrich_skipped_when_nothing_new(self) -> None:
        with mock.patch("signalwatch.enrich.enrich") as enrich:
            self.crawler._auto_enrich([TickResult("cninfo", True, 0, 0, 2, None)])
        enrich.assert_not_called()

    def test_enrich_skipped_when_failed(self) -> None:
        with mock.patch("signalwatch.enrich.enrich") as enrich:
            self.crawler._auto_enrich([TickResult("cninfo", False, 0, 0, 0, "x")])
        enrich.assert_not_called()

    def test_enrich_can_be_switched_off(self) -> None:
        admin.set_setting(self.crawler.conn, "enrich.auto", "0")
        with mock.patch("signalwatch.enrich.enrich") as enrich:
            self.crawler._auto_enrich([TickResult("cninfo", True, 5, 3, 0, None)])
        enrich.assert_not_called()


if __name__ == "__main__":
    unittest.main()
