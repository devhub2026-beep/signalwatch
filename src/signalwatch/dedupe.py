"""跨源去重：同一件事被两个源都报了，只留一条。

指纹（`items.fingerprint`）只能挡住同一个源的同一条；跨源抓同一件事时，
标题、时间、栏目都不一样，指纹挡不住。这里按标题的「字符二元组」算相似度，
在时间窗内跨源比较，超过阈值就把后来的标成 `dup_of = 早的那条`。

为什么不做得更聪明（比如向量）：这台机器装不了模型，而报表、订阅、投递
都只需要「别把同一件事算两遍」，二元组 + 阈值足够，且能解释（看得见为什么判成重复）。
"""

from __future__ import annotations

import re
import sqlite3
import time

THRESHOLD = 0.82
WINDOW_HOURS = 72

_PUNCT = re.compile(r"[\s\u3000，。、；：？！“”‘’（）《》〈〉【】…—－\-·,.;:?!\"'()\[\]<>/\\|_+=*&^%$#@~`]+")
_NOISE = tuple(sorted(("关于", "公告", "的通知", "的议案", "的公告", "提示性公告",
                       "临时公告", "进展公告"), key=len, reverse=True))


def normalize(title: str) -> str:
    text = _PUNCT.sub("", (title or "").lower())
    for word in _NOISE:
        text = text.replace(word, "")
    return text


def bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def find_duplicate(conn: sqlite3.Connection, item: dict, *, threshold: float = THRESHOLD,
                   window_hours: int = WINDOW_HOURS) -> int | None:
    """给一条待入库的条目找「已有记录里最像的那条」，找不到返回 None。"""
    norm = normalize(item["title"])
    if len(norm) < 6:
        return None
    since = int(item.get("published_at") or 0) - window_hours * 3600 * 1000
    until = int(item.get("published_at") or 0) + window_hours * 3600 * 1000
    # 只跟「更早的」比：早的那条是基准，晚的才是重复。不然先入库的会被后到的顶掉。
    at = int(item.get("published_at") or 0)
    rows = conn.execute(
        "SELECT id, source, title, published_at FROM items"
        " WHERE published_at BETWEEN ? AND ? AND dup_of = 0"
        " AND (published_at < ? OR (published_at = ? AND id < ?))"
        " ORDER BY published_at DESC LIMIT 400",
        (since, until, at, at, int(item.get("id") or 0)),
    ).fetchall()
    best_id, best_score = None, 0.0
    for row in rows:
        if row["source"] == item.get("source"):
            continue
        score = similarity(norm, normalize(row["title"]))
        if score > best_score:
            best_id, best_score = row["id"], score
    return best_id if best_score >= threshold else None


def mark_duplicates(conn: sqlite3.Connection, *, threshold: float = THRESHOLD,
                    limit: int = 500) -> int:
    """增量扫：从上一次扫到的位置往后看，把跨源重复的标上。返回这次标了几条。

    原来按 `published_at ASC` 取头 500 条，数据一涨就永远只扫最老那一批，
    新入库的条目一条也轮不到 —— 游标改成「上次扫到哪个 id」，才不会漏。
    """
    from .admin import get_setting, set_setting

    cursor = int(get_setting(conn, "dedupe.cursor", "0") or 0)
    rows = conn.execute(
        "SELECT id, source, title, published_at, dup_of FROM items"
        " WHERE id > ? ORDER BY id ASC LIMIT ?",
        (cursor, int(limit)),
    ).fetchall()
    if not rows:
        return 0
    marked = 0
    last_id = cursor
    for row in rows:
        item = dict(row)
        last_id = int(item["id"])
        if int(item["dup_of"] or 0):
            continue
        norm = normalize(item["title"])
        if len(norm) < 6:
            continue
        hit = find_duplicate(conn, item, threshold=threshold)
        if hit:
            with conn:
                conn.execute("UPDATE items SET dup_of = ? WHERE id = ?", (hit, item["id"]))
            marked += 1
    set_setting(conn, "dedupe.cursor", str(last_id))
    return marked


def recheck_all(conn: sqlite3.Connection, *, threshold: float = THRESHOLD, limit: int = 2000) -> int:
    """把游标归零重扫一遍（改了阈值或怀疑漏标时用）。"""
    from .admin import set_setting

    with conn:
        conn.execute("UPDATE items SET dup_of = 0 WHERE dup_of <> 0")
    set_setting(conn, "dedupe.cursor", "0")
    total = 0
    while True:
        n = mark_duplicates(conn, threshold=threshold, limit=limit)
        if n == 0:
            rows = conn.execute("SELECT COUNT(*) FROM items WHERE id > ?",
                                (int(get_cursor(conn)),)).fetchone()[0]
            if rows == 0:
                break
        total += n
        left = conn.execute("SELECT COUNT(*) FROM items WHERE id > ?",
                            (int(get_cursor(conn)),)).fetchone()[0]
        if left == 0:
            break
    return total


def get_cursor(conn: sqlite3.Connection) -> int:
    from .admin import get_setting

    return int(get_setting(conn, "dedupe.cursor", "0") or 0)


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    dups = conn.execute("SELECT COUNT(*) FROM items WHERE dup_of <> 0").fetchone()[0]
    return {"total": total, "duplicates": dups,
            "rate": round(dups / total * 100, 1) if total else 0.0}
