"""分类：入库即归类、老条目回填、未登记的源不硬塞。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalwatch import admin, db, taxonomy
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.ingest import save_records
from signalwatch.sources.base import Record


def rec(title: str) -> Record:
    return Record(external_id=f"x-{title[:8]}", title=title, published_at=1_700_000_000_000,
                  summary="", url="https://example.com/x")


class TaxonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.conn = connect(self.settings.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _cats(self, item_id: int) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT c.name FROM item_cat ic JOIN categories c ON c.id = ic.category_id"
            " WHERE ic.item_id = ?", (item_id,))]

    def test_new_item_is_categorised_on_insert(self) -> None:
        save_records(self.conn, "cninfo", [rec("某公司关于回购股份的公告")])
        item_id = self.conn.execute("SELECT id FROM items").fetchone()[0]
        self.assertEqual(self._cats(item_id), ["金融"])

    def test_each_source_maps_to_its_category(self) -> None:
        pairs = [("cninfo", "金融"), ("arxiv", "学术"), ("arxiv-fin", "金融"),
                 ("ithome", "科技"), ("sspai", "科技"), ("stats", "政策"), ("ccgp", "政策")]
        for source, _ in pairs:
            save_records(self.conn, source, [rec(f"{source}-标题一")])
        for source, expect in pairs:
            item_id = self.conn.execute(
                "SELECT id FROM items WHERE source = ?", (source,)).fetchone()[0]
            self.assertEqual(self._cats(item_id), [expect], source)

    def test_unknown_source_is_not_forced(self) -> None:
        save_records(self.conn, "somefeed", [rec("未知来源的标题")])
        item_id = self.conn.execute("SELECT id FROM items").fetchone()[0]
        self.assertEqual(self._cats(item_id), [])

    def test_backfill_covers_legacy_rows(self) -> None:
        save_records(self.conn, "ithome", [rec("科技新闻一")])
        with self.conn:
            self.conn.execute("DELETE FROM item_cat")   # 假装是升级前的老库
        self.assertEqual(taxonomy.backfill(self.conn), 1)

    def test_backfill_is_idempotent(self) -> None:
        save_records(self.conn, "stats", [rec("统计公报")])
        self.assertEqual(taxonomy.backfill(self.conn), 0)

    def test_auto_categorize_delegates(self) -> None:
        save_records(self.conn, "arxiv", [rec("论文一")])
        with self.conn:
            self.conn.execute("DELETE FROM item_cat")
        self.assertEqual(admin.auto_categorize(self.conn), 1)
        self.assertEqual(admin.auto_categorize(self.conn), 0)

    def test_default_categories_are_seeded(self) -> None:
        names = [r[0] for r in self.conn.execute("SELECT name FROM categories ORDER BY sort")]
        self.assertEqual(names, list(taxonomy.DEFAULT_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
