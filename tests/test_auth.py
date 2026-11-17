"""用户 / 口令 / 令牌 / 角色的单测（不碰网络、不碰 Tk）。"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch import auth  # noqa: E402
from signalwatch.db import init_db  # noqa: E402


def fresh():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify(self):
        salt, digest = auth.hash_password("hunter22")
        self.assertTrue(auth.verify_password("hunter22", salt, digest))
        self.assertFalse(auth.verify_password("hunter23", salt, digest))

    def test_same_password_different_salt(self):
        a = auth.hash_password("same-pass")
        b = auth.hash_password("same-pass")
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            auth.hash_password("123")


class TokenTests(unittest.TestCase):
    def test_roundtrip(self):
        conn = fresh()
        token = auth.make_token(conn, "admin")
        payload = auth.parse_token(conn, token)
        self.assertEqual(payload["u"], "admin")

    def test_tampered_rejected(self):
        conn = fresh()
        token = auth.make_token(conn, "admin")
        body, _, sig = token.rpartition(".")
        self.assertIsNone(auth.parse_token(conn, body[:-2] + "xx." + sig))
        self.assertIsNone(auth.parse_token(conn, body + "." + ("0" * len(sig))))

    def test_expired_rejected(self):
        conn = fresh()
        token = auth.make_token(conn, "admin", ttl=-1)
        self.assertIsNone(auth.parse_token(conn, token))

    def test_garbage_rejected(self):
        conn = fresh()
        for bad in ("", "abc", "a.b", "...."):
            self.assertIsNone(auth.parse_token(conn, bad))


class UserTests(unittest.TestCase):
    def test_create_and_login(self):
        conn = fresh()
        auth.create_user(conn, "alice", "secret1", role="analyst")
        result = auth.login(conn, "alice", "secret1")
        self.assertEqual(result["user"]["role"], "analyst")
        self.assertTrue(result["token"])

    def test_wrong_password(self):
        conn = fresh()
        auth.create_user(conn, "bob", "secret1")
        with self.assertRaises(auth.AuthError):
            auth.login(conn, "bob", "secret2")

    def test_duplicate_name(self):
        conn = fresh()
        auth.create_user(conn, "bob", "secret1")
        with self.assertRaises(ValueError):
            auth.create_user(conn, "bob", "secret1")

    def test_disabled_cannot_login(self):
        conn = fresh()
        auth.create_user(conn, "carol", "secret1")
        auth.set_enabled(conn, "carol", False)
        with self.assertRaises(auth.AuthError):
            auth.login(conn, "carol", "secret1")

    def test_password_change_invalidates_token(self):
        conn = fresh()
        auth.create_user(conn, "dave", "secret1")
        token = auth.login(conn, "dave", "secret1")["token"]
        auth.set_password(conn, "dave", "secret2")
        # 口令改了，旧令牌立刻作废（不给"改了密还能用七天"留口子）
        with self.assertRaises(auth.AuthError):
            auth.user_from_token(conn, token)
        self.assertEqual(auth.login(conn, "dave", "secret2")["user"]["username"], "dave")

    def test_last_admin_cannot_be_deleted(self):
        conn = fresh()
        auth.create_user(conn, "root1", "secret1", role="admin")
        with self.assertRaises(ValueError):
            auth.delete_user(conn, "root1")

    def test_roles(self):
        user = {"username": "u", "role": "viewer"}
        with self.assertRaises(auth.AuthError):
            auth.need(user, "admin")
        auth.need({"username": "a", "role": "admin"}, "admin")
        auth.need({"username": "n", "role": "analyst"}, "analyst", "admin")

    def test_seed_admin_once(self):
        conn = fresh()
        self.assertTrue(auth.seed_admin(conn, "admin", "admin123"))
        self.assertFalse(auth.seed_admin(conn, "admin", "admin123"))
        self.assertEqual(len(auth.list_users(conn)), 1)


if __name__ == "__main__":
    unittest.main()
