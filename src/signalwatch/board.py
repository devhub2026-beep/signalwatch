"""看板查询：分类、搜索、未读/收藏、订阅规则。

这一层不碰 UI、不碰 HTTP，纯 SQL + 一点字符串处理，方便直接测。
"""

from __future__ import annotations

import time

from .db import guard

# 分类 -> 源。页面上的 tab 就是这几行；新增源时在这里挂一个分类。
CATEGORIES: dict[str, tuple[str, ...]] = {
    "金融": ("cninfo",),
    "科技": ("ithome", "sspai"),
    "学术": ("arxiv",),
}

CATEGORY_OF_SOURCE = {src: cat for cat, srcs in CATEGORIES.items() for src in srcs}


def category_of(source: str) -> str:
    return CATEGORY_OF_SOURCE.get(source, "其他")


def _like(term: str) -> str:
    # 用户输入里的 % 和 _ 不该当通配符
    safe = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{safe}%"


def board_query(
    conn,
    *,
    q: str = "",
    category: str = "",
    only_unread: bool = False,
    only_starred: bool = False,
    limit: int = 300,
    include_dups: bool = False,
) -> list[dict]:
    where = ["1=1"]
    args: list = []
    if not include_dups:
        where.append("COALESCE(i.dup_of, 0) = 0")
    if category and category in CATEGORIES:
        srcs = CATEGORIES[category]
        where.append(f"i.source IN ({','.join('?' * len(srcs))})")
        args.extend(srcs)
    if q.strip():
        # 标题、公司名、代码、摘要都搜，大小写不敏感（SQLite 的 LIKE 对 ASCII 已经不敏感）
        where.append(
            "(i.title LIKE ? ESCAPE '\\' OR i.sec_name LIKE ? ESCAPE '\\'"
            " OR i.sec_code LIKE ? ESCAPE '\\' OR i.summary LIKE ? ESCAPE '\\')"
        )
        term = _like(q.strip())
        args.extend([term] * 4)
    if only_unread:
        where.append("m.read_at IS NULL")
    if only_starred:
        where.append("m.starred_at IS NOT NULL")

    sql = f"""
        SELECT i.id, i.source, i.title, i.summary, i.url, i.sec_code, i.sec_name,
               i.published_at, i.version, i.kind,
               m.read_at, m.starred_at
        FROM items i
        LEFT JOIN marks m ON m.item_id = i.id
        WHERE {' AND '.join(where)}
        ORDER BY i.published_at DESC, i.id DESC
        LIMIT ?
    """
    with guard(conn):
        rows = [dict(r) for r in conn.execute(sql, (*args, limit))]
    for r in rows:
        r["category"] = category_of(r["source"])
        r["matched"] = matched_rules(conn, r)
    return rows


def matched_rules(conn, item: dict) -> list[str]:
    """这条命中了哪些订阅词（空列表 = 没命中）。"""
    with guard(conn):
        rules = [
            dict(r)
            for r in conn.execute("SELECT keyword, category FROM rules WHERE enabled = 1")
        ]
    text = f"{item.get('title', '')} {item.get('sec_name') or ''} {item.get('summary', '')}".lower()
    hits = []
    for rule in rules:
        if rule["category"] and rule["category"] != item.get("category"):
            continue
        if rule["keyword"].lower() in text:
            hits.append(rule["keyword"])
    return hits


def unread_count(conn) -> int:
    with guard(conn):
        return conn.execute(
            "SELECT COUNT(*) n FROM items i LEFT JOIN marks m ON m.item_id=i.id"
            " WHERE m.read_at IS NULL"
        ).fetchone()["n"]


def star_count(conn) -> int:
    with guard(conn):
        return conn.execute(
            "SELECT COUNT(*) n FROM marks WHERE starred_at IS NOT NULL"
        ).fetchone()["n"]


def mark_read(conn, item_id: int, read: bool = True) -> None:
    now = int(time.time() * 1000)
    with guard(conn), conn:
        conn.execute(
            "INSERT INTO marks(item_id, read_at) VALUES(?,?)"
            " ON CONFLICT(item_id) DO UPDATE SET read_at=excluded.read_at",
            (item_id, now if read else None),
        )


def toggle_star(conn, item_id: int) -> bool:
    """返回切换后的状态：True = 已收藏。"""
    now = int(time.time() * 1000)
    with guard(conn), conn:
        row = conn.execute("SELECT starred_at FROM marks WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO marks(item_id, starred_at) VALUES(?,?)", (item_id, now))
            return True
        new = None if row["starred_at"] else now
        conn.execute("UPDATE marks SET starred_at=? WHERE item_id=?", (new, item_id))
        return new is not None


def add_rule(conn, keyword: str, category: str = "", source: str = "", user_id: int = 0) -> bool:
    kw = keyword.strip()
    if not kw:
        raise ValueError("订阅词不能是空的")
    with guard(conn), conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO rules(keyword, category, source, user_id, enabled, created_at)"
            " VALUES(?,?,?,?,1,?)",
            (kw, category, source, int(user_id), int(time.time() * 1000)),
        )
    return cur.rowcount > 0


def list_rules(conn, user_id: int | None = None) -> list[dict]:
    """user_id=None 看全部（管理员）；给数字时只看自己的 + 全局共享（user_id=0）。"""
    sql = "SELECT * FROM rules"
    args: tuple = ()
    if user_id is not None:
        sql += " WHERE user_id IN (0, ?)"
        args = (int(user_id),)
    sql += " ORDER BY id DESC"
    with guard(conn):
        return [dict(r) for r in conn.execute(sql, args)]


def set_rule_enabled(conn, rule_id: int, enabled: bool) -> None:
    with guard(conn), conn:
        conn.execute("UPDATE rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))


def delete_rule(conn, rule_id: int) -> None:
    with guard(conn), conn:
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

def export_csv(conn, rows: list[dict]) -> str:
    """把当前筛选结果导成 CSV 文本（带 BOM，Excel 打开不乱码）。"""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["时间", "分类", "源", "代码", "公司", "标题", "链接", "版本", "命中订阅词"])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M", time.localtime(r["published_at"] / 1000)),
            r.get("category", ""),
            r.get("source", ""),
            r.get("sec_code") or "",
            r.get("sec_name") or "",
            r.get("title", ""),
            r.get("url", ""),
            r.get("version", 1),
            "、".join(r.get("matched") or []),
        ])
    return "\ufeff" + buf.getvalue()

def is_starred(conn, item_id: int) -> bool:
    row = conn.execute("SELECT starred_at FROM marks WHERE item_id = ?", (item_id,)).fetchone()
    return bool(row and row["starred_at"])

