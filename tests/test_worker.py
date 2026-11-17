"""独立 worker：领任务、抓取、写结果；中断的任务能被打回队列。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from signalwatch import admin, db, worker


class FakeCrawler:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def snapshot(self) -> dict:
        return {"sources": [{"name": "cninfo"}, {"name": "ithome"}]}

    def run_source(self, name: str, force: bool = False) -> SimpleNamespace:
        self.calls.append(name)
        if self.fail:
            raise RuntimeError("网络断了")
        return SimpleNamespace(ok=True, fetched=10, inserted=2)


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_run_once_without_job_returns_none(self) -> None:
        self.assertIsNone(worker.run_once(self.conn, FakeCrawler()))

    def test_source_job_is_executed_and_marked_done(self) -> None:
        job_id = admin.enqueue_job(self.conn, "source", "ithome", by="test")
        crawler = FakeCrawler()
        row = worker.run_once(self.conn, crawler)
        self.assertEqual(crawler.calls, ["ithome"])
        self.assertEqual(row["status"], "done")
        self.assertIn("新增 2", row["result"])
        self.assertEqual(admin.job_counts(self.conn)["done"], 1)

    def test_all_job_runs_every_source(self) -> None:
        admin.enqueue_job(self.conn, "all", "", by="test")
        crawler = FakeCrawler()
        worker.run_once(self.conn, crawler)
        self.assertEqual(crawler.calls, ["cninfo", "ithome"])

    def test_failure_is_recorded_not_raised(self) -> None:
        admin.enqueue_job(self.conn, "source", "cninfo", by="test")
        row = worker.run_once(self.conn, FakeCrawler(fail=True))
        self.assertEqual(row["status"], "failed")
        self.assertIn("网络断了", row["error"])

    def test_recover_stale_puts_running_back(self) -> None:
        job_id = admin.enqueue_job(self.conn, "source", "cninfo", by="test")
        admin.claim_job(self.conn)          # 变成 running，模拟进程被杀
        self.assertEqual(admin.job_counts(self.conn)["running"], 1)
        self.assertEqual(worker.recover_stale(self.conn), 1)
        self.assertEqual(admin.job_counts(self.conn)["queued"], 1)
        self.assertEqual(admin.job_counts(self.conn)["running"], 0)

    def test_loop_stops_after_max_jobs(self) -> None:
        for _ in range(2):
            admin.enqueue_job(self.conn, "source", "cninfo", by="test")
        crawler = FakeCrawler()
        self.assertEqual(worker.loop(self.conn, crawler, idle=0.01, max_jobs=2), 2)
        self.assertEqual(len(crawler.calls), 2)
        self.assertEqual(admin.job_counts(self.conn)["queued"], 0)

    def test_worker_writes_audit_row(self) -> None:
        admin.enqueue_job(self.conn, "source", "ithome", by="test")
        worker.run_once(self.conn, FakeCrawler())
        rows = self.conn.execute(
            "SELECT action, target FROM audit WHERE action = 'job.run'").fetchall()
        self.assertEqual(len(rows), 1)
