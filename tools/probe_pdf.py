"""测一下巨潮的 PDF 到底有没有文字层：有就值得解析，没有就只能承认是扫描件。

做法：抓一份真 PDF，解 FlateDecode 流，看里面有没有 Tj/TJ 文本操作符和像中文的字符串。
不引第三方库（这台机器装不上）。
"""

import re
import sys
import urllib.request
import zlib

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
URL = sys.argv[1] if len(sys.argv) > 1 else (
    "http://static.cninfo.com.cn/finalpage/2026-11-16/1225672397.PDF")

req = urllib.request.Request(URL, headers={"User-Agent": UA})
raw = urllib.request.urlopen(req, timeout=30).read()
print("bytes", len(raw))

streams = 0
text_ops = 0
sample = []
for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
    body = m.group(1)
    try:
        data = zlib.decompress(body)
    except Exception:
        continue
    streams += 1
    if b"Tj" in data or b"TJ" in data:
        text_ops += 1
        if len(sample) < 3:
            sample.append(data[:300])

print("flate streams", streams, "with text ops", text_ops)
for s in sample:
    print("---", s[:200])
