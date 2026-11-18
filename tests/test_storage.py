"""对象存储抽象层：LocalStorage 的读写删、路径遍历防护。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from signalwatch.storage import LocalStorage, S3Storage


class LocalStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalStorage(Path(self.tmp.name) / "blobs")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_put_get_roundtrip(self) -> None:
        self.store.put("a/b.txt", b"hello")
        self.assertEqual(self.store.get("a/b.txt"), b"hello")

    def test_put_overwrites(self) -> None:
        self.store.put("k", b"old")
        self.store.put("k", b"new")
        self.assertEqual(self.store.get("k"), b"new")

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.get("missing"))

    def test_delete(self) -> None:
        self.store.put("k", b"x")
        self.assertTrue(self.store.delete("k"))
        self.assertFalse(self.store.exists("k"))
        self.assertFalse(self.store.delete("k"))   # 再删返回 False

    def test_exists(self) -> None:
        self.assertFalse(self.store.exists("k"))
        self.store.put("k", b"x")
        self.assertTrue(self.store.exists("k"))

    def test_url(self) -> None:
        self.assertEqual(self.store.url("a/b.txt"), "/api/blob/a/b.txt")

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store._path("../etc/passwd")
        with self.assertRaises(ValueError):
            self.store._path("..\\windows\\system32")


class S3ContractTests(unittest.TestCase):
    def test_not_implemented(self) -> None:
        s3 = S3Storage(endpoint="http://minio:9000", bucket="b",
                       access_key="a", secret_key="s")
        with self.assertRaises(NotImplementedError):
            s3.put("k", b"x")
        with self.assertRaises(NotImplementedError):
            s3.get("k")
        with self.assertRaises(NotImplementedError):
            s3.delete("k")
        with self.assertRaises(NotImplementedError):
            s3.exists("k")
        with self.assertRaises(NotImplementedError):
            s3.url("k")


if __name__ == "__main__":
    unittest.main()
