"""把公告 PDF 的正文抠出来存进库，顺便让「摘要」和搜索有东西可用。

三件事：
1. `fetch(url)` 下载 PDF（带 UA、限速在调用方）；
2. `text_of_item(item)` 转文本（`pdftext.extract`，失败返回空串而不是抛）；
3. `enrich(conn, limit=5, only=("cninfo",))` 挑出「有链接但没正文」的条目去补，写
   `items.body_text` 与 `body_bytes`，并把正文前 200 字补进 `summary`（原文摘要为空时）。
"""

from __future__ import annotations

import html
import re
import time
import urllib.request

from . import db, pdftext
from .sources.base import read_http, http_get

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_BYTES = 12 * 1024 * 1024

# 哪类源用哪种抠法：PDF 走 pdftext，网页走 html_to_text
SOURCE_KIND = {
    "cninfo": "pdf",
    "ithome": "html",
    "sspai": "html",
    "ccgp": "html",
    "stats": "html",
    "arxiv": "skip",      # 摘要在 feed 里已经够了，抓页面收益低
    "arxiv-fin": "skip",
}

_TAG = re.compile(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<h1[^>]*>(?P<t>.*?)</h1>", re.S | re.I)
_ARTICLE = re.compile(r"<(article|main)[^>]*>(?P<b>.*?)</\1>", re.S | re.I)
_BLOCK = re.compile(r"</?(p|div|br|li|h[1-6]|tr|section)[^>]*>", re.I)
_SPACE = re.compile(r"[ \t\x0b\f\r]+")
_BREADCRUMB = re.compile(r"首页\s*>\s*[^\n]{0,60}?>\s*")
# 网页正文的结尾标记，出现在这些词之后就当作无关内容切断
_TAIL_MARKERS = ("责任编辑", "责编：", "免责声明", "相关阅读", "热门推荐",
                 "本文由", "转载请注明", "Copyright", "版权所有")


def html_to_text(raw: str, limit: int = 6000) -> str:
    """从网页里抠正文：优先 <article>/<main>，没有就整页去掉脚本与标签。

    不做通用正文抽取（那是 readability 的活），只保证「拿得到通顺中文」，
    抓不到就返回空串，由调用方计入 empty。
    """
    body = _TAG.sub(" ", raw)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    block = _ARTICLE.search(body)
    if block and len(_BLOCK.sub("\n", block.group("b"))) > 400:
        body = block.group("b")
        title = _TITLE.search(raw)
        head = html.unescape(_BLOCK.sub(" ", title.group("t"))) if title else ""
    else:
        head = ""
    body = _BLOCK.sub("\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(body)
    text = _SPACE.sub(" ", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    lines = [line for line in lines if len(line) > 1]
    lines = _trim_chrome(lines)
    out = "\n".join(lines)
    if head and not out.startswith(head):
        out = f"{head}\n{out}"
    # 站点导航可能整段挤在一行里：先按面包屑「首页 > A > B >」把头部的标题+导航切掉
    m = _BREADCRUMB.search(out)
    if m and m.end() < 600:
        out = out[m.end():]
    out = _BREADCRUMB.sub("", out)
    for marker in _TAIL_MARKERS:
        cut = out.find(marker)
        if cut > 200:            # 太靠前说明正文里本来就提到了，不切
            out = out[:cut]
            break
    return out[:limit]


def fetch(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return read_http(req, timeout=timeout, max_bytes=MAX_BYTES)


def _trim_chrome(lines: list[str]) -> list[str]:
    """去掉网页顶部那堆导航（「首页 IT圈 最会买 设置 …」）。

    做法：找到第一段真正长的正文（>=60 字），把它之前所有短于 25 字的行丢掉；
    正文之后的行原样保留（结尾的面包屑通常也短，但已经没有前导噪声）。
    """
    first = next((i for i, line in enumerate(lines) if len(line) >= 60), None)
    if first is None:      # 整页都不长，可能本来就是短消息，别乱删
        return lines
    # 正文之前只保留「像句子」的行（以句号/问号/引号收尾），导航不会这样结尾
    head = [line for line in lines[:first]
            if len(line) >= 25 and line.rstrip().endswith(("。", "！", "？", ".", '"', "”"))]
    return head + lines[first:]


def text_of(pdf: bytes, limit: int = 6000) -> str:
    try:
        return pdftext.extract(pdf, limit=limit)
    except Exception:
        return ""


def text_of_url(url: str, kind: str, *, timeout: float = 30.0, limit: int = 6000) -> tuple[str, int]:
    """按源的类型取正文，返回 (文本, 下载字节数)。取不到就是 ("", 0)。"""
    if kind == "pdf":
        blob = fetch(url, timeout=timeout)
        return text_of(blob, limit=limit), len(blob)
    if kind == "html":
        raw = http_get(url, timeout=timeout, user_agent=UA)
        return html_to_text(raw, limit=limit), len(raw.encode("utf-8"))
    return "", 0


def _snippet(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text[:n] + ("…" if len(text) > n else "")


def enrich(conn, limit: int = 5, only: tuple[str, ...] | None = None,
           timeout: float = 30.0, pause: float = 1.0) -> dict:
    """补正文。默认覆盖所有「能用上」的源，返回计数供调用方写日志。

    `only=None` 表示按 `SOURCE_KIND` 里 kind != skip 的全部源来补。
    """
    if only is None:
        only = tuple(s for s, kind in SOURCE_KIND.items() if kind != "skip")
    marks = ",".join("?" * len(only))
    with db.guard(conn):
        rows = conn.execute(
            "SELECT id, title, url, summary, source FROM items"
            f" WHERE dup_of = 0 AND source IN ({marks})"
            " AND url LIKE 'http%'"
            " AND COALESCE(body_text, '') = ''"
            " ORDER BY published_at DESC, id DESC LIMIT ?",
            (*only, limit),
        ).fetchall()

    tried = ok = empty = failed = chars = 0
    for row in rows:
        tried += 1
        kind = SOURCE_KIND.get(row["source"], "pdf")
        try:
            text, size = text_of_url(row["url"], kind, timeout=timeout)
        except Exception:
            failed += 1
            continue
        if not text:
            empty += 1
            continue
        chars += len(text)
        ok += 1
        summary = row["summary"] or _snippet(text)
        with db.guard(conn), conn:
            conn.execute(
                "UPDATE items SET body_text = ?, body_bytes = ?, summary = ? WHERE id = ?",
                (text, size, summary, row["id"]),
            )
        if pause:
            time.sleep(pause)
    return {"tried": tried, "ok": ok, "empty": empty, "failed": failed, "chars": chars}
