"""推送：邮件配置、发送、摘要拼装。全部用假 SMTP，不联网。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalwatch import notify
from signalwatch.config import Settings
from signalwatch.db import connect, init_db


def make_db() -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    settings = Settings(home=home, db_path=home / "t.db")
    conn = connect(settings.db_path)
    init_db(conn)
    return conn, tmp


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=20.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.logged_in = None
        self.sent = []
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


class NotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()
        FakeSMTP.instances.clear()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_not_configured_raises(self) -> None:
        self.assertFalse(notify.is_ready(self.conn))
        with self.assertRaises(notify.NotConfigured):
            notify.send(self.conn, "s", "b")

    def test_save_hides_password(self) -> None:
        cfg = notify.save(self.conn, user="a@qq.com", password="secret", to="b@qq.com")
        self.assertTrue(cfg["has_password"])
        self.assertNotIn("secret", str(cfg))
        self.assertNotIn("secret", repr(notify.config(self.conn)))

    def test_password_none_keeps_old(self) -> None:
        notify.save(self.conn, user="a@qq.com", password="first", to="b@qq.com")
        notify.save(self.conn, user="a@qq.com")
        self.assertTrue(notify.config(self.conn)["has_password"])

    def test_send_uses_ssl_and_login(self) -> None:
        notify.save(self.conn, host="smtp.qq.com", port="465", user="a@qq.com",
                    password="pw", to="b@qq.com")
        with mock.patch.object(notify.smtplib, "SMTP_SSL", FakeSMTP):
            to = notify.send(self.conn, "标题", "正文")
        self.assertEqual(to, "b@qq.com")
        srv = FakeSMTP.instances[0]
        self.assertEqual((srv.host, srv.port), ("smtp.qq.com", 465))
        self.assertEqual(srv.logged_in, ("a@qq.com", "pw"))
        self.assertEqual(srv.sent[0]["Subject"], "标题")
        self.assertTrue(srv.quit_called)

    def test_digest_lists_rows_and_truncates(self) -> None:
        rows = [{"source": "cninfo", "title": f"公告{i}", "url": "http://x", "when": "11-16"}
                for i in range(25)]
        subject, body = notify.compose_digest(rows, limit=20)
        self.assertIn("25 条", subject)
        self.assertEqual(body.count("· [cninfo]"), 20)
        self.assertIn("还有 5 条", body)


if __name__ == "__main__":
    unittest.main()
