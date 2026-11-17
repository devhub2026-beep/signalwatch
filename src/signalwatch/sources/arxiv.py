"""arXiv：科技与学术前沿的公开 Atom 接口。

选它是因为「学业/学术」这一类真能公开、稳定、无登录地拿到，
而且是普通人不会天天盯的那种信息。
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from ..config import SETTINGS
from .rss import _parse_date
from .base import Record, http_get

ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class ArxivSource:
    name: str = "arxiv"
    kind: str = "paper"
    query: str = "cat:cs.AI"
    interval_seconds: int = 1800
    url: str = "http://export.arxiv.org/api/query"

    def fetch(self, cursor: str | None, limit: int) -> list[Record]:
        params = {
            "search_query": self.query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(limit),
        }
        raw = http_get(
            f"{self.url}?{urllib.parse.urlencode(params)}",
            timeout=SETTINGS.http_timeout,
            user_agent=SETTINGS.user_agent,
        )
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw)
        out: list[Record] = []
        for entry in root.findall(f"{ATOM}entry"):
            eid = (entry.findtext(f"{ATOM}id") or "").strip()
            title = (entry.findtext(f"{ATOM}title") or "").strip()
            if not eid or not title:
                continue
            summary = (entry.findtext(f"{ATOM}summary") or "").strip().replace("\n", " ")
            published = (entry.findtext(f"{ATOM}published") or "").strip()
            out.append(
                Record(
                    external_id=eid,
                    title=" ".join(title.split()),
                    published_at=_parse_date(published),
                    summary=summary[:500],
                    url=eid,
                    kind=self.kind,
                    tags="arXiv " + self.query,
                )
            )
        return out
