"""来源 → 默认分类。

一个条目进来就该有自己的分类，不能等有人手动点「自动归类」。
以前 `admin.auto_categorize` 只有测试和 CLI 会调，所以新抓的 604 条一直是
「未分类」——列表页按分类筛的时候它们集体消失。这里把映射抽出来，
让「入库即归类」和「巡检回填」走同一张表。
"""

from __future__ import annotations

import sqlite3

# 每条记录所属的一级分类（categories 表里必须有同名分类）
SOURCE_CATEGORY: dict[str, str] = {
    "cninfo": "金融",       # 巨潮：上市公司公告
    "arxiv": "学术",        # arXiv 全科
    "arxiv-fin": "金融",    # arXiv 金融计量
    "ithome": "科技",
    "sspai": "科技",
    "stats": "政策",        # 国家统计局
    "ccgp": "政策",         # 中国政府采购网
}

# 默认分类表；顺序即展示顺序
DEFAULT_CATEGORIES: tuple[str, ...] = ("金融", "科技", "学术", "政策", "招聘")


def name_for(source: str) -> str | None:
    """来源对应的分类名；没登记的源返回 None（不硬塞进「其他」）。"""
    return SOURCE_CATEGORY.get(source)


def ensure_seeded(conn: sqlite3.Connection) -> None:
    """保证默认分类都在（幂等）。调用方负责事务。"""
    import time

    now = int(time.time() * 1000)
    for i, name in enumerate(DEFAULT_CATEGORIES):
        conn.execute(
            "INSERT INTO categories(name, sort, enabled, created_at) VALUES (?, ?, 1, ?)"
            " ON CONFLICT(name) DO NOTHING",
            (name, i * 10, now),
        )


def assign(conn: sqlite3.Connection, item_id: int, source: str) -> bool:
    """给一条记录写默认分类；写成功返回 True。调用方负责事务。"""
    name = name_for(source)
    if not name:
        return False
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row is None:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO item_cat(item_id, category_id) VALUES (?, ?)",
        (int(item_id), int(row[0])),
    )
    return bool(cur.rowcount)


def backfill(conn: sqlite3.Connection) -> int:
    """把还没有分类的条目按来源补上，返回补了几条。调用方负责事务。"""
    rows = conn.execute(
        "SELECT i.id, i.source FROM items i"
        " WHERE NOT EXISTS (SELECT 1 FROM item_cat ic WHERE ic.item_id = i.id)"
    ).fetchall()
    done = 0
    for r in rows:
        if assign(conn, int(r[0]), str(r[1])):
            done += 1
    return done
