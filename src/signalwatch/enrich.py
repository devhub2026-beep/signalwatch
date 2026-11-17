"""把公告 PDF 的正文抠出来存进库，顺便让「摘要」和搜索有东西可用。

三件事：
1. `fetch(url)` 下载 PDF（带 UA、限速在调用方）；
2. `text_of_item(item)` 转文本（`pdftext.extract`，失败返回空串而不是抛）；
3. `enrich(conn, limit=5, only=("cninfo",))` 挑出「有链接但没正文」的条目去补，写
   `items.body_text` 与 `body_bytes`，并把正文前 200 字补进 `summary`（原文摘要为空时）。
"""

from __future__ import annotations

import time
import urllib.request

from . import db, pdftext

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_BYTES = 12 * 1024 * 1024


def fetch(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB，跳过")
    return data


def text_of(pdf: bytes, limit: int = 6000) -> str:
    try:
        return pdftext.extract(pdf, limit=limit)
    except Exception:
        return ""


def _snippet(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text[:n] + ("…" if len(text) > n else "")


def enrich(conn, limit: int = 5, only: tuple[str, ...] = ("cninfo",),
           timeout: float = 30.0, pause: float = 1.0) -> dict:
    """补正文。返回 {"tried","ok","empty","failed","chars"} 供调用方写日志。"""
    marks = ",".join("?" * len(only))
    with db.guard(conn):
        rows = conn.execute(
            "SELECT id, title, url, summary FROM items"
            f" WHERE dup_of = 0 AND source IN ({marks})"
            " AND url LIKE 'http%'"
            " AND COALESCE(body_text, '') = ''"
            " ORDER BY published_at DESC, id DESC LIMIT ?",
            (*only, limit),
        ).fetchall()

    tried = ok = empty = failed = chars = 0
    for row in rows:
        tried += 1
        try:
            pdf = fetch(row["url"], timeout=timeout)
        except Exception:
            failed += 1
            continue
        text = text_of(pdf)
        if not text:
            empty += 1
            continue
        chars += len(text)
        ok += 1
        summary = row["summary"] or _snippet(text)
        with db.guard(conn), conn:
            conn.execute(
                "UPDATE items SET body_text = ?, body_bytes = ?, summary = ? WHERE id = ?",
                (text, len(pdf), summary, row["id"]),
            )
        if pause:
            time.sleep(pause)
    return {"tried": tried, "ok": ok, "empty": empty, "failed": failed, "chars": chars}
