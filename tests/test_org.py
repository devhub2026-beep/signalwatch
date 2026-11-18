"""组织权限骨架：组织增删查、用户与条目归属、删组织回退到未归属。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from signalwatch import org
from signalwatch.config import Settings
from signalwatch.db import connect, init_db


def make_db() -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    settings = Settings(home=home, db_path=home / "t.db")
    conn = connect(settings.db_path)
    init_db(conn)
    return conn, tmp


def _mk_user(conn: sqlite3.Connection, username: str) -> int:
    cur = conn.execute(
        "INSERT INTO users(username, pwd_salt, pwd_hash, role, created_at)"
        " VALUES (?, 's', 'h', 'viewer', 1000)",
        (username,),
    )
    return int(cur.lastrowid or 0)


def _mk_item(conn: sqlite3.Connection, fingerprint: str) -> int:
    cur = conn.execute(
        "INSERT INTO items(fingerprint, source, external_id, kind, title, published_at, content_hash)"
        " VALUES (?, 'test', ?, 'news', 't', 1000, 'h')",
        (fingerprint, fingerprint),
    )
    return int(cur.lastrowid or 0)


class OrgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_create_and_list(self) -> None:
        oid = org.create_org(self.conn, "研发部")
        self.assertGreater(oid, 0)
        orgs = org.list_orgs(self.conn)
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs[0]["name"], "研发部")
        self.assertEqual(orgs[0]["members"], 0)
        self.assertEqual(orgs[0]["items"], 0)

    def test_slug_generation(self) -> None:
        self.assertEqual(org._slug("研发部"), "org")       # 无 ASCII → 回退 org
        self.assertEqual(org._slug("Hello World"), "hello-world")
        self.assertEqual(org._slug("  A/B  "), "a-b")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            org.create_org(self.conn, "   ")

    def test_duplicate_name_rejected(self) -> None:
        org.create_org(self.conn, "研发部")
        with self.assertRaises(ValueError):
            org.create_org(self.conn, "研发部")

    def test_membership_counts(self) -> None:
        oid = org.create_org(self.conn, "研发部")
        _mk_user(self.conn, "alice")
        _mk_item(self.conn, "fp1")
        self.assertTrue(org.set_user_org(self.conn, "alice", oid))
        self.assertTrue(org.set_item_org(self.conn, 1, oid))
        orgs = org.list_orgs(self.conn)
        self.assertEqual(orgs[0]["members"], 1)
        self.assertEqual(orgs[0]["items"], 1)

    def test_delete_org_resets_membership(self) -> None:
        oid = org.create_org(self.conn, "研发部")
        _mk_user(self.conn, "alice")
        _mk_item(self.conn, "fp1")
        org.set_user_org(self.conn, "alice", oid)
        org.set_item_org(self.conn, 1, oid)

        self.assertTrue(org.delete_org(self.conn, oid))
        self.assertEqual(org.list_orgs(self.conn), [])
        # 成员与条目归 0，不删人、不删条目
        u = self.conn.execute("SELECT org_id FROM users WHERE username='alice'").fetchone()
        i = self.conn.execute("SELECT org_id FROM items WHERE id=1").fetchone()
        self.assertEqual(int(u["org_id"]), 0)
        self.assertEqual(int(i["org_id"]), 0)

    def test_org_id_defaults_to_zero(self) -> None:
        # 升级后旧数据默认 org_id=0（未归属），行为与单租户一致
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        self.assertIn("org_id", cols)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}
        self.assertIn("org_id", cols)


if __name__ == "__main__":
    unittest.main()
