# signalwatch · 盯梢机器

把公开信息源盯住：每几分钟扫一遍，**只处理新增和内容变化的条目**，在本地时间线上看，点开是原文。
不登录、不上传、不采集个人数据，只读公开页面。桌面端常驻、右下角弹窗提醒，另带一套管理员后台。

## 它解决什么问题

公开信息每天在更新，但没有人为你「盯着」：公告在交易所，论文在 arXiv，科技新闻在十几个站。
人工刷的通病是**漏**和**重复看同一条**。这个项目的核心不是抓，是**判断哪条是新的**：

- 每条记录算一个**内容指纹**（`sha256(源 + 外部 id)`），同一个公告被两个栏目返回也只落一行；
- 每条再算一个**正文哈希**，标题/摘要变了就升一版，旧版进 `revisions` 表，页面标黄「已修订 v2」；
- 每个源存一个**游标**（上次抓到的最新时间），断了从断点接着抓；
- 每个源有**失败计数**：连续失败 3 次出红条，并指数退避（30s → 60s → … 封顶 900s）。

## 四种人，四个角色

| 角色 | 权限点 | 能干什么 |
|---|---|---|
| `viewer` | `items.read` | 看时间线、搜索 |
| `auditor` | 以上 + `audit.read` | 只读，但能看审计日志 |
| `analyst` | 以上 + `items.write` `items.export` `sources.manage` `jobs.manage` | 改条目、导 CSV、暂停/恢复源、改间隔、发任务 |
| `admin` | 全部（含 `items.delete` `users.manage` `settings.manage` `categories.manage`） | 删条目、用户管理、改系统设置、改分类 |

高等级自动继承低等级的权限点（`auth.perms_of()` 按 rank 合并），所以加角色只要往 `db.BUILTIN_ROLES` 里加一行。
每个用户带 `email`：填了邮箱的人会按**自己的订阅词**收到摘要投递，互不打扰。

第一次启动自动建一个管理员：**admin / admin123**（口令可用环境变量 `SIGNALWATCH_ADMIN_PASSWORD` 换掉）。
进去第一件事是改掉它——口令用 PBKDF2（12 万轮、每人独立盐）存，改了口令旧令牌立刻作废。

## 桌面端里有什么

双击 `SignalWatch.exe`（或 `python run.py desktop`），第一次用 `admin / admin123` 登录，之后会记住登录直接进。

左侧十二页：

- **工作台**：八个指标（总条目 / 今日新增 / 未读 / 被修订 / 抓取成功率 / 去重率 / 平均耗时 / 任务积压）+ 近 14 天柱状图 + 按来源排行；
- **首页**：时间线卡片 + 搜索 + 分类（金融 / 科技 / 学术）+ 只看未读 + 详情面板（含**改动历史与逐版差异**、**公司时间线**入口）+ 导出；
- **收藏**：打过星的条目；
- **条目**：表格式条目管理，搜索、上架/下架、**置顶**、删除、导出 CSV，选中一条可以改标题/摘要/分类，支持多选批量操作，分页（每页 50）；
- **分类**：分类的增删改停启用，条目数统计；
- **订阅**：订阅规则（关键词 + 分类/来源作用域），可**停用/启用**、显示命中次数，命中高亮；
- **数据源**：**七个源**的状态、间隔、上次成功时间，可单源「抓一轮」或停用；
- **任务**：抓取任务队列，排队/运行/完成/失败四态，可取消、重试，双击看详情（耗时、失败原因归类、完整结果）；
- **报表**：近 1/7/30 天切换，来源表（条数/修订）、分类分布、公司 Top10、导出；
- **用户**：建号、改角色（只读 → 分析员 → 管理员）、停用、改口令；
- **设置**：每个源的抓取间隔、通知开关（浮窗/提示音/只提醒订阅词/夜间免打扰）、**邮件推送**（SMTP 发件箱 + 授权码，收件箱可填自己；保存时发一封测试信，之后有新条目自动摘要投递，十分钟最多一封）、数据库备份；
- **我的**：当前账号、角色、上次登录、统计卡、库路径、退出登录。

新条目进来会弹右下角浮窗 + 提示音，不用一直盯着。

## 管理台里有什么

