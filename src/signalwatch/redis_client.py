"""一个只有 200 行的 Redis 客户端：够用就行，不引第三方包。

为什么要它：项目原本零第三方依赖（HTTP 自己写、xlsx 自己写、PDF 自己解），
加一个 `redis` 包就意味着打包体积、版本冲突、离线装不上。
我们需要的命令只有八条：PING / GET / SETEX / DEL / INCR / EXPIRE / LPUSH / BRPOP。

协议是 RESP2（文本行 + 长度前缀），自己拼字节比调库更可控，
出错时能直接把服务器返回的那行原文抛出来。
"""

from __future__ import annotations

import os
import socket
import time


class RedisError(RuntimeError):
    pass


class Redis:
    """最小 RESP2 客户端。连接断开会重连一次（长跑进程必须有这条）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, *,
                 db: int = 0, password: str = "", timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    # ---- 连接 --------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "Redis | None":
        """SIGNALWATCH_REDIS=host:port（不设就返回 None，调用方退回本地实现）。"""
        raw = os.environ.get("SIGNALWATCH_REDIS", "").strip()
        if not raw:
            return None
        host, _, port = raw.partition(":")
        return cls(host or "127.0.0.1", int(port or 6379),
                   password=os.environ.get("SIGNALWATCH_REDIS_PASSWORD", ""))

    def _connect(self) -> None:
        if self._sock is not None:
            return
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._buf = b""
        if self.password:
            self.command("AUTH", self.password)
        if self.db:
            self.command("SELECT", str(self.db))

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""

    # ---- 协议 --------------------------------------------------------------
    def _write(self, *args: str) -> None:
        self._connect()
        parts = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = str(a).encode()
            parts.append(b"$%d\r\n%s\r\n" % (len(b), b))
        assert self._sock is not None
        self._sock.sendall(b"".join(parts))

    def _readline(self) -> bytes:
        while b"\r\n" not in self._buf:
            self._recv()
        line, _, self._buf = self._buf.partition(b"\r\n")
        return line

    def _recv(self) -> None:
        assert self._sock is not None
        chunk = self._sock.recv(65536)
        if not chunk:
            raise RedisError("连接被 Redis 关掉了")
        self._buf += chunk

    def _read(self):
        line = self._readline()
        kind, rest = line[:1], line[1:]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RedisError(rest.decode(errors="replace"))
        if kind == b":":
            return int(rest)
        if kind == b"$":
            n = int(rest)
            if n == -1:
                return None
            while len(self._buf) < n + 2:
                self._recv()
            data, _, self._buf = self._buf[:n], self._buf[n:n + 2], self._buf[n + 2:]
            return data.decode(errors="replace")
        if kind == b"*":
            n = int(rest)
            if n == -1:
                return None
            return [self._read() for _ in range(n)]
        raise RedisError(f"看不懂的响应：{line[:80]!r}")

    def command(self, *args: str):
        for attempt in (0, 1):
            try:
                self._write(*args)
                return self._read()
            except (OSError, RedisError) as exc:
                self.close()
                if attempt or isinstance(exc, RedisError) and "关掉" not in str(exc):
                    raise
                time.sleep(0.05)
        raise RedisError("unreachable")

    # ---- 我们用到的命令 -----------------------------------------------------
    def ping(self) -> bool:
        try:
            return self.command("PING") == "PONG"
        except (OSError, RedisError):
            return False

    def get(self, key: str):
        return self.command("GET", key)

    def setex(self, key: str, seconds: int, value: str) -> None:
        self.command("SETEX", key, str(max(1, int(seconds))), value)

    def delete(self, *keys: str) -> int:
        return int(self.command("DEL", *keys) or 0)

    def incr(self, key: str) -> int:
        return int(self.command("INCR", key) or 0)

    def expire(self, key: str, seconds: int) -> None:
        self.command("EXPIRE", key, str(int(seconds)))

    def lpush(self, key: str, value: str) -> int:
        return int(self.command("LPUSH", key, value) or 0)

    def brpop(self, key: str, timeout: float = 0.0):
        """右边弹一个；timeout=0 表示不阻塞。返回 (key, value) 或 None。"""
        raw = self.command("BRPOP", key, str(int(max(1, timeout)) if timeout else 1))
        if not raw:
            return None
        return raw[0], raw[1]

    def __enter__(self) -> "Redis":
        self._connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
