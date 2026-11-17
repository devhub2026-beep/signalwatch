"""可观测与安全面：请求 id、安全响应头、指标、登录限速、OpenAPI 摘要。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from signalwatch.api import create_app
from signalwatch.config import Settings


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name))
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_request_id_and_security_headers(self) -> None:
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers.get("X-Request-ID"))
        self.assertTrue(res.headers.get("X-Response-Time", "").endswith("ms"))
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("default-src 'self'", res.headers.get("Content-Security-Policy", ""))

    def test_request_id_is_unique_per_request(self) -> None:
        a = self.client.get("/api/health").headers["X-Request-ID"]
        b = self.client.get("/api/health").headers["X-Request-ID"]
        self.assertNotEqual(a, b)

    def test_client_supplied_request_id_is_kept(self) -> None:
        res = self.client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
        self.assertEqual(res.headers["X-Request-ID"], "trace-abc-123")

    def test_metrics_counts_requests_and_latency(self) -> None:
        for _ in range(3):
            self.client.get("/api/stats")
        body = self.client.get("/api/metrics").json()
        self.assertGreaterEqual(body["requests"], 4)
        # 当前这次 /api/metrics 的耗时还没结算（响应发出后才记），所以样本少 1 条
        self.assertGreaterEqual(body["latency_ms"]["samples"], 3)
        self.assertGreater(body["latency_ms"]["p50"], 0)
        self.assertIn("200", body["by_status"])
        paths = [row["path"] for row in body["top_paths"]]
        self.assertIn("/api/stats", paths)
        self.assertGreater(body["uptime_seconds"], 0)

    def test_login_is_rate_limited_after_five_failures(self) -> None:
        for i in range(5):
            res = self.client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
            self.assertEqual(res.status_code, 401, f"第 {i + 1} 次应该还是 401")
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
        self.assertEqual(res.status_code, 429)
        self.assertIn("次数太多", res.json()["error"])

    def test_successful_login_clears_failures(self) -> None:
        password = "sw-test-pass-1"
        bad = {"username": "admin", "password": "bad"}
        self.client.post("/api/auth/login", json=bad)
        self.client.post("/api/auth/login", json=bad)
        from signalwatch.db import connect, init_db
        from signalwatch import auth as auth_mod

        conn = connect(self.settings.db_path)
        init_db(conn)
        auth_mod.set_password(conn, "admin", password)
        conn.close()

        ok = self.client.post("/api/auth/login", json={"username": "admin", "password": password})
        self.assertEqual(ok.status_code, 200)
        again = self.client.post("/api/auth/login", json=bad)
        self.assertEqual(again.status_code, 401, "成功登录后失败计数应该清零")

    def test_openapi_every_operation_has_summary(self) -> None:
        spec = self.client.get("/openapi.json").json()
        missing = [
            (path, method)
            for path, ops in spec["paths"].items()
            for method, op in ops.items()
            if isinstance(op, dict) and not op.get("summary")
        ]
        self.assertEqual(missing, [], f"这些接口没有 summary：{missing}")
        self.assertGreater(len(spec["paths"]), 40)


if __name__ == "__main__":
    unittest.main()
