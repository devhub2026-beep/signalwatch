import json, statistics, threading, time, urllib.request

URLS = ["http://127.0.0.1:8766/api/items?limit=50", "http://127.0.0.1:8766/api/stats",
        "http://127.0.0.1:8766/api/sources"]
N_THREADS, PER = 30, 20
lat = []
lock = threading.Lock()
errs = []

def worker(i):
    for k in range(PER):
        u = URLS[(i + k) % len(URLS)]
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(u, timeout=10) as r:
                r.read()
        except Exception as e:
            with lock: errs.append(repr(e))
        dt = (time.perf_counter() - t0) * 1000
        with lock: lat.append(dt)

t0 = time.perf_counter()
ts = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
[t.start() for t in ts]; [t.join() for t in ts]
dur = time.perf_counter() - t0
lat.sort()
p = lambda q: lat[int(len(lat) * q) - 1]
print(json.dumps({
  "requests": len(lat), "errors": len(errs), "seconds": round(dur, 2),
  "qps": round(len(lat) / dur, 1),
  "p50_ms": round(p(0.5), 1), "p95_ms": round(p(0.95), 1), "p99_ms": round(p(0.99), 1),
  "max_ms": round(lat[-1], 1), "first_err": errs[0] if errs else None,
}, ensure_ascii=False))
