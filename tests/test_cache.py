"""TTL 缓存：命中/过期/淘汰/线程安全，以及接口层是否真的用上它。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from signalwatch import cache


class TTLCacheTests(unittest.TestCase):
    def test_hit_within_ttl(self) -> None:
        c = cache.TTLCache(ttl=5.0)
        c.set("k", {"v": 1})
        self.assertEqual(c.get("k"), {"v": 1})
        self.assertEqual(c.stats()["hits"], 1)

    def test_expired_is_a_miss(self) -> None:
        c = cache.TTLCache(ttl=0.01)
        c.set("k", 1)
        time.sleep(0.02)
        self.assertIs(c.get("k"), cache.MISS)
        self.assertEqual(c.stats()["misses"], 1)

    def test_get_or_build_calls_build_once(self) -> None:
        c = cache.TTLCache(ttl=5.0)
        calls = []
        for _ in range(3):
            c.get_or_build("k", lambda: calls.append(1) or 42)
        self.assertEqual(len(calls), 1)

    def test_lru_eviction(self) -> None:
        c = cache.TTLCache(ttl=30.0, maxsize=2)
        c.set("a", 1); c.set("b", 2); c.set("c", 3)
        self.assertIs(c.get("a"), cache.MISS)
        self.assertEqual(c.get("c"), 3)

    def test_concurrent_access_is_safe(self) -> None:
        c = cache.TTLCache(ttl=30.0, maxsize=64)
        errors: list[BaseException] = []

        def run(n: int) -> None:
            try:
                for i in range(200):
                    c.set(f"k{(n + i) % 8}", i)
                    c.get(f"k{i % 8}")
            except BaseException as exc:      # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])

    def test_stats_hit_rate(self) -> None:
        c = cache.TTLCache(ttl=5.0)
        c.get("x")
        c.set("x", 1)
        c.get("x")
        st = c.stats()
        self.assertEqual(st["hits"], 1)
        self.assertEqual(st["misses"], 1)
        self.assertEqual(st["hit_rate"], 0.5)


class ApiUsesCacheTests(unittest.TestCase):
    def test_second_call_hits_cache(self) -> None:
        from fastapi.testclient import TestClient

        from signalwatch import api, db
        from signalwatch.config import Settings
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        settings = Settings(home=Path(tmp.name))
        app = api.create_app(settings)
        with TestClient(app) as client:
            first = client.get("/api/stats").json()
            second = client.get("/api/stats").json()
            self.assertEqual(first, second)
            st = client.get("/api/metrics").json()
            self.assertIn("cache", st)
            self.assertGreaterEqual(st["cache"]["hits"], 1)
