import sqlite3, sys
from signalwatch import dedupe
conn = sqlite3.connect("data/signalwatch.db"); conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT id, source, title, published_at FROM items WHERE dup_of = 0 ORDER BY published_at")]
print("items", len(rows))
pairs = []
for i in range(len(rows)):
    a = rows[i]
    na = dedupe.normalize(a["title"])
    if len(na) < 6: continue
    for j in range(i + 1, len(rows)):
        b = rows[j]
        if b["source"] == a["source"]: continue
        if b["published_at"] - a["published_at"] > 72 * 3600 * 1000: break
        nb = dedupe.normalize(b["title"])
        if len(nb) < 6: continue
        s = dedupe.similarity(na, nb)
        if s >= 0.55:
            pairs.append((round(s, 3), a["source"], b["source"], a["title"][:40], b["title"][:40]))
pairs.sort(reverse=True)
print("pairs>=0.55:", len(pairs))
for p in pairs[:25]: print(p)
import collections
buckets = collections.Counter()
for s, *_ in pairs:
    buckets[round(s, 1)] += 1
print("buckets", dict(sorted(buckets.items())))
