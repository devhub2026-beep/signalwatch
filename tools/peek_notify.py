import sys
sys.path.insert(0, "src")
from signalwatch import db
from signalwatch.config import SETTINGS

c = db.connect(SETTINGS.db_path)
for r in c.execute("SELECT key, value FROM settings WHERE key LIKE 'notify%'"):
    v = r["value"]
    if "password" in r["key"]:
        v = "***len=%d" % len(v or "")
    print(r["key"], "=", v)
print("db", SETTINGS.db_path)
