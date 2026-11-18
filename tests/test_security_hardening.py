"""13 项安全修复的专项回归：真实 API、受限 HTTP、恶意 XML 与导出。"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    from defusedxml.common import EntitiesForbidden
except ImportError:  # 这台机器装不上 defusedxml（pip 镜像卡死），这条用例只能跳过
    raise unittest.SkipTest("没有 defusedxml，跳过加固用例")
from fastapi.testclient import TestClient

from signalwatch import admin, audit, auth, notify
from signalwatch.api import create_app
from signalwatch.config import SETTINGS, Settings
from signalwatch.db import SCHEMA_VERSION, connect, init_db
from signalwatch.ingest import save_records
from signalwatch.sources.base import Record, http_get
from signalwatch.sources.rss import RssSource


STRONG_PASSWORD = "test-admin-pass"


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def seed_item(settings: Settings) -> int:
    conn = connect(settings.db_path)
    save_records(conn, "cninfo", [Record(
        external_id="security-1", title="安全回归条目", published_at=1_700_000_000_000,
        summary="摘要", url="https://example.com/security-1",
    )])
    item_id = int(conn.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()[0])
    conn.close()
    return item_id


class ApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", STRONG_PASSWORD)
        conn.close()
        self.item_id = seed_item(self.settings)
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        self.token = res.json()["token"]
        self.csrf = res.json()["csrf_token"]
        self.bearer = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def _token_for(self, username: str, role: str) -> str:
        conn = connect(self.settings.db_path)
        auth.create_user(conn, username, "role-pass-1", role=role)
        conn.close()
        res = TestClient(self.app).post("/api/auth/login", json={"username": username, "password": "role-pass-1"})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["token"]

    def test_anonymous_reads_and_writes_are_blocked_but_public_board_stays(self) -> None:
        anon = TestClient(self.app)
        self.assertEqual(anon.get("/api/items").status_code, 200)
        self.assertEqual(anon.get(f"/api/items/{self.item_id}/text").status_code, 401)
        self.assertEqual(anon.post(f"/api/items/{self.item_id}/read").status_code, 401)
        self.assertEqual(anon.post(f"/api/items/{self.item_id}/star").status_code, 401)
        self.assertEqual(anon.post("/api/crawl").status_code, 401)

    def test_viewer_can_mark_and_read_but_only_analyst_can_crawl(self) -> None:
        viewer = {"Authorization": f"Bearer {self._token_for('viewer-sec', 'viewer')}"}
        self.assertEqual(self.client.get(f"/api/items/{self.item_id}/text", headers=viewer).status_code, 200)
        self.assertEqual(self.client.post(f"/api/items/{self.item_id}/read", headers=viewer).status_code, 200)
        self.assertEqual(self.client.post(f"/api/items/{self.item_id}/star", headers=viewer).status_code, 200)
        self.assertEqual(self.client.post("/api/crawl", headers=viewer).status_code, 403)
        analyst = {"Authorization": f"Bearer {self._token_for('analyst-sec', 'analyst')}"}
        # 不存在的源走到 404，说明已通过 analyst 鉴权且没有发起真实抓取。
        self.assertEqual(self.client.post("/api/crawl?source=nope", headers=analyst).status_code, 404)

    def test_secure_cookies_and_double_submit_csrf(self) -> None:
        client = TestClient(self.app, base_url="https://testserver")
        res = client.post("/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        cookies = "\n".join(res.headers.get_list("set-cookie"))
        self.assertIn("sw_token=", cookies)
        self.assertIn("HttpOnly", cookies)
        self.assertIn("Secure", cookies)
        self.assertIn("SameSite=lax", cookies)
        self.assertIn("sw_csrf=", cookies)
        self.assertEqual(client.post(f"/api/items/{self.item_id}/read").status_code, 403)
        ok = client.post(f"/api/items/{self.item_id}/read", headers={"X-CSRF-Token": res.json()["csrf_token"]})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_batch_limit_and_500_response_do_not_leak_exception_class(self) -> None:
        too_many = self.client.post("/api/admin/items/state", headers=self.bearer,
                                    json={"ids": list(range(501)), "state": "online"})
        self.assertEqual(too_many.status_code, 422, too_many.text)
        with mock.patch("signalwatch.admin.item_page", side_effect=RuntimeError("internal detail")):
            no_raise = TestClient(self.app, raise_server_exceptions=False)
            res = no_raise.get("/api/admin/items", headers=self.bearer)
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("kind", res.json())
        self.assertNotIn("RuntimeError", res.text)

    def test_csp_external_assets_and_safe_url_guard(self) -> None:
        for path in ("/", "/admin"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200)
            csp = res.headers["content-security-policy"]
            self.assertNotIn("unsafe-inline", csp)
            self.assertIn("object-src 'none'", csp)
            self.assertNotIn("<style>", res.text)
            self.assertNotIn("<script>", res.text)
        for path in ("/index.css", "/index.js", "/admin.css", "/admin.js"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        js = self.client.get("/index.js").text
        self.assertIn("safeHttpUrl", js)
        self.assertIn('u.protocol === "http:" || u.protocol === "https:"', js)
        self.assertNotIn('href="${esc(it.url)}"', js)
        self.assertIn('rel="noopener noreferrer nofollow"', js)
        self.assertIn("must_change_password", self.client.get("/admin.js").text)


class DefaultPasswordPolicyTests(unittest.TestCase):
    def test_default_admin_must_change_password_before_admin_api(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = create_app(Settings(home=Path(tmp), min_interval=0.0), autostart=False)
            with TestClient(app) as client:
                res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertTrue(res.json()["user"]["must_change_password"])
                headers = {"Authorization": f"Bearer {res.json()['token']}"}
                blocked = client.get("/api/users", headers=headers)
                self.assertEqual(blocked.status_code, 403)
                self.assertIn("修改默认口令", blocked.text)
                changed = client.post("/api/auth/password", headers=headers,
                                      json={"old": "admin123", "new": STRONG_PASSWORD})
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(client.get("/api/auth/me", headers=headers).status_code, 401)
                fresh = client.post("/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD})
                self.assertFalse(fresh.json()["user"]["must_change_password"])
                fresh_headers = {"Authorization": f"Bearer {fresh.json()['token']}"}
                self.assertEqual(client.get("/api/users", headers=fresh_headers).status_code, 200)

    def test_schema_v10_and_legacy_default_password_detection(self) -> None:
        conn = memory_db()
        self.assertEqual(SCHEMA_VERSION, 11)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("must_change_password", columns)
        auth.seed_admin(conn)
        conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
        conn.commit()
        auth.enforce_default_password_policy(conn)
        self.assertEqual(auth.get_user(conn, "admin")["must_change_password"], 1)
        auth.set_password(conn, "admin", STRONG_PASSWORD)
        self.assertEqual(auth.get_user(conn, "admin")["must_change_password"], 0)
        conn.close()


class ExportAndNotifyTests(unittest.TestCase):
    def test_all_item_and_audit_csv_cells_are_formula_safe(self) -> None:
        conn = memory_db()
        save_records(conn, "cninfo", [Record(
            external_id="csv-1", title="=2+2", published_at=1_700_000_000_000,
            summary="s", url="@malicious", sec_code="+123", sec_name="-cmd",
        )])
        items = admin.export_items_csv(conn)
        self.assertIn("'=2+2", items)
        self.assertIn("'+123", items)
        self.assertIn("'-cmd", items)
        self.assertIn("'@malicious", items)
        audit.log(conn, "=attacker", "+action", "-target", "@detail")
        exported = audit.export_csv(conn)
        for cell in ("'=attacker", "'+action", "'-target", "'@detail"):
            self.assertIn(cell, exported)
        conn.close()

    def test_one_failed_recipient_does_not_block_the_next(self) -> None:
        conn = memory_db()
        notify.save(conn, host="smtp.example.com", port="465", user="from@example.com",
                    password="secret", to="first@example.com", enabled=True)
        rows = [{"title": "标题", "summary": "摘要", "published_at": 1_700_000_000_000}]
        with mock.patch("signalwatch.notify._targets", return_value=[("bad@example.com", set()), ("good@example.com", set())]), \
             mock.patch("signalwatch.notify.send", side_effect=[RuntimeError("smtp down"), "good@example.com"]):
            sent = notify.pump(conn, rows, force=True)
        self.assertEqual(sent, ["good@example.com"])
        actions = [r[0] for r in conn.execute("SELECT action FROM audit")]
        self.assertIn("notify.recipient_failed", actions)
        conn.close()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect-file":
            self.send_response(302)
            self.send_header("Location", "file:///D:/signalwatch/pyproject.toml")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"12345")

    def log_message(self, fmt: str, *args) -> None:
        pass


class NetworkAndXmlTests(unittest.TestCase):
    def test_http_scheme_size_and_redirect_limits(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(http_get(base + "/", timeout=2, user_agent="test", max_bytes=5), "12345")
            with self.assertRaisesRegex(ValueError, "上限"):
                http_get(base + "/", timeout=2, user_agent="test", max_bytes=4)
            with self.assertRaisesRegex(ValueError, "http/https"):
                http_get("file:///D:/signalwatch/pyproject.toml", timeout=2, user_agent="test")
            with self.assertRaisesRegex(ValueError, "http/https"):
                http_get(base + "/redirect-file", timeout=2, user_agent="test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rss_and_arxiv_reject_entity_declarations(self) -> None:
        payload = """<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///D:/signalwatch/pyproject.toml">]>
