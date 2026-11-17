"""RSS / Atom 源：科技与商业媒体的公开 feed。

解析使用 defusedxml，挡住实体扩展等恶意 XML：
- RSS 2.0: channel/item/title, link, pubDate, description
- Atom: entry/title, link[@href], published/updated, summary
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass, field

# defusedxml 写在 pyproject 的 dependencies 里；但本机 pip 装不上（镜像解析卡死），
# 所以留一条退回标准库的路，只在注释里标明风险，而不是让整个抓取起不来。
# 生产/CI 上装了 defusedxml 就走它，能挡实体扩展这类恶意 XML。
try:  # 有就用它
    from defusedxml import ElementTree as ET
except ImportError:  # 没装也照常跑，只是少一层防护
    from xml.etree import ElementTree as ET  # type: ignore[assignment]

from ..config import SETTINGS
from .base import Record, http_get

ATOM = "{http://www.w3.org/2005/Atom}"

# 真实 feed 里常混进 0x00-0x1f 的控制字符（媒体站从富文本里带出来的），
# ElementTree 遇到就直接 ParseError，所以先洗一遍再解析。
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(raw: str) -> str:
    text = _CTRL.sub("", raw).replace("\ufeff", "")
    return text


def _looks_like_feed(text: str) -> bool:
    head = text[:400].lower()
    return "<rss" in head or "<feed" in head or "<rdf" in head


def _parse_date(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    try:
        dt = email.utils.parsedate_to_datetime(text)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            import datetime as _dt

            return int(_dt.datetime.strptime(text, fmt).timestamp() * 1000)
        except Exception:
            continue
    return 0


def _text(node, path: str) -> str:
    el = node.find(path)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


@dataclass
class RssSource:
    name: str
    feed_url: str
    kind: str = "news"
    interval_seconds: int = 600
    url: str = field(init=False)

    def __post_init__(self) -> None:
        self.url = self.feed_url

    def fetch(self, cursor: str | None, limit: int) -> list[Record]:
        raw = http_get(self.feed_url, timeout=SETTINGS.http_timeout, user_agent=SETTINGS.user_agent)
        text = _clean(raw)
        if not _looks_like_feed(text):
            # 对方把 RSS 换成了网页（36kr 就是这样），别假装解析成功
            raise ValueError(f"{self.feed_url} 返回的不是 feed（前 80 字符：{text[:80]!r}）")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError(f"{self.feed_url} 的 XML 坏了：{exc}") from exc
        records: list[Record] = []
        if root.tag == f"{ATOM}feed":
            for entry in root.findall(f"{ATOM}entry")[:limit]:
                link_el = entry.find(f"{ATOM}link")
                href = link_el.get("href", "") if link_el is not None else ""
                eid = _text(entry, f"{ATOM}id") or href
                title = _text(entry, f"{ATOM}title")
                summary = _text(entry, f"{ATOM}summary") or _text(entry, f"{ATOM}content")
                published = _text(entry, f"{ATOM}published") or _text(entry, f"{ATOM}updated")
                if not eid or not title:
                    continue
                records.append(
                    Record(
                        external_id=eid,
                        title=title,
                        published_at=_parse_date(published),
                        summary=summary[:500],
                        url=href,
                        kind=self.kind,
                        tags=self.name,
                    )
                )
            return records
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall("item")
        for item in items[:limit]:
            link = _text(item, "link")
            guid = _text(item, "guid") or link
            title = _text(item, "title")
            if not guid or not title:
                continue
            records.append(
                Record(
                    external_id=guid,
                    title=title,
                    published_at=_parse_date(_text(item, "pubDate")),
                    summary=_text(item, "description")[:500],
                    url=link,
                    kind=self.kind,
                    tags=self.name,
                )
            )
        return records
