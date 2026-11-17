# 部署手册

这份文档只讲「给别人跑起来」和「自己升级时别把库搞坏」。所有命令都在 `D:\signalwatch` 下执行，
Python 用项目里验过的解释器（`E:\.venv\FastAPIProject7\.venv\Scripts\python.exe`），容器用 Docker Desktop。

## 0. 先看这一条：同一份库只能有一个写者

`data/signalwatch.db` 是 SQLite。下面三种跑法**互斥**，同时开会把库写烂（真实发生过，见 README 的「一次真实事故」）：

| 跑法 | 谁在写 | 什么时候用 |
| --- | --- | --- |
| 桌面窗口 `run.py desktop` | 本机进程 | 自己盯盘 |
| 本机服务 `run.py serve` | 本机进程 | 给别人用网页 |
| 容器 `docker compose up -d` | 容器内的进程 | 长期在线 / 手机访问 |

换跑法之前：关掉前一个 → `docker compose down` → 确认没有 `data/signalwatch.db-wal` 残留。

## 1. 本机直接跑（最快）

```bash
python run.py serve --host 127.0.0.1 --port 8766
# 打开 http://127.0.0.1:8766/       时间线
# 打开 http://127.0.0.1:8766/admin  管理台（默认 admin/admin123，首次登录必须改）
```

- 抓取调度在进程内，关掉进程就不抓了 → 长期跑用第 2 节。
- 加 `--tls` 走 HTTPS（需要 `certs/cert.pem` 与 `certs/key.pem`，由 `make_cert.ps1` 生成）。

## 2. 容器跑（长期在线，推荐）

```powershell
# 一次：起 Docker Desktop（没有它 docker 命令连不上守护进程）
.\start_docker.ps1
```

它做三件事：找不到 Docker Desktop 就拉起来 → 等守护进程就绪（最多 240 秒）→
`docker compose --profile tls up -d`。日志在 `data/docker-start.log`。

跑起来之后：

| 入口 | 地址 |
| --- | --- |
| 网页（宿主机） | `http://127.0.0.1:8766/` |
| 管理台 | `http://127.0.0.1:8766/admin` |
| 手机 / HTTPS | `https://<电脑局域网IP>:8443`（nginx 前置，`nginx-tls.conf`） |

容器内验证：

```powershell
docker compose exec signalwatch signalwatch stats
docker compose exec signalwatch signalwatch crawl      # 七个源抓一轮
docker compose exec signalwatch signalwatch migrate    # 迁移台账（只读）
curl.exe -s http://127.0.0.1:8766/api/metrics
```

### 基础镜像拉不下来怎么办

这台机器 `registry-1.docker.io` 是 **NXDOMAIN**。用 DaoCloud 镜像中转：

```powershell
docker pull m.daocloud.io/docker.io/library/python:3.11-slim
docker tag  m.daocloud.io/docker.io/library/python:3.11-slim python:3.11-slim
# 或直接指定：docker build --build-arg BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim -t signalwatch:0.1.0 .
```

`pip` 同理：`Dockerfile` 里 `ARG PIP_INDEX_URL`，构建时 `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`。

## 3. 手机装 PWA（需要 HTTPS）

1. `.\make_cert.ps1`（自动把本机非虚拟网卡 IP 写进证书 SAN，输出 `certs/cert.pem`、`certs/key.pem`）；
2. `docker compose --profile tls up -d`；
3. 手机连同一个 Wi-Fi，浏览器开 `https://<电脑局域网IP>:8443`；
4. 自签证书会警告 → 继续访问 → 浏览器菜单「添加到主屏幕」。

## 4. 备份与恢复（升级前必做）

```bash
python run.py backup --keep 7      # 出 data/backups/backup-YYYYmmdd-HHMMSS.db
python run.py backups              # 列出现有备份与最近一次距今多久
python run.py restore data/backups/backup-20261117-102350.db   # 恢复（会先另存 before-restore-*.db）
```

- 服务启动时会自己备一次（距上次超 20 小时），之后每 6 小时一次。
- 恢复前先停写者：`docker compose down`（或关掉桌面窗口）。
- 恢复后跑一次 `PRAGMA integrity_check`（`python -c` 或容器内）确认 `ok`。

## 5. 升级步骤（换代码）

```bash
git pull
docker compose down                 # 停写者
python run.py backup --keep 7       # 保险
docker build -t signalwatch:0.1.0 .
docker compose up -d
docker compose exec signalwatch signalwatch migrate --check   # 缺步骤会退出 1
curl.exe -s http://127.0.0.1:8766/api/health
```

`signalwatch migrate --check` 是只读的，不会改库——这是故意的，避免在容器跑着的时候从宿主机写库。

## 6. 开机自启

```powershell
.\install_autostart.ps1                # 默认桌面窗口
.\install_autostart.ps1 -Mode serve    # 本机服务（无窗口，但会和容器抢 8766）
```

现在注册起来的是 `start_docker.ps1`（容器模式）。想换回来跑同一条命令即可。

> Windows 计划任务（`install_task.ps1`）在当前会话令牌下注册会被拒（`ERROR: Access is denied.`），
> 需要管理员权限的终端执行。

## 7. 排错表

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `failed to connect to the docker API at npipe://...` | Docker Desktop 没起 | 跑 `start_docker.ps1` |
| `database disk image is malformed` | 两个写者同时写 SQLite（常见于容器 + 宿主机） | `docker compose down` → 移走坏库 → 用备份恢复 → 以后只留一个写者 |
| `sqlite3.OperationalError: disk I/O error` | 同上，或 `-wal` 残留 | 删 `data/signalwatch.db-wal`、`-shm` 后重启 |
| `registry-1.docker.io ... no such host` | 本机 DNS 解析失败 | 用 `m.daocloud.io` 中转（见上） |
| pypi 构建卡住 | pypi.org 超时 | `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| 8766 端口被占 | 桌面窗口 / 本机 serve / 容器三选一没清干净 | `Get-NetTCPConnection -LocalPort 8766` 找到 PID 后处理 |
| 手机打不开 HTTPS | 证书 SAN 里没有当前 IP | 重跑 `make_cert.ps1`，`docker compose restart signalwatch-tls` |

## 8. 健康检查与指标

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 存活探针（容器 healthcheck 用它） |
| `GET /api/metrics` | 请求数、RPS、按状态码分布、p50/p95/p99、Top 路径 |
| `GET /api/stats` | 条目数 / 今日新增 / 修订数 |
| `GET /api/sources` | 每个源的最近一次结果与退避状态 |

每个响应都带 `X-Request-ID`（客户端传 `x-request-id` 就沿用）与 `X-Response-Time`，
排查问题时把 `X-Request-ID` 贴进日志里搜。
