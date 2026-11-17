import sys

sys.path.insert(0, "src")
from signalwatch import db, notify
from signalwatch.config import SETTINGS
from signalwatch.desktop import human_time

conn = db.connect(SETTINGS.db_path)
rows = [dict(r) for r in conn.execute(
    "SELECT source, title, url, published_at FROM items ORDER BY published_at DESC LIMIT 5")]
payload = [{"source": r["source"], "title": r["title"], "url": r["url"],
            "when": human_time(r["published_at"])} for r in rows]
subject, body = notify.compose_digest(payload)
print("subject:", subject)
print("sent to:", notify.send(conn, subject, body))
