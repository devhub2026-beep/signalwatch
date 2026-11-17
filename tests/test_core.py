"""核心：去重、修订、并发插入、调度节奏、失败计数。"""

from __future__ import annotations

import sqlite3
import sys
import os
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch import ingest  # noqa: E402
from signalwatch.config import Settings  # noqa: E402
from signalwatch import db  # noqa: E402
from signalwatch.db import connect, db_stats, init_db  # noqa: E402
from signalwatch.scheduler import Crawler  # noqa: E402
from signalwatch.sources.base import RateLimiter, Record, normalize_records  # noqa: E402


def rec(eid="a1", title="某公司年度报告", ts=1_760_000_000_000, summary=""):
    return Record(external_id=eid, title=title, published_at=ts, summary=summary)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_same_record_twice_inserts_once(self):
        ins1, upd1 = ingest.save_records(self.conn, "cninfo", [rec()])
        ins2, upd2 = ingest.save_records(self.conn, "cninfo", [rec()])
        self.assertEqual((ins1, upd1), (1, 0))
        self.assertEqual((ins2, upd2), (0, 0))
        n = self.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_same_external_id_from_two_columns_is_one_row(self):
        # 深市/沪市两个 column 返回同一份公告 -> 只应落一条
        self.assertEqual(ingest.save_records(self.conn, "cninfo", [rec("x9")]), (1, 0))
        self.assertEqual(ingest.save_records(self.conn, "cninfo", [rec("x9")]), (0, 0))

    def test_different_sources_same_ext_id_are_two_rows(self):
        ingest.save_records(self.conn, "cninfo", [rec("same")])
        ingest.save_records(self.conn, "36kr", [rec("same")])
        n = self.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        self.assertEqual(n, 2)

    def test_content_change_bumps_version_and_keeps_history(self):
        ingest.save_records(self.conn, "cninfo", [rec("v1", "原标题")])
        ins, upd = ingest.save_records(self.conn, "cninfo", [rec("v1", "修正后的标题")])
        self.assertEqual((ins, upd), (0, 1))
        row = self.conn.execute("SELECT version, title FROM items WHERE external_id='v1'").fetchone()
        self.assertEqual(row["version"], 2)
        self.assertEqual(row["title"], "修正后的标题")
        revs = self.conn.execute("SELECT version, title FROM revisions ORDER BY version").fetchall()
        # v1 是首次入库的基线，v2 是这次改动 —— 两条都在，"改了什么"才有得比
        self.assertEqual([r["version"] for r in revs], [1, 2])
        self.assertEqual(revs[0]["title"], "原标题")
        self.assertEqual(revs[1]["title"], "修正后的标题")

    def test_unchanged_content_does_not_bump(self):
        ingest.save_records(self.conn, "cninfo", [rec("v2", "同一个标题")])
        ingest.save_records(self.conn, "cninfo", [rec("v2", "同一个标题")])
        ver = self.conn.execute("SELECT version FROM items WHERE external_id='v2'").fetchone()["version"]
        self.assertEqual(ver, 1)

    def test_concurrent_insert_same_records_no_duplicates(self):
        """50 个线程同时灌同一批：必须只有一份，且不能报 database is locked。"""
        tmp = tempfile.mkdtemp()
        conn = connect(Path(tmp) / "c.db")
        init_db(conn)
        errors: list[BaseException] = []

        def worker(i: int):
            try:
                ingest.save_records(conn, "cninfo", [rec(f"c{i % 10}") for i in range(10)])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        conn.close()
        self.assertEqual(errors, [])
        n = connect(Path(tmp) / "c.db").execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        self.assertEqual(n, 10)

    def test_query_filters(self):
        ingest.save_records(self.conn, "cninfo", [rec("q1", "关于回购的公告")])
        ingest.save_records(self.conn, "36kr", [rec("q2", "某研究机构发布报告")])
        self.assertEqual(len(ingest.query_items(self.conn, q="回购")), 1)
        self.assertEqual(len(ingest.query_items(self.conn, source="36kr")), 1)
        self.assertEqual(len(ingest.query_items(self.conn, limit=10)), 2)


