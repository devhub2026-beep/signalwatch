"""桌面客户端：登录 → 首页 / 收藏 / 订阅 / 我的 四个页。

界面全部手画（Canvas 圆角卡片、胶囊按钮、自绘滚动条），因为 ttk 长什么样由系统主题
决定，做不出统一的深色质感。

结构：
  Crawler（后台线程，按各自节奏抓）→ 新条目落库
  Tk 主线程：每 0.9 秒查一次库 → 列表刷新 / 右下角浮窗
  board.py：分类、搜索、未读、收藏、订阅词（纯 SQL）
  auth.py：登录、角色（admin / analyst / viewer）
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog
from typing import Iterable

from . import admin, audit, auth, board, db, notify
from .config import SETTINGS
from .scheduler import Crawler

# ---- 配色 ----------------------------------------------------------------
BG = "#07090c"
SIDE = "#0a0e13"
SURFACE = "#0d1218"
CARD = "#111820"
CARD_HOVER = "#16202b"
HAIR = "#1b2532"
FG = "#e9eef5"
MUTED = "#7d8899"
DIM = "#46566f"
ACCENT = "#5b8cff"
ACCENT_SOFT = "#22314f"
GOLD = "#d4a24a"
GREEN = "#3fb950"
RED = "#e5534b"
WARN = "#e0a458"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"
ROW_H = 78
PAD = 22
CATS = ["全部", "金融", "科技", "学术", "收藏"]
ROLE_TEXT = {"admin": "管理员", "analyst": "分析员", "viewer": "只读"}


def human_time(ms: int) -> str:
    if not ms:
        return "--:--"
    dt = datetime.fromtimestamp(ms / 1000.0)
    if dt.hour == 0 and dt.minute == 0:
        return dt.strftime("%m-%d")
    return dt.strftime("%H:%M")


def human_day(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M")


def human_ago(seconds: int) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


@dataclass
class DeskRow:
    id: int
    source: str
    title: str
    published_at: int
    url: str
    version: int
    hits: str = ""


def rows_from_db(conn, *, q="", category="", only_unread=False, only_starred=False, limit=300):
    items = board.board_query(conn, q=q, category=category, only_unread=only_unread,
                              only_starred=only_starred, limit=limit)
    return [
        DeskRow(id=int(it["id"]), source=str(it["source"]), title=str(it["title"]),
                published_at=int(it["published_at"]), url=str(it.get("url") or ""),
                version=int(it.get("version") or 1),
                hits=", ".join(board.matched_rules(conn, it)))
        for it in items
    ]


def new_ids(prev: Iterable[int], rows: Iterable[DeskRow]) -> list[int]:
    seen = set(prev)
    return [r.id for r in rows if r.id not in seen]


# ---- 手画控件 ------------------------------------------------------------
def rounded(canvas: tk.Canvas, x1, y1, x2, y2, r, fill, outline="", width=1):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=18,
                                 fill=fill, outline=outline or fill, width=width)


class Pill(tk.Canvas):
    def __init__(self, master, text, command=None, *, selected=False, accent=False,
                 height=32, padx=16, bg=BG, font=None, width=None):
        self.f = font or tkfont.Font(family=FONT, size=9)
        self.text = text
        self.command = command
        self.selected = selected
        self.accent = accent
        self._h = height
        self._bg = bg
        w = width or (self.f.measure(text) + padx * 2)
        super().__init__(master, width=w, height=height, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self._hover = False
        self._paint()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def _colors(self):
        if self.selected or self.accent:
            return ACCENT, "#07101f"
        if self._hover:
            return CARD_HOVER, FG
        return SURFACE, MUTED

    def _paint(self):
        self.delete("all")
        fill, fg = self._colors()
        rounded(self, 0, 0, self.winfo_reqwidth() - 1, self._h - 1, self._h / 2, fill)
        self.create_text(self.winfo_reqwidth() / 2, self._h / 2, text=self.text, fill=fg,
                         font=self.f)

    def _enter(self, _e):
        self._hover = True
        self._paint()

    def _leave(self, _e):
        self._hover = False
        self._paint()

    def _click(self, _e):
        if self.command:
            self.command()

    def set_selected(self, v: bool):
        self.selected = v
        self._paint()

    def set_text(self, t: str):
        self.text = t
        self._paint()


class IconMark(tk.Canvas):
    def __init__(self, master, size=26, bg=BG):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        c = size / 2
        for r in (size * 0.46, size * 0.32, size * 0.18):
            self.create_oval(c - r, c - r, c + r, c + r, outline=ACCENT, width=1.6)
        self.create_oval(c - 2.6, c - 2.6, c + 2.6, c + 2.6, fill=GREEN, outline="")


class Avatar(tk.Canvas):
    """圆底 + 首字母。"""

    def __init__(self, master, name: str, size=38, bg=BG):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        fill, fg = (ACCENT, "#07101f") if name else (HAIR, MUTED)
        self.create_oval(1, 1, size - 1, size - 1, fill=fill, outline="")
        self.create_text(size / 2, size / 2 + 1, text=(name or "?")[:1].upper(),
                         fill=fg, font=(FONT, int(size / 2.6), "bold"))


class Entry(tk.Entry):
    """带占位提示、聚焦描边的输入框。"""

    def __init__(self, master, placeholder="", width=18, show=None, bg=SURFACE):
        super().__init__(master, bg=bg, fg=MUTED, insertbackground=ACCENT, relief="flat",
                         font=(FONT, 10), width=width, highlightthickness=1,
                         highlightbackground=HAIR, highlightcolor=ACCENT_SOFT, show=show)
        self._ph = placeholder
        self._showing = False
        if placeholder:
            self.insert(0, placeholder)
            self._showing = True
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)

    def _focus_in(self, _e):
        if self._showing:
            self.delete(0, "end")
            self.configure(fg=FG, show=self.cget("show"))
            self._showing = False

    def _focus_out(self, _e):
        if not self.get().strip() and self._ph:
            self.insert(0, self._ph)
            self.configure(fg=MUTED)
            self._showing = True

    def value(self) -> str:
        return "" if self._showing else self.get().strip()

    def clear(self):
        self.delete(0, "end")
        self._showing = False


class Toast(tk.Toplevel):
    def __init__(self, master, title, body, on_open=None):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.98)
        except tk.TclError:
            pass
        self.configure(bg=HAIR)
        self._on_open = on_open
        inner = tk.Frame(self, bg=SURFACE, padx=16, pady=13)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(inner, bg=SURFACE)
        head.pack(fill="x")
        tk.Label(head, text="●", fg=GREEN, bg=SURFACE, font=(FONT, 9)).pack(side="left")
        tk.Label(head, text=title, fg=FG, bg=SURFACE, font=(FONT, 10, "bold")).pack(side="left", padx=(7, 0))
        tk.Label(head, text="刚刚", fg=MUTED, bg=SURFACE, font=(FONT, 8)).pack(side="right")
        tk.Label(inner, text=body, fg=MUTED, bg=SURFACE, font=(FONT, 9), wraplength=320,
                 justify="left", anchor="w").pack(fill="x", pady=(7, 0))
        for w in [self, inner, head] + list(inner.winfo_children()) + list(head.winfo_children()):
            w.bind("<Button-1>", self._click)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{sw - w - 26}+{sh - h - 64}")
        self.after(8000, self._close)

    def _click(self, _e=None):
        if self._on_open:
            self._on_open()
        self._close()

    def _close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


class App:
    def __init__(self, crawler: Crawler, *, paused: bool = False, start_page: str = "overview") -> None:
        self.start_page = start_page
        self.crawler = crawler
        self.conn = crawler.conn
        self.paused = paused
        self.user: dict | None = None
        self.page = "home"
        self.category = "全部"
        self.query = ""
        self.only_unread = False
        self.limit = 200
        self.rows: list[DeskRow] = []
        self.selected: DeskRow | None = None
        self.known: list[int] = []
        self.hover_id: int | None = None
        self.events: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("SignalWatch")
        self.root.configure(bg=BG)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(1340, sw - 60), min(820, sh - 60)
        self.root.geometry(f"{w}x{h}+{max(4, (sw - w) // 2)}+{max(4, (sh - h) // 2 - 20)}")
        self.root.minsize(980, 640)

        try:
            admin.seed_categories(self.conn)
            admin.auto_categorize(self.conn)
        except Exception:
            pass
        self._build_login()
        self.root.after(120, self._try_remember)
        self.root.after(1200, self._tick_status)
        if not paused:
            self.crawler.start()
            self._alive = True
            threading.Thread(target=self._job_worker, daemon=True).start()

    # ================= 登录 =================
    def _build_login(self) -> None:
        self._clear()
        self.root.configure(bg=BG)
        wrap = tk.Frame(self.root, bg=BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        IconMark(wrap, size=44, bg=BG).pack()
        tk.Label(wrap, text="SignalWatch", fg=FG, bg=BG, font=(FONT, 20, "bold")).pack(pady=(12, 2))
        tk.Label(wrap, text="盯住公开信息，把变化先递到你眼前", fg=MUTED, bg=BG,
                 font=(FONT, 9)).pack(pady=(0, 26))

        card = tk.Frame(wrap, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
        card.pack()
        inner = tk.Frame(card, bg=SURFACE, padx=30, pady=26)
        inner.pack()

        tk.Label(inner, text="账号", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w")
        self.login_user = Entry(inner, "admin", width=26)
        self.login_user.pack(fill="x", ipady=8, pady=(6, 14))
        tk.Label(inner, text="口令", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w")
        self.login_pass = Entry(inner, "", width=26, show="●")
        self.login_pass.pack(fill="x", ipady=8, pady=(6, 6))
        self.login_pass.bind("<Return>", lambda _e: self._do_login())

        self.login_msg = tk.Label(inner, text="", fg=RED, bg=SURFACE, font=(FONT, 8), anchor="w")
        self.login_msg.pack(fill="x", pady=(2, 10))

        Pill(inner, "登 录", self._do_login, accent=True, height=38, padx=0, bg=SURFACE,
             width=252).pack(pady=(4, 0))
        guest = tk.Label(inner, text="没有账号？以访客身份进入", fg=ACCENT, bg=SURFACE,
                         font=(FONT, 8), cursor="hand2")
        guest.pack(pady=(14, 0))
        guest.bind("<Button-1>", lambda _e: self._enter(None))

        tk.Label(wrap, text="首次启动会自动建一个 admin / admin123，进去后请先改口令",
                 fg=DIM, bg=BG, font=(FONT, 8)).pack(pady=(16, 0))

    def _do_login(self) -> None:
        name, pw = self.login_user.value(), self.login_pass.value()
        try:
            user = auth.login(self.conn, name, pw)
        except Exception as exc:  # 口令错 / 停用
            self.login_msg.configure(text=str(exc))
            return
        audit.log(self.conn, name, "auth.login", name, "桌面端登录")
        self._remember(user)
        self._enter(user["user"] if "user" in user else user)

    # ---------- 记住我：下次打开直接进 ----------
    def _remember_file(self):
        return SETTINGS.db_path.parent / "remember.json"

    def _remember(self, payload: dict) -> None:
        try:
            token = payload.get("token") or ""
            user = payload.get("user") or payload
            self._remember_file().write_text(
                json.dumps({"username": user.get("username", ""), "token": token}),
                encoding="utf-8")
        except Exception:
            pass

    def _forget(self) -> None:
        try:
            self._remember_file().unlink()
        except Exception:
            pass

    def _try_remember(self) -> None:
        try:
            data = json.loads(self._remember_file().read_text(encoding="utf-8"))
            user = auth.user_from_token(self.conn, data.get("token", ""))
        except Exception:
            return
        self.user = user
        self._build_shell()

    def _enter(self, user: dict | None) -> None:
        self.user = user or {"username": "访客", "role": "viewer"}
        self._build_shell()

    def _logout(self) -> None:
        self._forget()
        if self.user:
            audit.log(self.conn, self.user["username"], "auth.logout", self.user["username"], "")
        self.user = None
        self._build_login()

    # ================= 主框架 =================
    def _clear(self) -> None:
        for w in self.root.winfo_children():
            w.destroy()

    def _build_shell(self) -> None:
        self._clear()
        side = tk.Frame(self.root, bg=SIDE, width=208)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=SIDE)
        brand.pack(fill="x", padx=18, pady=(20, 26))
        IconMark(brand, size=24, bg=SIDE).pack(side="left")
        tk.Label(brand, text="SignalWatch", fg=FG, bg=SIDE,
                 font=(FONT, 11, "bold")).pack(side="left", padx=(9, 0))

        self.nav: dict[str, Pill] = {}
        for key, label in (("overview", "工作台"), ("home", "首页"), ("starred", "收藏"),
                           ("items", "条目"), ("categories", "分类"), ("rules", "订阅"),
                           ("sources", "数据源"), ("jobs", "任务"), ("reports", "报表"),
                           ("users", "用户"), ("settings", "设置"), ("me", "我的")):
            p = Pill(side, label, lambda k=key: self._go(k), selected=(key == "home"),
                     height=38, padx=18, bg=SIDE)
            p.pack(fill="x", padx=14, pady=(0, 6))
            self.nav[key] = p

        bottom = tk.Frame(side, bg=SIDE)
        bottom.pack(side="bottom", fill="x", padx=16, pady=16)
        self.dot = tk.Label(bottom, text="●", fg=GREEN, bg=SIDE, font=(FONT, 8))
        self.dot.pack(side="left")
        self.state_lbl = tk.Label(bottom, text="运行中", fg=MUTED, bg=SIDE, font=(FONT, 8))
        self.state_lbl.pack(side="left", padx=(6, 0))
        tk.Label(bottom, text=f"v{SETTINGS.version if hasattr(SETTINGS, 'version') else '0.1.0'}",
                 fg=DIM, bg=SIDE, font=(FONT, 8)).pack(side="right")

        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        self.pages: dict[str, tk.Frame] = {}
        self._build_overview()
        self._build_home()
        self._build_items()
        self._build_categories()
        self._build_rules()
        self._build_sources()
        self._build_jobs()
        self._build_reports()
        self._build_users()
        self._build_settings()
        self._build_me()
        self._go(self.start_page)
        self._pump()

    def _go(self, key: str) -> None:
        self.page = key
        target = {"home": "home", "starred": "home", "rules": "rules", "sources": "sources",
                  "users": "users", "me": "me", "overview": "overview", "items": "items",
                  "categories": "categories", "jobs": "jobs", "reports": "reports",
                  "settings": "settings"}[key]
        for name, frame in self.pages.items():
            if name == target:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for k, pill in self.nav.items():
            pill.set_selected(k == key)
        if key == "starred":
            self.category = "收藏"
        elif key == "home":
            self.category = "全部"
        if target == "home":
            for name, chip in getattr(self, "chips", {}).items():
                chip.set_selected(name == self.category)
            self._refresh()
        elif target == "rules":
            self._render_rules()
        elif target == "me":
            self._render_me()
        elif target == "sources":
            self._render_sources()
        elif target == "users":
            self._render_users()
        elif target == "overview":
            self._render_overview()
        elif target == "items":
            self._render_items(reset=True)
        elif target == "categories":
            self._render_categories()
        elif target == "jobs":
            self._render_jobs()
        elif target == "reports":
            self._render_reports()
        elif target == "settings":
            self._render_settings()

    # ================= 首页 =================
    def _build_home(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["home"] = page

        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="首页", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        self.count_lbl = tk.Label(head, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.count_lbl.pack(side="left", padx=(12, 0))

        right = tk.Frame(head, bg=BG)
        right.pack(side="right")
        self.btn_crawl = Pill(right, "立刻抓一轮", self._crawl_now, accent=True, height=34, padx=18)
        self.btn_crawl.pack(side="right")
        self.btn_pause = Pill(right, "继续" if self.paused else "暂停", self._toggle_pause,
                              height=34, padx=16)
        self.btn_pause.pack(side="right", padx=(0, 10))

        chips = tk.Frame(page, bg=BG)
        chips.pack(fill="x", padx=PAD, pady=(16, 0))
        self.chips: dict[str, Pill] = {}
        for name in CATS:
            p = Pill(chips, name, lambda n=name: self._set_category(n),
                     selected=(name == "全部"), height=32, padx=16)
            p.pack(side="left", padx=(0, 8))
            self.chips[name] = p

        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", padx=PAD, pady=(14, 0))
        search_wrap = tk.Frame(bar, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
        search_wrap.pack(side="left", fill="x", expand=True)
        tk.Label(search_wrap, text="⌕", fg=MUTED, bg=SURFACE, font=(FONT, 11)).pack(side="left", padx=(10, 0))
        self.search = tk.Entry(search_wrap, bg=SURFACE, fg=MUTED, insertbackground=ACCENT,
                               relief="flat", font=(FONT, 10))
        self.search.insert(0, "搜标题 / 公司 / 代码 / 摘要")
        self.search.pack(side="left", fill="x", expand=True, ipady=8, padx=6)
        self.search.bind("<KeyRelease>", lambda _e: self._on_query())
        self.search.bind("<FocusIn>", self._search_in)
        self.search.bind("<FocusOut>", self._search_out)
        self._search_ph = True

        self.unread_pill = Pill(bar, "只看未读", self._toggle_unread, height=34, padx=14)
        self.unread_pill.pack(side="left", padx=(12, 0))
        Pill(bar, "导出", self._export, height=34, padx=12).pack(side="right")

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=PAD, pady=(16, 0))
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(left, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Configure>", lambda _e: self._draw())

        self.thumb = tk.Canvas(left, width=6, bg=BG, highlightthickness=0, bd=0)
        self.thumb.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self.thumb.bind("<B1-Motion>", self._drag_thumb)

        self.panel = tk.Frame(body, bg=BG, width=330)
        self.panel.pack(side="right", fill="y", padx=(18, 0))
        self.panel.pack_propagate(False)
        self._build_panel()

        self.status = tk.Label(page, text="", fg=MUTED, bg=BG, font=(FONT, 8), anchor="w")
        self.status.pack(fill="x", padx=PAD, pady=(10, 12))

    def _search_in(self, _e):
        if self._search_ph:
            self.search.delete(0, "end")
            self.search.configure(fg=FG)
            self._search_ph = False

    def _search_out(self, _e):
        if not self.search.get().strip():
            self.search.insert(0, "搜标题 / 公司 / 代码 / 摘要")
            self.search.configure(fg=MUTED)
            self._search_ph = True

    def _build_panel(self) -> None:
        for w in self.panel.winfo_children():
            w.destroy()
        if self.selected is None:
            box = tk.Frame(self.panel, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
            box.pack(fill="both", expand=True)
            tk.Label(box, text="⌕", fg=HAIR, bg=SURFACE, font=(FONT, 26)).pack(pady=(110, 8))
            tk.Label(box, text="选一条看详情", fg=MUTED, bg=SURFACE, font=(FONT, 10)).pack()
            tk.Label(box, text="双击任意一条可以直接打开原文", fg=DIM, bg=SURFACE,
                     font=(FONT, 8), wraplength=240).pack(pady=(6, 0))
            return

        row = self.selected
        box = tk.Frame(self.panel, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
        box.pack(fill="both", expand=True)
        inner = tk.Frame(box, bg=SURFACE, padx=18, pady=16)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text=row.title, fg=FG, bg=SURFACE, font=(FONT, 11, "bold"),
                 wraplength=285, justify="left", anchor="w").pack(fill="x")
        meta = tk.Frame(inner, bg=SURFACE)
        meta.pack(fill="x", pady=(10, 0))
        cat = board.category_of(row.source) or "其他"
        for text, color in ((cat, ACCENT), (row.source, MUTED), (human_time(row.published_at), MUTED),
                            (f"v{row.version}", GOLD if row.version > 1 else MUTED)):
            tk.Label(meta, text=text, fg=color, bg=SURFACE, font=(FONT, 8)).pack(side="left", padx=(0, 12))

        tk.Frame(inner, bg=HAIR, height=1).pack(fill="x", pady=(14, 12))
        summary = tk.Text(inner, bg=SURFACE, fg=MUTED, relief="flat", wrap="word",
                          font=(FONT, 9), height=7, padx=0, pady=0, insertwidth=0,
                          highlightthickness=0, bd=0)
        summary.pack(fill="both", expand=True)
        summary.insert("1.0", self._summary_of(row))
        summary.configure(state="disabled")

        actions = tk.Frame(inner, bg=SURFACE)
        actions.pack(fill="x", pady=(14, 0))
        Pill(actions, "打开原文", lambda: self._open(row), accent=True, height=34, padx=14,
             bg=SURFACE).pack(side="left")
        Pill(actions, "标为已读", lambda: self._mark_read(row), height=34, padx=12,
             bg=SURFACE).pack(side="left", padx=8)
        starred = board.is_starred(self.conn, row.id)
        Pill(actions, "★ 已收藏" if starred else "☆ 收藏", lambda: self._star(row),
             height=34, padx=12, bg=SURFACE, selected=starred).pack(side="left")

        hist = admin.item_history(self.conn, row.id)
        if len(hist) > 1:
            tk.Frame(inner, bg=HAIR, height=1).pack(fill="x", pady=(14, 10))
            tk.Label(inner, text="改动历史", fg=MUTED, bg=SURFACE,
                     font=(FONT, 8, "bold")).pack(anchor="w")
            for line in admin.item_diff(self.conn, row.id)[-3:]:
                tk.Label(inner, text=line, fg=GOLD, bg=SURFACE, font=(FONT, 8),
                         wraplength=285, justify="left", anchor="w").pack(fill="x", pady=(3, 0))
        if row.sec_code:
            tk.Frame(inner, bg=HAIR, height=1).pack(fill="x", pady=(14, 10))
            Pill(inner, f"看 {row.sec_name or row.sec_code} 的时间线",
                 lambda: self._timeline(row), height=30, padx=12, bg=SURFACE).pack(anchor="w")

    def _summary_of(self, row: DeskRow) -> str:
        got = self.conn.execute("SELECT summary FROM items WHERE id = ?", (row.id,)).fetchone()
        text = (got["summary"] if got else "") or ""
        return text.strip() or "（这条没有摘要，点「打开原文」看全文）"

    # ================= 订阅 =================
    def _build_rules(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["rules"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="订阅词", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        tk.Label(head, text="标题里出现这些词的条目，会在列表里被标黄", fg=MUTED, bg=BG,
                 font=(FONT, 9)).pack(side="left", padx=(12, 0))

        add = tk.Frame(page, bg=BG)
        add.pack(fill="x", padx=PAD, pady=(16, 0))
        self.rule_input = Entry(add, "比如：中标、增持、回购", width=24)
        self.rule_input.pack(side="left", ipady=8)
        self.rule_input.bind("<Return>", lambda _e: self._add_rule())
        self.rule_source = Entry(add, "来源（可不填）", width=14)
        self.rule_source.pack(side="left", padx=(10, 0), ipady=8)
        Pill(add, "添加", self._add_rule, accent=True, height=36, padx=16).pack(side="left", padx=(10, 0))

        self.rule_list = tk.Frame(page, bg=BG)
        self.rule_list.pack(fill="both", expand=True, padx=PAD, pady=(18, 12))
        self._render_rules()

    def _render_rules(self) -> None:
        for w in self.rule_list.winfo_children():
            w.destroy()
        rules = board.list_rules(self.conn)
        if not rules:
            tk.Label(self.rule_list, text="还没有订阅词。加上「中标」，以后每条中标公告都会被标出来。",
                     fg=DIM, bg=BG, font=(FONT, 9)).pack(anchor="w", pady=10)
            return
        for r in rules:
            card = tk.Frame(self.rule_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=12)
            inner.pack(fill="x")
            tk.Label(inner, text=r["keyword"], fg=FG, bg=CARD, font=(FONT, 10, "bold")).pack(side="left")
            cat = r.get("category") or "全部"
            src = r.get("source") or "不限"
            on = bool(r.get("enabled", 1))
            tk.Label(inner, text=f"分类 {cat} · 来源 {src} · 命中 {r.get('hits', 0)}",
                     fg=MUTED, bg=CARD, font=(FONT, 8)).pack(side="left", padx=(14, 0))
            if not on:
                tk.Label(inner, text="已停用", fg=GOLD, bg=CARD, font=(FONT, 8)).pack(side="left", padx=(10, 0))
            tk.Button(inner, text="删除", command=lambda rid=r["id"]: self._del_rule(rid),
                      bg=CARD, fg=MUTED, activebackground=CARD, activeforeground=FG,
                      relief="flat", bd=0, highlightthickness=0, font=(FONT, 9),
                      padx=10, cursor="hand2").pack(side="right")
            tk.Button(inner, text="启用" if not on else "停用",
                      command=lambda rid=r["id"], e=on: self._toggle_rule(rid, e),
                      bg=CARD, fg=ACCENT if not on else MUTED, activebackground=CARD,
                      activeforeground=FG, relief="flat", bd=0, highlightthickness=0,
                      font=(FONT, 9), padx=10, cursor="hand2").pack(side="right", padx=(0, 4))

    def _add_rule(self) -> None:
        word = self.rule_input.value()
        if not word:
            return
        cat = "" if self.category in ("全部", "收藏") else self.category
        src = self.rule_source.value().strip()
        board.add_rule(self.conn, word, cat, source=src)
        audit.log(self.conn, self._name(), "rule.add", word,
                  f"分类 {cat or '不限'}，来源 {src or '不限'}")
        self.rule_input.clear()
        self.rule_source.clear()
        self._render_rules()
        self._refresh()

    def _del_rule(self, rid: int) -> None:
        board.delete_rule(self.conn, rid)
        audit.log(self.conn, self._name(), "rule.delete", f"#{rid}", "")
        self._render_rules()

    def _toggle_rule(self, rid: int, enabled: bool) -> None:
        board.set_rule_enabled(self.conn, rid, not enabled)
        audit.log(self.conn, self._name(), "rule.toggle", f"#{rid}",
                  "停用" if enabled else "启用")
        self._render_rules()

    def _name(self) -> str:
        return (self.user or {}).get("username", "guest")

    # ================= 我的 =================
    def _build_me(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["me"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="我的", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")

        self.me_card = tk.Frame(page, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
        self.me_card.pack(fill="x", padx=PAD, pady=(18, 0))
        self.me_inner = tk.Frame(self.me_card, bg=SURFACE, padx=22, pady=20)
        self.me_inner.pack(fill="x")

        self.stat_box = tk.Frame(page, bg=BG)
        self.stat_box.pack(fill="x", padx=PAD, pady=(16, 0))

        self.src_box = tk.Frame(page, bg=BG)
        self.src_box.pack(fill="x", padx=PAD, pady=(16, 0))

    def _render_me(self) -> None:
        for w in self.me_inner.winfo_children():
            w.destroy()
        for w in self.stat_box.winfo_children():
            w.destroy()
        for w in self.src_box.winfo_children():
            w.destroy()

        user = self.user or {"username": "访客", "role": "viewer"}
        role = ROLE_TEXT.get(user.get("role", "viewer"), user.get("role"))
        top = tk.Frame(self.me_inner, bg=SURFACE)
        top.pack(fill="x")
        Avatar(top, user["username"], size=46, bg=SURFACE).pack(side="left")
        who = tk.Frame(top, bg=SURFACE)
        who.pack(side="left", padx=(14, 0))
        tk.Label(who, text=user["username"], fg=FG, bg=SURFACE, font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(who, text=role, fg=ACCENT, bg=SURFACE, font=(FONT, 8)).pack(anchor="w", pady=(3, 0))
        Pill(top, "退出登录", self._logout, height=32, padx=14, bg=SURFACE).pack(side="right")

        if user.get("username") != "访客":
            last = user.get("last_login_at") or 0
            tk.Label(self.me_inner, text=f"上次登录：{human_day(last)}", fg=MUTED, bg=SURFACE,
                     font=(FONT, 8)).pack(anchor="w", pady=(14, 0))

        # 统计
        row = self.conn.execute("SELECT COUNT(*) n FROM items").fetchone()
        today = self.conn.execute(
            "SELECT COUNT(*) n FROM items WHERE first_seen_at >= ?",
            (int((time.time() - 86400) * 1000),)).fetchone()
        stats = [("总条目", int(row["n"])), ("今日新增", int(today["n"])),
                 ("未读", board.unread_count(self.conn)), ("收藏", board.star_count(self.conn))]
        for i, (label, value) in enumerate(stats):
            card = tk.Frame(self.stat_box, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0))
            self.stat_box.grid_columnconfigure(i, weight=1)
            inner = tk.Frame(card, bg=CARD, padx=18, pady=16)
            inner.pack(fill="both", expand=True)
            tk.Label(inner, text=str(value), fg=FG, bg=CARD, font=(MONO, 22, "bold")).pack(anchor="w")
            tk.Label(inner, text=label, fg=MUTED, bg=CARD, font=(FONT, 8)).pack(anchor="w", pady=(4, 0))

        # 数据源
        tk.Label(self.src_box, text="数据源", fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
        snap = self.crawler.snapshot().get("sources", [])
        for st in sorted(snap, key=lambda s: s.get("name", "")):
            name = st.get("name", "?")
            card = tk.Frame(self.src_box, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(8, 0))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=10)
            inner.pack(fill="x")
            ok = st.get("status") == "ok"
            tk.Label(inner, text="●", fg=GREEN if ok else WARN, bg=CARD, font=(FONT, 8)).pack(side="left")
            tk.Label(inner, text=name, fg=FG, bg=CARD, font=(FONT, 9, "bold")).pack(side="left", padx=(8, 0))
            tk.Label(inner, text=f"{st.get('items', 0)} 条", fg=MUTED, bg=CARD,
                     font=(FONT, 8)).pack(side="left", padx=(14, 0))
            tk.Label(inner, text=f"上次 {human_day(st.get('last_ok_at') or 0)}", fg=MUTED, bg=CARD,
                     font=(FONT, 8)).pack(side="right")

        tk.Label(self.src_box, text=f"数据库：{SETTINGS.db_path}", fg=DIM, bg=BG,
                 font=(FONT, 8)).pack(anchor="w", pady=(14, 0))

    # ================= 概览 =================
    def _build_overview(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["overview"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="概览", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        tk.Label(head, text="最近 14 天进了多少条、涨在哪、谁在动", fg=MUTED, bg=BG,
                 font=(FONT, 9)).pack(side="left", padx=(12, 0))

        self.kpi_box = tk.Frame(page, bg=BG)
        self.kpi_box.pack(fill="x", padx=PAD, pady=(16, 0))

        self.chart = tk.Canvas(page, bg=SURFACE, height=210, highlightthickness=1,
                               highlightbackground=HAIR, bd=0)
        self.chart.pack(fill="x", padx=PAD, pady=(16, 0))
        self.chart.bind("<Configure>", lambda _e: self._render_overview())

        self.rank_box = tk.Frame(page, bg=BG)
        self.rank_box.pack(fill="both", expand=True, padx=PAD, pady=(16, 12))

    def _render_overview(self) -> None:
        for w in self.kpi_box.winfo_children():
            w.destroy()
        for w in self.rank_box.winfo_children():
            w.destroy()

        total = int(self.conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"])
        today = int(self.conn.execute(
            "SELECT COUNT(*) n FROM items WHERE first_seen_at >= ?",
            (int((time.time() - 86400) * 1000),)).fetchone()["n"])
        revised = int(self.conn.execute("SELECT COUNT(*) n FROM items WHERE version > 1").fetchone()["n"])
        values = [("总条目", total, ACCENT), ("今日新增", today, GREEN),
                  ("未读", board.unread_count(self.conn), GOLD), ("被修订", revised, WARN)]
        for i, (label, value, color) in enumerate(values):
            card = tk.Frame(self.kpi_box, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0))
            self.kpi_box.grid_columnconfigure(i, weight=1)
            inner = tk.Frame(card, bg=CARD, padx=18, pady=14)
            inner.pack(fill="both", expand=True)
            tk.Label(inner, text=str(value), fg=color, bg=CARD, font=(MONO, 24, "bold")).pack(anchor="w")
            tk.Label(inner, text=label, fg=MUTED, bg=CARD, font=(FONT, 8)).pack(anchor="w", pady=(4, 0))

        wb = admin.workbench(self.conn)
        biz = [
            ("抓取成功率", f"{wb['success_rate']:.1f}%", GREEN if wb["success_rate"] >= 95 else WARN),
            ("去重率", f"{wb['dedupe_rate']:.1f}%", ACCENT),
            ("平均耗时", f"{wb['avg_seconds']:.2f}s", FG),
            ("任务积压", str(wb["jobs"]["queued"] + wb["jobs"]["running"]),
             WARN if wb["jobs"]["queued"] else MUTED),
        ]
        for i, (label, value, color) in enumerate(biz):
            card = tk.Frame(self.kpi_box, bg=SURFACE, highlightbackground=HAIR, highlightthickness=1)
            card.grid(row=1, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0), pady=(10, 0))
            inner = tk.Frame(card, bg=SURFACE, padx=18, pady=10)
            inner.pack(fill="both", expand=True)
            tk.Label(inner, text=value, fg=color, bg=SURFACE, font=(MONO, 16, "bold")).pack(anchor="w")
            tk.Label(inner, text=label, fg=MUTED, bg=SURFACE, font=(FONT, 8)).pack(anchor="w", pady=(2, 0))

        self._draw_chart()

        tk.Label(self.rank_box, text="按来源", fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
        rows = self.conn.execute(
            "SELECT source, COUNT(*) n FROM items GROUP BY source ORDER BY n DESC").fetchall()
        top = max([int(r["n"]) for r in rows], default=1)
        for r in rows:
            line = tk.Frame(self.rank_box, bg=BG)
            line.pack(fill="x", pady=(8, 0))
            tk.Label(line, text=r["source"], fg=FG, bg=BG, font=(FONT, 9), width=10,
                     anchor="w").pack(side="left")
            bar = tk.Canvas(line, bg=BG, height=10, highlightthickness=0, bd=0)
            bar.pack(side="left", fill="x", expand=True, padx=(8, 10))
            bar.bind("<Configure>", lambda e, n=int(r["n"]), t=top, c=bar: (
                c.delete("all"),
                rounded(c, 0, 1, max(3, (e.width - 4) * n / t), 9, 4, ACCENT_SOFT)))
            tk.Label(line, text=str(int(r["n"])), fg=MUTED, bg=BG, font=(MONO, 9)).pack(side="right")

    def _draw_chart(self) -> None:
        c = self.chart
        c.delete("all")
        width = max(c.winfo_width(), 480)
        height = 210
        days = 14
        base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        buckets = []
        for i in range(days - 1, -1, -1):
            start = base.timestamp() - i * 86400
            end = start + 86400
            n = int(self.conn.execute(
                "SELECT COUNT(*) n FROM items WHERE first_seen_at >= ? AND first_seen_at < ?",
                (int(start * 1000), int(end * 1000))).fetchone()["n"])
            buckets.append((datetime.fromtimestamp(start).strftime("%m-%d"), n))

        c.create_text(18, 16, text="每天新增", fill=MUTED, font=(FONT, 9), anchor="w")
        top = max([n for _, n in buckets], default=1) or 1
        left, right, bottom = 18, width - 18, height - 30
        area_h = bottom - 40
        gap = (right - left) / days
        bw = gap * 0.52
        for i, (label, n) in enumerate(buckets):
            x = left + gap * i + (gap - bw) / 2
            h = max(2, area_h * n / top)
            rounded(c, x, bottom - h, x + bw, bottom, 3, ACCENT if n else HAIR)
            c.create_text(x + bw / 2, bottom - h - 9, text=str(n), fill=MUTED, font=(MONO, 8))
            if i % 2 == 0 or i == days - 1:
                c.create_text(x + bw / 2, bottom + 12, text=label, fill=DIM, font=(FONT, 7))

    # ================= 数据源 =================
    def _build_sources(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["sources"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="数据源", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        tk.Label(head, text="每个源单独限速、单独退避；连续失败三次会标红", fg=MUTED, bg=BG,
                 font=(FONT, 9)).pack(side="left", padx=(12, 0))
        self.src_list = tk.Frame(page, bg=BG)
        self.src_list.pack(fill="both", expand=True, padx=PAD, pady=(16, 12))

    def _render_sources(self) -> None:
        for w in self.src_list.winfo_children():
            w.destroy()
        for st in self.crawler.snapshot().get("sources", []):
            name = st.get("name", "?")
            card = tk.Frame(self.src_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=12)
            inner.pack(fill="x")

            ok = st.get("status") == "ok"
            tk.Label(inner, text="●", fg=GREEN if ok else WARN, bg=CARD, font=(FONT, 9)).pack(side="left")
            tk.Label(inner, text=name, fg=FG, bg=CARD, font=(FONT, 10, "bold"), width=9,
                     anchor="w").pack(side="left", padx=(8, 0))
            tk.Label(inner, text=st.get("kind", ""), fg=MUTED, bg=CARD, font=(FONT, 8),
                     width=12, anchor="w").pack(side="left")
            alive = not st.get("paused")
            tk.Label(inner, text="启用" if alive else "已停", fg=MUTED if alive else DIM, bg=CARD,
                     font=(FONT, 8), width=6, anchor="w").pack(side="left")
            tk.Label(inner, text=f"每 {int(st.get('interval_seconds', 0)) // 60} 分钟", fg=MUTED,
                     bg=CARD, font=(FONT, 8), width=11, anchor="w").pack(side="left")
            tk.Label(inner, text=f"上次 {human_day(st.get('last_ok_at') or 0)}", fg=MUTED,
                     bg=CARD, font=(FONT, 8)).pack(side="left")

            Pill(inner, "抓一轮", lambda n=name: self._run_one(n), height=28, padx=12,
                 bg=CARD).pack(side="right", padx=(6, 0))
            Pill(inner, "停用" if alive else "启用", lambda n=name, a=alive: self._toggle_source(n, a),
                 height=28, padx=12, bg=CARD).pack(side="right")

    def _run_one(self, name: str) -> None:
        if self.crawler.snapshot() and self.crawler.sources.get(name) is None:
            return
        threading.Thread(target=lambda: (self.crawler.run_source(name, force=True),
                                         self.events.put(("crawled", None))), daemon=True).start()

    def _toggle_source(self, name: str, alive: bool) -> None:
        self.crawler.set_source_enabled(name, not alive)
        audit.log(self.conn, self._who(), "source.toggle", name, "停用" if alive else "启用")
        self._render_sources()

    # ================= 用户 =================
    def _build_users(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["users"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="用户", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        self.users_note = tk.Label(head, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.users_note.pack(side="left", padx=(12, 0))

        add = tk.Frame(page, bg=BG)
        add.pack(fill="x", padx=PAD, pady=(16, 0))
        self.new_user = Entry(add, "新用户名", width=16)
        self.new_user.pack(side="left", ipady=8)
        self.new_pw = Entry(add, "口令（至少 6 位）", width=18, show="●")
        self.new_pw.pack(side="left", padx=(10, 0), ipady=8)
        Pill(add, "加用户", self._add_user, accent=True, height=36, padx=16).pack(side="left", padx=(10, 0))

        self.user_list = tk.Frame(page, bg=BG)
        self.user_list.pack(fill="both", expand=True, padx=PAD, pady=(16, 12))

    def _render_users(self) -> None:
        for w in self.user_list.winfo_children():
            w.destroy()
        admin = (self.user or {}).get("role") == "admin"
        self.users_note.configure(text="" if admin else "只有管理员能改用户")
        for u in auth.list_users(self.conn):
            card = tk.Frame(self.user_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=10)
            inner.pack(fill="x")
            Avatar(inner, u["username"], size=32, bg=CARD).pack(side="left")
            tk.Label(inner, text=u["username"], fg=FG, bg=CARD, font=(FONT, 10, "bold"), width=14,
                     anchor="w").pack(side="left", padx=(10, 0))
            tk.Label(inner, text=ROLE_TEXT.get(u["role"], u["role"]), fg=ACCENT, bg=CARD,
                     font=(FONT, 8), width=8, anchor="w").pack(side="left")
            on = bool(u.get("enabled"))
            tk.Label(inner, text="启用" if on else "已停用", fg=MUTED if on else RED, bg=CARD,
                     font=(FONT, 8), width=7, anchor="w").pack(side="left")
            tk.Label(inner, text=f"上次登录 {human_day(u.get('last_login_at') or 0)}", fg=MUTED,
                     bg=CARD, font=(FONT, 8)).pack(side="left")
            if admin:
                Pill(inner, "停用" if on else "启用",
                     lambda n=u["username"], e=on: self._toggle_user(n, e), height=28, padx=10,
                     bg=CARD).pack(side="right", padx=(6, 0))
                Pill(inner, "改角色", lambda n=u["username"], r=u["role"]: self._cycle_role(n, r),
                     height=28, padx=10, bg=CARD).pack(side="right", padx=(6, 0))

    def _add_user(self) -> None:
        if (self.user or {}).get("role") != "admin":
            return
        name, pw = self.new_user.value(), self.new_pw.value()
        if not name or len(pw) < 6:
            self.users_note.configure(text="用户名不能空，口令至少 6 位")
            return
        try:
            auth.create_user(self.conn, name, pw, "viewer")
        except Exception as exc:
            self.users_note.configure(text=f"没建成：{exc}")
            return
        audit.log(self.conn, self._who(), "user.create", name, "桌面端")
        self.new_user.clear()
        self.new_pw.clear()
        self._render_users()

    def _toggle_user(self, name: str, enabled: bool) -> None:
        try:
            auth.set_enabled(self.conn, name, not enabled)
        except Exception as exc:
            self.users_note.configure(text=str(exc))
            return
        audit.log(self.conn, self._who(), "user.enable" if not enabled else "user.disable", name, "")
        self._render_users()

    def _cycle_role(self, name: str, role: str) -> None:
        order = ["viewer", "analyst", "admin"]
        nxt = order[(order.index(role) + 1) % len(order)] if role in order else "viewer"
        try:
            auth.set_role(self.conn, name, nxt)
        except Exception as exc:
            self.users_note.configure(text=str(exc))
            return
        audit.log(self.conn, self._who(), "user.role", name, nxt)
        self._render_users()

    def _who(self) -> str:
        return (self.user or {}).get("username", "system")

    # ================= 条目管理 =================
    def _tree(self, parent, columns, widths, height=18):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("SW.Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                        borderwidth=0, rowheight=30, font=(FONT, 9))
        style.configure("SW.Treeview.Heading", background=SURFACE, foreground=MUTED,
                        borderwidth=0, font=(FONT, 8))
        style.map("SW.Treeview", background=[("selected", "#1b2a3d")], foreground=[("selected", FG)])
        tree = ttk.Treeview(parent, columns=[c for c, _ in columns], show="headings",
                            style="SW.Treeview", height=height, selectmode="extended")
        for (key, title), w in zip(columns, widths):
            tree.heading(key, text=title)
            tree.column(key, width=w, anchor="w", stretch=(key == "title"))
        tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        bar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=bar.set)
        return tree

    def _build_items(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["items"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="条目管理", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        self.items_note = tk.Label(head, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.items_note.pack(side="left", padx=(12, 0))

        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", padx=PAD, pady=(14, 0))
        self.items_q = Entry(bar, "标题 / 公司 / 代码", width=26)
        self.items_q.pack(side="left", ipady=6)
        self.items_q.bind("<Return>", lambda _e: self._render_items(reset=True))
        for label, cmd in (("搜", lambda: self._render_items(reset=True)),
                           ("编辑", self._edit_item),
                           ("置顶", self._toggle_pin),
                           ("下架", lambda: self._set_state("offline")),
                           ("上架", lambda: self._set_state("online")),
                           ("删除", self._delete_items),
                           ("导出", self._export_items)):
            Pill(bar, label, cmd, height=30, padx=12, bg=BG).pack(side="left", padx=(8, 0))
        self.items_page_lbl = tk.Label(bar, text="", fg=MUTED, bg=BG, font=(MONO, 9))
        self.items_page_lbl.pack(side="right")

        wrap = tk.Frame(page, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=(12, 0))
        self.items_tree = self._tree(wrap, [("time", "时间"), ("source", "来源"), ("state", "状态"),
                                           ("title", "标题")], [140, 90, 70, 600])

        pager = tk.Frame(page, bg=BG)
        pager.pack(fill="x", padx=PAD, pady=(10, 12))
        Pill(pager, "上一页", lambda: self._page_items(-1), height=30, padx=12, bg=BG).pack(side="left")
        Pill(pager, "下一页", lambda: self._page_items(1), height=30, padx=12, bg=BG).pack(side="left", padx=(8, 0))
        self.items_offset = 0
        self.items_total = 0
        self.items_limit = 50

    def _render_items(self, *, reset: bool = False) -> None:
        if reset:
            self.items_offset = 0
        rows, total = admin.item_page(
            self.conn, q=self.items_q.value(), offset=self.items_offset, limit=self.items_limit)
        self.items_total = total
        self.items_tree.delete(*self.items_tree.get_children())
        for r in rows:
            self.items_tree.insert("", "end", iid=str(r["id"]), values=(
                human_day(r["published_at"]), r["source"],
                admin.ITEM_STATE_TEXT.get(r.get("state") or "online", ""),
                r["title"][:90]))
        shown = f"{len(rows)} / {total}"
        self.items_page_lbl.configure(text=shown)
        self.items_note.configure(
            text=f"第 {self.items_offset // self.items_limit + 1} 页 · 共 {total} 条")

    def _page_items(self, step: int) -> None:
        nxt = self.items_offset + step * self.items_limit
        if 0 <= nxt < max(self.items_total, 1):
            self.items_offset = nxt
            self._render_items()

    def _selected_items(self) -> list[int]:
        return [int(i) for i in self.items_tree.selection()]

    def _set_state(self, state: str) -> None:
        ids = self._selected_items()
        if not ids:
            self.items_note.configure(text="先选条目")
            return
        admin.set_items_state(self.conn, ids, state)
        audit.log(self.conn, self._who(), f"item.{state}", ",".join(map(str, ids[:5])), f"{len(ids)} 条")
        self._render_items()

    def _toggle_pin(self) -> None:
        ids = self._selected_items()
        if not ids:
            self.items_note.configure(text="先选条目")
            return
        for item_id in ids:
            admin.toggle_pin(self.conn, item_id)
        audit.log(self.conn, self._who(), "item.pin", ",".join(map(str, ids[:5])), f"{len(ids)} 条")
        self._render_items()

    def _delete_items(self) -> None:
        ids = self._selected_items()
        if not ids:
            self.items_note.configure(text="先选条目")
            return
        admin.delete_items(self.conn, ids)
        audit.log(self.conn, self._who(), "item.delete", ",".join(map(str, ids[:5])), f"{len(ids)} 条")
        self._render_items(reset=True)

    def _export_items(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="条目.csv")
        if not path:
            return
        from pathlib import Path

        Path(path).write_text(admin.export_items_csv(self.conn, q=self.items_q.value()),
                              encoding="utf-8")
        self.items_note.configure(text=f"已导出 {path}")

    def _edit_item(self) -> None:
        ids = self._selected_items()
        if not ids:
            self.items_note.configure(text="先选一条")
            return
        if len(ids) > 1:
            self.items_note.configure(text="编辑一次只能改一条")
            return
        row = None
        with db.guard(self.conn):
            row = self.conn.execute("SELECT * FROM items WHERE id = ?", (ids[0],)).fetchone()
        if row is None:
            return
        win = tk.Toplevel(self.root)
        win.title("编辑条目")
        win.configure(bg=SURFACE)
        win.geometry("560x420")
        win.transient(self.root)
        body = tk.Frame(win, bg=SURFACE, padx=18, pady=16)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="标题", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w")
        title = tk.Entry(body, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                         font=(FONT, 10))
        title.pack(fill="x", ipady=5, pady=(4, 12))
        title.insert(0, row["title"] or "")

        tk.Label(body, text="摘要", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w")
        summary = tk.Text(body, height=7, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                          font=(FONT, 9), wrap="word")
        summary.pack(fill="both", expand=True, pady=(4, 12))
        summary.insert("1.0", row["summary"] or "")

        cats = [c["name"] for c in admin.list_categories(self.conn) if c["enabled"]]
        current = admin.item_categories(self.conn, ids[0])
        tk.Label(body, text="分类", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w")
        cat = ttk.Combobox(body, values=[""] + cats, state="readonly")
        cat.pack(fill="x", pady=(4, 12))
        cat.set(current[0] if current else "")

        msg = tk.Label(body, text="", fg=RED, bg=SURFACE, font=(FONT, 9))
        msg.pack(anchor="w")

        def save() -> None:
            try:
                admin.update_item(self.conn, ids[0], title=title.get(),
                                  summary=summary.get("1.0", "end").strip(),
                                  category=cat.get() or None)
            except Exception as exc:
                msg.configure(text=str(exc))
                return
            audit.log(self.conn, self._who(), "item.edit", str(ids[0]), "")
            win.destroy()
            self._render_items()
            self.items_note.configure(text="已保存")

        btns = tk.Frame(body, bg=SURFACE)
        btns.pack(fill="x", pady=(6, 0))
        Pill(btns, "保存", save, accent=True, height=30, padx=16, bg=SURFACE).pack(side="right")
        Pill(btns, "取消", win.destroy, height=30, padx=16, bg=SURFACE).pack(side="right", padx=(0, 8))

    # ================= 分类管理 =================
    def _build_categories(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["categories"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="分类管理", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", padx=PAD, pady=(14, 0))
        self.cat_name = Entry(bar, "新分类名", width=18)
        self.cat_name.pack(side="left", ipady=6)
        Pill(bar, "新增", self._add_category, accent=True, height=30, padx=14, bg=BG).pack(side="left", padx=(8, 0))
        self.cat_list = tk.Frame(page, bg=BG)
        self.cat_list.pack(fill="both", expand=True, padx=PAD, pady=(14, 12))

    def _render_categories(self) -> None:
        for w in self.cat_list.winfo_children():
            w.destroy()
        for c in admin.list_categories(self.conn):
            card = tk.Frame(self.cat_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=10)
            inner.pack(fill="x")
            tk.Label(inner, text=c["name"], fg=FG, bg=CARD, font=(FONT, 10, "bold"), width=12,
                     anchor="w").pack(side="left")
            on = bool(c["enabled"])
            tk.Label(inner, text="启用" if on else "已停", fg=MUTED if on else RED, bg=CARD,
                     font=(FONT, 8), width=6, anchor="w").pack(side="left")
            tk.Label(inner, text=f"{c['n']} 条", fg=MUTED, bg=CARD, font=(MONO, 9), width=8,
                     anchor="w").pack(side="left")
            Pill(inner, "停用" if on else "启用",
                 lambda cid=c["id"], e=on: (admin.set_category_enabled(self.conn, cid, not e),
                                            audit.log(self.conn, self._who(), "category.toggle",
                                                      str(cid), ""),
                                            self._render_categories()),
                 height=28, padx=10, bg=CARD).pack(side="right", padx=(6, 0))
            Pill(inner, "删除",
                 lambda cid=c["id"]: (admin.delete_category(self.conn, cid), self._render_categories()),
                 height=28, padx=10, bg=CARD).pack(side="right", padx=(6, 0))

    def _add_category(self) -> None:
        try:
            admin.add_category(self.conn, self.cat_name.value())
        except Exception as exc:
            self.cat_name.delete(0, "end")
            return
        audit.log(self.conn, self._who(), "category.add", self.cat_name.value(), "")
        self.cat_name.delete(0, "end")
        self._render_categories()

    # ================= 抓取任务 =================
    def _build_jobs(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["jobs"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="抓取任务", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        self.jobs_note = tk.Label(head, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.jobs_note.pack(side="left", padx=(12, 0))

        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", padx=PAD, pady=(14, 0))
        Pill(bar, "全部抓一轮", lambda: self._new_job("all", ""), accent=True, height=30,
             padx=14, bg=BG).pack(side="left")
        for st in self.crawler.snapshot().get("sources", []):
            Pill(bar, st.get("name", "?"), lambda n=st.get("name", ""): self._new_job("source", n),
                 height=30, padx=12, bg=BG).pack(side="left", padx=(8, 0))
        Pill(bar, "重试", self._retry_job, height=30, padx=12, bg=BG).pack(side="right")
        Pill(bar, "取消", self._cancel_job, height=30, padx=12, bg=BG).pack(side="right", padx=(0, 8))

        wrap = tk.Frame(page, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=PAD, pady=(12, 12))
        self.jobs_tree = self._tree(wrap, [("id", "#"), ("kind", "类型"), ("target", "对象"),
                                          ("status", "状态"), ("by", "发起人"),
                                          ("reason", "原因"), ("result", "结果")],
                                    [50, 70, 110, 80, 90, 110, 420])
        self.jobs_tree.bind("<Double-1>", lambda _e: self._job_detail())

    def _render_jobs(self) -> None:
        counts = admin.job_counts(self.conn)
        self.jobs_note.configure(text=" · ".join(f"{admin.STATE_TEXT.get(k, k)} {v}"
                                                 for k, v in counts.items()))
        self.jobs_tree.delete(*self.jobs_tree.get_children())
        for j in admin.job_list(self.conn):
            result = j["error"] or j["result"] or ""
            reason = admin.JOB_REASON_TEXT.get(admin.job_failure_reason(j["error"] or ""), "")
            self.jobs_tree.insert("", "end", iid=str(j["id"]), values=(
                j["id"], j["kind"], j["target"] or "全部",
                admin.STATE_TEXT.get(j["status"], j["status"]),
                j["created_by"], reason, result[:110]))

    def _job_detail(self) -> None:
        sel = self.jobs_tree.selection()
        if not sel:
            return
        job = admin.job_detail(self.conn, int(sel[0]))
        if job is None:
            return
        win = tk.Toplevel(self.root)
        win.title(f"任务 #{job['id']}")
        win.configure(bg=SURFACE)
        win.geometry("620x460")
        win.transient(self.root)
        body = tk.Frame(win, bg=SURFACE, padx=18, pady=16)
        body.pack(fill="both", expand=True)

        def row(label: str, value: str) -> None:
            line = tk.Frame(body, bg=SURFACE)
            line.pack(fill="x", pady=(0, 8))
            tk.Label(line, text=label, fg=MUTED, bg=SURFACE, font=(FONT, 9), width=8,
                     anchor="w").pack(side="left")
            tk.Label(line, text=value or "—", fg=FG, bg=SURFACE, font=(FONT, 9),
                     anchor="w", justify="left", wraplength=470).pack(side="left")

        row("编号", f"#{job['id']}")
        row("类型", "全部抓一轮" if job["kind"] == "all" else f"单源：{job['target']}")
        row("状态", admin.STATE_TEXT.get(job["status"], job["status"]))
        row("发起人", job["created_by"])
        row("创建", human_time(job["created_at"]))
        row("开始", human_time(job["started_at"]) if job["started_at"] else "")
        row("结束", human_time(job["finished_at"]) if job["finished_at"] else "")
        row("耗时", f"{(job['finished_at'] - job['started_at']) / 1000:.1f} 秒"
            if job["started_at"] and job["finished_at"] else "")
        reason = admin.job_failure_reason(job["error"] or "")
        if reason:
            row("失败原因", admin.JOB_REASON_TEXT.get(reason, reason))
        tk.Label(body, text="结果", fg=MUTED, bg=SURFACE, font=(FONT, 9)).pack(anchor="w", pady=(6, 4))
        text = tk.Text(body, height=8, bg=CARD, fg=FG, relief="flat", font=(MONO, 9), wrap="word")
        text.pack(fill="both", expand=True)
        text.insert("1.0", job["error"] or job["result"] or "（还没结果）")
        text.configure(state="disabled")
        Pill(body, "关闭", win.destroy, height=30, padx=16, bg=SURFACE).pack(anchor="e", pady=(10, 0))

    def _new_job(self, kind: str, target: str) -> None:
        jid = admin.enqueue_job(self.conn, kind, target, self._who())
        audit.log(self.conn, self._who(), "job.enqueue", str(jid), f"{kind}:{target}")
        self._render_jobs()

    def _retry_job(self) -> None:
        for iid in self.jobs_tree.selection():
            admin.retry_job(self.conn, int(iid))
        self._render_jobs()

    def _cancel_job(self) -> None:
        for iid in self.jobs_tree.selection():
            admin.cancel_job(self.conn, int(iid))
        self._render_jobs()

    def _job_worker(self) -> None:
        while getattr(self, "_alive", True):
            try:
                job = admin.claim_job(self.conn)
            except Exception:
                job = None
            if not job:
                time.sleep(1.2)
                continue
            try:
                if job["kind"] == "all":
                    names = [s.get("name") for s in self.crawler.snapshot().get("sources", [])]
                else:
                    names = [job["target"]]
                parts = []
                for name in names:
                    if not name:
                        continue
                    res = self.crawler.run_source(name, force=True)
                    parts.append(f"{name}:{'ok' if res.ok else 'fail'} 抓到 {res.fetched} 新增 {res.inserted}")
                admin.finish_job(self.conn, job["id"], ok=True, result="; ".join(parts))
            except Exception as exc:
                admin.finish_job(self.conn, job["id"], ok=False, error=f"{type(exc).__name__}: {exc}")
            self.events.put(("job", None))

    # ================= 报表 =================
    def _build_reports(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["reports"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="报表", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")
        self.report_days = 7
        self.report_chips = {}
        for d in (1, 7, 30):
            p = Pill(head, f"近 {d} 天", lambda dd=d: self._set_report_days(dd),
                     selected=(d == 7), height=30, padx=12, bg=BG)
            p.pack(side="left", padx=(10, 0))
            self.report_chips[d] = p
        self.report_note = tk.Label(head, text="", fg=MUTED, bg=BG, font=(FONT, 9))
        self.report_note.pack(side="left", padx=(12, 0))
        Pill(head, "导出", self._export_report, height=30, padx=12, bg=BG).pack(side="right")

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=PAD, pady=(12, 12))
        left = tk.Frame(body, bg=CARD)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.rep_src = self._tree(left, [("name", "来源"), ("n", "条数"), ("rev", "修订")],
                                  [140, 90, 90], height=8)
        right = tk.Frame(body, bg=CARD)
        right.pack(side="left", fill="both", expand=True)
        self.rep_top = self._tree(right, [("code", "代码"), ("name", "公司"), ("n", "条数")],
                                  [90, 180, 70], height=8)
        self.rep_cat = None

    def _set_report_days(self, days: int) -> None:
        self.report_days = days
        for d, p in self.report_chips.items():
            p.set_selected(d == days)
        self._render_reports()

    def _render_reports(self) -> None:
        data = admin.report(self.conn, self.report_days)
        self.rep_src.delete(*self.rep_src.get_children())
        for r in data["by_source"]:
            self.rep_src.insert("", "end", values=(r["source"], r["n"], r["revised"] or 0))
        self.rep_top.delete(*self.rep_top.get_children())
        for r in data["top_company"]:
            self.rep_top.insert("", "end", values=(r["sec_code"], r["sec_name"], r["n"]))
        self.report_note.configure(
            text=f"近 {data['days']} 天 · 总 {data['total']} 条 · 下架 {data['offline']} · 置顶 {data['pinned']}")

    def _export_report(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="报表.csv")
        if not path:
            return
        from pathlib import Path

        Path(path).write_text(admin.export_items_csv(self.conn), encoding="utf-8")
        self.report_note.configure(text=f"已导出 {path}")

    # ================= 设置 =================
    def _build_settings(self) -> None:
        page = tk.Frame(self.main, bg=BG)
        self.pages["settings"] = page
        head = tk.Frame(page, bg=BG)
        head.pack(fill="x", padx=PAD, pady=(18, 0))
        tk.Label(head, text="设置", fg=FG, bg=BG, font=(FONT, 15, "bold")).pack(side="left")

        # 设置项比窗口高，必须能滚：Canvas + 内部 Frame，滚轮只在指针进来时生效。
        outer = tk.Frame(page, bg=BG)
        outer.pack(fill="both", expand=True, padx=PAD, pady=(14, 12))
        self.set_canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=self.set_canvas.yview)
        self.set_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.set_canvas.pack(side="left", fill="both", expand=True)
        self.set_list = tk.Frame(self.set_canvas, bg=BG)
        self._set_window = self.set_canvas.create_window((0, 0), window=self.set_list, anchor="nw")
        self.set_list.bind(
            "<Configure>",
            lambda _e: self.set_canvas.configure(scrollregion=self.set_canvas.bbox("all")))
        self.set_canvas.bind(
            "<Configure>",
            lambda e: self.set_canvas.itemconfigure(self._set_window, width=e.width))
        # 滚轮绑在 root 上再按指针位置过滤：绑 Enter/Leave 不可靠——指针进到内嵌 Frame 上
        # 时 canvas 会收到 Leave，滚轮就失效了。
        self.root.bind_all("<MouseWheel>", self._on_settings_wheel)

    def _on_settings_wheel(self, event) -> None:
        if getattr(self, "page", "") != "settings":
            return
        _x, y = self.root.winfo_pointerxy()
        top = self.set_canvas.winfo_rooty()
        if not (top <= y <= top + self.set_canvas.winfo_height()):
            return
        self.set_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _render_settings(self) -> None:
        for w in self.set_list.winfo_children():
            w.destroy()
        tk.Label(self.set_list, text="每个源的抓取间隔（分钟）", fg=MUTED, bg=BG,
                 font=(FONT, 9)).pack(anchor="w")
        for st in self.crawler.snapshot().get("sources", []):
            name = st.get("name", "?")
            card = tk.Frame(self.set_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
            card.pack(fill="x", pady=(8, 0))
            inner = tk.Frame(card, bg=CARD, padx=16, pady=10)
            inner.pack(fill="x")
            tk.Label(inner, text=name, fg=FG, bg=CARD, font=(FONT, 10, "bold"), width=10,
                     anchor="w").pack(side="left")
            box = Entry(inner, str(int(st.get("interval_seconds", 300)) // 60), width=6)
            box.pack(side="left", ipady=5)
            tk.Label(inner, text="分钟", fg=MUTED, bg=CARD, font=(FONT, 8)).pack(side="left", padx=(6, 0))
            Pill(inner, "保存", lambda n=name, b=box: self._save_interval(n, b),
                 height=28, padx=12, bg=CARD).pack(side="left", padx=(10, 0))

        tk.Label(self.set_list, text="通知", fg=MUTED, bg=BG, font=(FONT, 9)).pack(anchor="w", pady=(18, 0))
        notify_card = tk.Frame(self.set_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
        notify_card.pack(fill="x", pady=(8, 0))
        nin = tk.Frame(notify_card, bg=CARD, padx=16, pady=12)
        nin.pack(fill="x")
        self.notify_vars = {}
        for key, label, default in (("notify_toast", "新条目录屏提示", "1"),
                                    ("notify_sound", "提示音", "1"),
                                    ("notify_rules_only", "只提醒命中订阅词的条目", "0"),
                                    ("notify_pause", "免打扰（夜间 23:00–07:00 静音）", "0")):
            var = tk.IntVar(value=1 if admin.get_setting(self.conn, key, default) == "1" else 0)
            self.notify_vars[key] = var
            cb = tk.Checkbutton(nin, text=label, variable=var, bg=CARD, fg=FG,
                                activebackground=CARD, activeforeground=FG, selectcolor=CARD,
                                font=(FONT, 9), anchor="w", bd=0, highlightthickness=0,
                                command=lambda k=key: self._save_notify(k))
            cb.pack(anchor="w", pady=2)

        quiet = tk.Frame(nin, bg=CARD)
        quiet.pack(anchor="w", pady=(4, 0))
        tk.Label(quiet, text="免打扰时段：从", fg=MUTED, bg=CARD, font=(FONT, 9)).pack(side="left")
        self.quiet_start = Entry(quiet, "23", width=4)
        self.quiet_start.pack(side="left", ipady=3, padx=6)
        self.quiet_start.insert(0, admin.get_setting(self.conn, "notify.quiet_start", "23"))
        tk.Label(quiet, text="点到", fg=MUTED, bg=CARD, font=(FONT, 9)).pack(side="left")
        self.quiet_end = Entry(quiet, "7", width=4)
        self.quiet_end.pack(side="left", ipady=3, padx=6)
        self.quiet_end.insert(0, admin.get_setting(self.conn, "notify.quiet_end", "7"))
        tk.Label(quiet, text="点（整点小时，可跨午夜）", fg=MUTED, bg=CARD, font=(FONT, 9)).pack(side="left")
        Pill(quiet, "保存时段", lambda: self._save_quiet(), height=26, padx=10, bg=CARD).pack(side="left", padx=(8, 0))

        tk.Label(self.set_list, text="推送（邮件）", fg=MUTED, bg=BG, font=(FONT, 9)).pack(anchor="w", pady=(18, 0))
        push = tk.Frame(self.set_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
        push.pack(fill="x", pady=(8, 0))
        pin = tk.Frame(push, bg=CARD, padx=16, pady=12)
        pin.pack(fill="x")
        cfg = notify.config(self.conn)
        ready = notify.is_ready(self.conn)
        self.push_state = tk.Label(
            pin, text=("已配置 · 发到 " + cfg["to"]) if ready else "未配置：填好下面三项就能收邮件",
            fg=GREEN if ready else MUTED, bg=CARD, font=(FONT, 9), anchor="w")
        self.push_state.pack(fill="x", pady=(0, 8))

        fields = tk.Frame(pin, bg=CARD)
        fields.pack(fill="x")
        self.push_user = Entry(fields, "发件邮箱 xxx@qq.com", width=26)
        self.push_user.pack(side="left", ipady=5)
        self.push_to = Entry(fields, "收件邮箱（不填=发给自己）", width=26)
        self.push_to.pack(side="left", ipady=5, padx=(8, 0))
        self.push_pwd = Entry(fields, "授权码（不是登录密码）", width=24, show="*")
        self.push_pwd.pack(side="left", ipady=5, padx=(8, 0))

        line2 = tk.Frame(pin, bg=CARD)
        line2.pack(fill="x", pady=(10, 0))
        Pill(line2, "保存并发测试邮件", self._save_push, accent=True, height=30, padx=14,
             bg=CARD).pack(side="left")
        self.push_note = tk.Label(line2, text="", fg=MUTED, bg=CARD, font=(FONT, 8))
        self.push_note.pack(side="left", padx=(10, 0))

        tk.Label(self.set_list, text="其它", fg=MUTED, bg=BG, font=(FONT, 9)).pack(anchor="w", pady=(18, 0))
        row = tk.Frame(self.set_list, bg=CARD, highlightbackground=HAIR, highlightthickness=1)
        row.pack(fill="x", pady=(8, 0))
        inside = tk.Frame(row, bg=CARD, padx=16, pady=12)
        inside.pack(fill="x")
        db_path = SETTINGS.db_path
        tk.Label(inside, text=f"数据库：{db_path}", fg=MUTED, bg=CARD, font=(MONO, 8)).pack(anchor="w")
        Pill(inside, "备份一份", self._backup_db, height=28, padx=12, bg=CARD).pack(anchor="w", pady=(10, 0))

    def _save_push(self) -> None:
        user = self.push_user.value().strip()
        to = self.push_to.value().strip() or user
        pwd = self.push_pwd.value().strip()
        if not user:
            self.push_note.configure(text="先填发件邮箱", fg=RED)
            return
        notify.save(self.conn, user=user, to=to, password=pwd or None, enabled=True)
        audit.log(self.conn, self._who(), "notify.save", user, "配置邮件推送")
        self.push_pwd.clear()
        self.push_note.configure(text="已保存，正在发测试邮件…", fg=MUTED)
        self.push_state.configure(text="已配置（正在验证）", fg=MUTED)

        def work() -> None:
            try:
                got = notify.test_mail(self.conn)
                msg, color = f"测试邮件已发到 {got}", GREEN
            except Exception as exc:
                msg, color = f"发送失败：{type(exc).__name__}: {exc}", RED
            self.root.after(0, lambda: (self.push_note.configure(text=msg, fg=color),
                                        self._render_settings()))

        threading.Thread(target=work, daemon=True).start()

    def _save_notify(self, key: str) -> None:
        var = self.notify_vars.get(key)
        if var is None:
            return
        admin.set_setting(self.conn, key, "1" if var.get() else "0")
        audit.log(self.conn, self._who(), "setting.notify", key, "1" if var.get() else "0")

    def _save_quiet(self) -> None:
        try:
            start = max(0, min(23, int(self.quiet_start.value() or "23")))
            end = max(0, min(23, int(self.quiet_end.value() or "7")))
        except ValueError:
            self.push_note.configure(text="时段要填 0–23 的整数", fg=RED)
            return
        admin.set_setting(self.conn, "notify.quiet_start", str(start))
        admin.set_setting(self.conn, "notify.quiet_end", str(end))
        audit.log(self.conn, self._who(), "setting.quiet", f"{start}-{end}", "")
        self.push_note.configure(text=f"免打扰时段改成 {start:02d}:00–{end:02d}:00", fg=GREEN)

    def _save_interval(self, name: str, box: Entry) -> None:
        try:
            minutes = max(1, int(float(box.value())))
        except Exception:
            return
        self.crawler.apply_patch(name, interval_seconds=minutes * 60)
        audit.log(self.conn, self._who(), "source.interval", name, f"{minutes} 分钟")
        self._render_settings()

    def _backup_db(self) -> None:
        import shutil

        target = SETTINGS.db_path.parent / ("backup-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
        self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(SETTINGS.db_path, target)
        audit.log(self.conn, self._who(), "db.backup", str(target.name), "")
        self.set_list.winfo_children()[-1].winfo_children()[0].winfo_children()[-1].configure(
            text=f"已备份到 {target.name}")

    # ================= 数据 / 绘制 =================
    def _on_query(self) -> None:
        self.query = "" if getattr(self, "_search_ph", True) else self.search.get().strip()
        self._refresh()

    def _set_category(self, name: str) -> None:
        self.category = name
        for key, pill in self.chips.items():
            pill.set_selected(key == name)
        self._refresh()

    def _toggle_unread(self) -> None:
        self.only_unread = not self.only_unread
        self.unread_pill.set_selected(self.only_unread)
        self._refresh()

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.crawler.stop()
        else:
            self.crawler.start()
        self.btn_pause.set_text("继续" if self.paused else "暂停")
        self.dot.configure(fg=WARN if self.paused else GREEN)
        self.state_lbl.configure(text="已暂停" if self.paused else "运行中")

    def _crawl_now(self) -> None:
        self.btn_crawl.set_text("抓取中…")
        audit.log(self.conn, self._who(), "crawl.manual", "全部源", "桌面端手动抓取")

        def work() -> None:
            for name in list(self.crawler.sources):
                try:
                    self.crawler.run_source(name, force=True)
                except Exception:
                    pass
            self.events.put(("crawled", None))

        threading.Thread(target=work, daemon=True).start()

    def _open(self, row: DeskRow) -> None:
        if row.url:
            webbrowser.open(row.url)
            board.mark_read(self.conn, row.id)

    def _mark_read(self, row: DeskRow) -> None:
        board.mark_read(self.conn, row.id)
        self._refresh()

    def _star(self, row: DeskRow) -> None:
        board.toggle_star(self.conn, row.id)
        self._build_panel()
        self._draw()

    def _timeline(self, row: DeskRow) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"{row.sec_name or row.sec_code} · 时间线")
        win.configure(bg=BG)
        win.geometry("720x560")
        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 0))
        tk.Label(head, text=f"{row.sec_name or ''} {row.sec_code}", fg=FG, bg=BG,
                 font=(FONT, 13, "bold")).pack(side="left")
        rows = admin.company_timeline(self.conn, row.sec_code, limit=60)
        tk.Label(head, text=f"{len(rows)} 条", fg=MUTED, bg=BG, font=(FONT, 9)).pack(side="left", padx=(10, 0))
        box = tk.Frame(win, bg=BG)
        box.pack(fill="both", expand=True, padx=18, pady=(12, 16))
        for r in rows:
            line = tk.Frame(box, bg=BG)
            line.pack(fill="x", pady=2)
            tk.Label(line, text=human_time(r["published_at"]), fg=MUTED, bg=BG,
                     font=("Consolas", 9)).pack(side="left")
            tk.Label(line, text=r["title"][:58], fg=FG, bg=BG, font=(FONT, 9),
                     anchor="w").pack(side="left", padx=(12, 0), fill="x", expand=True)
        Pill(win, "关闭", win.destroy, height=32, padx=16).pack(pady=(0, 16))

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], initialfile="signalwatch.csv")
        if not path:
            return
        import csv

        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["时间", "分类", "源", "标题", "链接", "版本"])
            for r in self.rows:
                w.writerow([human_time(r.published_at), board.category_of(r.source) or "其他",
                            r.source, r.title, r.url, r.version])
        self.status.configure(text=f"已导出 {len(self.rows)} 条 → {path}")

    def _visible_rows(self) -> list[DeskRow]:
        category = "" if self.category in ("全部", "收藏") else self.category
        return rows_from_db(self.conn, q=self.query, category=category,
                            only_unread=self.only_unread,
                            only_starred=(self.category == "收藏"), limit=self.limit)

    def _refresh(self) -> None:
        self.rows = self._visible_rows()
        self._draw()
        if hasattr(self, "count_lbl"):
            self.count_lbl.configure(text=f"{len(self.rows)} 条")
        self._update_status()

    def _draw(self) -> None:
        c = getattr(self, "canvas", None)
        if c is None:
            return
        c.delete("all")
        width = max(c.winfo_width(), 560)
        rules = [r["keyword"] for r in board.list_rules(self.conn)]

        if not self.rows:
            c.create_text(width / 2, 110, text="⌕", fill=HAIR, font=(FONT, 28))
            c.create_text(width / 2, 156, text="这里还空着", fill=MUTED, font=(FONT, 11))
            c.create_text(width / 2, 180, text="点右上角「立刻抓一轮」", fill=DIM, font=(FONT, 9))
            c.configure(scrollregion=(0, 0, width, 240))
            return

        y = 0
        for row in self.rows:
            top, bottom = y + 3, y + ROW_H - 7
            chosen = self.selected is not None and row.id == self.selected.id
            fill = CARD_HOVER if row.id == self.hover_id else CARD
            rounded(c, 0, top, width - 14, bottom, 10, fill, outline=(ACCENT_SOFT if chosen else HAIR))
            if chosen:
                c.create_rectangle(1, top + 12, 3, bottom - 12, fill=ACCENT, outline="")

            c.create_text(18, top + 15, text=human_time(row.published_at), fill=MUTED,
                          font=(MONO, 9), anchor="nw")
            c.create_text(18, top + 36, text=row.source[:8], fill=DIM, font=(FONT, 8), anchor="nw")
            self._fit_text(c, row.title, 92, top + 14, width - 180, FG, (FONT, 10))

            bits = [board.category_of(row.source) or "其他"]
            if row.version > 1:
                bits.append(f"已修订 v{row.version}")
            hit = next((w for w in rules if w and w in row.title), "")
            if hit:
                bits.append(f"命中「{hit}」")
            c.create_text(92, top + 42, text="   ·   ".join(bits), fill=MUTED,
                          font=(FONT, 8), anchor="nw")
            y += ROW_H

        c.configure(scrollregion=(0, 0, width, max(y, 1)))
        self._update_thumb(max(y, 1), c.winfo_height())

    def _fit_text(self, c, text, x, y, max_w, color, font):
        f = tkfont.Font(family=font[0], size=font[1])
        if f.measure(text) <= max_w:
            c.create_text(x, y, text=text, fill=color, font=font, anchor="nw")
            return
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(text[:mid] + "…") <= max_w:
                lo = mid
            else:
                hi = mid - 1
        c.create_text(x, y, text=text[:lo] + "…", fill=color, font=font, anchor="nw")

    def _update_thumb(self, total_h: int, view_h: int) -> None:
        self.thumb.delete("all")
        if total_h <= 0 or view_h <= 0:
            return
        first, last = self.canvas.yview()
        top = int(first * view_h) + 4
        height = max(28, int((last - first) * view_h) - 8)
        rounded(self.thumb, 1, top, 5, top + height, 2, HAIR)

    def _row_at(self, evt):
        idx = int(self.canvas.canvasy(evt.y) // ROW_H)
        return self.rows[idx] if 0 <= idx < len(self.rows) else None

    def _on_click(self, evt) -> None:
        row = self._row_at(evt)
        if row is None:
            return
        self.selected = row
        self._build_panel()
        self._draw()

    def _on_double(self, evt) -> None:
        row = self._row_at(evt)
        if row is not None:
            self._open(row)
            self._refresh()

    def _on_motion(self, evt) -> None:
        row = self._row_at(evt)
        rid = row.id if row else None
        if rid != self.hover_id:
            self.hover_id = rid
            self.canvas.configure(cursor="hand2" if rid else "")
            self._draw()

    def _on_wheel(self, evt) -> None:
        self.canvas.yview_scroll(-1 * int(evt.delta / 120), "units")
        box = self.canvas.bbox("all")
        self._update_thumb(box[3] if box else 0, self.canvas.winfo_height())

    def _drag_thumb(self, evt) -> None:
        box = self.canvas.bbox("all")
        if not box:
            return
        frac = evt.y / max(1, self.canvas.winfo_height())
        self.canvas.yview_moveto(max(0.0, min(1.0, frac - 0.05)))
        self._update_thumb(box[3], self.canvas.winfo_height())

    def _update_status(self) -> None:
        if not hasattr(self, "status"):
            return
        total = self.conn.execute("SELECT COUNT(*) n FROM items").fetchone()
        self.status.configure(
            text=f"共 {int(total['n'])} 条   ·   未读 {board.unread_count(self.conn)}   ·   "
                 f"收藏 {board.star_count(self.conn)}   ·   当前列表 {len(self.rows)} 条   ·   "
                 f"{SETTINGS.db_path}")

    # ================= 轮询 =================
    def _pump(self) -> None:
        try:
            while True:
                kind, _ = self.events.get_nowait()
                if kind == "crawled":
                    self.btn_crawl.set_text("立刻抓一轮")
                    self._refresh()
                elif kind == "job" and self.page == "jobs":
                    self._render_jobs()
        except queue.Empty:
            pass

        rows = self._visible_rows()
        ids = [r.id for r in rows]
        known = set(self.known)
        fresh = [i for i in ids if i not in known]
        if fresh and not self.paused and rows and self._notify_on(rows[0]):
            Toast(self.root, f"新条目 {len(fresh)} 条", rows[0].title[:60],
                  on_open=lambda r=rows[0]: self._open(r))
            if self._setting("notify_sound", "1") == "1":
                try:
                    import winsound

                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
        self.known = ids
        if fresh and not self.paused and rows:
            self._maybe_push(rows[:30])
        self._refresh()
        self.root.after(900, self._pump)

    def _maybe_push(self, rows: list) -> None:
        """有新条目就发摘要。发不发、发给谁、多久一次，全在 notify.pump 里定。"""
        if not rows:
            return
        payload = [{"source": r.source, "title": r.title, "url": r.url,
                    "when": human_time(r.published_at), "sec_name": r.sec_name,
                    "summary": r.summary} for r in rows]

        def work() -> None:
            try:
                sent = notify.pump(self.conn, payload)
                if sent:
                    audit.log(self.conn, "system", "notify.sent", "mail", "、".join(sent))
            except Exception as exc:
                audit.log(self.conn, "system", "notify.fail", "mail",
                          f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _setting(self, key: str, default: str) -> str:
        try:
            return admin.get_setting(self.conn, key, default)
        except Exception:
            return default

    def _notify_on(self, row) -> bool:
        if self._setting("notify_toast", "1") != "1":
            return False
        if self._setting("notify_pause", "0") == "1":
            hour = datetime.now().hour
            try:
                start = int(admin.get_setting(self.conn, "notify.quiet_start", "23"))
                end = int(admin.get_setting(self.conn, "notify.quiet_end", "7"))
            except (TypeError, ValueError):
                start, end = 23, 7
            quiet = (start <= hour or hour < end) if start > end else (start <= hour < end)
            if quiet:
                return False
        if self._setting("notify_rules_only", "0") == "1":
            return bool(getattr(row, "hits", ""))
        return True

    def _tick_status(self) -> None:
        self._update_status()
        if self.page == "me":
            self._render_me()
        elif self.page == "jobs":
            self._render_jobs()
        elif self.page == "overview":
            self._render_overview()
        self.root.after(5000, self._tick_status)

    def run(self) -> None:
        if not self.paused:
            self.crawler.start()
        self.root.mainloop()
        self.crawler.stop()


def _dpi_aware() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _dpi_aware()
    paused = "--paused" in argv
    page = "overview"
    if "--page" in argv:
        i = argv.index("--page")
        if i + 1 < len(argv):
            page = argv[i + 1]
    crawler = Crawler()
    auth.seed_admin(crawler.conn)
    app = App(crawler, paused=paused, start_page=page)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

















