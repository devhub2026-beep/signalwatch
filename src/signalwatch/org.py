"""组织（多团队隔离的骨架）。

组织权限不是把 if 写死在代码里，而是先有「组织」这个实体、用户和条目
都能归属到组织，后续查询层再按 org_id 过滤。org_id=0 表示未归属/平台级，
与升级前单租户行为完全一致。调用方负责事务。
"""
from __future__ import annotations

import re
import sqlite3
import time

try:
    from psycopg2 import IntegrityError as _pg_integrity
except ImportError:  # SQLite 模式没有 psycopg2，用占位
    _pg_integrity = sqlite3.IntegrityError

_INTEGRITY_ERRORS = (sqlite3.IntegrityError, _pg_integrity)


def _slug(name: str) -> str:
    """组织名 → slug：小写、非字母数字折叠成连字符。"""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "org"


def create_org(conn: sqlite3.Connection, name: str) -> int:
    """新建组织，返回组织 id。名字为空抛 ValueError；重名抛 ValueError。"""
    name = name.strip()
    if not name:
        raise ValueError("组织名不能为空")
    try:
        cur = conn.execute(
            "INSERT INTO organizations(name, slug, created_at) VALUES (?, ?, ?)",
            (name, _slug(name), int(time.time() * 1000)),
        )
    except _INTEGRITY_ERRORS:
        raise ValueError(f"组织名已存在：{name}") from None
    return int(cur.lastrowid or 0)


def list_orgs(conn: sqlite3.Connection) -> list[dict]:
    """列出全部组织，带成员数与条目数。"""
    rows = conn.execute(
        "SELECT o.*,"
        " (SELECT COUNT(*) FROM users u WHERE u.org_id = o.id) AS members,"
        " (SELECT COUNT(*) FROM items i WHERE i.org_id = o.id) AS items"
        " FROM organizations o ORDER BY o.id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_org(conn: sqlite3.Connection, org_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return dict(row) if row else None


def set_user_org(conn: sqlite3.Connection, username: str, org_id: int) -> bool:
    """把用户归入某个组织（org_id=0 表示移出到平台级）。"""
    cur = conn.execute("UPDATE users SET org_id = ? WHERE username = ?", (org_id, username))
    return bool(cur.rowcount)


def set_item_org(conn: sqlite3.Connection, item_id: int, org_id: int) -> bool:
    """把条目标记到某个组织（org_id=0 表示全局可见）。"""
    cur = conn.execute("UPDATE items SET org_id = ? WHERE id = ?", (org_id, item_id))
    return bool(cur.rowcount)


def delete_org(conn: sqlite3.Connection, org_id: int) -> bool:
    """删组织：成员与条目先归 0（不删人、不删条目），再删组织本体。"""
    conn.execute("UPDATE users SET org_id = 0 WHERE org_id = ?", (org_id,))
    conn.execute("UPDATE items SET org_id = 0 WHERE org_id = ?", (org_id,))
    cur = conn.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
    return bool(cur.rowcount)
