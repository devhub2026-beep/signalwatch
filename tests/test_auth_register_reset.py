"""自助注册与找回口令的回归测试。

这几条路径以前完全不存在（用户只能拿到我们发的口令），所以顺手把边界一起钉住：
重名、弱口令、验证码换人用、验证码重放、改完之后旧令牌必须失效。
"""

import pathlib
import tempfile
import unittest
from unittest import mock

from signalwatch import auth, db
from signalwatch.config import Settings


def _conn():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    settings = Settings(home=pathlib.Path(tmp.name))
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    return conn, tmp


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.tmp = _conn()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_register_returns_usable_token(self):
        out = auth.register(self.conn, "alice", "secret123", "alice@example.com")
        self.assertEqual(out["user"]["username"], "alice")
        self.assertEqual(out["user"]["role"], "viewer")     # 注册不许直接给 admin
        self.assertEqual(auth.user_from_token(self.conn, out["token"])["username"], "alice")
        self.assertEqual(out["user"]["email"], "alice@example.com")

    def test_username_regex_and_duplicate(self):
        with self.assertRaises(auth.AuthError):
            auth.register(self.conn, "a", "secret123")
        with self.assertRaises(auth.AuthError):
            auth.register(self.conn, "有中文", "secret123")
        auth.register(self.conn, "bob", "secret123")
        with self.assertRaises(auth.AuthError) as ctx:
            auth.register(self.conn, "bob", "secret123")
        self.assertEqual(ctx.exception.status, 409)

    def test_weak_password_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.register(self.conn, "carol", "123")

    def test_bad_email_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.register(self.conn, "dave", "secret123", "not-an-email")


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.conn, self.tmp = _conn()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
        auth.register(self.conn, "erin", "oldpass123", "erin@example.com")

    def test_reset_by_email_rotates_password_and_token(self):
        old = auth.login(self.conn, "erin", "oldpass123")
        made = auth.make_reset(self.conn, "erin@example.com")
        self.assertIsNotNone(made)
        username, code = made
        self.assertEqual(username, "erin")
        self.assertEqual(len(code), 6)
        auth.reset_password(self.conn, "erin@example.com", code, "newpass456")
        self.assertTrue(auth.login(self.conn, "erin", "newpass456"))
        # 改口令之后旧令牌必须立刻失效（user_from_token 是抛错式，不是返回 None）
        with self.assertRaises(auth.AuthError):
            auth.user_from_token(self.conn, old["token"])

    def test_code_single_use(self):
        _, code = auth.make_reset(self.conn, "erin")
        auth.reset_password(self.conn, "erin", code, "newpass456")
        with self.assertRaises(auth.AuthError):
            auth.reset_password(self.conn, "erin", code, "another789")

    def test_new_code_invalidates_old(self):
        _, first = auth.make_reset(self.conn, "erin")
        _, second = auth.make_reset(self.conn, "erin")
        self.assertNotEqual(first, second)
        with self.assertRaises(auth.AuthError):
            auth.reset_password(self.conn, "erin", first, "newpass456")
        auth.reset_password(self.conn, "erin", second, "newpass456")

    def test_wrong_code_counts_towards_limit(self):
        _, code = auth.make_reset(self.conn, "erin")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(auth.RESET_MAX_TRIES):
            with self.assertRaises(auth.AuthError):
                auth.reset_password(self.conn, "erin", wrong, "newpass456")
        with self.assertRaises(auth.AuthError) as ctx:
            auth.reset_password(self.conn, "erin", code, "newpass456")
        self.assertEqual(ctx.exception.status, 429)      # 试错太多次，连对的也不认

    def test_expired_code_rejected(self):
        _, code = auth.make_reset(self.conn, "erin")
        self.conn.execute("UPDATE password_resets SET expires_at = 0")
        self.conn.commit()
        with self.assertRaises(auth.AuthError):
            auth.reset_password(self.conn, "erin", code, "newpass456")

    def test_unknown_user_returns_none(self):
        self.assertIsNone(auth.make_reset(self.conn, "nobody"))
        self.assertIsNone(auth.make_reset(self.conn, "nobody@example.com"))

    def test_disabled_user_cannot_reset(self):
        auth.set_enabled(self.conn, "erin", False)
        self.assertIsNone(auth.make_reset(self.conn, "erin"))


class ForgotApiTests(unittest.TestCase):
    """接口层：验证码真的走邮件通道，邮件挂了不能假装成功。"""

    def test_mail_failure_is_reported(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        from fastapi.testclient import TestClient
        from signalwatch import api
        app = api.create_app(Settings(home=pathlib.Path(tmp.name)), autostart=False)
        conn = app.state.crawler.conn
        auth.register(conn, "frank", "secret123", "frank@example.com")
        with mock.patch("signalwatch.notify.send", side_effect=RuntimeError("smtp down")):
            client = TestClient(app)
            with client:
                r = client.post("/api/auth/forgot", json={"who": "frank"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["sent"])
        self.assertIn("邮件没发出去", r.json()["hint"])


if __name__ == "__main__":
    unittest.main()
