"""报表：按天、按源、按分类、按公司出数。

报表的意义是能回答"变化"：今天比昨天多多少、哪个源在掉、哪家公司最近动静最多。
数字全部来自 SQL，不来自模型。
"""

from __future__ import annotations

import datetime as _dt

from .board import CATEGORIES


def _day_start_ms(day: _dt.date) -> int:
    return int(_dt.datetime(day.year, day.month, day.day).timestamp() * 1000)


def daily(conn, *, days: int = 7, today: _dt.date | None = None) -> list[dict]:
    today = today or _dt.date.today()
    out = []
    for offset in range(days - 1, -1, -1):
        day = today - _dt.timedelta(days=offset)
        start = _day_start_ms(day)
        end = start + 24 * 3600 * 1000
        total = int(conn.execute(
            "SELECT COUNT(*) FROM items WHERE published_at >= ? AND published_at < ?", (start, end)
        ).fetchone()[0])
        revised = int(conn.execute(
            "SELECT COUNT(*) FROM items WHERE revised_at >= ? AND revised_at < ?", (start, end)
        ).fetchone()[0])
        out.append({"day": day.isoformat(), "items": total, "revised": revised})
    return out


def by_source(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT source, COUNT(*) AS n, MAX(published_at) AS latest"
        " FROM items GROUP BY source ORDER BY n DESC"
    ).fetchall()
    return [{"source": r["source"], "items": int(r["n"]), "latest": int(r["latest"] or 0)} for r in rows]


def by_category(conn) -> list[dict]:
    out = []
    for name, sources in CATEGORIES.items():
        marks = ",".join("?" * len(sources))
        n = int(conn.execute(
            f"SELECT COUNT(*) FROM items WHERE source IN ({marks})", tuple(sources)
        ).fetchone()[0])
        out.append({"category": name, "items": n})
    other = int(conn.execute(
        "SELECT COUNT(*) FROM items WHERE source NOT IN"
        f" ({','.join('?' * len(sum(CATEGORIES.values(), ())))})",
        tuple(sum(CATEGORIES.values(), ())),
    ).fetchone()[0]) if CATEGORIES else 0
    if other:
        out.append({"category": "其他", "items": other})
    return out


def top_companies(conn, *, limit: int = 10, days: int = 30, today: _dt.date | None = None) -> list[dict]:
    today = today or _dt.date.today()
    since = _day_start_ms(today - _dt.timedelta(days=days))
    rows = conn.execute(
        "SELECT sec_code, sec_name, COUNT(*) AS n FROM items"
        " WHERE sec_code IS NOT NULL AND sec_code != '' AND published_at >= ?"
        " GROUP BY sec_code ORDER BY n DESC LIMIT ?",
        (since, max(1, min(int(limit), 100))),
    ).fetchall()
    return [
        {"sec_code": r["sec_code"], "sec_name": r["sec_name"] or "", "items": int(r["n"])}
        for r in rows
    ]


def overview(conn, *, days: int = 7, today: _dt.date | None = None) -> dict:
    revision_total = int(conn.execute("SELECT COUNT(*) FROM items WHERE version > 1").fetchone()[0])
    source_rows = by_source(conn)
    return {
        "days": days,
        "daily": daily(conn, days=days, today=today),
        "by_source": source_rows,
        "by_category": by_category(conn),
        "top_companies": top_companies(conn, days=max(days, 30), today=today),
        "sources": len(source_rows),
        "revisions": revision_total,
    }