<rss><channel><item><guid>&xxe;</guid><title>bad</title></item></channel></rss>"""
        with mock.patch("signalwatch.sources.rss.http_get", return_value=payload):
            with self.assertRaises(EntitiesForbidden):
                RssSource(name="bad", feed_url="https://example.com/feed").fetch(None, 10)
        from signalwatch.sources.arxiv import ArxivSource
        with mock.patch("signalwatch.sources.arxiv.http_get", return_value=payload):
            with self.assertRaises(EntitiesForbidden):
                ArxivSource().fetch(None, 10)

    def test_user_agent_matches_current_release(self) -> None:
        self.assertIn("signalwatch/0.2", SETTINGS.user_agent)


class DesktopDefaultPasswordTests(unittest.TestCase):
    def test_desktop_login_cannot_enter_before_default_password_changes(self) -> None:
        from signalwatch.desktop import App

        conn = memory_db()
        auth.seed_admin(conn)
        app = App.__new__(App)
        app.conn = conn
        app.root = None
        app.login_user = mock.Mock(value=lambda: "admin")
        app.login_pass = mock.Mock(value=lambda: "admin123")
        app.login_msg = mock.Mock()
        app._remember = mock.Mock()
        app._enter = mock.Mock()
        with mock.patch("signalwatch.desktop.simpledialog.askstring",
                        side_effect=[STRONG_PASSWORD, STRONG_PASSWORD]):
            app._do_login()
        app._enter.assert_called_once()
        account = app._enter.call_args.args[0]
        self.assertFalse(account["must_change_password"])
        app._remember.assert_called_once()
        self.assertEqual(auth.get_user(conn, "admin")["must_change_password"], 0)
        self.assertEqual(auth.login(conn, "admin", STRONG_PASSWORD)["user"]["username"], "admin")
        conn.close()


if __name__ == "__main__":
    unittest.main()
