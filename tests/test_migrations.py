"""迁移台账：能不能回答「这个库跑过哪几步、什么时候跑的」。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from signalwatch import migrations
from signalwatch.db import SCHEMA_VERSION, connect, init_db


class MigrationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_fresh_database_records_every_step(self) -> None:
        versions = migrations.applied_versions(self.conn)
        self.assertEqual(versions, [v for v, _ in migrations.STEPS])
        self.assertEqual(max(versions), SCHEMA_VERSION)
        self.assertEqual(migrations.pending(self.conn, SCHEMA_VERSION), [])

    def test_sync_is_idempotent(self) -> None:
        before = migrations.applied_versions(self.conn)
        added = migrations.sync(self.conn, SCHEMA_VERSION)
        self.assertEqual(added, 0)
        self.assertEqual(migrations.applied_versions(self.conn), before)

    def test_status_reports_current_and_missing(self) -> None:
        info = migrations.status(self.conn, SCHEMA_VERSION)
        self.assertEqual(info["current_version"], SCHEMA_VERSION)
        self.assertEqual(info["recorded_max"], SCHEMA_VERSION)
        self.assertEqual(len(info["applied"]), len(migrations.STEPS))
        self.assertEqual(info["missing"], [])
        self.assertTrue(all(r["applied_at"] > 0 for r in info["applied"]))

    def test_legacy_database_without_ledger_is_bootstrapped(self) -> None:
        """老库：schema_meta 说是第 9 版，但根本没有台账表。"""
        legacy = Path(self.tmp.name) / "legacy.db"
        conn = connect(legacy)
        init_db(conn)
        conn.execute("DROP TABLE schema_migrations")
        conn.commit()
        conn.close()

        conn = connect(legacy)
        self.assertFalse(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
        )
        added = migrations.sync(conn, SCHEMA_VERSION)
        self.assertEqual(added, len(migrations.STEPS))
        self.assertEqual(migrations.pending(conn, SCHEMA_VERSION), [])
        conn.close()

    def test_missing_step_is_detected(self) -> None:
        """台账里少了 v9，--check 就该红。"""
        self.conn.execute("DELETE FROM schema_migrations WHERE version = 9")
        self.conn.commit()
        info = migrations.status(self.conn, SCHEMA_VERSION)
        self.assertEqual([m["version"] for m in info["missing"]], [9])
        self.assertEqual(info["recorded_max"], 8)

    def test_future_version_in_ledger_is_not_reported_as_pending(self) -> None:
        migrations.record(self.conn, 99, "未来的版本")
        self.assertEqual(migrations.pending(self.conn, SCHEMA_VERSION), [])


if __name__ == "__main__":
    unittest.main()
