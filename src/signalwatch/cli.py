"""命令行入口：serve / crawl / stats / sources / db。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # 支持 python run.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="signalwatch", description="盯梢机器")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="起本地网页（默认 127.0.0.1:8766）")
    s.add_argument("--host", default="127.0.0.1", help="绑 0.0.0.0 才能被同网设备访问")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--tls", action="store_true", help="走 HTTPS（手机装 PWA 必须）")
    s.add_argument("--cert", default="certs/cert.pem", help="证书 PEM")
    s.add_argument("--key", default="certs/key.pem", help="私钥 PEM")

    d = sub.add_parser("desktop", help="桌面窗口（常驻，实时刷新，新条目弹浮窗）")
    d.add_argument("--paused", action="store_true", help="打开后先不抓，手动点「立刻抓一轮」")
    d.add_argument("--page", default="overview", help="启动后停在哪个页（overview/items/jobs/reports/...）")

    c = sub.add_parser("crawl", help="立刻抓一轮（不等节奏）")
    c.add_argument("--source", default=None, help="只抓某个源")
    c.add_argument("--show", action="store_true", help="打印每条结果")

    sub.add_parser("stats", help="库里有多少条")
    sub.add_parser("sources", help="源的运行状态")
    sub.add_parser("db", help="打印数据库路径")
    mg = sub.add_parser("migrate", help="看迁移台账（--check 用于 CI/发布前检查）")
    mg.add_argument("--check", action="store_true", help="有未记录的步骤就退出 1")
    mg.add_argument("--json", action="store_true", help="输出 JSON")
    b = sub.add_parser("backup", help="备份数据库到 data/backups")
    b.add_argument("--keep", type=int, default=7, help="保留最近几份（默认 7）")
    r = sub.add_parser("restore", help="从备份恢复（会先给当前库留一份）")
    r.add_argument("file", help="备份文件路径")
    lb = sub.add_parser("backups", help="列出已有备份")
    en = sub.add_parser("enrich", help="把公告 PDF / 网页正文抠进库（默认覆盖所有能用的源）")
    en.add_argument("--limit", type=int, default=5)
    en.add_argument("--source", default="", help="只补这个源；留空=全部可补的源")
    en.add_argument("--show", action="store_true", help="打印抓到的正文开头")
    lb.add_argument("--limit", type=int, default=20)
    return p


def _cmd_crawl(args) -> int:
    from .scheduler import Crawler

    crawler = Crawler()
    try:
        names = [args.source] if args.source else list(crawler.sources)
        ok = 0
        for name in names:
            r = crawler.run_source(name, force=True)
            if r is None:
                print(f"[skip] {name}")
                continue
            status = "ok" if r.ok else "FAIL"
            print(f"[{status}] {name:<8} 取回 {r.fetched:>3} 新增 {r.inserted:>3} 修订 {r.updated:>2}"
                  + (f" 错误: {r.error}" if r.error else ""))
            ok += 1 if r.ok else 0
            if args.show and r.ok:
                for item in crawler.conn.execute(
                    "SELECT title, source, published_at FROM items WHERE source=? ORDER BY id DESC LIMIT 5",
                    (name,),
                ):
                    print("   -", item["title"][:70])
        return 0 if ok == len(names) else 1
    finally:
        crawler.conn.close()


def _cmd_backup(args) -> int:
    from . import maintenance
    from .config import SETTINGS
    from .db import connect, init_db

    conn = connect(SETTINGS.db_path)
    init_db(conn)
    try:
        target = maintenance.backup(conn, keep=args.keep)
    finally:
        conn.close()
    size = target.stat().st_size / 1024
    print(f"[ok] 备份到 {target}（{size:.1f} KB），保留最近 {args.keep} 份")
    return 0


def _cmd_restore(args) -> int:
    from . import maintenance

    try:
        safety = maintenance.restore(args.file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}")
        return 1
    print(f"[ok] 已从 {args.file} 恢复；恢复前的库留在 {safety}")
    return 0


def _cmd_backups(args) -> int:
    from . import maintenance

    items = maintenance.list_backups()
    if not items:
        print("还没有备份。运行：python run.py backup")
        return 0
    for it in items[-args.limit:]:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(it["at"] / 1000))
        print(f"{when}  {it['size'] / 1024:8.1f} KB  {it['name']}")
    return 0


def _cmd_enrich(args) -> int:
    from . import enrich
    from .config import SETTINGS
    from .db import connect, init_db

    conn = connect(SETTINGS.db_path)
    try:
        init_db(conn)
        only = (args.source,) if args.source else None
        result = enrich.enrich(conn, limit=args.limit, only=only)
        print(
            f"[ok] 试了 {result['tried']} 条，抠到正文 {result['ok']} 条"
            f"（{result['chars']} 字），空文字层 {result['empty']}，下载失败 {result['failed']}"
        )
        if args.show and result["ok"]:
            row = conn.execute(
                "SELECT title, body_text FROM items WHERE COALESCE(body_text,'') <> ''"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                print("  -", row["title"][:60])
                print("  ", " ".join(row["body_text"].split())[:200])
        return 0 if result["failed"] == 0 else 1
    finally:
        conn.close()


def _cmd_stats() -> int:
    from .db import connect, db_stats, init_db

    from .config import SETTINGS

    conn = connect(SETTINGS.db_path)
    try:
        init_db(conn)
        print(json.dumps(db_stats(conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


def _cmd_sources() -> int:
    from .scheduler import Crawler

    crawler = Crawler()
    try:
        for s in crawler.snapshot()["sources"]:
            print(f"{s['name']:<8} {s['status']:<9} 每 {s['interval_seconds']:>4}s  "
                  f"失败 {s['fail_count']}  {s['last_error'] or ''}")
    finally:
        crawler.conn.close()
    return 0


def _cmd_migrate(args) -> int:
    """只读：看台账，不动数据。

    写库只允许一个写者（容器或桌面窗口），所以这里刻意不调 init_db——
    否则你在宿主机敲一下，就可能和容器同时写同一个 SQLite 文件。
    """
    from . import migrations
    from .config import SETTINGS
    from .db import SCHEMA_VERSION, connect

    conn = connect(SETTINGS.db_path)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if not has_table:
            print(f"{SETTINGS.db_path} 还没有迁移台账。"
                  f"先跑一次 `signalwatch serve` 或 `signalwatch stats`（会在容器/本机建台账）。")
            return 2 if args.check else 0
        rows = [
            {"version": int(v), "name": n, "applied_at": int(t)}
            for v, n, t in conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            )
        ]
    finally:
        conn.close()

    done = {r["version"] for r in rows}
    info = {
        "current_version": SCHEMA_VERSION,
        "recorded_max": max(done) if done else 0,
        "applied": rows,
        "missing": [{"version": v, "name": n} for v, n in migrations.STEPS
                    if v not in done and v <= SCHEMA_VERSION],
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"数据库 {SETTINGS.db_path}")
        print(f"代码版本 {info['current_version']}，台账最高 {info['recorded_max']}，"
              f"已记录 {len(rows)} 步")
        for row in rows:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["applied_at"]))
            print(f"  v{row['version']:<2} {stamp}  {row['name']}")
        for row in info["missing"]:
            print(f"  [缺] v{row['version']} {row['name']}")
    if args.check and info["missing"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文输出会变乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if args.cmd == "serve":
        from .api import serve

        cert = getattr(args, "cert", None) if getattr(args, "tls", False) else None
        key = getattr(args, "key", None) if getattr(args, "tls", False) else None
        if getattr(args, "tls", False):
            from pathlib import Path as _P
            for label, path in (("证书", cert), ("私钥", key)):
                if not _P(path).exists():
                    print(f"[错误] 找不到{label}：{path}（用 tools/make_cert.ps1 生成，或 --cert/--key 指定）")
                    return 1
        serve(host=args.host, port=args.port, certfile=cert, keyfile=key)
        return 0
    if args.cmd == "desktop":
        from .desktop import main as desktop_main

        argv = ["--page", getattr(args, "page", "overview")]
        if args.paused:
            argv.insert(0, "--paused")
        return desktop_main(argv)
    if args.cmd == "crawl":
        return _cmd_crawl(args)
    if args.cmd == "stats":
        return _cmd_stats()
    if args.cmd == "sources":
        return _cmd_sources()
    if args.cmd == "db":
        from .config import SETTINGS

        print(SETTINGS.db_path)
        return 0
    if args.cmd == "migrate":
        return _cmd_migrate(args)
    if args.cmd == "backup":
        return _cmd_backup(args)
    if args.cmd == "restore":
        return _cmd_restore(args)
    if args.cmd == "backups":
        return _cmd_backups(args)
    if args.cmd == "enrich":
        return _cmd_enrich(args)
    raise SystemExit(f"未知命令：{args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())


