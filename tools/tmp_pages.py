import sys, tempfile, pathlib, tkinter as tk
sys.path.insert(0, "src")
from signalwatch.config import Settings
s = Settings(home=pathlib.Path(tempfile.mkdtemp()), min_interval=0.0)
import signalwatch.desktop as d, signalwatch.config as cfg
cfg.SETTINGS = s
d.SETTINGS = s
from signalwatch.scheduler import Crawler
app = d.App(Crawler(s), paused=True)
app._enter({"username": "admin", "role": "admin"})
app.root.update_idletasks()
print("pages:", sorted(app.pages.keys()))
print("nav keys:", [k for k in getattr(app, "nav", {})] if hasattr(app, "nav") else "no nav")
app.root.destroy()
