"""PostgreSQL 后端：把基于 sqlite3 的代码透明映射到 PostgreSQL。

通过环境变量 ``SIGNALWATCH_DB_URL`` 切换（形如
``postgresql://signalwatch:密码@127.0.0.1:5432/signalwatch``），
``db.connect()`` 检测到该变量时自动走本模块。

适配层只做「方言翻译」，不改业务代码的调用方式：
- 占位符 ``?`` → ``%s``
- ``PRAGMA ...`` → 跳过（PG 无对应，返回空语句）
- ``INSERT OR IGNORE`` → ``INSERT ... ON CONFLICT DO NOTHING``
- ``INSERT OR REPLACE`` → ``INSERT ... ON CONFLICT DO UPDATE``（按表特判）
- ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``SERIAL PRIMARY KEY``
- ``sqlite3.Row`` → psycopg2 ``RealDictRow``（同样支持 ``row["x"]`` 与 ``dict(row)``）
- ``lastrowid`` → 对已知自增表自动补 ``RETURNING id``

已知限制（后续补）：
- ``maintenance.backup`` / 直接 ``sqlite3.connect`` 仍是 SQLite 专属，PG 模式跳过。
- ``sqlite3.IntegrityError`` 的捕获处需换 ``psycopg2.IntegrityError``（auth.py）。
"""
from __future__ import annotations

import re
from typing import Any

#: 有自增 id 列、需要 lastrowid 的表
_ID_TABLES = {"items", "revisions", "runs", "rules", "users",
              "categories", "jobs", "webhooks", "organizations"}

#: INSERT OR REPLACE 的目标表 → (冲突列, 需要覆盖更新的列)
_REPLACE_TABLES = {
    "app_meta": ("key", ["value"]),
}


def translate_ddl(sql: str) -> str:
    """建表语句的方言翻译。

    SignalWatch 用毫秒时间戳（int64 范围），SQLite 的 INTEGER 是 64 位，
    PG 的 INTEGER 只有 32 位，存不下，所以整型统一翻成 BIGINT / BIGSERIAL。
    """
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    return sql.replace("INTEGER", "BIGINT")


def translate(sql: str) -> str:
    """DML 语句的方言翻译。PRAGMA 返回空串，调用方跳过执行。"""
    s = sql.strip()
    if not s:
        return s
    if s.upper().startswith("PRAGMA"):
        # PRAGMA table_info(x) 在 PG 用 information_schema 查列
        m = re.match(r"PRAGMA\s+table_info\((\w+)\)", s, re.IGNORECASE)
        if m:
            return (
                "SELECT column_name AS name FROM information_schema.columns"
                f" WHERE table_name = '{m.group(1).lower()}'"
            )
        # 其余 PRAGMA（busy_timeout/journal_mode/synchronous/foreign_keys）无对应
        return ""

    # 占位符：? → %s
    s = s.replace("?", "%s")

    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    if re.match(r"INSERT\s+OR\s+IGNORE\b", s, re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+IGNORE\b", "INSERT", s, count=1, flags=re.I)
        if "ON CONFLICT" not in s.upper():
            s += " ON CONFLICT DO NOTHING"
        return s

    # INSERT OR REPLACE → ON CONFLICT DO UPDATE（按表特判）
    if re.match(r"INSERT\s+OR\s+REPLACE\b", s, re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+REPLACE\b", "INSERT", s, count=1, flags=re.I)
        if "ON CONFLICT" not in s.upper():
            m = re.match(r"INSERT\s+INTO\s+(\w+)", s, re.IGNORECASE)
            if m and m.group(1).lower() in _REPLACE_TABLES:
                conflict_col, cols = _REPLACE_TABLES[m.group(1).lower()]
                assignments = ", ".join(
                    f"{c} = EXCLUDED.{c}" for c in cols
                )
                s += f" ON CONFLICT({conflict_col}) DO UPDATE SET {assignments}"
        return s

    return s


def _needs_returning(sql: str) -> bool:
    """判断单行 INSERT 是否需要补 RETURNING id 以支持 lastrowid。"""
    if "RETURNING" in sql.upper():
        return False
    m = re.match(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", sql, re.IGNORECASE)
    if not m:
        return False
    return m.group(1).lower() in _ID_TABLES


class Row(dict):
    """模拟 sqlite3.Row：同时支持 ``row["name"]`` 和 ``row[0]`` 两种索引。

    sqlite3.Row 既可用列名也可用整数下标取列，而 psycopg2 的
    RealDictRow 只支持列名；业务代码里存在 ``row[0]`` 的写法
    （如 auth._secret），这里统一补齐。
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            key = list(self.keys())[key]
        return dict.__getitem__(self, key)


class PGCursor:
    """psycopg2 游标的薄封装，模拟 sqlite3.Cursor 的关键接口。"""

    def __init__(self, raw, conn: "PGConnection") -> None:
        self._raw = raw
        self._conn = conn
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: Any = ()) -> "PGCursor":
        # DDL 走建表翻译，DML 走通用翻译；两者统一在这里处理，
        # 保证 RETURNING id 补全和 lastrowid 逻辑不绕道。
        if sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")):
            sql = translate_ddl(sql)
        else:
            sql = translate(sql)
        if not sql:
            self.lastrowid = None
            return self
        need_ret = _needs_returning(sql)
        if need_ret:
            sql += " RETURNING id"
        self._raw.execute(sql, params)
        if need_ret:
            row = self._raw.fetchone()
            if row is not None and "id" in row:
                self.lastrowid = int(row["id"])
        return self

    def fetchone(self):
        row = self._raw.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(r) for r in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    def close(self) -> None:
        self._raw.close()


class PGConnection:
    """模拟 sqlite3.Connection 的最小接口，让现有代码透明跑在 PG 上。"""

    def __init__(self, raw) -> None:
        self._raw = raw
        self.row_factory = None  # 占位，兼容 sqlite3 语义（实际由 RealDictCursor 提供）

    def execute(self, sql: str, params: Any = ()) -> PGCursor:
        cur = self._raw.cursor()
        return PGCursor(cur, self).execute(sql, params)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "PGConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def connect(url: str) -> PGConnection:
    """连 PostgreSQL，返回 sqlite3 风格的连接对象。"""
    import psycopg2
    import psycopg2.extras

    raw = psycopg2.connect(url, connect_timeout=10)
    raw.autocommit = False
    raw.cursor_factory = psycopg2.extras.RealDictCursor
    return PGConnection(raw)


__all__ = ["PGConnection", "PGCursor", "connect", "translate", "translate_ddl"]
