"""进程内 TTL 缓存：只解决「同一秒内几十个请求读同一行数据」这一件事。

不引入 Redis 的理由写在 README「为什么没上 Redis」：本机 SQLite 首屏 0.4ms，
瓶颈在网络与 fsync，不在查询；缓存只挡重复读，用带锁的字典就够。
真要多进程共享时，换 Redis 是接口不变（get/set/clear/stats）的替换。
"""

from __future__ import annotations

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
