"""桌面窗口的自动化测试：不开真窗口也能证明「每个页面都画得出来」。

tkinter 没有官方 headless，但这台机器有桌面会话，Tk 能建窗口（只是不显示）。
所以这里建一个真 App，切一遍所有页面，再把窗口销毁——比人眼点一遍可靠。

CI/无桌面环境下 Tk 会抛 TclError，这时跳过而不是失败。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalwatch.config import Settings

try:
    import tkinter as tk
except Exception:  # pragma: no cover
    tk = None


class DesktopPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        if tk is None:
            self.skipTest("没有 tkinter")
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        self.patches = [mock.patch("signalwatch.config.SETTINGS", self.settings),
                        mock.patch("signalwatch.desktop.SETTINGS", self.settings)]
        for p in self.patches:
            p.start()
        try:
            probe = tk.Tk()
            probe.destroy()
        except tk.TclError:
            self.skipTest("这个环境没有图形界面")

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _app(self):
        from signalwatch.desktop import App
        from signalwatch.scheduler import Crawler

        crawler = Crawler(self.settings)
        app = App(crawler, paused=True)
        app._enter({"username": "admin", "role": "admin"})

        def cleanup():
            for conn in (getattr(app, "conn", None), getattr(crawler, "conn", None)):
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
            try:
                app.root.destroy()
            except Exception:
                pass

        self.addCleanup(cleanup)
        return app

    def test_all_pages_render(self) -> None:
        app = self._app()
        app.root.update_idletasks()
        pages = ["overview", "home", "items", "categories", "rules",
                 "sources", "jobs", "reports", "users", "settings", "me"]
        for name in pages:
            app._go(name)
            app.root.update_idletasks()
            self.assertTrue(app.pages[name].winfo_exists(), name)

    def test_starred_is_a_home_filter_not_a_page(self) -> None:
        app = self._app()
        app.root.update_idletasks()
        app._go("starred")
        app.root.update_idletasks()
        self.assertIn("home", app.pages)

    def test_login_screen_accepts_guest(self) -> None:
        from signalwatch.desktop import App
        from signalwatch.scheduler import Crawler

        crawler = Crawler(self.settings)
        app = App(crawler, paused=True)

        def cleanup():
            for conn in (getattr(app, "conn", None), getattr(crawler, "conn", None)):
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
            try:
                app.root.destroy()
            except Exception:
                pass

        self.addCleanup(cleanup)
        app._enter(None)
        app.root.update_idletasks()
        self.assertEqual(app.user["username"], "访客")


if __name__ == "__main__":
    unittest.main()
