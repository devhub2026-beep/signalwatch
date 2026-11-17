"""独立 worker：把「抓取任务」从图形界面里搬出来，服务器上没有窗口也能干活。

它只做一件事——从 `jobs` 表领一条 `queued`，抓完写回结果，再领下一条。
这样三件事就成立了：
1. 关掉桌面窗口，任务队列不会烂在那里；
2. 多个 worker 可以一起跑（`claim_job` 是「查一条 + 立刻改成 running」在同一把锁里，
   两条同时领不会领到同一条）；
3. 崩溃恢复有据可查：进程被 kill 时任务停在 `running`，`--recover` 会把它们打回
   `queued`，下次继续。
"""

from __future__ import annotations

import signal
import sqlite3
import time

from . import admin, audit, dedupe, db, enrich, taxonomy

IDLE_SLEEP = 2.0


def recover_stale(conn: sqlite3.Connection) -> int:
    """把上次没跑完的任务打回队列（进程被杀时留下的 running）。"""
    with db.guard(conn), conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', started_at = NULL,"
            " result = '', error = '上次进程中断，重新排队'"
            " WHERE status = 'running'")
        n = cur.rowcount or 0
    return n


def run_once(conn: sqlite3.Connection, crawler) -> dict | None:
    """领一条任务并做完。没有任务返回 None。"""
    job = admin.claim_job(conn)
    if not job:
        return None
    started = time.time()
    try:
        if job["kind"] == "all":
            names = [s.get("name") for s in crawler.snapshot().get("sources", [])]
        else:
            names = [job["target"]]
        parts, inserted = [], 0
        for name in names:
            if not name:
                continue
            res = crawler.run_source(name, force=True)
            inserted += int(res.inserted or 0)
            parts.append(f"{name}:{'ok' if res.ok else 'fail'}"
                         f" 抓到 {res.fetched} 新增 {res.inserted}")
        if inserted:
            dedupe.mark_duplicates(conn, limit=200)
            enrich.enrich(conn, limit=2, only=("cninfo",))
        taxonomy.backfill(conn)
        admin.finish_job(conn, job["id"], ok=True, result="; ".join(parts))
    except Exception as exc:                                    # noqa: BLE001
        admin.finish_job(conn, job["id"], ok=False,
                         error=f"{type(exc).__name__}: {exc}")
    took = round(time.time() - started, 2)
    audit.log(conn, "worker", "job.run", f"job#{job['id']}",
              f"{job['kind']}:{job['target']} 用时 {took}s")
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    return dict(row) if row else None


def loop(conn: sqlite3.Connection, crawler, *, idle: float = IDLE_SLEEP,
         max_jobs: int | None = None, stop: list[bool] | None = None) -> int:
    """一直领活干。`stop` 是外部塞 True 就能停的开关（测试与信号处理都用它）。"""
    done = 0
    while True:
        if stop and stop[0]:
            break
        job = run_once(conn, crawler)
        if job is None:
            if max_jobs is not None and done >= max_jobs:
                break
            time.sleep(idle)
            continue
        done += 1
        if max_jobs is not None and done >= max_jobs:
            break
    return done


def install_signals(stop: list[bool]) -> None:
    def _bye(_signum, _frame):
        stop[0] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _bye)
        except (AttributeError, ValueError):        # 非主线程 / 平台不支持
            pass
