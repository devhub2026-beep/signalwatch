"""桌面窗口的自动化测试：不开真窗口也能证明「每个页面都画得出来」。

tkinter 没有官方 headless，但这台机器有桌面会话，Tk 能建窗口（只是不显示）。
所以这里建一个真 App，切一遍所有页面，再把窗口销毁——比人眼点一遍可靠。

**为什么用子进程跑**：把 tkinter 和别的测试放在同一个进程里时，进程退出阶段
会报 `Tcl_AsyncDelete: async handler deleted by the wrong thread` 并让整个
unittest 进程异常退出（Tk 的 async handler 被非创建线程清掉）。这不是断言失败，
是进程级崩溃，会把后面的用例一起带走。所以真正的 Tk 用例放在
`SIGNALWATCH_TK_INPROC=1` 的子进程里跑，外层只做调度和结果转发。

无桌面环境（CI）里子进程会因 TclError 跳过，外层也跳过，不会红。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalwatch.config import Settings

try:
    import tkinter as tk
except Exception:  # pragma: no cover
    tk = None

INPROC = os.environ.get("SIGNALWATCH_TK_INPROC") == "1"
ROOT = Path(__file__).resolve().parents[1]
@unittest.skipIf(not INPROC, "真实 Tk 用例只在子进程里跑（见模块文档）")
class DesktopPagesInproc(unittest.TestCase):
    """只在子进程里跑：建真 App、切页面、销毁。"""

    def setUp(self) -> None:
        if tk is None:
            self.skipTest("没有 tkinter")
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.settings = Settings(home=Path(self.tmp.name), min_interval=0.0)
        import signalwatch.desktop  # noqa: F401  —— patch 之前必须先导入，否则取不到属性
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
            app._alive = False
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
        pages = ["overview", "home", "items", "categories", "rules", "sources",
                 "jobs", "reports", "users", "audit", "settings", "me"]
        for name in pages:
            app._go(name)
            app.root.update_idletasks()
            self.assertTrue(app.pages[name].winfo_exists(), name)

    def test_audit_page_lists_log_rows(self) -> None:
        from signalwatch import audit

        app = self._app()
        audit.log(app.conn, "admin", "test.audit", "obj-1", "明细")
        app._go("audit")
        app.root.update_idletasks()
        rows = app.audit_tree.get_children()
        self.assertGreaterEqual(len(rows), 1)

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
            app._alive = False
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


@unittest.skipIf(INPROC, "子进程内不调度自己")
class DesktopPagesTests(unittest.TestCase):
    """外层调度器：每条用例起一个干净的子进程。"""

    def _run(self, case: str) -> None:
        env = dict(os.environ, SIGNALWATCH_TK_INPROC="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.test_desktop_ui.{case}"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and ("没有图形界面" in output or "没有 tkinter" in output):
            self.skipTest("这个环境没有图形界面")
        self.assertEqual(proc.returncode, 0, output[-1200:])

    def test_all_pages_render(self) -> None:
        self._run("DesktopPagesInproc.test_all_pages_render")

    def test_audit_page_lists_log_rows(self) -> None:
        self._run("DesktopPagesInproc.test_audit_page_lists_log_rows")

    def test_starred_is_a_home_filter_not_a_page(self) -> None:
        self._run("DesktopPagesInproc.test_starred_is_a_home_filter_not_a_page")

    def test_login_screen_accepts_guest(self) -> None:
        self._run("DesktopPagesInproc.test_login_screen_accepts_guest")


if __name__ == "__main__":
    unittest.main()
