"""巨潮资讯（cninfo）A 股公告。

接口是公开的 POST 表单，返回 JSON。要点：
- 同一份公告在深市/沪市两个 column 里都可能出现，用 announcementId 做身份，天然去重
- announcementTime 是毫秒时间戳（东八区当天零点，官方如此）
- 我们只取最近若干条，不做全量回溯（239 万条，没必要）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import SETTINGS
from .base import Record, http_post_form, loads_cursor

ENDPOINT = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE = "http://static.cninfo.com.cn/"


@dataclass
class CninfoSource:
    name: str = "cninfo"
    kind: str = "announcement"
    url: str = ENDPOINT
    interval_seconds: int = 300
    columns: tuple[str, ...] = ("szse", "sse")

    def fetch(self, cursor: str | None, limit: int) -> list[Record]:
        state = loads_cursor(cursor)
        since_ms = int(state.get("since_ms", 0))
        out: list[Record] = []
        for column in self.columns:
            payload = {
                "pageNum": 1,
                "pageSize": str(limit),
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": "",
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            raw = http_post_form(
                self.url, payload, timeout=SETTINGS.http_timeout, user_agent=SETTINGS.user_agent
            )
            data = json.loads(raw)
            for item in data.get("announcements") or []:
                rec = self._to_record(item)
                if rec is None:
                    continue
                if rec.published_at < since_ms:
                    continue
                out.append(rec)
        return out

    def _to_record(self, item: dict) -> Record | None:
        ann_id = str(item.get("announcementId") or "").strip()
        title = (item.get("announcementTitle") or "").strip()
        if not ann_id or not title:
            return None
        ts = item.get("announcementTime")
        if not isinstance(ts, int):
            return None
        adjunct = (item.get("adjunctUrl") or "").strip()
        return Record(
            external_id=ann_id,
            title=title,
            published_at=ts,
            summary="",
            url=(PDF_BASE + adjunct) if adjunct else "",
            sec_code=(item.get("secCode") or None),
            sec_name=(item.get("secName") or item.get("tileSecName") or None),
            kind="announcement",
            tags=(item.get("pageColumn") or ""),
        )