`http://127.0.0.1:8766/admin`

九个页签，和桌面端功能对齐：

- **概览**：近 7 天每天新增/修订数、按源统计、分类分布、最活跃公司；
- **条目**：分页浏览、搜索、多选上架/下架/删除、置顶、导出 CSV；
- **分类**：新增、停用/启用、条目数；
- **任务**：抓取队列，可发起、重试、取消，看结果；
- **报表**：近 1/7/30 天切换，来源表 + 公司 Top 10；
- **用户**：建号、改角色、停用、改口令、删除（最后一个管理员删不掉）；
- **数据源**：每个源的间隔、上次运行、失败数，可单独暂停/恢复；
- **设置**：通知开关（录屏提示 / 提示音 / 只提醒订阅词）；
- **审计**：谁在什么时候改了什么，只增不改。

接口出错时统一返回 `{"error": "人话"}`（例：`{"error": "没有这个源：nope"}`），不再把堆栈或英文异常甩到页面上。

## 数字（本机实测，2026-11-15）

| 项目 | 数值 |
|---|---|
| 源 | **7 个**（巨潮公告 / IT之家 / 少数派 / arXiv / arXiv-q-fin / 国家统计局 / 中国政府采购网） |
| 首轮全量抓取 | **约 5 秒**，入库 **100 条**（早期四源）；加两个政府源后又入库 46 条 |
| 第二轮（同样 5 秒） | 新增 **0** 条 —— 去重生效 |
| 数据库 | 156 KB → 加源后约 **200 KB**（SQLite，WAL） |
| 单元测试 | **138 个，18.5 秒全绿**（业务层 / 邮件推送 / 配置隔离 / 网页端接口 / 政府源解析 / 解析护栏 / PDF 抠字 / 备份恢复 / 跨源去重 / Excel / 桌面 11 页渲染） |
| PDF 正文 | 巨潮公告 **有文字层**：实测 3 条共抠出 **14353 字**，网页上点「看正文」直接读全文（`pdftext.py`，纯标准库，靠 ToUnicode 还原汉字子集字体） |
| 跨源去重 | 标题归一后 Jaccard ≥ 0.82、72 小时内、跨源才判重；早的那条留、晚的标 `dup_of` |
| 备份 | `python run.py backup --keep 3` → `data/backups/backup-20261116-181845.db`（1068 KB）；启动时距上次超 20 小时自动备一次 |
| 抓取成功率（近 24 小时，83 次运行） | **98.8%**，去重率 **93.5%**，平均每次 **0.86 秒** |
| 任务队列 | 优先级领取、失败可重试、可取消；实测两条任务 `done`（cninfo 30 条 / 全量 100 条） |
| 并发写入 | 50 线程同批插入 10 条 → 库里正好 10 条，无 `database is locked` |
| 接口压测（`python loadtest.py`） | 30 线程 × 20 次 = **600 请求 / 1.46 秒**，**410 QPS**，p50 **71 ms** / p95 **89 ms** / p99 **104 ms**，错误 **0** |
| 单文件 exe（PyInstaller） | `dist\SignalWatch.exe` **19.7 MB**，`serve --port 8899` 起来后 `/api/health` 返回 200；`desktop` 模式窗口能开（截图 `docs/shot-exe-desktop.png`） |
| 自动补正文 | 开启后累计 **19 条**巨潮公告有正文（含首屏 628 / 795 字的短公告），每轮最多抠 2 条，不拖慢抓取 |
| git | 已初始化，3 次提交，161 个文件进版本库（`.git` 17 MB），CI 配置在 `.github/workflows/tests.yml` |

## 跑起来

```bash
python run.py serve            # 网页 + API，只监听 127.0.0.1:8766
python run.py desktop          # 桌面常驻窗口（右下角弹窗）
python run.py crawl            # 立刻抓一轮
python run.py stats            # 库里多少条、源健不健康
python run.py enrich --limit 5 # 把巨潮公告的 PDF 正文抠进库
python run.py backup --keep 7  # 备份（另有 restore / backups）
```

Windows 上双击 `启动盯梢.bat` 就是桌面版；`install_autostart.ps1` 把它塞进开机启动。

## 邮件推送

