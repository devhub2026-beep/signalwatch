"""出站 webhook：增删查、HMAC 签名、事件过滤、失败留痕。用本地 HTTP server 收，不联网。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from signalwatch import notify
from signalwatch.config import Settings
from signalwatch.db import connect, init_db


def make_db() -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    settings = Settings(home=home, db_path=home / "t.db")
    conn = connect(settings.db_path)
    init_db(conn)
    return conn, tmp


class _Handler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _Handler.received.append({
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        pass


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.tmp = make_db()
        _Handler.received = []
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.conn.close()
        self.tmp.cleanup()

    def _wait_received(self, n: int, timeout: float = 2.0) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline and len(_Handler.received) < n:
            time.sleep(0.02)
        return _Handler.received

    def test_add_list_remove(self) -> None:
        wid = notify.add_webhook(self.conn, url="http://example.com/hook", name="CI",
                                 secret="s3cr3t", events="new_items,source_failed")
        hooks = notify.list_webhooks(self.conn)
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["name"], "CI")
        self.assertNotIn("secret", hooks[0])      # 口令不回显
        self.assertTrue(notify.remove_webhook(self.conn, wid))
        self.assertEqual(notify.list_webhooks(self.conn), [])

    def test_add_rejects_bad_url(self) -> None:
        with self.assertRaises(ValueError):
            notify.add_webhook(self.conn, url="ftp://x")

    def test_dispatch_with_signature(self) -> None:
        url = f"http://127.0.0.1:{self.port}/hook"
        notify.add_webhook(self.conn, url=url, secret="s3cr3t", events="new_items")
        n = notify.dispatch_webhooks(self.conn, "new_items",
                                     {"event": "new_items", "count": 2})
        self.assertEqual(n, 1)
        recv = self._wait_received(1)
        self.assertEqual(len(recv), 1)
        headers = recv[0]["headers"]
        self.assertEqual(headers.get("x-signalwatch-event"), "new_items")
        sig = headers.get("x-signalwatch-signature", "")
        self.assertTrue(sig.startswith("sha256="))
        payload = json.loads(recv[0]["body"])
        self.assertEqual(payload["count"], 2)

    def test_dispatch_filters_events(self) -> None:
        notify.add_webhook(self.conn, url=f"http://127.0.0.1:{self.port}/hook",
                           events="source_failed")
        n = notify.dispatch_webhooks(self.conn, "new_items", {"event": "new_items"})
        self.assertEqual(n, 0)                    # 没订阅 new_items，不推

    def test_dispatch_marks_failure(self) -> None:
        wid = notify.add_webhook(self.conn, url="http://127.0.0.1:1/hook",
                                 events="new_items")
        n = notify.dispatch_webhooks(self.conn, "new_items", {"event": "new_items"})
        self.assertEqual(n, 0)
        hooks = notify.list_webhooks(self.conn)
        self.assertEqual(hooks[0]["id"], wid)
        self.assertTrue(hooks[0]["last_error"])   # 失败留痕


if __name__ == "__main__":
    unittest.main()
