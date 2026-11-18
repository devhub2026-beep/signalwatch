"""内部接口：worker 用它们干活，所以「不碰数据库」这件事必须由契约保证。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from signalwatch import admin
from signalwatch.api import create_app
from signalwatch.config import Settings
from signalwatch.db import connect, init_db

TOKEN = "test-internal-token"


class InternalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        home = Path(self.tmp.name)
        self.settings = Settings(home=home)
        self.env = mock.patch.dict(os.environ, {"SIGNALWATCH_INTERNAL_TOKEN": TOKEN})
        self.env.start()
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.hdr = {"X-Internal-Token": TOKEN}
        # 服务端 main 连接与测试用连接分开：同一个库，但别共用一个 sqlite3 对象
        self.conn = connect(self.settings.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.conn.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_without_token_is_403(self) -> None:
        for path in ("/api/internal/sources", "/api/internal/jobs/claim"):
            self.assertEqual(403, self.client.get(path).status_code, path)
        self.assertEqual(403, self.client.post("/api/internal/jobs/1/finish", json={"ok": True}).status_code)

    def test_claim_returns_queued_job_once(self) -> None:
        job_id = admin.enqueue_job(self.conn, "source", "stats", by="tester")
        first = self.client.get("/api/internal/jobs/claim", headers=self.hdr).json()["job"]
        self.assertEqual(job_id, first["id"])
        self.assertEqual("running", first["status"])
        self.assertIsNone(self.client.get("/api/internal/jobs/claim", headers=self.hdr).json()["job"])

    def test_finish_moves_to_done_and_rejects_illegal_transition(self) -> None:
        job_id = admin.enqueue_job(self.conn, "source", "stats", by="tester")
        self.client.get("/api/internal/jobs/claim", headers=self.hdr)
        r = self.client.post(f"/api/internal/jobs/{job_id}/finish",
                             json={"ok": True, "result": "stats:ok"}, headers=self.hdr)
        self.assertEqual(200, r.status_code)
        again = self.client.post(f"/api/internal/jobs/{job_id}/finish",
                                 json={"ok": True, "result": "again"}, headers=self.hdr)
        self.assertEqual(409, again.status_code)          # done 是终态

    def test_sources_lists_all_names(self) -> None:
        names = self.client.get("/api/internal/sources", headers=self.hdr).json()["sources"]
        self.assertIn("cninfo", names)
        self.assertIn("arxiv", names)

    def test_crawl_rejects_unknown_source(self) -> None:
        with mock.patch.object(self.app.state.crawler, "run_source", side_effect=KeyError("没有这个源")):
            r = self.client.post("/api/internal/crawl", json={"source": "nope"}, headers=self.hdr)
        self.assertEqual(404, r.status_code)

    def test_crawl_returns_counts(self) -> None:
        class Fake:
            ok, fetched, inserted, updated, error = True, 3, 2, 0, ""

        with mock.patch.object(self.app.state.crawler, "run_source", return_value=Fake()):
            r = self.client.post("/api/internal/crawl", json={"source": "stats"}, headers=self.hdr)
        self.assertEqual({"ok": True, "fetched": 3, "inserted": 2, "parts": ["stats:ok"]}, r.json())

    def test_routes_absent_when_token_not_configured(self) -> None:
        with mock.patch.dict(os.environ, {"SIGNALWATCH_INTERNAL_TOKEN": ""}):
            app2 = create_app(self.settings, autostart=False)
            paths = [getattr(r, "path", "") for r in app2.routes]
            self.assertNotIn("/api/internal/jobs/claim", paths)


if __name__ == "__main__":
    unittest.main()