class SourceStateTests(unittest.TestCase):
    def test_failure_count_and_degraded(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        with conn:
            conn.execute("INSERT INTO sources(name, kind, url) VALUES('x','news','u')")
        for i in (1, 2):
            self.assertEqual(ingest.mark_failure(conn, "x", "boom", threshold=3), i)
        self.assertEqual(ingest.mark_failure(conn, "x", "boom", threshold=3), 3)
        row = conn.execute("SELECT status FROM sources WHERE name='x'").fetchone()
        self.assertEqual(row["status"], "degraded")
        ingest.mark_success(conn, "x", "{}")
        row = conn.execute("SELECT status, fail_count FROM sources WHERE name='x'").fetchone()
        self.assertEqual((row["status"], row["fail_count"]), ("ok", 0))

    def test_rate_limiter_waits(self):
        limiter = RateLimiter(min_interval=0.15)
        limiter.wait()
        import time

        t0 = time.monotonic()
        limiter.wait()
        self.assertGreaterEqual(time.monotonic() - t0, 0.10)

    def test_normalize_drops_empty_and_dedupes(self):
        rows = normalize_records([
            rec("a", "A"), rec("a", "A2"), rec("b", "B"), Record("", "no id", 1), Record("c", "", 1),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.external_id for r in rows}, {"a", "b"})
        self.assertEqual([r.title for r in rows if r.external_id == "a"], ["A2"])


class FakeSource:
    name = "fake"
    kind = "news"
    url = "http://example.invalid"
    interval_seconds = 300

    def __init__(self, boom=False, records=None):
        self.boom = boom
        self.records = records or [rec("f1", "假源的一条")]
        self.calls = 0

    def fetch(self, cursor, limit):
        self.calls += 1
        if self.boom:
            raise RuntimeError("parser broke")
        return self.records


class SchedulerTests(unittest.TestCase):
    def _crawler(self, source):
        tmp = Path(tempfile.mkdtemp())
        settings = Settings(home=tmp, db_path=tmp / "s.db", min_interval=0.0)
        return Crawler(settings, sources=[source])

    def test_run_source_inserts_and_sets_cursor(self):
        c = self._crawler(FakeSource())
        try:
            r = c.run_source("fake", force=True)
            self.assertTrue(r.ok)
            self.assertEqual((r.fetched, r.inserted), (1, 1))
            row = c.conn.execute("SELECT last_cursor, status FROM sources WHERE name='fake'").fetchone()
            self.assertIn("since_ms", row["last_cursor"])
            self.assertEqual(row["status"], "ok")
            self.assertEqual(len(c.tick()), 0, "刚跑完不应该立即再跑（节奏未到）")
        finally:
            c.conn.close()

    def test_failure_marks_and_backoff(self):
        c = self._crawler(FakeSource(boom=True))
        try:
            r = c.run_source("fake", force=True)
            self.assertFalse(r.ok)
            row = c.conn.execute("SELECT fail_count FROM sources WHERE name='fake'").fetchone()
            self.assertEqual(row["fail_count"], 1)
            self.assertFalse(c._due("fake"), "失败后应退避，不该马上重试")
        finally:
            c.conn.close()

    def test_runs_table_records_both_outcomes(self):
        c = self._crawler(FakeSource())
        try:
            c.run_source("fake", force=True)
            n = c.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
            self.assertEqual(n, 1)
        finally:
            c.conn.close()


if __name__ == "__main__":
    unittest.main()

class SettingsIsolationTests(unittest.TestCase):
    """换成临时 home 就必须换库——否则测试会写进真库、把线上配置覆盖掉。"""

    def test_home_drives_db_path(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            s = Settings(home=Path(tmp.name))
            self.assertEqual(s.db_path, Path(tmp.name) / "data" / "signalwatch.db")
        finally:
            tmp.cleanup()

    def test_explicit_db_path_wins(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            target = Path(tmp.name) / "x.db"
            self.assertEqual(Settings(home=Path(tmp.name), db_path=target).db_path, target)
        finally:
            tmp.cleanup()


class JournalModeTests(unittest.TestCase):
    """Docker 里 bind mount 到 Windows 目录不能用 WAL，会烂库（database disk image is malformed）。"""

    def test_default_is_wal(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            conn = db.connect(Path(d) / "a.db")
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            conn.close()

    def test_env_switches_to_delete(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            old = os.environ.get("SIGNALWATCH_JOURNAL")
            os.environ["SIGNALWATCH_JOURNAL"] = "delete"
            try:
                conn = db.connect(Path(d) / "b.db")
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                conn.close()
            finally:
                if old is None:
                    os.environ.pop("SIGNALWATCH_JOURNAL", None)
                else:
                    os.environ["SIGNALWATCH_JOURNAL"] = old
