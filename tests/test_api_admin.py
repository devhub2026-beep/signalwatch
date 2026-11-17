"""企业化接口的端到端测试：真起一个 app，真发 HTTP（TestClient）。"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from signalwatch import auth  # noqa: E402
from signalwatch.api import create_app  # noqa: E402
from signalwatch.config import Settings  # noqa: E402
from signalwatch.db import connect  # noqa: E402


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.settings = Settings(home=home, db_path=home / "data" / "sw.db")
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", "test-admin-pass")
        conn.close()

    def tearDown(self):
        try:
            self.client.__exit__(None, None, None)
        finally:
            self.tmp.cleanup()

    def login(self, username="admin", password="test-admin-pass"):
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["token"]

    def test_seed_admin_and_me(self):
        token = self.login()
        r = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.json()["user"]["role"], "admin")

    def test_no_token_is_401(self):
        self.assertEqual(self.client.get("/api/users").status_code, 401)

    def test_bad_password_is_401_and_audited(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        self.assertEqual(r.status_code, 401)
        token = self.login()
        rows = self.client.get("/api/audit", headers={"Authorization": f"Bearer {token}"}).json()["rows"]
        self.assertTrue(any(x["action"] == "auth.login_failed" for x in rows))

    def test_create_user_and_viewer_cannot_list(self):
        token = self.login()
        r = self.client.post(
            "/api/users",
            json={"username": "watch", "password": "watch123", "role": "viewer"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 201, r.text)
        vtoken = self.login("watch", "watch123")
        r2 = self.client.get("/api/users", headers={"Authorization": f"Bearer {vtoken}"})
        self.assertEqual(r2.status_code, 403)

    def test_disable_user_blocks_login(self):
        token = self.login()
        h = {"Authorization": f"Bearer {token}"}
        self.client.post("/api/users", json={"username": "temp", "password": "temp123", "role": "viewer"}, headers=h)
        self.client.patch("/api/users/temp", json={"enabled": False}, headers=h)
        r = self.client.post("/api/auth/login", json={"username": "temp", "password": "temp123"})
        self.assertEqual(r.status_code, 403)

    def test_login_sets_cookie(self):
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-pass"})
        self.assertIn("sw_token", r.cookies)
        r2 = self.client.get("/api/auth/me")
        self.assertEqual(r2.status_code, 200)

    def test_report_and_sources(self):
        token = self.login()
        h = {"Authorization": f"Bearer {token}"}
        rep = self.client.get("/api/report?days=3", headers=h).json()
        self.assertEqual(len(rep["daily"]), 3)
        src = self.client.get("/api/sources").json()["sources"]
        name = src[0]["name"]
        r = self.client.patch(f"/api/sources/{name}", json={"enabled": False}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        after = {s["name"]: s for s in r.json()["sources"]}
        self.assertEqual(int(after[name]["paused"]), 1)

    def test_audit_only_for_admin(self):
        token = self.login()
        h = {"Authorization": f"Bearer {token}"}
        self.client.post("/api/users", json={"username": "ana", "password": "ana1234", "role": "analyst"}, headers=h)
        atoken = self.login("ana", "ana1234")
        self.assertEqual(self.client.get("/api/audit", headers={"Authorization": f"Bearer {atoken}"}).status_code, 403)
        self.assertEqual(self.client.get("/api/users", headers={"Authorization": f"Bearer {atoken}"}).status_code, 403)

    def test_admin_page_served(self):
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("管理台", r.text)


if __name__ == "__main__":
    unittest.main()
