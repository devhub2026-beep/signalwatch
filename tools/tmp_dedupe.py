import sys, tempfile, pathlib
sys.path.insert(0, "src")
from signalwatch import dedupe
from signalwatch.config import Settings
from signalwatch.db import connect, init_db
from signalwatch.ingest import save_records
from signalwatch.sources.base import Record

BASE = 1_700_000_000_000
tmp = tempfile.mkdtemp()
s = Settings(home=pathlib.Path(tmp), min_interval=0.0)
c = connect(s.db_path)
init_db(c)
save_records(c, "cninfo", [Record(external_id="a", title="通威股份关于2026年员工持股计划（草案）的公告", published_at=BASE, summary="", url="https://e/x")])
save_records(c, "ithome", [Record(external_id="b", title="通威股份 2026 年员工持股计划草案", published_at=BASE + 3600_000, summary="", url="https://e/y")])
print("marked", dedupe.mark_duplicates(c))
rows = c.execute("SELECT title, dup_of FROM items ORDER BY id").fetchall()
print([dict(r) for r in rows])
