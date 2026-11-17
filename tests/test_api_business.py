"""网页端业务接口：条目 / 分类 / 任务 / 设置 / 统一错误格式。

这些用例存在的原因：桌面端早就有了这些功能，网页端补的时候最容易出现
「接口能跑但字段对不上」「没登录也能改」这类问题，所以按角色和字段都钉一遍。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from signalwatch import auth
from signalwatch.api import create_app
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.ingest import save_records


def _settings(tmp: str) -> Settings:
    return Settings(home=Path(tmp), min_interval=0.0)


def _seed(settings: Settings, n: int = 3) -> list[int]:
    conn = connect(settings.db_path)
    init_db(conn)
    from signalwatch.sources.base import Record

    records = [
        Record(external_id=f"id-{i}", title=f"第 {i} 条公告", published_at=1_700_000_000_000 + i,
               summary="摘要", url=f"https://example.com/{i}", sec_code=f"00000{i}", sec_name=f"公司{i}")
        for i in range(n)
    ]
    save_records(conn, "cninfo", records)
    ids = [row["id"] for row in conn.execute("SELECT id FROM items ORDER BY id")]
    conn.close()
    return ids


class BusinessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = _settings(self.tmp.name)
        self.ids = _seed(self.settings)
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", "test-admin-pass")
        conn.close()
        token = self.client.post("/api/auth/login",
                                 json={"username": "admin", "password": "test-admin-pass"}).json()["token"]
        self.h = {"Authorization": f"Bearer {token}"}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    # ---- 未登录一律挡住 ---------------------------------------------------
    def test_requires_login(self) -> None:
        anon = TestClient(self.app)
        try:
            for path in ("/api/admin/items", "/api/admin/categories", "/api/admin/jobs",
                         "/api/admin/settings", "/api/audit"):
                r = anon.get(path)
                self.assertEqual(r.status_code, 401, path)
                self.assertIn("error", r.json())
        finally:
            anon.close()

    # ---- 条目 -------------------------------------------------------------
    def test_items_paging_and_fields(self) -> None:
        r = self.client.get("/api/admin/items?page=1&size=2", headers=self.h).json()
        self.assertEqual(len(r["items"]), 2)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["pages"], 2)
        for key in ("id", "title", "source", "published_at"):
            self.assertIn(key, r["items"][0])

    def test_items_search(self) -> None:
        r = self.client.get("/api/admin/items?q=第 1 条", headers=self.h).json()
        self.assertEqual(r["total"], 1)

    def test_item_state_toggles_and_validates(self) -> None:
        iid = self.ids[0]
        ok = self.client.post("/api/admin/items/state", json={"ids": [iid], "state": "offline"}, headers=self.h)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["changed"], 1)
        row = self.client.get("/api/admin/items?size=10", headers=self.h).json()["items"]
        self.assertEqual([x["state"] for x in row if x["id"] == iid][0], "offline")

        bad = self.client.post("/api/admin/items/state", json={"ids": [iid], "state": "gone"}, headers=self.h)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("error", bad.json())

    def test_item_edit_and_delete(self) -> None:
        iid = self.ids[0]
        r = self.client.patch(f"/api/admin/items/{iid}", json={"title": "改过的标题"}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["item"]["title"], "改过的标题")

        empty = self.client.patch(f"/api/admin/items/{iid}", json={"title": "  "}, headers=self.h)
        self.assertEqual(empty.status_code, 400)

        d = self.client.post("/api/admin/items/delete", json={"ids": [iid]}, headers=self.h)
        self.assertEqual(d.json()["deleted"], 1)
        left = self.client.get("/api/admin/items?size=10", headers=self.h).json()["total"]
        self.assertEqual(left, 2)

    def test_items_csv_export(self) -> None:
        r = self.client.get("/api/admin/items.csv", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("第 0 条公告", r.text)

    # ---- 分类 -------------------------------------------------------------
    def test_categories_add_and_toggle(self) -> None:
        before = len(self.client.get("/api/admin/categories", headers=self.h).json()["categories"])
        r = self.client.post("/api/admin/categories", json={"name": "测试分类"}, headers=self.h)
        self.assertEqual(r.status_code, 201)
        cid = r.json()["id"]
        listed = self.client.get("/api/admin/categories", headers=self.h).json()["categories"]
        self.assertEqual(len(listed), before + 1)

        off = self.client.patch(f"/api/admin/categories/{cid}", json={"enabled": False}, headers=self.h)
        self.assertEqual(off.status_code, 200)
        me = [c for c in off.json()["categories"] if c["id"] == cid][0]
        self.assertFalse(me["enabled"])

    def test_categories_reject_empty_name(self) -> None:
        r = self.client.post("/api/admin/categories", json={"name": "  "}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    # ---- 任务 -------------------------------------------------------------
    def test_jobs_enqueue_list_retry(self) -> None:
        r = self.client.post("/api/admin/jobs", json={"kind": "source", "target": "cninfo"}, headers=self.h)
        self.assertEqual(r.status_code, 201)
        jid = r.json()["id"]
        listed = self.client.get("/api/admin/jobs", headers=self.h).json()
        self.assertIn(jid, [j["id"] for j in listed["jobs"]])
        self.assertGreaterEqual(listed["counts"]["queued"], 1)

        # 排队中的任务不能「重试」（那是失败态的动作），要撤就取消
        retry = self.client.post(f"/api/admin/jobs/{jid}/retry", headers=self.h)
        self.assertEqual(retry.status_code, 409)
        cancel = self.client.post(f"/api/admin/jobs/{jid}/cancel", headers=self.h)
        self.assertEqual(cancel.status_code, 200)
        bad = self.client.post(f"/api/admin/jobs/{jid}/nonsense", headers=self.h)
        self.assertEqual(bad.status_code, 400)

    # ---- 设置 -------------------------------------------------------------
    def test_settings_roundtrip(self) -> None:
        r = self.client.get("/api/admin/settings", headers=self.h).json()
        self.assertIn("notify_sound", r["values"])
        saved = self.client.patch("/api/admin/settings", json={"values": {"notify_sound": "0"}}, headers=self.h)
        self.assertEqual(saved.status_code, 200)
        again = self.client.get("/api/admin/settings", headers=self.h).json()
        self.assertEqual(again["values"]["notify_sound"], "0")

    # ---- 统一错误格式 -----------------------------------------------------
    def test_errors_are_chinese_and_structured(self) -> None:
        r = self.client.post("/api/crawl?source=nope", headers=self.h)
        self.assertEqual(r.status_code, 404)
        body = r.json()
        self.assertIn("error", body)
        self.assertIn("没有这个源", body["error"])

        anon = TestClient(self.app)
        try:
            body = anon.get("/api/admin/items").json()
            self.assertIn("error", body)
        finally:
            anon.close()


class MailAndAuditTests(unittest.TestCase):
    """新增的四件事：网页配发件箱、邮件结果可见、审计导出/清理、自己改口令。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = _settings(self.tmp.name)
        _seed(self.settings)
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", "test-admin-pass")
        conn.close()
        token = self.client.post("/api/auth/login",
                                 json={"username": "admin", "password": "test-admin-pass"}).json()["token"]
        self.h = {"Authorization": f"Bearer {token}"}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_notify_state_starts_unconfigured(self) -> None:
        r = self.client.get("/api/admin/notify", headers=self.h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["ready"])
        self.assertFalse(body["has_password"])
        self.assertEqual(body["last"]["at"], 0)

    def test_notify_save_keeps_password_and_hides_it(self) -> None:
        r = self.client.post("/api/admin/notify", headers=self.h, json={
            "host": "smtp.qq.com", "port": "465", "user": "a@qq.com",
            "password": "secret-16-chars", "to": "a@qq.com", "enabled": True,
        })
        self.assertEqual(r.status_code, 200)
        cfg = r.json()["config"]
        self.assertTrue(cfg["has_password"])
        self.assertNotIn("secret-16-chars", r.text)
        self.assertTrue(r.json()["config"]["has_password"])

    def test_notify_test_without_config_reports_error(self) -> None:
        r = self.client.post("/api/admin/notify", headers=self.h, json={
            "host": "smtp.qq.com", "port": "465", "user": "", "to": "", "send_test": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("error", r.json())
        self.assertFalse(r.json()["last"]["ok"])

    def test_audit_csv_export(self) -> None:
        r = self.client.get("/api/admin/audit.csv", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("时间,账号,动作", r.text)

    def test_audit_prune_removes_old_rows(self) -> None:
        conn = connect(self.settings.db_path)
        old = 1_600_000_000_000
        conn.execute("INSERT INTO audit(at, username, action, target, detail) VALUES (?,?,?,?,?)",
                     (old, "admin", "test.old", "", ""))
        conn.commit()
        conn.close()
        r = self.client.post("/api/admin/audit/prune", headers=self.h, json={"days": 30})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["removed"], 1)

    def test_password_change_invalidates_old_token(self) -> None:
        bad = self.client.post("/api/auth/password", headers=self.h,
                               json={"old": "wrong", "new": "another123"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/auth/password", headers=self.h,
                              json={"old": "test-admin-pass", "new": "another123"})
        self.assertEqual(ok.status_code, 200)
        # 旧令牌作废
        self.assertEqual(self.client.get("/api/auth/me", headers=self.h).status_code, 401)
        # 新口令能登
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": "another123"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class RuleApiTests(unittest.TestCase):
    """订阅词接口：增、列、停用、删，以及权限边界。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.app = create_app(self.settings, autostart=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", "test-admin-pass")
        conn.close()

    def tearDown(self) -> None:
        try:
            self.client.__exit__(None, None, None)
        finally:
            self.tmp.cleanup()

    def _login(self, username="admin", password="test-admin-pass") -> None:
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        self.client.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    def test_anonymous_cannot_list(self) -> None:
        self.assertEqual(TestClient(self.app).get("/api/admin/rules").status_code, 401)

    def test_add_list_toggle_delete(self) -> None:
        self._login()
        r = self.client.post("/api/admin/rules", json={"keyword": "中标", "category": "政策"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        rows = self.client.get("/api/admin/rules").json()["rules"]
        self.assertEqual([x["keyword"] for x in rows], ["中标"])
        rid = rows[0]["id"]
        self.assertEqual(self.client.patch(f"/api/admin/rules/{rid}", json={"enabled": False}).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/rules").json()["rules"][0]["enabled"], 0)
        self.assertEqual(self.client.delete(f"/api/admin/rules/{rid}").status_code, 200)
        self.assertEqual(self.client.get("/api/admin/rules").json()["rules"], [])

    def test_duplicate_keyword_is_reported(self) -> None:
        self._login()
        self.client.post("/api/admin/rules", json={"keyword": "回购"})
        r = self.client.post("/api/admin/rules", json={"keyword": "回购"})
        self.assertEqual(r.json(), {"ok": False, "duplicated": True})

    def test_empty_keyword_is_400(self) -> None:
        self._login()
        self.assertEqual(self.client.post("/api/admin/rules", json={"keyword": "   "}).status_code, 400)

    def test_viewer_cannot_add(self) -> None:
        self._login()
        self.client.post("/api/users", json={"username": "v1", "password": "viewer123", "role": "viewer"})
        vc = TestClient(self.app)
        vc.__enter__()
        vc.post("/api/auth/login", json={"username": "v1", "password": "viewer123"})
        self.assertEqual(vc.post("/api/admin/rules", json={"keyword": "x"}).status_code, 403)


class JobStateApiTests(unittest.TestCase):
    """任务状态机的接口层：非法迁移必须 409，而不是 200 假装成功。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.patches = [mock.patch("signalwatch.config.SETTINGS", self.settings),
                        mock.patch("signalwatch.api.SETTINGS", self.settings)]
        for p in self.patches:
            p.start()
        self.client = TestClient(create_app())
        self.client.__enter__()
        conn = connect(self.settings.db_path)
        auth.set_password(conn, "admin", "test-admin-pass")
        conn.close()
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-pass"})
        self.client.headers["X-CSRF-Token"] = res.json()["csrf_token"]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _job(self) -> int:
        r = self.client.post("/api/admin/jobs", json={"kind": "source", "target": "cninfo", "priority": 0})
        self.assertEqual(r.status_code, 201)
        return r.json()["id"]

    def test_six_states_in_counts(self) -> None:
        r = self.client.get("/api/admin/jobs")
        counts = r.json()["counts"]
        for state in ("queued", "running", "paused", "failed", "done", "cancelled"):
            self.assertIn(state, counts)

    def test_cancel_then_resume_is_409(self) -> None:
        jid = self._job()
        self.assertEqual(self.client.post(f"/api/admin/jobs/{jid}/cancel").status_code, 200)
        r = self.client.post(f"/api/admin/jobs/{jid}/resume")
        self.assertEqual(r.status_code, 409)
        self.assertIn("不能从", r.json()["detail"])

    def test_unknown_action_is_400(self) -> None:
        jid = self._job()
        self.assertEqual(self.client.post(f"/api/admin/jobs/{jid}/explode").status_code, 400)

    def test_pause_requires_running(self) -> None:
        jid = self._job()
        r = self.client.post(f"/api/admin/jobs/{jid}/pause")
        self.assertEqual(r.status_code, 409)
