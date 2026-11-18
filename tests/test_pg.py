"""PostgreSQL 适配层的方言翻译测试：不连真实 PG，只验证 SQL 翻译正确。"""
from __future__ import annotations

import unittest

from signalwatch.pg import _needs_returning, translate, translate_ddl


class TranslateTests(unittest.TestCase):
    def test_pragma_becomes_noop(self) -> None:
        for sql in ("PRAGMA busy_timeout=30000", "PRAGMA journal_mode=WAL",
                    "PRAGMA foreign_keys=ON"):
            self.assertEqual(translate(sql), "")
        # PRAGMA table_info 是唯一的例外，映射到 information_schema 查列
        out = translate("PRAGMA table_info(items)")
        self.assertIn("information_schema.columns", out)
        self.assertIn("table_name = 'items'", out)

    def test_placeholder_question_to_percent(self) -> None:
        self.assertEqual(
            translate("SELECT * FROM items WHERE id = ? AND source = ?"),
            "SELECT * FROM items WHERE id = %s AND source = %s",
        )

    def test_insert_or_ignore(self) -> None:
        out = translate(
            "INSERT OR IGNORE INTO roles(name, rank, perms, builtin) VALUES(?,?,?,?)"
        )
        self.assertIn("INSERT INTO roles", out)
        self.assertIn("VALUES(%s,%s,%s,%s)", out)
        self.assertTrue(out.endswith(" ON CONFLICT DO NOTHING"))

    def test_insert_or_replace_app_meta(self) -> None:
        out = translate(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('auth_secret', ?)"
        )
        self.assertIn("INSERT INTO app_meta", out)
        self.assertIn("ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value", out)

    def test_insert_with_on_conflict_kept(self) -> None:
        sql = "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        out = translate(sql)
        self.assertIn("%s", out)
        self.assertIn("ON CONFLICT(key) DO UPDATE", out)

    def test_ddl_autoincrement_to_serial(self) -> None:
        out = translate_ddl(
            "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)"
        )
        self.assertIn("BIGSERIAL PRIMARY KEY", out)
        self.assertNotIn("AUTOINCREMENT", out)
        self.assertNotIn("INTEGER", out)

    def test_needs_returning(self) -> None:
        self.assertTrue(_needs_returning("INSERT INTO items(fingerprint) VALUES(?)"))
        self.assertTrue(_needs_returning("INSERT OR IGNORE INTO rules(keyword) VALUES(?)"))
        self.assertFalse(_needs_returning("INSERT INTO settings(key, value) VALUES(?,?)"))
        self.assertFalse(_needs_returning("SELECT * FROM items"))
        self.assertFalse(_needs_returning("INSERT INTO items(x) VALUES(?) RETURNING id"))


if __name__ == "__main__":
    unittest.main()
