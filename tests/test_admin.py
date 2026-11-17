"""业务层测试：分类、条目增删改、任务队列、报表、导出。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from signalwatch import admin, db


def make_db() -> tuple[object, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    conn = db.connect(Path(tmp.name) / "t.db")
    db.init_db(conn)
    admin.seed_categories(conn)
    conn.execute(
        "INSERT INTO items(fingerprint, source, external_id, kind, title, summary, url,"
        " published_at, first_seen_at, last_seen_at, content_hash)"
        " VALUES ('f1','cninfo','e1','announcement','公告甲','摘要甲','http://x',1000,1000,1000,'h1')")
    conn.execute(
        "INSERT INTO items(fingerprint, source, external_id, kind, title, summary, url,"
        " published_at, first_seen_at, last_seen_at, content_hash, sec_code, sec_name)"
        " VALUES ('f2','cninfo','e2','announcement','公告乙','摘要乙','http://y',2000,2000,2000,'h2',"
        "'600000','浦发银行')")
    conn.commit()
    return conn, tmp


class CategoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_seed_has_five(self) -> None:
        names = [c["name"] for c in admin.list_categories(self.conn)]
        self.assertEqual(len(names), 5)
        self.assertIn("金融", names)

    def test_add_and_disable(self) -> None:
        cid = admin.add_category(self.conn, "地产")
        admin.set_category_enabled(self.conn, cid, False)
        cats = {c["name"]: c for c in admin.list_categories(self.conn)}
        self.assertEqual(cats["地产"]["enabled"], 0)
        self.assertEqual([c["name"] for c in admin.list_categories(self.conn, only_enabled=True)],
                         [n for n in ("金融", "科技", "学术", "政策", "招聘")])

    def test_delete_clears_links(self) -> None:
        cid = admin.add_category(self.conn, "地产")
        admin.set_item_categories(self.conn, 1, ["地产"])
        self.assertEqual(admin.item_categories(self.conn, 1), ["地产"])
        admin.delete_category(self.conn, cid)
        self.assertEqual(admin.item_categories(self.conn, 1), [])

    def test_assign_multiple(self) -> None:
        admin.set_item_categories(self.conn, 1, ["金融", "政策"])
        self.assertEqual(sorted(admin.item_categories(self.conn, 1)), ["政策", "金融"])


class ItemAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_search_and_total(self) -> None:
        rows, total = admin.item_page(self.conn, q="乙")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["title"], "公告乙")

    def test_offline_filter(self) -> None:
        admin.set_items_state(self.conn, [1], "offline")
        rows, total = admin.item_page(self.conn, state="offline")
        self.assertEqual([r["id"] for r in rows], [1])
        rows, total = admin.item_page(self.conn, state="online")
        self.assertEqual([r["id"] for r in rows], [2])

    def test_bad_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            admin.set_items_state(self.conn, [1], "deleted")

    def test_pin_toggle(self) -> None:
        self.assertEqual(admin.toggle_pin(self.conn, 1), 1)
        self.assertEqual(admin.toggle_pin(self.conn, 1), 0)

    def test_delete_items(self) -> None:
        admin.set_item_categories(self.conn, 1, ["金融"])
        self.assertEqual(admin.delete_items(self.conn, [1]), 1)
        _, total = admin.item_page(self.conn)
        self.assertEqual(total, 1)

    def test_export_csv_has_bom_and_rows(self) -> None:
        text = admin.export_items_csv(self.conn)
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn("公告甲", text)
        self.assertIn("浦发银行", text)

    def test_update_item_title_and_category(self) -> None:
        row = admin.update_item(self.conn, 1, title="改过的标题", category="科技")
        self.assertEqual(row["title"], "改过的标题")
        self.assertTrue(row["edited_at"])
        cats = admin.item_categories(self.conn, 1)
        self.assertEqual(cats, ["科技"])

    def test_update_item_rejects_empty_title(self) -> None:
        with self.assertRaises(ValueError):
            admin.update_item(self.conn, 1, title="   ")

    def test_failure_reason_buckets(self) -> None:
        self.assertEqual(admin.job_failure_reason("TimeoutError: timed out"), "timeout")
        self.assertEqual(admin.job_failure_reason("URLError: connection reset"), "network")
        self.assertEqual(admin.job_failure_reason('HTTPError: 403 Forbidden'), "auth")
        self.assertEqual(admin.job_failure_reason("ParseError: not well-formed"), "parse")
        self.assertEqual(admin.job_failure_reason(""), "")
        self.assertEqual(admin.job_failure_reason("??? weird"), "unknown")


class JobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_priority_claim(self) -> None:
        low = admin.enqueue_job(self.conn, "source", "cninfo")
        high = admin.enqueue_job(self.conn, "all", "", priority=5)
        first = admin.claim_job(self.conn)
        self.assertEqual(first["id"], high)
        self.assertIsNotNone(low)

    def test_finish_and_retry(self) -> None:
        jid = admin.enqueue_job(self.conn, "source", "cninfo", by="admin")
        admin.claim_job(self.conn)
        admin.finish_job(self.conn, jid, ok=False, error="boom")
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "failed")
        admin.retry_job(self.conn, jid)
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "queued")

    def test_cancel_only_pending(self) -> None:
        jid = admin.enqueue_job(self.conn, "source", "cninfo")
        admin.cancel_job(self.conn, jid)
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "cancelled")

    def test_counts(self) -> None:
        admin.enqueue_job(self.conn, "source", "a")
        admin.enqueue_job(self.conn, "source", "b")
        self.assertEqual(admin.job_counts(self.conn)["queued"], 2)


class RuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_add_rule_and_hits(self) -> None:
        admin.add_rule(self.conn, "公告")
        rules = admin.list_rules(self.conn)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["hits"], 2)

    def test_rule_scoped_by_source(self) -> None:
        admin.add_rule(self.conn, "公告", source="ithome")
        self.assertEqual(admin.list_rules(self.conn)[0]["hits"], 0)

    def test_toggle_and_delete(self) -> None:
        rid = admin.add_rule(self.conn, "公告")
        admin.set_rule_enabled(self.conn, rid, False)
        self.assertEqual(admin.list_rules(self.conn)[0]["enabled"], 0)
        admin.delete_rule(self.conn, rid)
        self.assertEqual(admin.list_rules(self.conn), [])


class WorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_rates(self) -> None:
        now = int(__import__("time").time() * 1000)
        self.conn.execute(
            "INSERT INTO runs(source, started_at, ended_at, ok, fetched, inserted, updated)"
            " VALUES ('cninfo', ?, ?, 1, 10, 2, 0)", (now - 5000, now - 4000))
        self.conn.execute(
            "INSERT INTO runs(source, started_at, ended_at, ok, fetched, inserted, updated)"
            " VALUES ('cninfo', ?, ?, 0, 0, 0, 0)", (now - 3000, now - 2500))
        self.conn.commit()
        wb = admin.workbench(self.conn)
        self.assertAlmostEqual(wb["success_rate"], 50.0)
        self.assertAlmostEqual(wb["dedupe_rate"], 80.0)

    def test_settings_roundtrip(self) -> None:
        admin.set_setting(self.conn, "x", "1")
        self.assertEqual(admin.get_setting(self.conn, "x", "0"), "1")
        self.assertEqual(admin.get_setting(self.conn, "y", "9"), "9")


if __name__ == "__main__":
    unittest.main()


class JobStateMachineTests(unittest.TestCase):
    """任务六态：状态机把非法迁移挡在代码里，不靠页面自觉。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _job(self) -> int:
        return admin.enqueue_job(self.conn, "source", "cninfo", by="admin")

    def test_six_states_are_declared(self) -> None:
        self.assertEqual(admin.JOB_STATES,
                         ("queued", "running", "paused", "failed", "done", "cancelled"))
        self.assertEqual(set(admin.STATE_TEXT), set(admin.JOB_STATES))

    def test_happy_path_queued_running_done(self) -> None:
        jid = self._job()
        admin.claim_job(self.conn)
        admin.finish_job(self.conn, jid, ok=True, result="抓到 30 条")
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "done")

    def test_done_is_terminal(self) -> None:
        jid = self._job()
        admin.claim_job(self.conn)
        admin.finish_job(self.conn, jid, ok=True)
        for fn in (admin.retry_job, admin.cancel_job, admin.pause_job):
            with self.assertRaises(admin.JobStateError):
                fn(self.conn, jid)

    def test_pause_and_resume(self) -> None:
        jid = self._job()
        admin.claim_job(self.conn)
        admin.pause_job(self.conn, jid)
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "paused")
        admin.resume_job(self.conn, jid)
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "queued")
        self.assertIsNotNone(admin.claim_job(self.conn))

    def test_failed_can_retry_but_not_finish(self) -> None:
        jid = self._job()
        admin.claim_job(self.conn)
        admin.finish_job(self.conn, jid, ok=False, error="boom")
        with self.assertRaises(admin.JobStateError):
            admin.finish_job(self.conn, jid, ok=True)
        admin.retry_job(self.conn, jid)
        self.assertEqual(admin.job_detail(self.conn, jid)["status"], "queued")

    def test_cancelled_is_terminal(self) -> None:
        jid = self._job()
        admin.cancel_job(self.conn, jid)
        with self.assertRaises(admin.JobStateError):
            admin.resume_job(self.conn, jid)

    def test_unknown_job_and_state(self) -> None:
        with self.assertRaises(admin.JobStateError):
            admin.pause_job(self.conn, 9999)
        jid = self._job()
        with self.assertRaises(admin.JobStateError):
            admin._transition(self.conn, jid, "exploded")

    def test_counts_include_new_states(self) -> None:
        jid = self._job()
        admin.claim_job(self.conn)
        admin.pause_job(self.conn, jid)
        counts = admin.job_counts(self.conn)
        self.assertEqual(counts["paused"], 1)
        self.assertEqual(set(counts), set(admin.JOB_STATES))


