"""迁移台账：这个库是怎么走到第 9 版的，谁在什么时候升的。

之前只有一个 `schema_meta.version` 数字——它只说明「现在是第几版」，
说明不了「哪几步跑过、哪步没跑、什么时候跑的」。上线出问题时，
这两件事必须能答。

设计上刻意做得很薄：
- 真正的迁移动作仍在 `db._migrate()` 里（幂等，反复跑没事）；
- 这里只维护一张台账 `schema_migrations(version, name, applied_at)`；
- 老库第一次见到这套台账时，按当前版本一次性 bootstrap 记 1..N（因为这些步骤
  确实已经生效了），不会重跑去改数据。
"""

from __future__ import annotations

import sqlite3
import time

# 版本号 → 这一步做了什么。只增不改，改了就说明有人在骗自己。
STEPS: tuple[tuple[int, str], ...] = (
    (1, "初始表：sources / items / revisions / runs / schema_meta"),
    (2, "marks（已读/收藏）+ rules（订阅词）+ items.kind 索引"),
    (3, "users / audit / app_meta；sources.paused；列级 ALTER 机制"),
    (4, "categories / item_cat / jobs / settings；items.state/pinned/edited_at"),
    (5, "rules.source；users.email/display_name；roles 表与权限点"),
    (6, "items.dup_of：跨源去重标记"),
    (7, "items.body_text/body_bytes：PDF 正文"),
    (8, "marks 按用户隔离（PRIMARY KEY(user_id,item_id)）；marks_legacy 一次性搬迁"),
    (9, "分类入库即归类：taxonomy.ensure_seeded + backfill；schema_meta 跟随迁移更新"),
)

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at INTEGER NOT NULL
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(DDL)


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    ensure_table(conn)
    return [int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def record(conn: sqlite3.Connection, version: int, name: str, *, now: int | None = None) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)",
            (version, name, int(now if now is not None else time.time())),
        )


def sync(conn: sqlite3.Connection, current: int) -> int:
    """把台账补到当前版本。返回这次新记了几条。

    只在 `db._migrate()` 跑完之后调用——那时所有步骤已经生效，
    这里记的是事实，不是期望。
    """
    ensure_table(conn)
    done = set(applied_versions(conn))
    added = 0
    for version, name in STEPS:
        if version > current:
            break
        if version not in done:
            record(conn, version, name)
            added += 1
    return added


def pending(conn: sqlite3.Connection, current: int) -> list[tuple[int, str]]:
    """代码里还没落到台账上的步骤（正常应为空）。"""
    done = set(applied_versions(conn))
    return [(v, n) for v, n in STEPS if v not in done and v <= current]


def status(conn: sqlite3.Connection, current: int) -> dict:
    ensure_table(conn)
    rows = [
        {"version": int(v), "name": n, "applied_at": int(t)}
        for v, n, t in conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        )
    ]
    done = {r["version"] for r in rows}
    return {
        "current_version": current,
        "recorded_max": max(done) if done else 0,
        "applied": rows,
        "missing": [
            {"version": v, "name": n}
            for v, n in STEPS
            if v not in done and v <= current
        ],
    }
