import shutil, tempfile, pathlib
from signalwatch import db, dedupe
tmp = pathlib.Path(tempfile.mkdtemp()) / "x.db"
shutil.copy("data/signalwatch.db", tmp)
c = db.connect(tmp); db.init_db(c)
total = 0
while True:
    n = dedupe.mark_duplicates(c, limit=500)
    left = c.execute("SELECT COUNT(*) FROM items WHERE id > ?", (dedupe.get_cursor(c),)).fetchone()[0]
    total += n
    if left == 0:
        break
print("首次全量扫描：标出", total, "条；游标", dedupe.get_cursor(c))
SQL = ("INSERT INTO items(source,kind,external_id,fingerprint,title,summary,url,published_at,"
       "first_seen_at,last_seen_at,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)")
c.execute(SQL, ("ithome","tech","x1","fp-dup-a","关于召开2026年第三次临时股东大会的通知","s","u",1794900000000,0,0,"h1"))
c.execute(SQL, ("sspai","tech","x2","fp-dup-b","召开2026年第三次临时股东大会的通知","s","u",1794900600000,0,0,"h2"))
c.commit()
ids = [r["id"] for r in c.execute("SELECT id FROM items ORDER BY id DESC LIMIT 2")]
print("新插入", ids, "→ 增量扫描标出", dedupe.mark_duplicates(c, limit=50))
print("结果", [dict(r) for r in c.execute("SELECT id, dup_of FROM items WHERE id IN (?,?)", ids)])
