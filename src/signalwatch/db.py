"""SQLite 落地：表结构、连接、迁移。

设计要点（都在表里体现）：
- items.fingerprint UNIQUE —— 同一条消息只进库一次，靠数据库约束而不是靠 Python 的 if
- items.content_hash + revisions 表 —— 内容被改了能看出来，不是简单地"又抓到一条"
- sources.last_cursor —— 断点续抓，重启不从零开始
- sources.fail_count / status —— 源挂了在页面上能看见，不静默
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 8

# 一条 sqlite 连接不能被多个线程同时用（游标会互相踩，报 "not an error" / "another row available"）。
# 这里给每条连接配一把锁，所有读写都先拿锁 —— 并发安全靠这一处，而不是靠调用方自觉。
_LOCKS: dict[int, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_for(conn: sqlite3.Connection) -> threading.RLock:
    key = id(conn)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def guard(conn: sqlite3.Connection):
    with lock_for(conn):
        yield conn

DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        name             TEXT PRIMARY KEY,
        kind             TEXT NOT NULL,
        url              TEXT NOT NULL,
        enabled          INTEGER NOT NULL DEFAULT 1,
        interval_seconds INTEGER NOT NULL DEFAULT 300,
        last_cursor      TEXT,
        last_run_at      INTEGER,
        last_ok_at       INTEGER,
        fail_count       INTEGER NOT NULL DEFAULT 0,
        status           TEXT NOT NULL DEFAULT 'ok',
        last_error       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint   TEXT NOT NULL UNIQUE,
        source        TEXT NOT NULL,
        external_id   TEXT NOT NULL,
        kind          TEXT NOT NULL,
        title         TEXT NOT NULL,
        summary       TEXT NOT NULL DEFAULT '',
        url           TEXT NOT NULL DEFAULT '',
        sec_code      TEXT,
        sec_name      TEXT,
        tags          TEXT NOT NULL DEFAULT '',
        published_at  INTEGER NOT NULL,
        first_seen_at INTEGER NOT NULL DEFAULT 0,
        last_seen_at  INTEGER NOT NULL DEFAULT 0,
        content_hash  TEXT NOT NULL,
        version       INTEGER NOT NULL DEFAULT 1,
        revised_at    INTEGER,
        state         TEXT NOT NULL DEFAULT 'online',
        pinned        INTEGER NOT NULL DEFAULT 0,
        edited_at     INTEGER,
        dup_of        INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_items_source ON items(source, published_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_items_sec ON items(sec_code)",
    """
    CREATE TABLE IF NOT EXISTS revisions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint  TEXT NOT NULL,
        version      INTEGER NOT NULL,
        title        TEXT NOT NULL,
        summary      TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        seen_at      INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_revisions_fp ON revisions(fingerprint, version)",
    """
    CREATE TABLE IF NOT EXISTS runs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT NOT NULL,
        started_at INTEGER NOT NULL,
        ended_at   INTEGER,
        ok         INTEGER NOT NULL DEFAULT 0,
        fetched    INTEGER NOT NULL DEFAULT 0,
        inserted   INTEGER NOT NULL DEFAULT 0,
        updated    INTEGER NOT NULL DEFAULT 0,
        error      TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_source ON runs(source, started_at DESC)",
    # v2：人用起来才有的两件事 —— 读过没有、盯哪些词
    """
    CREATE TABLE IF NOT EXISTS marks (
        user_id    INTEGER NOT NULL DEFAULT 0,
        item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        read_at    INTEGER,
        starred_at INTEGER,
        PRIMARY KEY (user_id, item_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marks_item ON marks(item_id)",
    """
    CREATE TABLE IF NOT EXISTS rules (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword    TEXT NOT NULL,
        category   TEXT NOT NULL DEFAULT '',
        enabled    INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        UNIQUE(keyword, category)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind, published_at DESC)",
    # v3：企业化要的三张表 —— 人（用户）、账（审计）、配置（key-value）
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        pwd_salt      TEXT NOT NULL,
        pwd_hash      TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'viewer',
        enabled       INTEGER NOT NULL DEFAULT 1,
        created_at    INTEGER NOT NULL,
        last_login_at INTEGER NOT NULL DEFAULT 0,
        token_version INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        at       INTEGER NOT NULL,
        username TEXT NOT NULL,
        action   TEXT NOT NULL,
        target   TEXT NOT NULL DEFAULT '',
        detail   TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at DESC)",
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # v4：业务层 —— 分类、条目状态、任务队列、设置
    """
    CREATE TABLE IF NOT EXISTS categories (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE,
        sort       INTEGER NOT NULL DEFAULT 100,
        enabled    INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS item_cat (
        item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        PRIMARY KEY (item_id, category_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kind       TEXT NOT NULL,
        target     TEXT NOT NULL DEFAULT '',
        status     TEXT NOT NULL DEFAULT 'queued',
        priority   INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        result     TEXT NOT NULL DEFAULT '',
        error      TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC, id)",
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # v5：角色与权限从代码搬进表里 —— 加一个角色不用改代码，改一行数据就行
    """
    CREATE TABLE IF NOT EXISTS roles (
        name   TEXT PRIMARY KEY,
        rank   INTEGER NOT NULL,
        perms  TEXT NOT NULL DEFAULT '',
        builtin INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_items_state ON items(state)",
]

# 内置角色：rank 决定继承关系（高 rank 自动拥有低 rank 的全部权限）
BUILTIN_ROLES = [
    ("viewer", 10, "items.read", 1),
    ("auditor", 20, "items.read,audit.read", 1),
    ("analyst", 50, "items.read,items.write,items.export,sources.manage,jobs.manage", 1),
    ("admin", 100, "items.read,items.write,items.export,items.delete,sources.manage,"
                    "jobs.manage,users.manage,audit.read,settings.manage,categories.manage", 1),
]


def connect(db_path: Path | str) -> sqlite3.Connection:
    """开连接。默认 WAL + busy_timeout（并发下不报 database is locked）。

    但 WAL 在「容器 bind mount 到 Windows 目录」这种场景会烂库：
    -wal/-shm 由两个 OS 各自缓存，写进去的页顺序对不上，最后
    报 `database disk image is malformed`。所以 Docker 里要设
    SIGNALWATCH_JOURNAL=delete 退回回滚日志（单写者，够用且安全）。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    journal = (os.environ.get("SIGNALWATCH_JOURNAL") or "wal").strip().lower()
    if journal in ("delete", "truncate", "persist", "memory", "off"):
        conn.execute(f"PRAGMA journal_mode={journal.upper()}")
        conn.execute("PRAGMA synchronous=FULL")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn) -> None:
    """老库补列。企业的库不能靠"删了重建"，只能往上加。"""
    wanted = {
        "sources": {"paused": "INTEGER NOT NULL DEFAULT 0"},
        "items": {"state": "TEXT NOT NULL DEFAULT 'online'",
                  "pinned": "INTEGER NOT NULL DEFAULT 0",
                  "edited_at": "INTEGER",
                  "dup_of": "INTEGER NOT NULL DEFAULT 0",
                  "body_text": "TEXT NOT NULL DEFAULT ''",
                  "body_bytes": "INTEGER NOT NULL DEFAULT 0"},
        "rules": {"source": "TEXT NOT NULL DEFAULT ''",
                  "user_id": "INTEGER NOT NULL DEFAULT 0"},
        "users": {"email": "TEXT NOT NULL DEFAULT ''",
                  "display_name": "TEXT NOT NULL DEFAULT ''"},
    }
    for table, cols in wanted.items():
        have = _columns(conn, table)
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # v8：标记按人存。老库里的 marks 是「所有人共用一份」，迁移时统一归到 user_id=0，
    # 登录用户从此各看各的（历史标记不会丢，只是变成匿名那一份）。
    marks_cols = _columns(conn, "marks")
    if "user_id" not in marks_cols:
        conn.execute("ALTER TABLE marks RENAME TO marks_legacy")
        conn.execute(
            """
            CREATE TABLE marks (
                user_id    INTEGER NOT NULL DEFAULT 0,
                item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                read_at    INTEGER,
                starred_at INTEGER,
                PRIMARY KEY (user_id, item_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO marks(user_id, item_id, read_at, starred_at)"
            " SELECT 0, item_id, read_at, starred_at FROM marks_legacy"
        )
        conn.execute("DROP TABLE marks_legacy")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_marks_item ON marks(item_id)")

    # 老库里的条目缺 v1 历史（早期版本只在被修订时才写 revisions），补一条，
    # 否则「改了什么」永远只能从 v2 比，基线是空的。
    conn.execute(
        """
        INSERT INTO revisions (fingerprint, version, title, summary, content_hash, seen_at)
        SELECT i.fingerprint, 1, i.title, i.summary, i.content_hash, i.first_seen_at
        FROM items i
        WHERE NOT EXISTS (
            SELECT 1 FROM revisions r WHERE r.fingerprint = i.fingerprint AND r.version = 1
        )
        """
    )


def init_db(conn: sqlite3.Connection) -> None:
    with guard(conn), conn:
        for stmt in DDL:
            conn.execute(stmt)
        _migrate(conn)
        for name, rank, perms, builtin in BUILTIN_ROLES:
            conn.execute(
                "INSERT OR IGNORE INTO roles(name, rank, perms, builtin) VALUES(?,?,?,?)",
                (name, rank, perms, builtin),
            )
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_meta(key, value) VALUES('version', ?)", (str(SCHEMA_VERSION),))
        elif int(row["value"]) > SCHEMA_VERSION:
            raise RuntimeError(f"数据库版本 {row['value']} 比代码支持的 {SCHEMA_VERSION} 新，先升级代码")


def db_stats(conn: sqlite3.Connection) -> dict:
    with guard(conn):
        total = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        today_start = conn.execute(
            "SELECT strftime('%s','now','start of day','localtime') AS t"
        ).fetchone()["t"]
        today = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE first_seen_at >= ?", (int(today_start) * 1000,)
        ).fetchone()["n"]
        revised = conn.execute("SELECT COUNT(*) AS n FROM items WHERE version > 1").fetchone()["n"]
    return {"items": total, "today": today, "revised": revised}



