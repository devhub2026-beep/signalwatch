"""备份、恢复、清理：这三件事出错比没做还糟，所以每一步都钉住。"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from signalwatch import maintenance
from signalwatch.config import Settings
from signalwatch.db import connect, init_db


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(home=Path(self.tmp.name))
        self.conn = connect(self.settings.db_path)
        init_db(self.conn)
        self.conn.execute(
            "INSERT INTO items(fingerprint, source, external_id, kind, title, published_at,"
            " first_seen_at, content_hash) VALUES('fp1','cninfo','e1','fin','测试条目',1,1,'h')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_backup_creates_file_with_data(self) -> None:
        target = maintenance.backup(self.conn, self.settings, keep=5)
        self.assertTrue(target.exists())
        self.assertGreater(target.stat().st_size, 0)
        c = sqlite3.connect(str(target))
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
        finally:
            c.close()

    def test_prune_keeps_only_n(self) -> None:
        for _ in range(4):
            maintenance.backup(self.conn, self.settings, keep=2)
            time.sleep(1.05)  # 文件名精确到秒，隔开才不覆盖
        files = list(maintenance.backup_dir(self.settings).glob("backup-*.db"))
        self.assertEqual(len(files), 2)

    def test_restore_round_trip_and_safety_copy(self) -> None:
        target = maintenance.backup(self.conn, self.settings)
        with self.conn:
            self.conn.execute("DELETE FROM items")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
        self.conn.close()
        safety = maintenance.restore(target, self.settings)
        self.assertTrue(safety.exists())
        again = connect(self.settings.db_path)
        self.assertEqual(again.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
        again.close()

    def test_restore_rejects_foreign_file(self) -> None:
        junk = Path(self.tmp.name) / "junk.db"
        c = sqlite3.connect(str(junk))
        try:
            c.execute("CREATE TABLE other(x)")
            c.commit()
        finally:
            c.close()
        with self.assertRaises(ValueError):
            maintenance.restore(junk, self.settings)

    def test_restore_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            maintenance.restore(Path(self.tmp.name) / "nope.db", self.settings)

    def test_last_backup_age(self) -> None:
        self.assertEqual(maintenance.last_backup_age(self.settings), float("inf"))
        maintenance.backup(self.conn, self.settings)
        self.assertLess(maintenance.last_backup_age(self.settings), 60)


if __name__ == "__main__":
    unittest.main()
