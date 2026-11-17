"""桌面端的纯逻辑：时间格式、增量判断、（不测 Tk 本身）。"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch.db import init_db  # noqa: E402
from signalwatch.desktop import DeskRow, human_ago, human_time, new_ids, rows_from_db  # noqa: E402


def ms(y, mo, d, h=0, mi=0):
    import datetime

    return int(datetime.datetime(y, mo, d, h, mi).timestamp() * 1000)


class HumanTimeTests(unittest.TestCase):
    def test_midnight_shows_date(self):
        # 巨潮公告只给到某天，时间部分是 00:00
        self.assertEqual(human_time(ms(2026, 11, 16)), "11-16")

    def test_normal_shows_clock(self):
        self.assertEqual(human_time(ms(2026, 11, 15, 16, 53)), "16:53")

    def test_zero(self):
        self.assertEqual(human_time(0), "--:--")

    def test_human_ago(self):
        self.assertEqual(human_ago(3), "3 秒前")
        self.assertEqual(human_ago(150), "2 分钟前")
        self.assertEqual(human_ago(7200), "2 小时前")


class DiffTests(unittest.TestCase):
    def test_new_ids_only_returns_unseen(self):
        rows = [DeskRow(1, "a", "t", 0, "", 1), DeskRow(2, "a", "t", 0, "", 1)]
        self.assertEqual(new_ids([1], rows), [2])
        self.assertEqual(new_ids([1, 2], rows), [])
        self.assertEqual(new_ids([], rows), [1, 2])


class RowsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        with self.conn:
            for i, (src, ts, title) in enumerate(
                [("cninfo", 3000, "新"), ("ithome", 2000, "中"), ("sspai", 1000, "旧")]
            ):
                self.conn.execute(
                    "INSERT INTO items(fingerprint, source, kind, external_id, title, url,"
                    " published_at, content_hash, first_seen_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"fp{i}", src, "news", f"e{i}", title, "http://x", ts, "h", 0),
                )

    def tearDown(self):
        self.conn.close()

    def test_ordered_by_time_desc(self):
        rows = rows_from_db(self.conn)
        self.assertEqual([r.title for r in rows], ["新", "中", "旧"])
        self.assertEqual(rows[0].source, "cninfo")
        self.assertEqual(rows[0].version, 1)

    def test_limit(self):
        self.assertEqual(len(rows_from_db(self.conn, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
