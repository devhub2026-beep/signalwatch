"""跨源去重的用例：判得准、别误判、别把同源也去重。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalwatch import dedupe
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.ingest import save_records
from signalwatch.sources.base import Record

BASE = 1_700_000_000_000


class SimilarityTests(unittest.TestCase):
    def test_normalize_drops_punctuation_and_noise(self) -> None:
        self.assertEqual(dedupe.normalize("关于《通威股份》2026年员工持股计划（草案）的公告"),
                         dedupe.normalize("通威股份 2026 年员工持股计划 草案"))

    def test_identical_titles_are_one(self) -> None:
        self.assertEqual(dedupe.similarity("通威股份2026年员工持股计划", "通威股份2026年员工持股计划"), 1.0)

    def test_different_titles_are_low(self) -> None:
        a = "通威股份关于召开2026年第一次临时股东会的通知"
        b = "南卫股份关于公司实际控制人被刑事拘留的公告"
        self.assertLess(dedupe.similarity(dedupe.normalize(a), dedupe.normalize(b)), 0.4)

    def test_short_title_is_not_compared(self) -> None:
        self.assertEqual(dedupe.bigrams(""), set())


class MarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.conn = connect(self.settings.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _add(self, source: str, title: str, at: int) -> None:
        save_records(self.conn, source, [
            Record(external_id=f"{source}-{at}-{title[:6]}", title=title, published_at=at,
                   summary="", url="https://example.com/x")
        ])

    def test_cross_source_duplicate_is_marked(self) -> None:
        self._add("cninfo", "通威股份关于2026年员工持股计划（草案）的公告", BASE)
        self._add("ithome", "通威股份 2026 年员工持股计划草案", BASE + 3600_000)
        marked = dedupe.mark_duplicates(self.conn)
        self.assertEqual(marked, 1)
        rows = self.conn.execute("SELECT id, title, dup_of FROM items ORDER BY id").fetchall()
        self.assertEqual(rows[0]["dup_of"], 0)
        self.assertEqual(rows[1]["dup_of"], rows[0]["id"])

    def test_same_source_is_never_deduped(self) -> None:
        self._add("cninfo", "关于子公司经营管理情况的进展公告", BASE)
        self._add("cninfo", "关于子公司经营管理情况的进展公告", BASE + 1000)
        self.assertEqual(dedupe.mark_duplicates(self.conn), 0)

    def test_far_apart_in_time_is_not_duplicate(self) -> None:
        self._add("cninfo", "通威股份2026年员工持股计划", BASE)
        self._add("ithome", "通威股份2026年员工持股计划", BASE + 30 * 24 * 3600_000)
        self.assertEqual(dedupe.mark_duplicates(self.conn), 0)

    def test_stats_counts(self) -> None:
        self._add("cninfo", "通威股份关于2026年员工持股计划（草案）的公告", BASE)
        self._add("ithome", "通威股份 2026 年员工持股计划草案", BASE + 1000)
        dedupe.mark_duplicates(self.conn)
        s = dedupe.stats(self.conn)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["duplicates"], 1)
        self.assertEqual(s["rate"], 50.0)

    def test_query_hides_duplicates_by_default(self) -> None:
        from signalwatch.ingest import query_items

        self._add("cninfo", "通威股份关于2026年员工持股计划（草案）的公告", BASE)
        self._add("ithome", "通威股份 2026 年员工持股计划草案", BASE + 1000)
        dedupe.mark_duplicates(self.conn)
        self.assertEqual(len(query_items(self.conn, limit=10)), 1)
        self.assertEqual(len(query_items(self.conn, limit=10, include_dups=True)), 2)


if __name__ == "__main__":
    unittest.main()
