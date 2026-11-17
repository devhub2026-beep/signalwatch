"""后台业务层：分类、条目上下架、抓取任务队列、报表、设置。

这一层不碰界面，桌面端和网页端共用。
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
from typing import Iterable, Sequence

from .db import guard

# 任务状态：排队 → 运行 → 成功 / 失败 →（重试）→ 排队
JOB_STATES = ("queued", "running", "done", "failed")
STATE_TEXT = {"queued": "排队中", "running": "运行中", "done": "已完成", "failed": "失败"}

ITEM_STATES = ("online", "offline")
ITEM_STATE_TEXT = {"online": "在架", "offline": "下架"}

DEFAULT_CATEGORIES = ("金融", "科技", "学术", "政策", "招聘")


def _now() -> int:
    return int(time.time() * 1000)


# ---------------- 分类 ----------------
def seed_categories(conn: sqlite3.Connection) -> None:
    with guard(conn), conn:
        for i, name in enumerate(DEFAULT_CATEGORIES):
            conn.execute(
                "INSERT INTO categories(name, sort, enabled, created_at) VALUES (?, ?, 1, ?)"
                " ON CONFLICT(name) DO NOTHING",
                (name, i * 10, _now()),
            )


def list_categories(conn: sqlite3.Connection, *, only_enabled: bool = False) -> list[dict]:
    sql = "SELECT c.*, (SELECT COUNT(*) FROM item_cat ic WHERE ic.category_id = c.id) n FROM categories c"
    if only_enabled:
        sql += " WHERE c.enabled = 1"
    sql += " ORDER BY c.sort, c.id"
    with guard(conn):
        return [dict(r) for r in conn.execute(sql)]


def add_category(conn: sqlite3.Connection, name: str, sort: int = 100) -> int:
    name = name.strip()
    if not name:
        raise ValueError("分类名不能为空")
    with guard(conn), conn:
        cur = conn.execute(
            "INSERT INTO categories(name, sort, enabled, created_at) VALUES (?, ?, 1, ?)"
            " ON CONFLICT(name) DO UPDATE SET enabled = 1",
            (name, sort, _now()),
        )
        return int(cur.lastrowid or 0)


def set_category_enabled(conn: sqlite3.Connection, cid: int, enabled: bool) -> None:
    with guard(conn), conn:
        conn.execute("UPDATE categories SET enabled = ? WHERE id = ?", (1 if enabled else 0, cid))


def rename_category(conn: sqlite3.Connection, cid: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("分类名不能为空")
    with guard(conn), conn:
        conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))


def delete_category(conn: sqlite3.Connection, cid: int) -> None:
    with guard(conn), conn:
        conn.execute("DELETE FROM item_cat WHERE category_id = ?", (cid,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cid,))


def set_item_categories(conn: sqlite3.Connection, item_id: int, names: Sequence[str]) -> None:
    ids = []
    with guard(conn), conn:
        for name in names:
            row = conn.execute("SELECT id FROM categories WHERE name = ?", (name.strip(),)).fetchone()
            if row:
                ids.append(int(row["id"]))
        conn.execute("DELETE FROM item_cat WHERE item_id = ?", (item_id,))
        for cid in ids:
            conn.execute("INSERT OR IGNORE INTO item_cat(item_id, category_id) VALUES (?, ?)", (item_id, cid))


def item_categories(conn: sqlite3.Connection, item_id: int) -> list[str]:
    with guard(conn):
        rows = conn.execute(
            "SELECT c.name FROM item_cat ic JOIN categories c ON c.id = ic.category_id"
            " WHERE ic.item_id = ? ORDER BY c.sort", (item_id,)).fetchall()
    return [r["name"] for r in rows]


# ---------------- 条目管理 ----------------
def item_page(conn: sqlite3.Connection, *, q: str = "", source: str = "", state: str = "",
              category: str = "", offset: int = 0, limit: int = 50,
              include_dups: bool = False) -> tuple[list[dict], int]:
    where, args = [], []
    if not include_dups:
        where.append("COALESCE(i.dup_of, 0) = 0")
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(i.title LIKE ? OR i.sec_name LIKE ? OR i.sec_code LIKE ? OR i.summary LIKE ?)")
        args += [like, like, like, like]
    if source:
        where.append("i.source = ?")
        args.append(source)
    if state:
        where.append("COALESCE(i.state, 'online') = ?")
        args.append(state)
    if category:
        where.append(
            "EXISTS (SELECT 1 FROM item_cat ic JOIN categories c ON c.id = ic.category_id"
            " WHERE ic.item_id = i.id AND c.name = ?)")
        args.append(category)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with guard(conn):
        total = int(conn.execute(f"SELECT COUNT(*) n FROM items i{clause}", args).fetchone()["n"])
        rows = conn.execute(
            f"SELECT i.*, COALESCE(i.state, 'online') state FROM items i{clause}"
            " ORDER BY i.pinned DESC, i.published_at DESC, i.id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
    return [dict(r) for r in rows], total


def set_items_state(conn: sqlite3.Connection, ids: Iterable[int], state: str) -> int:
    if state not in ITEM_STATES:
        raise ValueError(f"未知状态：{state}")
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with guard(conn), conn:
        conn.execute(f"UPDATE items SET state = ? WHERE id IN ({marks})", (state, *ids))
    return len(ids)


def update_item(conn: sqlite3.Connection, item_id: int, *, title: str | None = None,
                summary: str | None = None, category: str | None = None) -> dict:
    """人工修订条目：只改传进来的字段，并打上 edited_at。"""
    item_id = int(item_id)
    sets, args = [], []
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        sets.append("title = ?")
        args.append(title[:400])
    if summary is not None:
        sets.append("summary = ?")
        args.append(summary.strip()[:2000])
    if not sets and category is None:
        raise ValueError("没有要改的内容")
    if sets:
        sets.append("edited_at = ?")
        args.append(int(time.time() * 1000))
        with guard(conn), conn:
            conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", (*args, item_id))
    if category is not None:
        set_item_categories(conn, item_id, [category] if category else [])
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ValueError(f"条目不存在：{item_id}")
    return dict(row)


def item_history(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    """这条消息的全部版本，按版本号升序。"""
    with guard(conn):
        row = conn.execute("SELECT fingerprint FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("没有这条")
        return [
            dict(r)
            for r in conn.execute(
                "SELECT version, title, summary, seen_at FROM revisions"
                " WHERE fingerprint = ? ORDER BY version",
                (row["fingerprint"],),
            )
        ]


def item_diff(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    """相邻两版之间到底改了什么：标题变了、摘要变了，各自的前后值。"""
    history = item_history(conn, item_id)
    out: list[dict] = []
    for prev, cur in zip(history, history[1:]):
        changes = []
        if prev["title"] != cur["title"]:
            changes.append({"field": "标题", "before": prev["title"], "after": cur["title"]})
        if prev["summary"] != cur["summary"]:
            changes.append({"field": "摘要", "before": prev["summary"], "after": cur["summary"]})
        out.append({"from": prev["version"], "to": cur["version"], "at": cur["seen_at"],
                    "changes": changes})
    return out


def company_timeline(conn: sqlite3.Connection, sec_code: str, limit: int = 50) -> list[dict]:
    """同一家公司（股票代码）在库里的全部消息，按时间倒序 —— 「这家公司今年都发生了什么」。"""
    code = (sec_code or "").strip()
    if not code:
        return []
    with guard(conn):
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, source, title, published_at, sec_code, sec_name, version,"
                " COALESCE(state, 'online') state FROM items WHERE sec_code = ?"
                " ORDER BY published_at DESC, id DESC LIMIT ?",
                (code, int(limit)),
            )
        ]


def toggle_pin(conn: sqlite3.Connection, item_id: int) -> int:
    with guard(conn), conn:
        cur = conn.execute("UPDATE items SET pinned = 1 - COALESCE(pinned, 0) WHERE id = ?", (item_id,))
        row = conn.execute("SELECT COALESCE(pinned,0) p FROM items WHERE id = ?", (item_id,)).fetchone()
    return int(row["p"]) if row else 0


def delete_items(conn: sqlite3.Connection, ids: Iterable[int]) -> int:
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with guard(conn), conn:
        conn.execute(f"DELETE FROM item_cat WHERE item_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM marks WHERE item_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM items WHERE id IN ({marks})", ids)
    return len(ids)


# ---------------- 抓取任务 ----------------
def enqueue_job(conn: sqlite3.Connection, kind: str, target: str, by: str = "system",
                priority: int = 0) -> int:
    if kind not in ("source", "all"):
        raise ValueError(f"未知任务类型：{kind}")
    with guard(conn), conn:
        cur = conn.execute(
            "INSERT INTO jobs(kind, target, status, created_by, created_at, priority)"
            " VALUES (?, ?, 'queued', ?, ?, ?)",
            (kind, target, by, _now(), int(priority)))
        return int(cur.lastrowid)


def claim_job(conn: sqlite3.Connection) -> dict | None:
    with guard(conn), conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued'"
            " ORDER BY priority DESC, id LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?", (_now(), row["id"]))
        out = dict(row)
    out["status"] = "running"
    return out


def finish_job(conn: sqlite3.Connection, job_id: int, *, ok: bool, result: str = "", error: str = "") -> None:
    with guard(conn), conn:
        conn.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, result = ?, error = ? WHERE id = ?",
            ("done" if ok else "failed", _now(), result, error, job_id))


def retry_job(conn: sqlite3.Connection, job_id: int) -> None:
    with guard(conn), conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', started_at = NULL, finished_at = NULL,"
            " result = '', error = '' WHERE id = ?", (job_id,))


def job_list(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    with guard(conn):
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    with guard(conn):
        rows = conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    out = {s: 0 for s in JOB_STATES}
    for r in rows:
        out[r["status"]] = int(r["n"])
    return out


# ---------------- 报表 ----------------
def report(conn: sqlite3.Connection, days: int = 7) -> dict:
    since = _now() - days * 86400_000
    with guard(conn):
        by_source = [dict(r) for r in conn.execute(
            "SELECT source, COUNT(*) n, SUM(version > 1) revised FROM items"
            " WHERE first_seen_at >= ? GROUP BY source ORDER BY n DESC", (since,))]
        by_category = [dict(r) for r in conn.execute(
            "SELECT c.name, COUNT(ic.item_id) n FROM categories c"
            " LEFT JOIN item_cat ic ON ic.category_id = c.id"
            " LEFT JOIN items i ON i.id = ic.item_id AND i.first_seen_at >= ?"
            " GROUP BY c.id ORDER BY n DESC", (since,))]
        by_day = [dict(r) for r in conn.execute(
            "SELECT date(first_seen_at / 1000, 'unixepoch', 'localtime') d, COUNT(*) n"
            " FROM items WHERE first_seen_at >= ? GROUP BY d ORDER BY d", (since,))]
        top_company = [dict(r) for r in conn.execute(
            "SELECT sec_code, sec_name, COUNT(*) n FROM items"
            " WHERE first_seen_at >= ? AND sec_code <> '' GROUP BY sec_code"
            " ORDER BY n DESC LIMIT 10", (since,))]
        totals = conn.execute(
            "SELECT COUNT(*) n, SUM(COALESCE(state,'online') = 'offline') off,"
            " SUM(COALESCE(pinned,0) = 1) pinned FROM items").fetchone()
    return {
        "days": days,
        "by_source": by_source,
        "by_category": by_category,
        "by_day": by_day,
        "top_company": top_company,
        "total": int(totals["n"] or 0),
        "offline": int(totals["off"] or 0),
        "pinned": int(totals["pinned"] or 0),
    }


def export_items_csv(conn: sqlite3.Connection, *, q: str = "", source: str = "") -> str:
    rows, _ = item_page(conn, q=q, source=source, limit=100000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["时间", "来源", "代码", "公司", "标题", "状态", "版本", "链接"])
    for r in rows:
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M", time.localtime((r["published_at"] or 0) / 1000)),
            r["source"], r["sec_code"], r["sec_name"], r["title"],
            ITEM_STATE_TEXT.get(r.get("state") or "online", ""), r["version"], r["url"],
        ])
    return "\ufeff" + buf.getvalue()


# ---------------- 设置 ----------------
def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    with guard(conn):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with guard(conn), conn:
        conn.execute("INSERT INTO settings(key, value) VALUES (?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# ---------------- 工作中的数字 ----------------
def workbench(conn: sqlite3.Connection, hours: int = 24) -> dict:
    since = _now() - hours * 3600_000
    with guard(conn):
        row = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok, COALESCE(SUM(fetched),0) fetched,"
            " COALESCE(SUM(inserted),0) inserted, COALESCE(SUM(updated),0) updated,"
            " COALESCE(AVG(CASE WHEN ended_at IS NOT NULL THEN ended_at - started_at END),0) avg_ms"
            " FROM runs WHERE started_at >= ?", (since,)).fetchone()
        bad = conn.execute("SELECT name, status, last_error FROM sources"
                           " WHERE status <> 'ok' ORDER BY name").fetchall()
        backlog = job_counts(conn)
    n = int(row["n"] or 0)
    fetched = int(row["fetched"] or 0)
    inserted = int(row["inserted"] or 0)
    return {
        "runs": n,
        "ok_runs": int(row["ok"] or 0),
        "success_rate": (int(row["ok"] or 0) / n * 100) if n else 0.0,
        "fetched": fetched,
        "inserted": inserted,
        "updated": int(row["updated"] or 0),
        "dedupe_rate": ((fetched - inserted) / fetched * 100) if fetched else 0.0,
        "avg_seconds": float(row["avg_ms"] or 0) / 1000.0,
        "bad_sources": [dict(r) for r in bad],
        "jobs": backlog,
    }


# ---------------- 订阅规则（组合） ----------------
def add_rule(conn: sqlite3.Connection, keyword: str, *, category: str = "", source: str = "") -> int:
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("订阅词不能为空")
    with guard(conn), conn:
        cur = conn.execute(
            "INSERT INTO rules(keyword, category, source, enabled, created_at) VALUES (?, ?, ?, 1, ?)"
            " ON CONFLICT(keyword, category) DO UPDATE SET enabled = 1, source = excluded.source",
            (keyword, category.strip(), source.strip(), _now()))
        return int(cur.lastrowid or 0)


def list_rules(conn: sqlite3.Connection) -> list[dict]:
    with guard(conn):
        rows = conn.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        like = f"%{r['keyword']}%"
        sql = ("SELECT COUNT(*) n FROM items WHERE (title LIKE ? OR summary LIKE ? OR sec_name LIKE ?)")
        args = [like, like, like]
        if r["source"]:
            sql += " AND source = ?"
            args.append(r["source"])
        if r["category"]:
            sql += (" AND EXISTS (SELECT 1 FROM item_cat ic JOIN categories c ON c.id = ic.category_id"
                    " WHERE ic.item_id = items.id AND c.name = ?)")
            args.append(r["category"])
        with guard(conn):
            hits = int(conn.execute(sql, args).fetchone()["n"])
        out.append({**dict(r), "hits": hits})
    return out


def set_rule_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool) -> None:
    with guard(conn), conn:
        conn.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    with guard(conn), conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


# ---------------- 任务：人工干预 ----------------
def cancel_job(conn: sqlite3.Connection, job_id: int) -> None:
    with guard(conn), conn:
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status IN ('queued','running')",
            (_now(), job_id))


def bump_job(conn: sqlite3.Connection, job_id: int) -> None:
    with guard(conn), conn:
        conn.execute("UPDATE jobs SET priority = priority + 10 WHERE id = ? AND status = 'queued'", (job_id,))


def job_detail(conn: sqlite3.Connection, job_id: int) -> dict | None:
    with guard(conn):
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


JOB_REASON_TEXT = {
    "timeout": "超时",
    "network": "网络不通",
    "http": "对方返回错误",
    "parse": "解析失败（页面格式变了）",
    "auth": "没有权限",
    "unknown": "未知原因",
}


def job_failure_reason(error: str) -> str:
    """把异常文本归到几类可行动的原因上，页面不用再让人读堆栈。"""
    text = (error or "").lower()
    if not text:
        return ""
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "urlerror" in text or "connection" in text or "getaddrinfo" in text or "reset" in text:
        return "network"
    if "401" in text or "403" in text or "unauthor" in text or "forbidden" in text:
        return "auth"
    if "parse" in text or "not well-formed" in text or "xml" in text or "json" in text:
        return "parse"
    if "http" in text or "500" in text or "502" in text or "503" in text:
        return "http"
    return "unknown"

# 源 → 分类的默认归属（新条目进库就落好，不用人工点）
SOURCE_CATEGORY = {
    "cninfo": "金融",
    "arxiv": "学术",
    "arxiv-fin": "金融",
    "ithome": "科技",
    "sspai": "科技",
    "stats": "政策",
    "ccgp": "政策",
}


def auto_categorize(conn: sqlite3.Connection) -> int:
    """把还没有分类的条目按来源归到默认分类，返回补了几条。"""
    with guard(conn), conn:
        rows = conn.execute(
            "SELECT i.id, i.source FROM items i"
            " WHERE NOT EXISTS (SELECT 1 FROM item_cat ic WHERE ic.item_id = i.id)").fetchall()
        done = 0
        for r in rows:
            name = SOURCE_CATEGORY.get(r["source"])
            if not name:
                continue
            row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
            if not row:
                continue
            conn.execute("INSERT OR IGNORE INTO item_cat(item_id, category_id) VALUES (?, ?)",
                         (r["id"], row["id"]))
            done += 1
    return done