设置页最下面的「推送（邮件）」：填发件邮箱（QQ 邮箱用 `smtp.qq.com:465`）、**授权码**（不是登录密码，在 QQ 邮箱 → 设置 → 账户里生成）、收件邮箱，点「保存并发测试邮件」—— 能收到就说明通了。**网页管理台 → 设置**里同样能配，配完会显示「上次发送 时间 · 成功/失败原因」，失败不会闷在日志里。

多用户收摘要：管理台 → 用户，给每个人填邮箱并点「存邮箱」；之后摘要按人发，各自只收命中自己订阅词的条目。没填邮箱的账号不收。

之后每当有新条目，程序把最近 30 条拼成一封摘要（标题「SignalWatch：N 条新信息」，每条带来源、时间、公司、原文链接），**十分钟最多一封**，失败只记审计不弹窗。

凭据只存在本机数据库的 `settings` 表里（`data/` 已在 `.gitignore`），不进代码、不进包、不打日志。换人用就换授权码；不想推送就把「开关」关掉。

## 自己干活的三件事（不用你盯着）

**1. 新条目自动抠正文。** 巨潮的公告是 PDF，很多浏览器里点开是空白或要下载。程序在每次抓到新条目后，顺手抠 2 条 PDF 的文字层（`data` 里那几条），时间线卡片上就多一个「看正文（N 字）」按钮，点开直接读。开关在设置页「新条目自动抠正文」，关掉就不抓。实测一份员工持股计划公告抠出 6 千字通顺中文，比原 PDF 好读。

**2. 源连续失败会发邮件。** 连续失败到达阈值（默认 3 次）就发一封告警，同一源 6 小时内只发一封，邮件里有最后一次错误和处置建议。没有配 SMTP 就只在页面显示红条，不会假装发了。开关在设置页「源连续失败时发邮件告警」。

