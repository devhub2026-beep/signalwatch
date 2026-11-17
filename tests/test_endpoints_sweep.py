"""把「测试从没请求过」的接口一条条打一遍。

`tools/route_coverage.py` 会列出没被请求过的路由；这个文件就是那条清单的答案。
每条都断言真实状态码和关键字段，不是「点一下不报错」。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from signalwatch.api import create_app
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch import auth


class EndpointSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name))
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

        self.password = "sweep-pass-1"
        conn = connect(self.settings.db_path)
        init_db(conn)
        auth.set_password(conn, "admin", self.password)
        conn.close()

        res = self.client.post("/api/auth/login",
                               json={"username": "admin", "password": self.password})
        self.assertEqual(res.status_code, 200)
        self.token = res.json()["token"]
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def _seed_item(self) -> int:
        conn = connect(self.settings.db_path)
        with conn:
            cur = conn.execute(
                "INSERT INTO items(kind, source, external_id, title, summary, url,"
                " content_hash, fingerprint, published_at, first_seen_at, last_seen_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("announcement", "cninfo", "S1", "扫尾测试条目", "摘要一个",
                 "https://example.com/a", "h1", "fp-sweep-1", 1000, 1000, 1000),
            )
            item_id = int(cur.lastrowid)
        conn.close()
        return item_id

    def test_timeline_page_serves_html(self) -> None:
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("signalwatch", res.text.lower())

    def test_item_text_endpoint(self) -> None:
        item_id = self._seed_item()
        res = self.client.get(f"/api/items/{item_id}/text", headers=self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIn("text", res.json())

    def test_admin_pin_history_timeline(self) -> None:
        item_id = self._seed_item()
        pin = self.client.post(f"/api/admin/items/{item_id}/pin", headers=self.auth)
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(pin.json()["pinned"])

        hist = self.client.get(f"/api/admin/items/{item_id}/history", headers=self.auth)
        self.assertEqual(hist.status_code, 200)
        self.assertIn("history", hist.json())

        line = self.client.get("/api/admin/timeline?code=600000", headers=self.auth)
        self.assertEqual(line.status_code, 200)
        self.assertEqual(line.json()["code"], "600000")

    def test_admin_roles_lists_permission_points(self) -> None:
        res = self.client.get("/api/admin/roles", headers=self.auth)
        self.assertEqual(res.status_code, 200)
        names = {r["name"] for r in res.json()["roles"]}
        self.assertTrue({"admin", "analyst", "auditor", "viewer"} <= names)

    def test_excel_exports(self) -> None:
        self._seed_item()
        items = self.client.get("/api/admin/items.xlsx", headers=self.auth)
        self.assertEqual(items.status_code, 200)
        self.assertEqual(items.content[:2], b"PK")

        rep = self.client.get("/api/report.xlsx?days=7", headers=self.auth)
        self.assertEqual(rep.status_code, 200)
        self.assertEqual(rep.content[:2], b"PK")

    def test_enrich_endpoint_is_wired(self) -> None:
        """不真的去下 PDF：把 enrich 换掉，只验路由、权限、返回结构。"""
        from unittest import mock

        fake = {"tried": 0, "ok": 0, "chars": 0, "empty": 0, "failed": 0}
        with mock.patch("signalwatch.enrich.enrich", return_value=fake):
            res = self.client.post("/api/admin/enrich?limit=1&source=cninfo", headers=self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_logout_clears_cookie(self) -> None:
        res = self.client.post("/api/auth/logout", headers={"X-CSRF-Token": self.client.cookies.get("sw_csrf")})
        self.assertEqual(res.status_code, 200)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 401, "登出后不该还能读到当前用户")

    def test_delete_user(self) -> None:
        made = self.client.post("/api/users", headers=self.auth,
                                json={"username": "sweep1", "password": "sweep1pass", "role": "viewer"})
        self.assertEqual(made.status_code, 201)
        gone = self.client.delete("/api/users/sweep1", headers=self.auth)
        self.assertEqual(gone.status_code, 200)
        self.assertTrue(gone.json()["ok"])

    def test_openapi_and_docs_are_reachable(self) -> None:
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/redoc").status_code, 200)
        self.assertLess(self.client.get("/docs/oauth2-redirect").status_code, 400)


if __name__ == "__main__":
    unittest.main()


class SseEndpointTests(unittest.TestCase):
    """SSE 只在真正被订阅时才「活」——所以单独开流、读第一帧、立刻关掉。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name))
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_events_stream_pushes_first_frame(self) -> None:
        with self.client.stream("GET", "/api/events/stats") as res:
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/event-stream", res.headers.get("content-type", ""))
            for line in res.iter_lines():
                if line.startswith("data:"):
                    self.assertIn("items", line)
                    break
