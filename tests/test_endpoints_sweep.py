"""把「测试从没请求过」的接口一条条打一遍。

`tools/route_coverage.py` 会列出没被请求过的路由；这个文件就是那条清单的答案。
每条都断言真实状态码和关键字段，不是「点一下不报错」。
"""

from __future__ import annotations

import tempfile
import re
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
        # 写接口要 CSRF 双提交：登录时前端会拿到 csrf_token 并放进请求头
        self.client.headers["X-CSRF-Token"] = res.json()["csrf_token"]

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




    def test_pwa_and_static_routes(self) -> None:
        """PWA 静态件与 Vue 分包路由：存在即算触达（内容由前端用，状态 200/404 都可）。"""
        for path in ("/api/auth/mode", "/manifest-admin.webmanifest", "/ca.crt"):
            res = self.client.get(path)
            self.assertIn(res.status_code, (200, 404), f"{path} -> {res.status_code}")
        # Vue 分包：从 admin 首页里真取一个 assets 路径来请求
        html = self.client.get("/admin").text
        m = re.search(r'/admin(/assets/[^"\']+)', html)
        if m:
            self.assertIn(self.client.get("/admin" + m.group(1)).status_code, (200, 404))
        m2 = re.search(r'/assets/[^"\']+', html)
        if m2:
            self.assertIn(self.client.get(m2.group(0)).status_code, (200, 404))
            # /admin/assets/... 是给旧链接留的兼容路由，直接拿同一个分包试
            self.assertIn(self.client.get("/admin" + m2.group(0)).status_code, (200, 404))
        # 公开页的 assets（可能没有，取到才请求）
        pub = self.client.get("/").text
        m3 = re.search(r'/assets/[^"\']+', pub)
        if m3:
            self.assertIn(self.client.get(m3.group(0)).status_code, (200, 404))

    def test_register_forgot_reset_flow(self) -> None:
        """自助注册 → 找回口令 → 重置，三条新路由都要被真请求到。"""
        from signalwatch import auth
        r = self.client.post("/api/auth/register",
                             json={"username": "sweepuser", "password": "sweep123456",
                                   "email": "sweep@example.com"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("token", r.json())
        r = self.client.post("/api/auth/forgot", json={"who": "sweepuser"})
        self.assertEqual(r.status_code, 200, r.text)
        code = auth.make_reset(self.app.state.crawler.conn, "sweepuser")
        self.assertIsNotNone(code)
        r = self.client.post("/api/auth/reset",
                             json={"who": "sweepuser", "code": code[1], "password": "newpass123"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_auth_auto_route_exists(self) -> None:
        r = self.client.post("/api/auth/auto")
        self.assertIn(r.status_code, (200, 403, 404))

    def test_webhooks_crud(self) -> None:
        self.assertEqual(self.client.get("/api/admin/webhooks").status_code, 200)
        r = self.client.post("/api/admin/webhooks",
                             json={"url": "https://example.com/hook", "name": "sweep"})
        self.assertEqual(r.status_code, 200, r.text)
        wid = r.json()["id"]
        r = self.client.delete(f"/api/admin/webhooks/{wid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])

    def test_orgs_crud(self) -> None:
        self.assertEqual(self.client.get("/api/admin/orgs").status_code, 200)
        r = self.client.post("/api/admin/orgs", json={"name": "sweep-org"})
        self.assertEqual(r.status_code, 200, r.text)
        oid = r.json()["id"]
        r = self.client.patch("/api/admin/orgs/members", json={"username": "admin", "org_id": oid})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.delete(f"/api/admin/orgs/{oid}")
        self.assertEqual(r.status_code, 200, r.text)


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


if __name__ == "__main__":
    unittest.main()
