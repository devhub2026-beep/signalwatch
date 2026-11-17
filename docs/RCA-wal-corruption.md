# RCA：容器跑着的时候从宿主机写库，把 SQLite 写烂了

发生时间：2026-11-17 18:26 左右。影响：主库 `data/signalwatch.db` 变成
`database disk image is malformed`，容器与宿主机都读不了，网页 500。

## 现象

1. 在容器里用 `signalwatch serve` 正常跑了几个小时，`PRAGMA integrity_check` = ok。
2. 为了改 admin 口令，我在**宿主机**上跑 `python -c "auth.set_password(...)"`。
3. 几秒后容器里 `POST /api/auth/login` 返回：
   `sqlite3.DatabaseError: database disk image is malformed`。
4. 回头再在宿主机查同一个文件：也是 malformed。

## 时间线

| 时间 | 动作 |
| --- | --- |
| 18:26 | 宿主机 `set_password` 写库成功（返回 ok） |
| 18:27 | 容器登录 500，报 malformed |
| 18:31 | `docker compose down`，坏库另存到 `data/corrupt/` |
| 18:34 | 用 `data/backups/backup-20261117-102350.db` 恢复（1593 条，integrity ok） |
| 18:45 | 改成「口令只在容器内改」+ 加 `SIGNALWATCH_JOURNAL=delete` |
| 19:05 | 复测：容器 healthy、登录正常、integrity_check = ok |

## 根因

`docker-compose.yml` 把 `./data` 以 bind mount 挂进容器（Windows 主机目录）。
SQLite 的 WAL 模式要维护 `-wal` 与 `-shm` 两个附属文件，靠内存映射做索引。
容器（Linux，WSL2 9p/virtiofs）和 Windows 宿主机各自缓存同一批页，
**没有共同的锁与回写顺序**，两边交替写就会写出互相矛盾的页版本。

WAL 模式在「一个文件、一个内核、一个写者」的假设下才安全，
bind mount 打破了「一个内核」这条。

## 修法（三条一起上）

1. **容器里不用 WAL**：`db.connect()` 读 `SIGNALWATCH_JOURNAL`，
   compose 里设成 `delete`，退回归滚日志；`synchronous=FULL` 保证掉电不丢。
2. **单写者纪律**：同一份库同一时刻只能有一个写者——容器、桌面窗口、本机 serve
   三选一。README 与 `docs/DEPLOY.md` 都写成表；`signalwatch migrate` 因此做成**只读**。
3. **口令在容器内改**：`docker compose exec signalwatch python -c "...set_password..."`，
   宿主机不再碰库。

## 没做的事（留着给以后）

- 没做「库在容器内、宿主机只读快照」的方案；现在的恢复靠 `data/backups/` 里的每日备份
  + `AutoBackup`（距上次 >20 小时自动备，之后每 6 小时一次）。
- 没有自动检测 malformed 并自愈：现在要人工 `restore`。

## 复盘

错的不是 SQLite，也不是 Docker，是**我把「单写者」当成建议写进了 README，却没写进代码**。
修完之后它是代码：journal 模式由环境变量决定、`migrate` 只读、备份自动跑。
