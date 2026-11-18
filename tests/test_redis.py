"""Redis 门铃与共享缓存：有真 Redis 就测真协议，没有就跳过（不假绿）。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from signalwatch import cache, wakeup
from signalwatch.redis_client import Redis, RedisError


def _live_redis() -> Redis | None:
    port = int(os.environ.get("SIGNALWATCH_TEST_REDIS_PORT", "6390"))
    client = Redis("127.0.0.1", port, timeout=1.0)
    return client if client.ping() else None


class RedisProtocolTests(unittest.TestCase):
    """直接和真 Redis 说话：协议拼错、返回类型不对，这里就会红。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _live_redis()
        if cls.client is None:
            raise unittest.SkipTest("本机没有 Redis（设 SIGNALWATCH_TEST_REDIS_PORT）")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_setex_get_delete(self) -> None:
        self.client.setex("t:k", 5, "值一")
        self.assertEqual(self.client.get("t:k"), "值一")
        self.client.delete("t:k")
        self.assertIsNone(self.client.get("t:k"))

    def test_incr_and_ttl_survive(self) -> None:
        self.client.delete("t:n")
        self.assertEqual(self.client.incr("t:n"), 1)
        self.assertEqual(self.client.incr("t:n"), 2)
        self.client.expire("t:n", 30)
        self.assertGreater(int(self.client.command("TTL", "t:n")), 0)
        self.client.delete("t:n")

    def test_lpush_brpop_is_fifo(self) -> None:
        self.client.delete("t:q")
        self.client.lpush("t:q", "第一个")
        self.client.lpush("t:q", "第二个")
        self.assertEqual(self.client.brpop("t:q", 1)[1], "第一个")
        self.assertEqual(self.client.brpop("t:q", 1)[1], "第二个")

    def test_error_response_raises_with_text(self) -> None:
        with self.assertRaises(RedisError):
            self.client.command("HGET", "t:not-a-hash")


class RedisCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _live_redis()
        if self.client is None:
            raise unittest.SkipTest("本机没有 Redis")
        self.cache = cache.RedisCache(self.client, ttl=2, prefix="t:cache:")
        self.cache.clear()

    def tearDown(self) -> None:
        self.cache.clear()
        self.client.close()

    def test_hit_and_miss_and_stats(self) -> None:
        self.cache.get("k")                      # miss
        self.cache.set("k", {"a": 1})
        self.assertEqual(self.cache.get("k")["a"], 1)   # hit
        stats = self.cache.stats()
        self.assertEqual(stats["backend"], "redis")
        self.assertEqual((stats["hits"], stats["misses"]), (1, 1))
        self.assertEqual(stats["hit_rate"], 0.5)

    def test_two_instances_share_one_cache(self) -> None:
        self.cache.set("shared", [1, 2, 3])
        other = cache.RedisCache(self.client, ttl=2, prefix="t:cache:")
        self.assertEqual(other.get("shared"), [1, 2, 3])
        self.assertEqual(other.stats()["hits"], 1)      # 命中记在各自的进程里

    def test_broken_json_is_dropped_not_raised(self) -> None:
        self.client.setex("t:cache:bad", 5, "{不是 json")
        self.assertIs(cache.MISS, self.cache.get("bad"))

    def test_ttl_zero_disables(self) -> None:
        off = cache.RedisCache(self.client, ttl=0, prefix="t:cache:")
        off.set("z", 1)
        self.assertIs(cache.MISS, off.get("z"))


class CacheFactoryTests(unittest.TestCase):
    def test_default_is_memory(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIGNALWATCH_CACHE", None)
            self.assertIsInstance(cache.make_cache(), cache.TTLCache)

    def test_redis_without_server_raises_rather_than_silently_local(self) -> None:
        env = {"SIGNALWATCH_CACHE": "redis", "SIGNALWATCH_REDIS": "127.0.0.1:6399"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(RuntimeError):
                cache.make_cache()


class WakeupTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"SIGNALWATCH_WAKEUP": ""}, clear=False):
            self.assertFalse(wakeup.enabled())

    def test_notify_then_wait_wakes_worker(self) -> None:
        client = _live_redis()
        if client is None:
            raise unittest.SkipTest("本机没有 Redis")
        env = {"SIGNALWATCH_WAKEUP": "redis",
               "SIGNALWATCH_REDIS": f"127.0.0.1:{client.port}"}
        client.delete(wakeup.KEY)
        client.close()
        with mock.patch.dict(os.environ, env):
            self.assertTrue(wakeup.enabled())
            wakeup.notify(42)
            self.assertEqual(wakeup.wait(2), 42)
            self.assertIsNone(wakeup.wait(1))          # 没门铃就超时返回 None


if __name__ == "__main__":
    unittest.main()
