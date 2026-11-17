import sys

sys.path.insert(0, "src")
from signalwatch.config import SETTINGS
from signalwatch.scheduler import Crawler
from signalwatch.desktop import App

app = App(Crawler(SETTINGS), paused=True)
app.root.update()
app._build_shell()
app.root.update()
app._go("settings")
app.root.update()
app._render_settings()
app.root.update()
cv = app.set_canvas
bbox = cv.bbox("all")
print("canvas", cv.winfo_height(), "content", bbox)
before = cv.yview()
cv.yview_scroll(5, "units")
app.root.update()
after = cv.yview()
print("yview before", before, "after", after, "scrollable", bbox[3] > cv.winfo_height())
app.root.destroy()
