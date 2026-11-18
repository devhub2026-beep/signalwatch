"""TTL 缓存：两种后端，接口一样（get / set / get_or_build / clear / stats）。

- `TTLCache`（默认）：进程内带锁字典。单进程部署时够用，本机 SQLite 首屏 0.4ms，
  瓶颈在网络与 fsync，不在查询。
- `RedisCache`：多进程/多容器共享。`SIGNALWATCH_CACHE=redis` 打开，配合
  `SIGNALWATCH_REDIS=host:port`；值用 JSON 存，命中率在 Redis 里用 INCR 累加。
  两个 API 实例读同一份缓存时，这才是「独立缓存层」该有的样子。

切换只改环境变量，调用方（api.py 的 `_cached`）一行都不用动。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict

MISS = object()


class TTLCache:
    def __init__(self, *, ttl: float | None = None, maxsize: int = 512) -> None:
        # SIGNALWATCH_CACHE_TTL=0 关掉缓存（对照实验用），不设就用 2 秒
        if ttl is None:
            try:
                ttl = float(os.environ.get("SIGNALWATCH_CACHE_TTL", "2"))
            except ValueError:
                ttl = 2.0
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        if self.ttl <= 0:
            self.misses += 1
            return MISS
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return MISS
            stamp, value = item
            if now - stamp >= self.ttl:
                self._data.pop(key, None)
                self.misses += 1
                return MISS
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: object) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def get_or_build(self, key: str, build):
        found = self.get(key)
        if found is not MISS:
            return found
        value = build()
        self.set(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._data),
                "maxsize": self.maxsize,
                "ttl": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }


class RedisCache:
    """共享缓存。值只能是 JSON 可序列化的东西（本项目缓存的就是这种）。"""

    def __init__(self, client, *, ttl: float | None = None, prefix: str = "sw:cache:") -> None:
        if ttl is None:
            try:
                ttl = float(os.environ.get("SIGNALWATCH_CACHE_TTL", "2"))
            except ValueError:
                ttl = 2.0
        self.client = client
        self.ttl = ttl
        self.prefix = prefix
        self._hits = 0
        self._misses = 0

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str):
        if self.ttl <= 0:
            self._misses += 1
            return MISS
        raw = self.client.get(self._key(key))
        if raw is None:
            self._misses += 1
            return MISS
        self._hits += 1
        try:
            return json.loads(raw)
        except ValueError:
            self.client.delete(self._key(key))
            self._misses += 1
            return MISS

    def set(self, key: str, value: object) -> None:
        if self.ttl <= 0:
            return
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return
        self.client.setex(self._key(key), max(1, int(round(self.ttl))), raw)

    def get_or_build(self, key: str, build):
        found = self.get(key)
        if found is not MISS:
            return found
        value = build()
        self.set(key, value)
        return value

    def clear(self) -> None:
        # 只清自己的前缀：Redis 里可能还有别的应用
        try:
            keys = self.client.command("KEYS", f"{self.prefix}*") or []
        except Exception:                                        # noqa: BLE001
            keys = []
        if keys:
            self.client.delete(*keys)

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "size": -1,          # 共享缓存不数本地条数，免得每次 KEYS
            "maxsize": -1,
            "ttl": self.ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }


def make_cache(maxsize: int = 512):
    """按 `SIGNALWATCH_CACHE` 选后端：redis / memory（默认）。"""
    backend = os.environ.get("SIGNALWATCH_CACHE", "memory").strip().lower()
    if backend == "redis":
        from .redis_client import Redis

        client = Redis.from_env()
        if client is not None and client.ping():
            return RedisCache(client)
        # 配了 redis 但连不上：不静默降级成内存，否则「共享了」是假的
        raise RuntimeError(
            "SIGNALWATCH_CACHE=redis 但连不上 Redis（检查 SIGNALWATCH_REDIS=host:port）")
    return TTLCache(maxsize=maxsize)
