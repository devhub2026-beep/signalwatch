# 实测数据（都在本机跑出来的，不是估算）

机器：Windows 11 + Docker Desktop 29.9.3（WSL2 后端），16 vCPU / 7.18 GiB 给容器。
数据库：SQLite（容器内 `journal_mode=delete`，单写者）。
最近一次运行：2026-11-17。

所有数字都可以自己复现，命令写在每节下面。

## 1. 抓取

| 指标 | 数字 | 怎么来的 |
| --- | --- | --- |
| 数据源 | **8 个**（巨潮公告、统计局、中国政府采购网、IT之家、少数派、arXiv、arXiv q-fin） | `signalwatch sources` |
| 单个源一轮（ithome，30 条） | **4.36 秒** | `audit` 表里 `job.run` 的「用时」 |
| 全量一轮（8 源） | 约 **40 秒**，受各站响应影响 | `docker compose exec signalwatch signalwatch crawl` |
| 库里条目 | **1913 条**（含 08:00 起累积） | `signalwatch stats` |
| 去重 | 同源按内容指纹，跨源按标题 Jaccard ≥ 0.82 且 72 小时窗 | `dedupe.py` |

## 2. 正文抠取

| 类型 | 实现 | 实测 |
| --- | --- | --- |
| 巨潮 PDF | `pdftext.py`：解析对象 → 每字体 `/ToUnicode` 码表 → 内容流 `Tj/TJ` 还原汉字 | 单份公告 **14353 字 / 3 条**；累计 146 条有正文 |
| 网页（IT之家/少数派/政采/统计局） | `enrich.html_to_text()`：去 `script/style/nav`，取 `<article>`，切面包屑与文末「责任编辑…」 | 一轮 30 条 → **抠到 25 条 / 77307 字**，空正文 3、下载失败 2 |
| 覆盖 | 手动 `run.py enrich --limit N`，默认覆盖所有可补源 | 库内 **155 / 1904 = 8.1%**（自动补每轮只 2 条） |

## 3. 接口性能（10 万行样例库）

`python tools/scale_probe.py`，样本 100 000 条：

| 操作 | 优化前 | 优化后 | 做法 |
| --- | --- | --- | --- |
| 首屏 30 条 | — | **0.4 ms** | `idx_items_pin_pub` 覆盖排序 |
| 关键词搜索 | — | **0.4 ms** | 标题/公司索引 |
| 未读数 | — | **7.8 ms** | `idx_marks_item` |
| 深翻页 offset=90000 | **6 227 ms** | **866 ms** | 覆盖索引 `(pinned, published_at, id)` |
| 键集翻页（cursor） | 178 ms | **53.7 ms** | `WHERE (published_at, id) < (?, ?)` |
| 7 天报表 | **1 530 ms** | **24 ms / 300 ms** | `INDEXED BY idx_items_first_seen` 强制走索引 |
| 库文件 | — | **37.3 MB** | 10 万条含标题/摘要/正文 |

## 4. 压测（600 请求 / 30 线程）

`python loadtest.py`（`SW_BASE` 可指向任意实例）：

| 跑法 | 吞吐 | p50 | p95 | p99 | 错误 |
| --- | --- | --- | --- | --- | --- |
| 宿主机 `run.py serve` | **281 QPS** | 103 ms | 127 ms | 138 ms | 0 |
| 容器 `docker compose` | **59.7 QPS** | 466 ms | 1104 ms | 1290 ms | 0 |

**容器慢 4.7 倍**，原因不是 Python：`./data` 用 bind mount 挂到 Windows 目录，
SQLite 每次 fsync 都要穿一层 9p/virtiofs。要跑满就把数据卷换成 named volume，
或把库放到容器内路径（代价：宿主机看不到库文件）。

## 5. 缓存（进程内 TTL，同一实例、同库、同 600 请求对比）

| 跑法 | 吞吐 | p50 | 命中率 |
| --- | --- | --- | --- |
| 缓存关（`SIGNALWATCH_CACHE_TTL=0`） | **216.7 QPS** | 135.6 ms | 0 |
| 缓存开（默认 2 秒） | **319.6 QPS** | **90.6 ms** | **97.7%** |

+47.5% 吞吐、p50 降 33%。缓存只挡「同一秒内重复读同一行」，`/api/items`、`/api/stats`、
`/api/sources`、报表都走它；`/api/metrics` 里能看 `cache.hits/misses/hit_rate`。
为什么不直接上 Redis：SQLite 首屏 0.4ms、瓶颈在网络不在查询，进程内字典就够了；
真要多进程共享再换 Redis（`cache.py` 的 get/set/clear/stats 接口不用改）。

## 6. 测试

| 指标 | 数字 |
| --- | --- |
| 用例数 | **247**（含 4 个 skip） |
| 时长 | 约 88 秒 |
| 接口触达率 | **64 / 64 = 100%**（`python tools/route_coverage.py`） |
| CI 本机演练 | 全通过（见 `docs/CI.md`） |

## 7. 构建与镜像

| 指标 | 数字 |
| --- | --- |
| 镜像大小 | **215 MB**（python:3.11-slim + 本项目） |
| 容器内 pip install | **14.0 秒**（清华源） |
| 单文件 exe | **19.7 MB**（PyInstaller onefile） |
| arm64 构建 | 通过 QEMU 交叉构建，容器内 `platform.machine()` = `aarch64` |
