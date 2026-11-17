"""桌面端的纯逻辑：时间格式、行查询、增量判断（不测 Tk 本身）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch.db import connect, init_db  # noqa: E402
from signalwatch.desktop import DeskRow, human_time, rows_from_db  # noqa: E402


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


class RowQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = connect(Path(self.tmp.name) / "t.db")
        init_db(self.conn)
        for i, (title, at) in enumerate([("旧", ms(2026, 11, 10)), ("中", ms(2026, 11, 11)),
                                         ("新", ms(2026, 11, 12))]):
            with self.conn:
                self.conn.execute(
                    "INSERT INTO items(fingerprint, source, kind, external_id, title, summary, url,"
                    " published_at, first_seen_at, last_seen_at, content_hash, version)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                    (f"fp-i{i}", "cninfo", "announcement", f"e{i}", title, "摘要",
                     "https://example.com/x", at, at, at, f"h{i}"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_ordered_by_time_desc(self):
        rows = rows_from_db(self.conn)
        self.assertEqual([r.title for r in rows], ["新", "中", "旧"])
        self.assertEqual(rows[0].source, "cninfo")
        self.assertEqual(rows[0].version, 1)

    def test_limit(self):
        self.assertEqual(len(rows_from_db(self.conn, limit=2)), 2)

    def test_desk_row_is_plain(self):
        row = DeskRow(id=1, source="cninfo", title="t", published_at=0, url="", version=1)
        self.assertEqual(row.title, "t")


if __name__ == "__main__":
    unittest.main()