**3. 关掉窗口也能抓。** 用 `install_task.ps1` 把抓取注册成 Windows 计划任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1        # 每 10 分钟
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Minutes 30
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Remove
```

它注册的任务是 `SignalWatch.exe crawl`（没有 exe 就退回 `run.py crawl`），抓完就退出，日志追加到 `data\crawl.log`，只在当前用户登录时运行、不需要管理员权限。注意：如果当前会话被限制（比如以受限令牌跑），`schtasks` 会报 `Access is denied`，那就自己开一个普通 PowerShell 窗口跑上面那条命令。

## 版本管理与 CI

仓库是 git（`.git` 已初始化，第一次提交 161 个文件）。`.github/workflows/tests.yml` 跑三件事：

- **测试**：ubuntu + windows × Python 3.11/3.12，四个组合都跑 `unittest discover`，再跑一次 `run.py stats` 冒烟；
- **构建**：`python -m build` 出 wheel，再 `pip install --no-deps dist/*.whl` 验证装得上、`signalwatch/web` 里的静态文件进了包；
- **容器**：`docker build` + 容器里跑一次 CLI + `docker compose config` 校验。

这台机器没装 docker（WSL 都没装），所以镜像从来没在本地跑过——CI 里那一步是给「有 docker 的环境」验证的。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/admin` | 时间线 / 管理台 |
| GET | `/api/health` | 版本、运行状态 |
| GET | `/api/items?source=&q=&limit=&since=` | 时间线（支持搜索） |
| GET | `/api/stats`、`/api/sources` | 统计 / 每个源的节奏与失败数 |
| POST | `/api/crawl` | 立刻抓一轮（正在跑返回 409） |
| POST | `/api/auth/login`、`/api/auth/logout` | 登录（同时下发 cookie）、退出 |
| GET | `/api/auth/me` | 当前用户 |
| GET/POST | `/api/users` | 用户列表 / 建用户（admin） |
| PATCH/DELETE | `/api/users/{name}` | 改角色/停用/改口令 / 删除（admin） |
| GET | `/api/audit` | 审计日志（admin） |
| GET | `/api/admin/audit.csv` | 导出审计（CSV，带 BOM） |
| POST | `/api/admin/audit/prune` | 删掉 N 天前的审计 |
| GET/POST | `/api/admin/notify` | 读/存发件箱配置（口令不回显） |
| POST | `/api/auth/password` | 自己改口令（改完旧令牌作废） |
| POST | `/api/admin/items/{id}/pin` | 置顶/取消置顶 |
| GET | `/api/admin/items/{id}/history` | 改动历史 + 相邻版本差异 |
| GET | `/api/admin/timeline?code=` | 某公司的时间线 |
| GET | `/api/report?days=7` | 报表：按天、按源、按分类、最活跃公司 |
| GET | `/api/items/{id}/text` | 某条的 PDF 正文（没抠到就是空串） |
| POST | `/api/admin/enrich?limit=&source=` | 现抠几条正文（analyst+） |
| GET | `/api/admin/items.xlsx`、`/api/report.xlsx?days=` | Excel 导出（纯标准库生成，openpyxl 能读） |
| GET | `/api/events/stats` | SSE，每 3 秒推一次计数（有新条目立刻推） |
| PATCH | `/api/sources/{name}` | 改间隔、暂停/恢复（analyst+） |

## 加一个源

在 `src/signalwatch/sources/` 写一个类，实现 `fetch(cursor, limit) -> list[Record]`，
然后在 `sources/__init__.py` 的 `default_sources()` 登记一行。调度器、接口、页面都从这张表读。

## 部署给别人

```bash
docker compose up --build      # http://127.0.0.1:8766
```

数据库挂到 `./data`，容器重启不丢；端口默认只绑本机。

## 已知限制

- **源是别人给的，随时会变**。36kr 的 `/feed` 就悄悄换成了网页，现在会直接报错而不是静默空跑；
- 只存标题、摘要、链接；**PDF 正文抓到的会存进库**（`items.body_text`，单条最多 6000 字），不落原始 PDF 文件；自动补抓只覆盖首屏那几条，历史条目要自己跑 `run.py enrich`；
- 桌面端也要登录（可以「以访客身份进入」只读看板），改动都会记审计；
- 调度是进程内的，服务/窗口停了就不抓了——要么用 `install_task.ps1` 注册计划任务、要么 Docker + `restart: unless-stopped`；
- **docker 没在本机验过**（这台机器没 WSL/docker，装 WSL 要管理员 + 重启），`Dockerfile` 与 `compose.yml` 只在 CI 里构建；
- **手机 PWA 没在真机装过**：`manifest` 与 `sw.js` 都在，但 PWA 安装要求 https 或 localhost，手机走局域网地址（http）装不上；
- 令牌用自签 HMAC，没有刷新机制，7 天过期就得重登。

## 目录

```
src/signalwatch/
  config.py      设置（路径、限速、退避、超时）
  db.py          建表、迁移、连接（WAL + 每连接一把锁）、内置角色
  ingest.py      去重、修订、源状态
  scheduler.py   调度线程（节奏、退避、游标、暂停）
  board.py       分类 / 搜索 / 未读 / 收藏 / 订阅词（可按来源、可停用）/ 导出
  admin.py       条目增删改置顶 / 分类 / 任务队列 / 报表 / 设置 / 改动历史与公司时间线
  report.py      按天、按源、按分类、按公司出数
  auth.py        口令哈希、令牌、角色与权限点（rank 继承）
  audit.py       操作审计
  notify.py      邮件摘要投递（按人、十分钟一封、免打扰时段可配）
  pdftext.py     PDF 正文抠字（ToUnicode + 子集字体，纯标准库）
  enrich.py      把抠出来的正文写进库，顺带补摘要
  maintenance.py 备份 / 恢复 / 定期保留 / 启动自备
  dedupe.py      跨源近重复判定（Jaccard ≥ 0.82、72 小时窗、早的留）
  xlsx.py        纯标准库写 Excel（两份导出都用它）
  api.py         FastAPI 路由（页面 + JSON + 管理台 + SSE + Excel）
  desktop.py     tkinter 十二页常驻窗口 + 浮窗提醒
  sources/       cninfo / rss / arxiv / html_list（统计局、政府采购）
  web/           index.html（时间线）、admin.html（管理台）、manifest、sw.js
tests/           138 个用例（去重、并发、源解析、路由、权限、审计、报表、任务队列、邮件推送、配置隔离、PDF 抠字、备份恢复、跨源去重、Excel、桌面渲染）
docs/            验收截图
```




