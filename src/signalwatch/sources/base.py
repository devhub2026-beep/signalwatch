"""源协议与抓取基础设施：限速、重试、退避、失败计数。

一个源要实现的就是两条：
- ``fetch(cursor)``：拿回一批 Record（新→旧或旧→新都行，ingest 按 fingerprint 去重）
- ``advance(cursor, records)``：算出下次从哪继续

其余（限速、退避、把失败记到 sources 表）由基类统一做，避免每个源各写一套。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


@dataclass
class Record:
    """一条待入库的消息。字段全部是"抓下来那一刻的事实"。"""

    external_id: str
    title: str
    published_at: int  # 毫秒时间戳
    summary: str = ""
    url: str = ""
    sec_code: str | None = None
    sec_name: str | None = None
    kind: str = "announcement"
    tags: str = ""

    def content_hash_input(self) -> str:
        return f"{self.title}\x1f{self.summary}"


class Source(Protocol):
    name: str
    kind: str
    url: str
    interval_seconds: int

    def fetch(self, cursor: str | None, limit: int) -> list[Record]: ...


@dataclass
class RateLimiter:
    """两次请求之间至少间隔 min_interval 秒。进程内共享一个实例。"""

    min_interval: float
    _last: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


class RetryableError(RuntimeError):
    """5xx、超时、连接被重置——值得再试一次的错。"""


@dataclass
class FetchOutcome:
    records: list[Record] = field(default_factory=list)
    ok: bool = True
    error: str | None = None


def http_get(url: str, *, timeout: float, user_agent: str, headers: dict[str, str] | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == 429:
            raise RetryableError(f"HTTP {exc.code}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RetryableError(str(exc)) from exc


def http_post_form(
    url: str, data: dict[str, Any], *, timeout: float, user_agent: str
) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == 429:
            raise RetryableError(f"HTTP {exc.code}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RetryableError(str(exc)) from exc


def fetch_with_retry(
    fn,
    *,
    attempts: int = 3,
    backoff: float = 1.0,
    on_retry=None,
) -> FetchOutcome:
    """跑 fn，RetryableError 时按指数退避重试；用尽后把错误装进 outcome 而不是抛出去。"""
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            return FetchOutcome(records=list(fn()), ok=True)
        except RetryableError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == attempts:
                break
            delay = backoff * (2 ** (attempt - 1))
            if on_retry is not None:
                on_retry(attempt, delay, last_error)
            time.sleep(delay)
        except Exception as exc:  # 不可重试：解析失败、字段变了
            return FetchOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
    return FetchOutcome(ok=False, error=last_error)


def dumps_cursor(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads_cursor(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_records(records: Iterable[Record]) -> list[Record]:
    """去掉空标题、去重（同一次抓取里同一个 external_id 只留最新的一条）。"""
    seen: dict[str, Record] = {}
    for r in records:
        if not r.title or not r.external_id:
            continue
        seen[r.external_id] = r
    return list(seen.values())
