"""远端 worker：用 HTTP 把「领活—抓取—交活」跑通，本地起一个假 API 当对手。

真接口用 TestClient 测过（tests/test_internal_api.py），这里测的是**客户端这一侧**：
请求体对不对、失败会不会把任务标成 failed、连不上 API 会不会抛 RemoteError。
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from signalwatch.remote import RemoteError, RemoteWorker

TOKEN = "t0ken"


class _Handler(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict]] = []
    pending: list[dict] = []
    mode = "happy"          # happy / broken / refuse

    def log_message(self, *args) -> None:      # 别把访问日志打到测试输出里
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).calls.append(("GET", self.path, dict(self.headers)))
        if self.headers.get("X-Internal-Token") != TOKEN:
            return self._send(403, {"error": "no token"})
        if self.path == "/api/internal/jobs/claim":
            if _Handler.pending:
                return self._send(200, {"job": _Handler.pending.pop(0)})
            return self._send(200, {"job": None})
        if self.path == "/api/internal/sources":
            return self._send(200, {"sources": ["stats", "cninfo"]})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls.append(("POST", self.path, body))
        if self.headers.get("X-Internal-Token") != TOKEN:
            return self._send(403, {"error": "no token"})
        if self.path == "/api/internal/crawl":
            if self.mode == "broken":
                return self._send(500, {"error": "爬取炸了"})
            return self._send(200, {"ok": True, "fetched": 7, "inserted": 1, "parts": ["stats:ok"]})
        if self.path.startswith("/api/internal/jobs/") and self.path.endswith("/finish"):
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


class RemoteWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _Handler.calls = []
        _Handler.mode = "happy"
        _Handler.pending = [{"id": 7, "kind": "source", "target": "stats", "status": "running"}]

    def test_run_once_crawls_and_finishes(self) -> None:
        worker = RemoteWorker(self.base, TOKEN)
        job = worker.run_once()
        self.assertEqual(7, job["id"])
        paths = [c[1] for c in _Handler.calls]
        self.assertIn("/api/internal/jobs/claim", paths)
        self.assertIn("/api/internal/crawl", paths)
        crawl = next(c for c in _Handler.calls if c[1] == "/api/internal/crawl")
        self.assertEqual({"source": "stats"}, crawl[2])
        finish = next(c for c in _Handler.calls if c[1].endswith("/finish"))
        self.assertTrue(finish[2]["ok"])
        self.assertIn("stats:ok", finish[2]["result"])
        self.assertIn("抓到 7 新增 1", finish[2]["result"])

    def test_kind_all_walks_every_source(self) -> None:
        _Handler.pending = [{"id": 8, "kind": "all", "target": "", "status": "running"}]
        RemoteWorker(self.base, TOKEN).run_once()
        crawled = [c[2]["source"] for c in _Handler.calls if c[1] == "/api/internal/crawl"]
        self.assertEqual(["stats", "cninfo"], crawled)

    def test_no_job_returns_none(self) -> None:
        _Handler.pending = []
        self.assertIsNone(RemoteWorker(self.base, TOKEN).run_once())

    def test_bad_token_is_remote_error(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            RemoteWorker(self.base, "wrong").claim()
        self.assertIn("403", str(ctx.exception))

    def test_crawl_failure_marks_job_failed(self) -> None:
        _Handler.mode = "broken"
        worker = RemoteWorker(self.base, TOKEN)
        job = worker.run_once()
        self.assertEqual(7, job["id"])
        finish = next(c for c in _Handler.calls if c[1].endswith("/finish"))
        self.assertFalse(finish[2]["ok"])
        self.assertIn("爬取炸了", finish[2]["error"])

    def test_unreachable_api_raises(self) -> None:
        with self.assertRaises(RemoteError):
            RemoteWorker("http://127.0.0.1:9", TOKEN, timeout=1).claim()


if __name__ == "__main__":
    unittest.main()
