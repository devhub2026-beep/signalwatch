"""入库：去重、变更检测、运行记录、失败计数。

三条硬规则：
1. 同一条消息只进库一次 —— 靠 `fingerprint` 唯一索引，并发插入用 ON CONFLICT 兜住
2. 内容变了要标出来 —— 同一 fingerprint 但 content_hash 不同 → version+1，历史进 revisions
3. 每次抓取都留痕 —— runs 表记一条，成功失败都写，失败同时累加 sources.fail_count
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

from .db import guard
from .sources.base import Record


def fingerprint(source: str, external_id: str) -> str:
    return hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()


def content_hash(record: Record) -> str:
    return hashlib.sha256(record.content_hash_input().encode()).hexdigest()


def now_ms() -> int:
    return int(time.time() * 1000)


def save_records(
    conn: sqlite3.Connection, source: str, records: list[Record], *, at: int | None = None
) -> tuple[int, int]:
    """返回 (新增条数, 修订条数)。"""
    seen_at = at if at is not None else now_ms()
    inserted = 0
    updated = 0
    with guard(conn), conn:  # 一个源一轮 = 一个事务
        for rec in records:
            fp = fingerprint(source, rec.external_id)
            ch = content_hash(rec)
            cur = conn.execute(
                """
                INSERT INTO items (fingerprint, source, external_id, kind, title, summary, url,
                                   sec_code, sec_name, tags, published_at, first_seen_at,
                                   last_seen_at, content_hash, version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(fingerprint) DO NOTHING
                """,
                (
                    fp, source, rec.external_id, rec.kind, rec.title, rec.summary, rec.url,
                    rec.sec_code, rec.sec_name, rec.tags, rec.published_at, seen_at, seen_at, ch,
                ),
            )
            if cur.rowcount:
                inserted += 1
                # 第一版也进历史表，否则「改了什么」只能从 v2 开始比，v1 永远缺失
                conn.execute(
                    """
                    INSERT INTO revisions (fingerprint, version, title, summary, content_hash, seen_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (fp, 1, rec.title, rec.summary, ch, seen_at),
                )
                continue
            row = conn.execute(
                "SELECT id, content_hash, version FROM items WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if row is None:  # 理论上不可能
                continue
            if row["content_hash"] == ch:
                conn.execute("UPDATE items SET last_seen_at = ? WHERE id = ?", (seen_at, row["id"]))
                continue
            # 内容变了：升版本、留历史
            new_version = int(row["version"]) + 1
            conn.execute(
                """
                UPDATE items SET title = ?, summary = ?, url = ?, sec_code = ?, sec_name = ?,
                                 content_hash = ?, version = ?, revised_at = ?, last_seen_at = ?
                WHERE id = ?
                """,
                (
                    rec.title, rec.summary, rec.url, rec.sec_code, rec.sec_name,
                    ch, new_version, seen_at, seen_at, row["id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO revisions (fingerprint, version, title, summary, content_hash, seen_at)
                VALUES (?,?,?,?,?,?)
                """,
                (fp, new_version, rec.title, rec.summary, ch, seen_at),
            )
            updated += 1
    return inserted, updated


def sync_sources(conn: sqlite3.Connection, sources) -> None:
    """把代码里的源登记进表；已存在的不覆盖运行态字段。

    代码里删掉的源在库里的旧行会被置 enabled=0（而不是删掉 —— 跑批历史还要留着）。
    """
    names = [s.name for s in sources]
    with guard(conn), conn:
        for s in sources:
            conn.execute(
                """
                INSERT INTO sources (name, kind, url, interval_seconds, enabled)
                VALUES (?,?,?,?,1)
                ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, url=excluded.url,
                                               interval_seconds=excluded.interval_seconds,
                                               enabled=1
                """,
                (s.name, getattr(s, "kind", ""), getattr(s, "url", ""),
                 int(getattr(s, "interval_seconds", 300))),
            )
        if names:
            marks = ",".join("?" * len(names))
            conn.execute(f"UPDATE sources SET enabled = 0 WHERE name NOT IN ({marks})", names)
        else:
            conn.execute("UPDATE sources SET enabled = 0")


def enabled_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY name"))


def record_run(
    conn: sqlite3.Connection,
    source: str,
    *,
    started_at: int,
    ok: bool,
    fetched: int,
    inserted: int,
    updated: int,
    error: str | None,
) -> None:
    with guard(conn), conn:
        conn.execute(
            """
            INSERT INTO runs (source, started_at, ended_at, ok, fetched, inserted, updated, error)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (source, started_at, now_ms(), 1 if ok else 0, fetched, inserted, updated, error),
        )


def mark_success(conn: sqlite3.Connection, source: str, cursor: str | None) -> None:
    with guard(conn), conn:
        conn.execute(
            """
            UPDATE sources SET last_run_at = ?, last_ok_at = ?, fail_count = 0,
                               status = 'ok', last_error = NULL, last_cursor = ?
            WHERE name = ?
            """,
            (now_ms(), now_ms(), cursor, source),
        )


def mark_failure(
    conn: sqlite3.Connection, source: str, error: str, *, threshold: int = 3
) -> int:
    """返回累计失败次数。到阈值就把 status 标成 degraded。"""
    with guard(conn), conn:
        conn.execute(
            "UPDATE sources SET last_run_at = ?, fail_count = fail_count + 1, last_error = ? WHERE name = ?",
            (now_ms(), error[:500], source),
        )
        row = conn.execute("SELECT fail_count FROM sources WHERE name = ?", (source,)).fetchone()
        count = int(row["fail_count"]) if row else 1
        if count >= threshold:
            conn.execute("UPDATE sources SET status = 'degraded' WHERE name = ?", (source,))
    return count


def query_items(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    source: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    sec_code: str | None = None,
    since_ms: int | None = None,
    include_dups: bool = False,
) -> list[dict]:
    where: list[str] = []
    args: list = []
    if not include_dups:
        where.append("dup_of = 0")
    if source:
        where.append("source = ?")
        args.append(source)
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if sec_code:
        where.append("sec_code = ?")
        args.append(sec_code)
    if since_ms:
        where.append("published_at >= ?")
        args.append(since_ms)
    if q:
        where.append("(title LIKE ? OR summary LIKE ? OR sec_name LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    sql = "SELECT * FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY published_at DESC, id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with guard(conn):
        return [dict(r) for r in conn.execute(sql, args)]
