"""备份与恢复：出事时能把库拿回来，才算「备份」。

规则：
- 备份 = WAL checkpoint 之后复制一份 db 到 `backups/`，文件名带时间；
- 默认只留最近 7 份，多了删最旧的；
- 恢复之前，先把当前库另存一份（叫 `before-restore-*.db`），免得越恢复越糟；
- 自动备份：启动时和之后每 6 小时看一次，距上次超过 20 小时就备一份。
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .config import SETTINGS, Settings

BACKUP_DIRNAME = "backups"
KEEP = 7
AUTO_EVERY_SECONDS = 6 * 3600
AUTO_MIN_AGE_SECONDS = 20 * 3600


def backup_dir(settings: Settings | None = None) -> Path:
    settings = settings or SETTINGS
    path = Path(settings.db_path).parent / BACKUP_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_backups(settings: Settings | None = None) -> list[dict]:
    items = []
    for p in sorted(backup_dir(settings).glob("backup-*.db")):
        st = p.stat()
        items.append({"name": p.name, "path": str(p), "size": st.st_size, "at": int(st.st_mtime * 1000)})
    return items


def backup(conn: sqlite3.Connection, settings: Settings | None = None, keep: int = KEEP) -> Path:
    settings = settings or SETTINGS
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    except sqlite3.Error:
        pass
    target = backup_dir(settings) / ("backup-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
    shutil.copy2(settings.db_path, target)
    prune(settings, keep=keep)
    return target


def prune(settings: Settings | None = None, keep: int = KEEP) -> int:
    files = sorted(backup_dir(settings).glob("backup-*.db"))
    removed = 0
    for old in files[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore(source: str | Path, settings: Settings | None = None) -> Path:
    """把 source 覆盖成当前库；返回「恢复前」那份安全备份的路径。"""
    settings = settings or SETTINGS
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"找不到备份文件：{src}")
    with sqlite3.connect(str(src)) as check:
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check.close()
    if "items" not in tables:
        raise ValueError("这个文件不像 SignalWatch 的库（没有 items 表）")
    current = Path(settings.db_path)
    current.parent.mkdir(parents=True, exist_ok=True)
    safety = current.parent / ("before-restore-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
    if current.exists():
        shutil.copy2(current, safety)
    for suffix in ("-wal", "-shm"):
        stale = Path(str(current) + suffix)
        if stale.exists():
            stale.unlink()
    shutil.copy2(src, current)
    return safety


def last_backup_age(settings: Settings | None = None) -> float:
    items = list_backups(settings)
    if not items:
        return float("inf")
    return time.time() - items[-1]["at"] / 1000


class AutoBackup:
    """后台线程：启动备一次（若太久没备），之后每 6 小时看一次。"""

    def __init__(self, settings: Settings | None = None, keep: int = KEEP) -> None:
        self.settings = settings or SETTINGS
        self.keep = keep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if last_backup_age(self.settings) >= AUTO_MIN_AGE_SECONDS:
                    conn = sqlite3.connect(str(self.settings.db_path))
                    try:
                        backup(conn, self.settings, keep=self.keep)
                    finally:
                        conn.close()
            except Exception:
                pass
            self._stop.wait(AUTO_EVERY_SECONDS)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="auto-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
