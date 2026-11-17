"""10 万级数据量测：造一个大库，量查询与接口的耗时。

为什么不直接往生产库灌：生产库只有一个写者，灌数据要停服务。
这里在临时目录建库，跑完删掉，数字只反映 SQLite + 索引本身。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalwatch import admin, board, db  # noqa: E402

SOURCES = ["cninfo", "ithome", "sspai", "arxiv", "arxiv-fin", "ccgp", "stats"]
CATS = ["金融", "科技", "学术", "政策"]
WORDS = ["关于", "股份", "有限公司", "公告", "通知", "召开", "股东", "回购", "中标", "增持"]


def make(rows: int) -> tuple[Path, object]:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    path = Path(tmp.name) / "big.db"
    conn = db.connect(path)
    db.init_db(conn)
    now = int(time.time() * 1000)
    rnd = random.Random(7)
    cats = {r["name"]: r["id"] for r in admin.list_categories(conn)}
    batch = []
    for i in range(rows):
        title = "".join(rnd.choice(WORDS) for _ in range(4)) + f" 第{i}号"
        src = rnd.choice(SOURCES)
        batch.append((f"fp{i}", src, f"ext-{i}", "announcement", title, "", "", "600000",
                      "某公司", "", now - rnd.randint(0, 90 * 86400_000), now, now,
                      f"h{i}", 1, 0))
        if len(batch) >= 5000:
            _flush(conn, batch, cats)
            batch = []
    if batch:
        _flush(conn, batch, cats)
    conn.execute("UPDATE items SET body_text = ? WHERE id % 50 = 0", ("正" * 500,))
    conn.commit()
    return tmp, conn, path


def _flush(conn, batch, cats) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO items(fingerprint, source, external_id, kind, title, summary, url,"
        " sec_code, sec_name, tags, published_at, first_seen_at, last_seen_at, content_hash,"
        " version, dup_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    conn.commit()


def timed(fn, rounds: int = 20) -> dict:
    took = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        took.append((time.perf_counter() - t0) * 1000)
    took.sort()
    return {"p50_ms": round(statistics.median(took), 1),
            "p95_ms": round(took[int(len(took) * 0.95) - 1], 1),
            "max_ms": round(took[-1], 1)}


def main() -> int:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    tmp, conn, path = make(rows)
    try:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        row = conn.execute("SELECT published_at, id FROM items"
                           " ORDER BY published_at DESC, id DESC LIMIT 1 OFFSET 90000").fetchone()
        last_cursor = (int(row["published_at"]), int(row["id"]))
        size_mb = round(path.stat().st_size / 1048576, 1)
        out = {
            "rows": total,
            "db_size_mb": size_mb,
            "first_page": timed(lambda: board.board_query(conn, limit=30)),
            "search_company": timed(lambda: board.board_query(conn, q="某公司", limit=30)),
            "unread_count": timed(lambda: board.unread_count(conn)),
            "admin_page_offset_90000": timed(
                lambda: admin.item_page(conn, offset=90000, limit=30), rounds=5),
            "admin_page_cursor": timed(lambda: admin.item_page(
                conn, before=last_cursor, limit=30)),
            "unread_count_indexed": timed(lambda: board.unread_count(conn)),
            "report_7d": timed(lambda: admin.report(conn, days=7), rounds=5),
            "workbench": timed(lambda: admin.workbench(conn), rounds=5),
            "explain": [
                dict(r) for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM items WHERE dup_of = 0"
                    " ORDER BY published_at DESC LIMIT 30")
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
