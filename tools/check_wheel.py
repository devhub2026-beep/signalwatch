"""验证装好的 wheel 里带上了网页静态文件。"""
from __future__ import annotations

import pathlib
import sys

import signalwatch

web = pathlib.Path(signalwatch.__file__).parent / "web"
ok = (web / "index.html").exists() and (web / "admin.html").exists()
print("web ok:", ok, "->", web)
sys.exit(0 if ok else 1)
