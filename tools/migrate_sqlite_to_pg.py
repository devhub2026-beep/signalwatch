#!/usr/bin/env python
"""SQLite → PostgreSQL 迁移脚本（快照式）。

用法（在 Docker 网络内跑，能连到 signalwatch-postgres）：
    docker run --rm --network signalwatch_default \
      -e SIGNALWATCH_DB_URL=postgresql://signalwatch:密码@signalwatch-postgres:5432/signalwatch \
      -e PYTHONPATH=/src \
      -v D:\signalwatch\src:/src \
      -v D:\signalwatch\data:/data \
      -v <本脚本>:/migrate.py \
      signalwatch:0.1.0 python /migrate.py /data/signalwatch.db

SQLite 以只读模式打开（mode=ro），不写 WAL，不影响正在运行的容器。
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, "/src")  # 容器内挂载路径；本机跑时改为相对 src 路径

from signalwatch.db import connect as pg_connect, init_db  # noqa: E402

# 迁移顺序：父表先于子表（item_cat 依赖 items/categories）
TABLES = [
    "schema_meta", "sources", "categories", "items", "revisions", "runs",
    "marks", "rules", "users", "password_resets", "audit", "app_meta",
    "item_cat", "jobs", "settings", "webhooks", "organizations", "roles",
    "schema_migrations",
]

# 有 BIGSERIAL 自增 id、迁移后需同步序列的表
SERIAL_TABLES = {"items", "revisions", "runs", "rules", "users", "audit",
                 "categories", "jobs", "webhooks", "organizations",
                 "password_resets"}


def _clean(v):
    """SQLite 的 TEXT 可含 NUL（0x00），PG 不允许，迁移时清洗。"""
    if isinstance(v, str):
        return v.replace("\x00", "")
    return v


def migrate(sqlite_path: str, pg_url: str) -> None:
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    os.environ["SIGNALWATCH_DB_URL"] = pg_url
    dst = pg_connect("unused")   # PG 模式忽略路径参数
    init_db(dst)
    print("init_db（PG 建表 + 种子数据）完成")

    total = 0
    for table in TABLES:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"[{table}] 0 行，跳过")
            continue
        cols = list(rows[0].keys())
        ph = ",".join("?" * len(cols))
        # ON CONFLICT DO NOTHING：init_db 已种子的表（roles/schema_meta/categories/
        # schema_migrations）重复键时跳过，其余真实数据正常插入
        sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({ph}) ON CONFLICT DO NOTHING"
        for row in rows:
            dst.execute(sql, tuple(_clean(row[c]) for c in cols))
        dst.commit()
        total += len(rows)
        print(f"[{table}] {len(rows)} 行")

    # 显式插入 id 后，PG 自增序列不会自动前进，必须手动同步
    for table in sorted(SERIAL_TABLES):
        dst.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
            f" COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
    dst.commit()
    print(f"序列同步完成，共迁移 {total} 行")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python migrate.py <sqlite_path>", file=sys.stderr)
        sys.exit(2)
    pg_url = os.environ.get("SIGNALWATCH_DB_URL")
    if not pg_url:
        print("未设置 SIGNALWATCH_DB_URL", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1], pg_url)
