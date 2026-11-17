"""PyInstaller 的入口：一个 exe 同时支持 serve / desktop / crawl / enrich…

两个打包特有的坑在这里处理：
1. `--windowed` 模式下 `sys.stdout` / `sys.stderr` 是 `None`，uvicorn 的默认日志配置
   一碰就炸，而且炸了也没有地方显示——所以先把它们指到一个日志文件；
2. 数据目录在 `config.py` 里已按 `sys.frozen` 处理，这里不用管。
"""

import sys
from pathlib import Path


def _ensure_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = Path(sys.executable).resolve().parent
    except Exception:
        base = Path.cwd()
    try:
        (base / "data").mkdir(parents=True, exist_ok=True)
        log = open(base / "data" / "signalwatch.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        import io

        log = io.StringIO()
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_ensure_streams()

from signalwatch.cli import main  # noqa: E402  (必须在流修好之后再导入)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
