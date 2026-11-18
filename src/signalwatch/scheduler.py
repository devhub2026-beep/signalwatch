"""调度：一个后台线程，按每个源自己的节奏跑。

为什么是单线程：这些源都是 HTTP 拉取，串行最省事也不容易被封；
节奏由 RateLimiter 保证（每源请求间隔 ≥ min_interval）。
要并发就把 sources 分片成多个 Crawler，SQLite 已有 WAL + busy_timeout 兜底。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from . import ingest
from .config import SETTINGS, Settings
from .sources.base import RateLimiter, fetch_with_retry, normalize_records
from .db import connect


@dataclass
class TickResult:
    source: str
    ok: bool
    fetched: int
    inserted: int
    updated: int
    error: str | None


class Crawler:
    def __init__(self, settings: Settings | None = None, sources=None):
        self.settings = settings or SETTINGS
        self.settings.ensure_dirs()
        self.conn = connect(self.settings.db_path)
        from .db import init_db

        init_db(self.conn)
        self.sources = {s.name: s for s in (sources or _registry())}
        ingest.sync_sources(self.conn, self.sources.values())
        self._paused: set[str] = {
            str(r["name"])
            for r in self.conn.execute("SELECT name FROM sources WHERE paused = 1")
        }
        self._paused_sources: dict[str, object] = {}
        for name in list(self._paused):
            source = self.sources.pop(name, None)
            if source is not None:
                self._paused_sources[name] = source
        self.limiter = RateLimiter(self.settings.min_interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 单源 -----------------------------------------------------------------
    def run_source(self, name: str, *, force: bool = False) -> TickResult | None:
        source = self.sources.get(name)
        if source is None:
            raise KeyError(f"没有这个源：{name}")
        if not force and not self._due(name):
            return None

        started = ingest.now_ms()
        self.limiter.wait()
        cursor_raw = self.conn.execute(
            "SELECT last_cursor FROM sources WHERE name = ?", (name,)
        ).fetchone()
        cursor = cursor_raw["last_cursor"] if cursor_raw else None

        outcome = fetch_with_retry(
            lambda: source.fetch(cursor, self.settings.page_size),
            attempts=3,
            backoff=1.0,
        )
        if not outcome.ok:
            count = ingest.mark_failure(
                self.conn, name, outcome.error or "unknown", threshold=self.settings.fail_threshold
            )
            ingest.record_run(
                self.conn, name, started_at=started, ok=False, fetched=0,
                inserted=0, updated=0, error=outcome.error,
            )
            if count >= self.settings.fail_threshold:
                self._alert_failure(name, count, outcome.error or "unknown")
            return TickResult(name, False, 0, 0, 0, f"{outcome.error}（连续失败 {count} 次）")

        records = normalize_records(outcome.records)
        inserted, updated = ingest.save_records(self.conn, name, records)
        ingest.record_run(
            self.conn, name, started_at=started, ok=True, fetched=len(records),
            inserted=inserted, updated=updated, error=None,
        )
        ingest.mark_success(self.conn, name, self._next_cursor(cursor, records))
        # 新进来的条目顺手判一次跨源重复：同一件事被两个源都报，只留一条
        try:
            from . import dedupe

            if inserted:
                dedupe.mark_duplicates(self.conn, limit=200)
        except Exception:
            pass
        return TickResult(name, True, len(records), inserted, updated, None)

    # ---- 一轮 -----------------------------------------------------------------
    def tick(self) -> list[TickResult]:
        results: list[TickResult] = []
        for name in self.sources:
            try:
                r = self.run_source(name)
            except Exception as exc:  # 单个源炸了不拖垮整轮
                ingest.mark_failure(
                    self.conn, name, f"{type(exc).__name__}: {exc}",
                    threshold=self.settings.fail_threshold,
                )
                results.append(TickResult(name, False, 0, 0, 0, str(exc)))
                continue
            if r is not None:
                results.append(r)
        self._auto_enrich(results)
        return results

    # ---- 失败告警 -------------------------------------------------------------
    def _alert_failure(self, name: str, count: int, error: str) -> None:
        """连续失败到达阈值才发一封，且同一源 6 小时内只发一封，避免刷屏。"""
        try:
            from . import admin, notify

            if count < self.settings.fail_threshold:  # 调用方已经判过一次，这里再兜一层
                return
            if admin.get_setting(self.conn, "notify.alert", "1") != "1":
                return
            if not notify.is_ready(self.conn):
                return
            last = admin.get_setting(self.conn, f"alert.last.{name}", "")
            now = ingest.now_ms()
            if last.isdigit() and now - int(last) < 6 * 3600 * 1000:
                return
            subject = f"SignalWatch 告警：{name} 连续失败 {count} 次"
            body = (
                f"源 {name} 已经连续失败 {count} 次，页面上的状态会变成「降级」。\n\n"
                f"最后一次错误：\n{error}\n\n"
                f"可以先去「数据源」页看它的间隔设置，或者手动抓一轮确认站点是否改了结构。"
            )
            notify.send(self.conn, subject, body)
            admin.set_setting(self.conn, f"alert.last.{name}", str(now))
        except Exception:
            pass  # 告警失败不能影响抓取

    # ---- 正文自动补抓 ---------------------------------------------------------
    def _auto_enrich(self, results: list[TickResult]) -> None:
        """有新闻条目进来就顺手抠几条正文，每轮最多 10 条，覆盖所有能用的源。

        放在这里而不是定时任务里，是因为「新条目不在时抠旧条目」没有意义：
        抠正文的目的就是让用户点开首屏那条时能看到内容。
        之前只抓 cninfo，导致 ccgp/ithome/sspai 的正文长期为 0，这里改为覆盖
        `enrich.SOURCE_KIND` 里 kind != skip 的全部源（arxiv 摘要已在 feed 里，仍跳过）。
        """
        if not any(r.ok and r.inserted for r in results):
            return
        try:
            from . import admin, enrich

            if admin.get_setting(self.conn, "enrich.auto", "1") != "1":
                return
            enrich.enrich(self.conn, limit=10, only=None)
        except Exception:
            pass

    # ---- 后台循环 -------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="signalwatch-crawler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(15.0)

    # ---- 内部 -----------------------------------------------------------------
    def _due(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT last_run_at, interval_seconds, fail_count FROM sources WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return True
        if row["last_run_at"] is None:
            return True
        wait = max(int(row["interval_seconds"]), 1) * 1000
        if row["fail_count"]:
            backoff = min(
                self.settings.max_backoff,
                self.settings.backoff_base * (2 ** (int(row["fail_count"]) - 1)),
            )
            wait = int(backoff * 1000)
        return ingest.now_ms() - int(row["last_run_at"]) >= wait

    def _next_cursor(self, cursor_raw: str | None, records) -> str | None:
        if not records:
            return cursor_raw
        from .sources.base import dumps_cursor, loads_cursor

        state = loads_cursor(cursor_raw)
        state["since_ms"] = max(int(r.published_at) for r in records)
        return dumps_cursor(state)

    def apply_patch(self, name: str, *, interval_seconds: int | None = None,
                    enabled: bool | None = None) -> None:
        """管理员改一个源的间隔/启停。改了立刻生效，下一轮 tick 按新值判。"""
        if name not in self.sources:
            raise KeyError(f"没有这个源：{name}")
        with self.conn:
            if interval_seconds is not None:
                self.conn.execute(
                    "UPDATE sources SET interval_seconds = ? WHERE name = ?",
                    (int(interval_seconds), name),
                )
            if enabled is not None:
                self.conn.execute(
                    "UPDATE sources SET paused = ? WHERE name = ?", (0 if enabled else 1, name)
                )
                self.set_source_enabled(name, bool(enabled))

    def set_source_enabled(self, name: str, enabled: bool) -> None:
        """运行时停用/启用：从调度表里拿掉或放回。"""
        if enabled:
            if name not in self._paused:
                return
            self._paused.discard(name)
            source = self._paused_sources.pop(name, None)
            if source is not None:
                self.sources[name] = source
            return
        source = self.sources.pop(name, None)
        if source is not None:
            self._paused.add(name)
            self._paused_sources[name] = source

    def snapshot(self) -> dict:
        sources = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY name")
        ]
        runs = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 20")
        ]
        return {"sources": sources, "runs": runs, "db": _db_stats(self.conn)}


def _registry():
    from .sources import default_sources

    return default_sources()


def _db_stats(conn):
    from .db import db_stats

    return db_stats(conn)


