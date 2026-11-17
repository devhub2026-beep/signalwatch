"""PWA 安装条件的回归测试：证书之外的硬条件都在这儿盯着。

浏览器肯让你「添加到主屏幕」，至少要：https（部署层的事）、一个
manifest、192 与 512 两种 PNG 图标、一个能注册的 service worker。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from signalwatch.api import create_app
from signalwatch.config import Settings

WEB = Path(__file__).resolve().parents[1] / "src" / "signalwatch" / "web"


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))

    def test_has_png_192_and_512(self) -> None:
        icons = self.manifest["icons"]
        sizes = {(i["sizes"], i["type"]) for i in icons}
        self.assertIn(("192x192", "image/png"), sizes)
        self.assertIn(("512x512", "image/png"), sizes)

    def test_icon_files_actually_exist_and_are_png(self) -> None:
        for icon in self.manifest["icons"]:
            name = icon["src"].lstrip("/")
            path = WEB / name
            self.assertTrue(path.exists(), f"manifest 指向的 {name} 不存在")
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n", name)

    def test_standalone_and_start_url(self) -> None:
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertTrue(self.manifest["start_url"])
        self.assertTrue(self.manifest["name"])


class ServableTests(unittest.TestCase):
    def test_manifest_and_icons_are_served(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
            app = create_app(Settings(home=Path(home)))
            client = TestClient(app)
            manifest = client.get("/manifest.webmanifest")
            self.assertEqual(manifest.status_code, 200)
            self.assertIn("manifest+json", manifest.headers["content-type"])
            for path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/sw.js"):
                resp = client.get(path)
                self.assertEqual(resp.status_code, 200, path)

    def test_index_links_manifest_and_apple_icon(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn("apple-touch-icon", html)
        self.assertIn('<script src="/index.js" defer></script>', html)
        js = (WEB / "index.js").read_text(encoding="utf-8")
        self.assertIn("serviceWorker", js)


if __name__ == "__main__":
    unittest.main()
