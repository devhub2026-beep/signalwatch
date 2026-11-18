"""worker 的远端模式：不碰数据库，只通过 API 干活。

为什么要有它——SQLite 只有一个写者是硬约束（两条进程同时写同一个库，
在 Windows 的 bind mount 上会把库写烂，这是真出过的事故，见 docs/RCA-wal-corruption.md）。
所以「把采集从 API 进程里拆出来」不能靠两个容器各开一个连接去抢同一个文件，
得让 worker 变成**不碰库的执行器**：

    worker  --claim-->  API  --写库-->  SQLite
            --crawl-->  API
            --finish--> API

好处是三条都成立：进程真的分开了（API 挂了 worker 还在等着重连）、
库里永远只有一个写者（安全）、任务状态机与审计仍然走同一套代码。

走 HTTP 的代价是每次任务多两次请求；在局域网里这是毫秒级，
比起「两个写者把库写烂」便宜得多。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 30.0


class RemoteError(RuntimeError):
    pass


class RemoteWorker:
    """把 claim / crawl / finish 三个动作翻译成 HTTP 请求。"""

    def __init__(self, base_url: str, token: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        data = None
        headers = {"X-Internal-Token": self.token, "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            raise RemoteError(f"{exc.code} {path}：{body}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RemoteError(f"连不上 API（{self.base}）：{exc}") from None

    def claim(self) -> dict | None:
        return self._call("/api/internal/jobs/claim")["job"]

    def crawl(self, name: str) -> dict:
        return self._call("/api/internal/crawl", {"source": name})

    def finish(self, job_id: int, *, ok: bool, result: str = "", error: str = "") -> None:
        self._call(f"/api/internal/jobs/{job_id}/finish",
                   {"ok": ok, "result": result, "error": error})

    def run_once(self) -> dict | None:
        """领一条、干完、交回去。没有任务返回 None。"""
        job = self.claim()
        if not job:
            return None
        started = time.time()
        parts: list[str] = []
        try:
            if job["kind"] == "all":
                names = self._call("/api/internal/sources")["sources"]
            else:
                names = [job.get("target") or ""]
            for name in [n for n in names if n]:
                res = self.crawl(name)
                parts.append(f"{name}:{'ok' if res.get('ok') else 'fail'}"
                             f" 抓到 {res.get('fetched', 0)} 新增 {res.get('inserted', 0)}")
            self.finish(job["id"], ok=True, result="; ".join(parts))
        except Exception as exc:                                 # noqa: BLE001
            self.finish(job["id"], ok=False, error=f"{type(exc).__name__}: {exc}")
        job["seconds"] = round(time.time() - started, 2)
        return job
