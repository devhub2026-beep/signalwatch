"""列表型 HTML 源：政策、统计数据、采购公告这类「一页若干条链接」的政府站点。

这类站点没有 API、没有 RSS，但列表页结构十几年不变：日期和编号直接写在链接里
（`./202611/t20261113_1965820.html`）。所以不引第三方解析库，用一条正则取
「链接 + 8 位日期 + 数字编号」，再把标题当正文首段。

不假装成功：状态码不对、页面里一条都匹配不到，都抛 `ValueError`——宁可让这个源
变红，也不能让它在页面上显示「成功，0 条」。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from .base import Record, RateLimiter, RetryableError, http_get, loads_cursor

# href="./202611/t20261113_1965820.html" 或 "./gzgg/202611/t20261115_27687129.htm"
# → 路径任意、日期 8 位、编号若干、扩展名 .htm/.html
LINK_RE = re.compile(
    r'href="(?P<href>[^"]*?/t(?P<ymd>\d{8})_(?P<num>\d+)\.s?html?)"[^>]*>\s*(?P<title>[^<]{6,200})<',
    re.IGNORECASE,
)


@dataclass
class HtmlListSource:
    name: str
    list_url: str
    kind: str = "policy"
    interval_seconds: int = 1800
    min_interval: float = 1.0

    def __post_init__(self) -> None:
        self._limiter = RateLimiter(self.min_interval)

    def fetch(self, cursor: str | None, limit: int) -> list[Record]:
        state = loads_cursor(cursor)
        since_ms = int(state.get("since_ms", 0))
        html = self._get_retry()
        rows = self.parse(html, limit=limit + 20)
        if not rows:
            raise ValueError(f"{self.list_url} 里一条公告都没解析出来（页面结构变了？）")
        return [r for r in rows if r.published_at >= since_ms][:limit]

    def _get_retry(self) -> str:
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._get()
            except RetryableError as exc:  # 5xx / 超时才重试，4xx 直接让源变红
                last = exc
                time.sleep(1.0 * (2 ** attempt))
        raise ValueError(f"{self.list_url} 连不上：{last}")

    def _get(self) -> str:
        self._limiter.wait()
        from ..config import SETTINGS

        text = http_get(self.list_url, timeout=SETTINGS.http_timeout, user_agent=SETTINGS.user_agent)
        if len(text) < 2000:
            raise ValueError(f"{self.list_url} 返回太短（{len(text)} 字节），像是被拦了")
        return text

    def parse(self, html: str, limit: int = 50) -> list[Record]:
        out: list[Record] = []
        seen: set[str] = set()
        for m in LINK_RE.finditer(html):
            num = m.group("num")
            if num in seen:
                continue
            seen.add(num)
            ymd = m.group("ymd")
            title = re.sub(r"\s+", " ", m.group("title")).strip()
            if not title or len(title) < 6:
                continue
            out.append(
                Record(
                    external_id=f"{self.name}-{num}",
                    title=title,
                    published_at=_midnight(ymd),
                    summary="",
                    url=urljoin(self.list_url, m.group("href")),
                    kind=self.kind,
                    tags="",
                )
            )
            if len(out) >= limit:
                break
        return out


def _midnight(ymd: str) -> int:
    try:
        dt = datetime.strptime(ymd, "%Y%m%d")
    except ValueError:
        return 0
    return int(dt.timestamp() * 1000)