class KeysetPagingTests(unittest.TestCase):
    """深翻页不能靠 OFFSET：10 万行时 OFFSET 9 万要 0.87 秒，游标 54 毫秒。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn)
        now = int(time.time() * 1000)
        with db.guard(self.conn), self.conn:
            for i in range(120):
                self.conn.execute(
                    "INSERT INTO items(fingerprint, source, external_id, kind, title, content_hash,"
                    " published_at, first_seen_at, last_seen_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"fp{i}", "cninfo", f"e{i}", "announcement", f"第{i}条", f"h{i}",
                     now + i, now, now))

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_cursor_pages_cover_everything_once(self) -> None:
        seen, cursor = [], None
        for _ in range(20):
            rows, total = admin.item_page(self.conn, limit=25, before=cursor)
            if not rows:
                break
            seen += [r["id"] for r in rows]
            cursor = (rows[-1]["published_at"], rows[-1]["id"])
            self.assertEqual(total, 120)
        self.assertEqual(len(seen), 120)
        self.assertEqual(len(set(seen)), 120)

    def test_cursor_is_strictly_descending(self) -> None:
        rows, _ = admin.item_page(self.conn, limit=10)
        keys = [(r["published_at"], r["id"]) for r in rows]
        self.assertEqual(keys, sorted(keys, reverse=True))
