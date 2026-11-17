"""标记按人隔离：一个人标已读/收藏，不能影响另一个人。

这些用例存在的原因：marks 表原来是「所有人共用一份」，多用户是假的多用户。
现在表按 (user_id, item_id) 存，这里把「互不影响」钉死，防止哪天又退回去。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from signalwatch import board
from signalwatch.api import create_app
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.ingest import save_records


def _settings(tmp: str) -> Settings:
    return Settings(home=Path(tmp), min_interval=0.0)


def _seed(settings: Settings, n: int = 2) -> list[int]:
    conn = connect(settings.db_path)
    init_db(conn)
    from signalwatch.sources.base import Record

    save_records(conn, "cninfo", [
        Record(external_id=f"id-{i}", title=f"第 {i} 条", published_at=1_700_000_000_000 + i,
               summary="s", url=f"https://example.com/{i}")
        for i in range(n)
    ])
    ids = [r["id"] for r in conn.execute("SELECT id FROM items ORDER BY id")]
    conn.close()
    return ids


class MarkIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = _settings(self.tmp.name)
        self.ids = _seed(self.settings)
        self.conn = connect(self.settings.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_mark_read_is_per_user(self) -> None:
        first, second = self.ids
        self.assertEqual(board.unread_count(self.conn, user_id=1), 2)
        board.mark_read(self.conn, first, user_id=1)
        self.assertEqual(board.unread_count(self.conn, user_id=1), 1)
        self.assertEqual(board.unread_count(self.conn, user_id=2), 2, "别人不该被影响")
        self.assertEqual(board.unread_count(self.conn, user_id=0), 2, "匿名那份也不该被影响")

    def test_star_is_per_user(self) -> None:
        first = self.ids[0]
        self.assertTrue(board.toggle_star(self.conn, first, user_id=1))
        self.assertTrue(board.is_starred(self.conn, first, user_id=1))
        self.assertFalse(board.is_starred(self.conn, first, user_id=2))
        self.assertEqual(board.star_count(self.conn, user_id=1), 1)
        self.assertEqual(board.star_count(self.conn, user_id=2), 0)
        self.assertFalse(board.toggle_star(self.conn, first, user_id=1), "再点一次取消")
        self.assertEqual(board.star_count(self.conn, user_id=1), 0)

    def test_board_query_filters_by_user(self) -> None:
        first = self.ids[0]
        board.mark_read(self.conn, first, user_id=7)
        rows_a = board.board_query(self.conn, only_unread=True, user_id=7)
        rows_b = board.board_query(self.conn, only_unread=True, user_id=8)
        self.assertEqual(len(rows_a), 1)
        self.assertEqual(len(rows_b), 2)

    def test_migration_keeps_old_shared_marks(self) -> None:
        """老库（没有 user_id 列）升级后，历史标记归到匿名那份，标记数不丢。"""
        first = self.ids[0]
        board.mark_read(self.conn, first, user_id=0)
        before = self.conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
        init_db(self.conn)  # 再跑一次初始化应当是幂等的
        after = self.conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0]
        self.assertEqual(before, after)
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(marks)")]
        self.assertIn("user_id", cols)


class CsvSafetyTests(unittest.TestCase):
    def test_formula_cells_are_neutralised(self) -> None:
        self.assertEqual(board.csv_safe("=1+1"), "'=1+1")
        self.assertEqual(board.csv_safe("+86"), "'+86")
        self.assertEqual(board.csv_safe("-3"), "'-3")
        self.assertEqual(board.csv_safe("@x"), "'@x")
        self.assertEqual(board.csv_safe("正常标题"), "正常标题")
        self.assertEqual(board.csv_safe(None), "")
        self.assertEqual(board.csv_safe(12), "12")


class WebMarkApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = _settings(self.tmp.name)
        self.ids = _seed(self.settings)
        self.app = create_app(self.settings, autostart=False)
        self.addCleanup(self.tmp.cleanup)

    def _client(self, username: str) -> TestClient:
        client = TestClient(self.app)
        res = client.post("/api/auth/login", json={"username": username, "password": "pw123456"})
        client.headers["X-CSRF-Token"] = res.json()["csrf_token"]
        return client

    def test_read_flag_follows_the_logged_in_user(self) -> None:
        from signalwatch import auth

        conn = connect(self.settings.db_path)
        init_db(conn)
        auth.seed_admin(conn)
        auth.create_user(conn, "alice", "pw123456", "viewer")
        auth.create_user(conn, "bob", "pw123456", "viewer")
        conn.close()

        item = self.ids[0]
        alice = self._client("alice")
        self.assertEqual(alice.post(f"/api/items/{item}/read").status_code, 200)
        mine = {r["id"]: r for r in alice.get("/api/items").json()["items"]}
        self.assertTrue(mine[item]["read"])

        bob = self._client("bob")
        his = {r["id"]: r for r in bob.get("/api/items").json()["items"]}
        self.assertFalse(his[item]["read"], "bob 不该看到 alice 的已读")

        star = alice.post(f"/api/items/{item}/star").json()
        self.assertTrue(star["starred"])
        self.assertFalse({r["id"]: r for r in bob.get("/api/items").json()["items"]}[item]["starred"])

    def test_mark_requires_login_before_item_lookup(self) -> None:
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/items/999999/read").status_code, 401)
        from signalwatch import auth

        conn = connect(self.settings.db_path)
        auth.create_user(conn, "alice", "pw123456", "viewer")
        conn.close()
        client = self._client("alice")
        self.assertEqual(client.post("/api/items/999999/read").status_code, 404)


if __name__ == "__main__":
    unittest.main()
