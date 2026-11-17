"""源注册表。

新增一个源 = 写一个 dataclass（实现 fetch）+ 在这里登记一行。
调度器、API、页面都从这张表读，不各自维护列表。
"""

from __future__ import annotations

from .arxiv import ArxivSource
from .base import Record, Source
from .cninfo import CninfoSource
from .html_list import HtmlListSource
from .rss import RssSource


def default_sources() -> list[Source]:
    """三个方向：金融（公告/采购）、科技（媒体）、学业（论文）。

    每条都必须能在本机直连 —— 挂了要在页面上看见，不许静默。
    """
    return [
        CninfoSource(),
        HtmlListSource(
            name="stats",
            list_url="https://www.stats.gov.cn/sj/zxfb/",
            kind="policy",
            interval_seconds=1800,
        ),
        HtmlListSource(
            name="ccgp",
            list_url="https://www.ccgp.gov.cn/cggg/zygg/",
            kind="procurement",
            interval_seconds=1800,
        ),
        RssSource(name="ithome", feed_url="https://www.ithome.com/rss/", interval_seconds=600, kind="tech"),
        RssSource(name="sspai", feed_url="https://sspai.com/feed", interval_seconds=900, kind="tech"),
        ArxivSource(),
        ArxivSource(query="cat:q-fin.GN", name="arxiv-fin", interval_seconds=3600),
    ]


__all__ = [
    "Record",
    "Source",
    "CninfoSource",
    "RssSource",
    "ArxivSource",
    "HtmlListSource",
    "default_sources",
]
