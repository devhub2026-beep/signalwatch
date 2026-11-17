"""操作审计：谁、什么时候、干了什么。

企业系统里这张表的用途不是"看日志"，是出事了能回答"是谁改的"。
所以它只增不改，写失败也不能影响主流程。
"""

from __future__ import annotations

import time

from .board import csv_safe


def log(conn, username: str, action: str, target: str = "", detail: str = "") -> None:
    try:
        with conn:
            conn.execute(
                "INSERT INTO audit(at, username, action, target, detail) VALUES (?,?,?,?,?)",
                (int(time.time() * 1000), username or "-", action, target or "", detail or ""),
            )
    except Exception:
        # 审计失败不阻断业务：本地单机系统，丢了这条也不该让抓取停下来
        pass


def query(conn, *, limit: int = 200, username: str | None = None, action: str | None = None) -> list[dict]:
    where = ["1=1"]
    args: list = []
    if username:
        where.append("username = ?")
        args.append(username)
    if action:
        where.append("action = ?")
        args.append(action)
    args.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(
        f"SELECT id, at, username, action, target, detail FROM audit"
        f" WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
        args,
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "at": int(r["at"]),
            "username": r["username"],
            "action": r["action"],
            "target": r["target"],
            "detail": r["detail"],
        }
        for r in rows
    ]


def count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])


def prune(conn, days: int) -> int:
    """删掉 days 天之前的审计。返回删了几行。"""
    if days <= 0:
        raise ValueError("天数要大于 0")
    before = int((time.time() - days * 86400) * 1000)
    with conn:
        cur = conn.execute("DELETE FROM audit WHERE at < ?", (before,))
    return cur.rowcount or 0


def export_csv(conn, *, limit: int = 5000) -> str:
    """导出成 CSV 文本（带 BOM，Excel 直接打开不乱码）。"""
    import csv
    import io

    rows = conn.execute(
        "SELECT at, username, action, target, detail FROM audit ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["时间", "账号", "动作", "对象", "详情"])
    for r in rows:
        writer.writerow([csv_safe(x) for x in
                         [r["at"], r["username"], r["action"], r["target"], r["detail"]]])
    return buf.getvalue()
