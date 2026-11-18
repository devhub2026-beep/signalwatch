# signalwatch · 盯梢机器

把公开信息源盯住：每几分钟扫一遍，**只处理新增和内容变化的条目**，在本地时间线上看，点开是原文。
公开时间线无需登录即可阅读；正文、已读/收藏和抓取按角色鉴权。不上传、不采集个人数据，只读公开页面。桌面端常驻、右下角弹窗提醒，另带一套管理员后台。

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

第一次启动自动建一个管理员：**admin / admin123**（口令可用环境变量 `SIGNALWATCH_ADMIN_PASSWORD` 换掉）；若仍用默认口令，Web 和桌面端首次登录都会强制先改密。
进去第一件事是改掉它——口令用 PBKDF2（12 万轮、每人独立盐）存，改了口令旧令牌立刻作废。

## 桌面端里有什么

双击 `SignalWatch.exe`（或 `python run.py desktop`），第一次用 `admin / admin123` 登录并强制改密，之后会记住登录直接进。

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
管理台是 Vue 3 + Element Plus（`admin-ui/`，构建产物在 `src/signalwatch/web/admin-vue/`），入口 `/admin`。

**手机上也能用**：窗口宽度 ≤ 820px 时左侧固定栏收成抽屉（左上角汉堡），条目页从 5 列表格换成卡片（标题两行、时间/来源/状态一行、置顶/历史/时间线按钮）。两条 playwright 回归脚本钉住这件事：

```
node admin-ui/e2e/mobile.spec.mjs     # 竖屏：无固定侧栏 / 有汉堡 / 抽屉 13 项 / 条目页出卡片
node admin-ui/e2e/mobile-sweep.mjs    # 13 个页面逐个量 scrollWidth，不许横向溢出
```


`http://127.0.0.1:8766/admin`

十一个页签，和桌面端对齐（桌面有审计之外的十二页，网页把「收藏」并进时间线过滤）：

- **概览**：近 7 天每天新增/修订数、按源统计、分类分布、最活跃公司；
- **条目**：分页浏览、搜索、多选上架/下架/删除、置顶、导出 CSV；
- **分类**：新增、停用/启用、条目数；
- **任务**：抓取队列，可发起、重试、取消，看结果；
- **报表**：近 1/7/30 天切换，来源表 + 公司 Top 10；
- **用户**：建号、改角色、停用、改口令、删除（最后一个管理员删不掉）；
- **数据源**：每个源的间隔、上次运行、失败数，可单独暂停/恢复；
- **设置**：通知开关（提示音 / 只提醒订阅词）、邮件推送（SMTP + 授权码，可发测试信）、免打扰时段；
- **订阅词**：关键词 + 分类/来源作用域，可停用/启用、删除（之前只有桌面能加，网页只能看命中效果）；
- **审计**：谁在什么时候改了什么，只增不改；


接口出错时统一返回 `{"error": "人话"}`（例：`{"error": "没有这个源：nope"}`），不再把堆栈或英文异常甩到页面上。

## 数字（本机实测，2026-11-15）

| 项目 | 数值 |
|---|---|
| 源 | **7 个**（巨潮公告 / IT之家 / 少数派 / arXiv / arXiv-q-fin / 国家统计局 / 中国政府采购网） |
| 首轮全量抓取 | **约 5 秒**，入库 **100 条**（早期四源）；加两个政府源后又入库 46 条 |
| 第二轮（同样 5 秒） | 新增 **0** 条 —— 去重生效 |
| 数据库 | 156 KB → 加源后约 **200 KB**（SQLite，WAL） |
| 单元测试 | **316 个，126 秒全绿**（业务层 / 邮件推送 / 配置隔离 / 网页端接口 / 政府源解析 / 解析护栏 / PDF 抠字 / 备份恢复 / 跨源去重 / Excel / PWA 安装条件 / 桌面 12 页渲染 + 审计页） |
| 正文抠取 | 两条路：巨潮 PDF 走 `pdftext.py`（纯标准库，ToUnicode 还原汉字子集字体），IT之家/少数派/政采/统计局走 `enrich.html_to_text()`（去掉脚本、导航、面包屑、文末版权）。实测一轮补 30 条：抠到 **25 条 / 77307 字**，空正文 3、下载失败 2 |
| 跨源去重 | 标题归一后 Jaccard ≥ 0.82、72 小时内、跨源才判重；早的那条留、晚的标 `dup_of` |
| 备份 | `python run.py backup --keep 3` → `data/backups/backup-20261116-181845.db`（1068 KB）；启动时距上次超 20 小时自动备一次 |
| 抓取成功率（近 24 小时，83 次运行） | **98.8%**，去重率 **93.5%**，平均每次 **0.86 秒** |
| 任务队列 | 优先级领取、失败可重试、可取消；实测两条任务 `done`（cninfo 30 条 / 全量 100 条） |
| 并发写入 | 50 线程同批插入 10 条 → 库里正好 10 条，无 `database is locked` |
| 接口压测·宿主机（`python loadtest.py`） | 30 线程 × 20 次 = **600 请求 / 2.13 秒**，**281 QPS**，p50 **103 ms** / p95 **127 ms** / p99 **138 ms**，错误 **0** |
| 接口压测·容器（同一份代码） | **600 请求 / 10.05 秒**，**59.7 QPS**，p50 **466 ms** / p95 **1104 ms** / p99 **1290 ms**，错误 **0** —— Docker Desktop 的 Windows bind mount 让 SQLite 读慢 4–5 倍，这是实测差距，不是估算 |
| 接口触达率 | **78/78 = 100%**（`python tools/route_coverage.py`：跑一遍测试，统计 64 条路由里有几条真被请求过） |
| 进程内指标 | `GET /api/metrics` 实测 `{"requests":603,"by_status":{"200":602},"latency_ms":{"p50":441.5,"p95":1083.5,"p99":1264.0,"samples":602}}`（容器内，压力测试后读取） |
| 单文件 exe（PyInstaller） | `dist\SignalWatch.exe` **19.7 MB**，`serve --port 8899` 起来后 `/api/health` 返回 200；`desktop` 模式窗口能开（截图 `docs/shot-exe-desktop.png`） |
| 自动补正文 | 开启后每轮最多抠 2 条（默认 cninfo），累计库里 **155 / 1904 条**有正文（cninfo 146、ithome 7、sspai 2）。手动 `run.py enrich --limit 30` 可批量补；`--source` 留空就覆盖所有能补的源 |
| git | 已初始化，3 次提交，161 个文件进版本库（`.git` 17 MB），CI 配置在 `.github/workflows/tests.yml` |

## 跑起来

```bash
python run.py serve            # 网页 + API，只监听 127.0.0.1:8766
python run.py desktop          # 桌面常驻窗口（右下角弹窗）
python run.py crawl            # 立刻抓一轮
python run.py stats            # 库里多少条、源健不健康
python run.py worker                      # 不启界面，只领 jobs 表里的任务去抓
python run.py worker --once --recover     # 把中断的任务打回队列，干完就退出
python run.py enrich --limit 5            # 补 5 条正文（默认覆盖 PDF 与网页两类源）
python run.py enrich --limit 20 --source ithome   # 只补 IT之家
python run.py backup --keep 7  # 备份（另有 restore / backups）
python run.py migrate          # 看迁移台账（只读；--check 有缺步骤则退出 1）
```

Windows 上双击 `启动盯梢.bat` 就是桌面版；`install_autostart.ps1` 把它塞进开机启动。

## 注册 / 登录 / 找回口令

三种进入方式，都不需要我在旁边发口令：

| 场景 | 路径 | 说明 |
| --- | --- | --- |
| 新用户 | `/admin#/register` | 自助注册，默认 **viewer（只读）**；管理员在「用户」页提权 |
| 日常登录 | `/admin#/login` | 用户名 + 口令；连错 5 次锁 5 分钟（内存计数，重启即清） |
| 忘了口令 | `/admin#/forgot` | 填用户名或注册邮箱 → 邮件收 6 位验证码（10 分钟有效、最多试 5 次）→ 设新口令并自动登录 |

设计上刻意做的三件事：

1. **注册不给管理员权限**——自助注册默认只读，避免任何能上网的人拿到删数据的权限。
2. **验证码只存哈希**——`password_resets.code_hash` 是 sha256，泄露库也拿不到验证码；同一账号发新码时旧码立刻作废。
3. **改口令就踢掉旧会话**——`reset_password` 成功会把 `token_version + 1`，之前在别的设备上登录的令牌立刻失效。

邮件走「设置」页里配的 SMTP（`smtp.qq.com:465` + 授权码）。没配 SMTP 时 `/api/auth/forgot` 返回 `sent:false` 并说明原因，不会假装发出去了。

## 邮件推送

设置页最下面的「推送（邮件）」：填发件邮箱（QQ 邮箱用 `smtp.qq.com:465`）、**授权码**（不是登录密码，在 QQ 邮箱 → 设置 → 账户里生成）、收件邮箱，点「保存并发测试邮件」—— 能收到就说明通了。**网页管理台 → 设置**里同样能配，配完会显示「上次发送 时间 · 成功/失败原因」，失败不会闷在日志里。

多用户收摘要：管理台 → 用户，给每个人填邮箱并点「存邮箱」；之后摘要按人发，各自只收命中自己订阅词的条目。没填邮箱的账号不收。

之后每当有新条目，程序把最近 30 条拼成一封摘要（标题「SignalWatch：N 条新信息」，每条带来源、时间、公司、原文链接），**十分钟最多一封**，失败只记审计不弹窗。

凭据只存在本机数据库的 `settings` 表里（`data/` 已在 `.gitignore`），不进代码、不进包、不打日志。换人用就换授权码；不想推送就把「开关」关掉。

## 自己干活的三件事（不用你盯着）

**1. 新条目自动抠正文。** 巨潮的公告是 PDF（浏览器点开常常空白或要下载），IT之家/少数派是网页（正文被导航和广告裹着）。程序在每次抓到新条目后顺手抠 2 条：PDF 走文字层，网页走 `html_to_text()`（先砍 `<script>/<style>/<nav>`，只取 `<article>`，再切掉面包屑「首页 > A > B >」和文末「责任编辑/免责声明」那一段）。时间线卡片上就多一个「看正文（N 字）」按钮。开关在设置页「新条目自动抠正文」。实测一轮 30 条补出 25 条、77307 字。

**2. 源连续失败会发邮件。** 连续失败到达阈值（默认 3 次）就发一封告警，同一源 6 小时内只发一封，邮件里有最后一次错误和处置建议。没有配 SMTP 就只在页面显示红条，不会假装发了。开关在设置页「源连续失败时发邮件告警」。

**3. 关掉窗口也能抓。** 用 `install_task.ps1` 把抓取注册成 Windows 计划任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1        # 每 10 分钟
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Minutes 30
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Remove
```

它注册的任务是 `SignalWatch.exe crawl`（没有 exe 就退回 `run.py crawl`），抓完就退出，日志追加到 `data\crawl.log`，只在当前用户登录时运行、不需要管理员权限。注意：如果当前会话被限制（比如以受限令牌跑），`schtasks` 会报 `Access is denied`，那就自己开一个普通 PowerShell 窗口跑上面那条命令。

**本机现在用的是容器方式开机自启**（不再用 `run.py serve`，否则两者会抢 8766）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_docker.ps1
```

`start_docker.ps1` 干三件事：Docker Desktop 没起就先起（最多等 240 秒）→ 等守护进程可用 → `docker compose up -d`，日志写 `data\docker-start.log`。启动文件夹里的 `signalwatch.lnk` 就是它（隐藏窗口，不弹任何东西）。

想换回窗口版就改成 `install_autostart.ps1`（`-Mode desktop` 或 `-Mode serve`），但 **`-Mode serve` 会和容器抢 8766**，只能留一个。

## 版本管理与 CI

仓库是 git（`.git` 已初始化，第一次提交 161 个文件）。`.github/workflows/tests.yml` 跑三件事：

- **测试**：ubuntu + windows × Python 3.11/3.12，四个组合都跑 `unittest discover`，再跑一次 `run.py stats` 冒烟；
- **构建**：`python -m build` 出 wheel，再 `pip install --no-deps dist/*.whl` 验证装得上、`signalwatch/web` 里的静态文件进了包；
- **容器**：`docker build` + 容器里跑一次 CLI + `docker compose config` 校验。

本机真跑过（Docker Desktop 29.9.3 + WSL2 后端）：

| 检查项 | 实测 |
| --- | --- |
| 镜像 | `signalwatch:0.1.0` **215 MB**（基础 `python:3.11-slim`） |
| 多架构 | `docker buildx build --platform linux/arm64` 成功；容器内 `platform.machine()` = **aarch64**，`signalwatch.__version__` = 0.2.0（QEMU 模拟跑通） |
| 基础镜像换源 | `Dockerfile` 支持 `--build-arg BASE_IMAGE=...`；本机 `registry-1.docker.io` 是 NXDOMAIN，走 `m.daocloud.io/docker.io/library/python:3.11-slim` |
| 容器内装依赖 | `pip install .` **14.0 秒**（走清华源；默认 pypi.org 在这台机器会超时） |
| `docker compose up -d` | 容器 `Up (healthy)`，健康检查 200 |
| 接口 | `GET /api/health` → `{"ok":true,"version":"0.2.0"}`；`/api/stats` → 1593 条 |
| 容器内 CLI | `signalwatch stats` / `signalwatch crawl` 都正常，七个源全部 `[ok]`（ccgp 新增 20 条） |

两个坑：`registry-1.docker.io` 在这台机器 **DNS 直接 NXDOMAIN**，基础镜像得从 `m.daocloud.io` 拉再 `docker tag`；pypi.org 超时，所以 `Dockerfile` 加了 `ARG PIP_INDEX_URL`：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t signalwatch:0.1.0 .
```

## 接口

共 **78 条路由**（`tests/test_docs.py` 会核对：代码里每条路由都必须在下面出现，否则测试失败）。

**页面与 PWA**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 时间线（手机可装成 App） |
| GET | `/admin` | 管理台 |
| GET | `/index.css`、`/index.js`、`/admin.css`、`/admin.js` | 外置前端样式与脚本（CSP 不允许内联） |
| GET | `/assets/{path:path}`、`/admin/assets/{path:path}` | Vue 管理端构建产物（JS/CSS，带路径遍历防护） |
| GET | `/manifest.webmanifest`、`/sw.js` | PWA 清单与 Service Worker |
| GET | `/icon-192.png`、`/icon-512.png`、`/apple-touch-icon.png` | 图标 |
| GET | `/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect` | FastAPI 自带的交互式接口文档 |

**公开数据**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本、运行状态 |
| GET | `/api/metrics` | 进程内指标：请求数、RPS、p50/p95/p99、状态码分布、最热路径 |
| GET | `/api/items?source=&q=&limit=&since=` | 时间线（支持搜索；带上 `read`/`starred` 按当前用户算） |
| GET | `/api/items/{id}/text` | 某条的 PDF 正文（viewer+；没抠到就是空串） |
| GET | `/api/stats`、`/api/sources` | 统计 / 每个源的节奏与失败数 |
| GET | `/api/events/stats` | SSE，每 3 秒推一次计数（有新条目立刻推） |
| — | 所有响应 | 带 `X-Request-ID` 与 `X-Response-Time`，并附五个安全响应头 |
| POST | `/api/crawl` | 立刻抓一轮（analyst+；正在跑返回 409） |
| POST | `/api/items/{id}/read`、`/api/items/{id}/star` | 标记已读 / 收藏（viewer+，**按用户隔离**） |

**登录与用户**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login`、`/api/auth/logout` | 登录（同时下发 Secure-ready cookie 与 CSRF cookie）、退出 |
| POST | `/api/auth/register` | 自助注册（默认 viewer；填了邮箱才能找回口令）→ 201，同时下发 cookie 与 CSRF |
| POST | `/api/auth/forgot` | 给注册邮箱发 6 位验证码（10 分钟有效）；账号或邮箱不存在时 `sent:false`，不泄露差在哪 |
| POST | `/api/auth/reset` | 验证码 + 新口令改口令；成功后 `token_version+1`，旧令牌立刻失效 |
| GET | `/api/auth/mode` | 问服务端开没开免密进入：`{"auto_login":false,"auto_user":""}` |
| POST | `/api/auth/auto` | 免密进入（仅 `SIGNALWATCH_AUTO_LOGIN=1` 时可用，个人单机部署用；写审计 `auth.auto`） |
| GET/POST | `/api/admin/webhooks` | 事件推送订阅（企业微信/飞书/钉钉群机器人 URL），admin |
| DELETE | `/api/admin/webhooks/{wid}` | 删一条订阅 |
| GET/POST | `/api/admin/orgs` | 组织（多租户）列表与新建，admin |
| DELETE | `/api/admin/orgs/{oid}` | 删组织 |
| PATCH | `/api/admin/orgs/members` | 把用户挪进/挪出组织 |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/password` | 自己改口令（改完旧令牌作废）；默认 `admin123` 首次登录必须先改密 |

Cookie 认证的写请求必须带 `X-CSRF-Token`（值来自登录响应或 `sw_csrf` cookie）；Bearer 客户端不需要 CSRF 头。HTTPS 下两个 cookie 都带 `Secure`。
| GET/POST | `/api/users` | 用户列表 / 建用户（admin） |
| PATCH/DELETE | `/api/users/{name}` | 改角色、停用、改口令、改邮箱 / 删除（admin） |
| GET | `/api/admin/roles` | 角色清单与权限位 |
| GET/POST | `/api/admin/rules` | 订阅词列表 / 新增（关键词 + 分类 + 来源） |
| PATCH/DELETE | `/api/admin/rules/{id}` | 停用/启用 / 删除 |

**条目（管理）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/items?page=&size=&state=&category=&q=` | 分页列表（含已下架） |
| PATCH | `/api/admin/items/{id}` | 改标题/摘要/分类 |
| POST | `/api/admin/items/state` | 批量上架/下架 |
| POST | `/api/admin/items/delete` | 批量删除 |
| POST | `/api/admin/items/{id}/pin` | 置顶/取消置顶 |
| GET | `/api/admin/items/{id}/history` | 改动历史 + 相邻版本差异 |
| GET | `/api/admin/timeline?code=` | 某公司的时间线 |
| GET | `/api/admin/items.csv`、`/api/admin/items.xlsx` | 导出（CSV 带 BOM 且做了公式注入转义；xlsx 纯标准库生成） |

**分类 / 任务 / 报表 / 设置 / 审计**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/admin/categories` | 分类列表 / 新增 |
| PATCH | `/api/admin/categories/{id}` | 改名、停用、排序 |
| GET/POST | `/api/admin/jobs` | 任务队列 / 投递任务 |
| POST | `/api/admin/jobs/{id}/{act}` | `retry` 重试、`cancel` 取消 |
| GET | `/api/report?days=7`、`/api/report.xlsx?days=` | 报表（按天/源/分类/公司） |
| GET/PATCH | `/api/admin/settings` | 开关与参数 |
| GET/POST | `/api/admin/notify` | 读/存发件箱配置（口令不回显，可带 `send_test` 发测试信） |
| GET | `/api/audit`、`/api/admin/audit.csv` | 审计日志 / 导出 |
| POST | `/api/admin/audit/prune` | 删掉 N 天前的审计 |
| POST | `/api/admin/enrich?limit=&source=` | 现抠几条正文（analyst+） |
| PATCH | `/api/sources/{name}` | 改间隔、暂停/恢复（analyst+） |

## 加一个源

在 `src/signalwatch/sources/` 写一个类，实现 `fetch(cursor, limit) -> list[Record]`，
然后在 `sources/__init__.py` 的 `default_sources()` 登记一行。调度器、接口、页面都从这张表读。

## 部署给别人

完整步骤（三种跑法互斥、镜像中转、升级、排错表）见 [docs/DEPLOY.md](docs/DEPLOY.md)。

```bash
docker compose up --build      # http://127.0.0.1:8766
```

数据库挂到 `./data`，容器重启不丢；端口默认只绑本机。

## 手机装 PWA（需要 HTTPS）

PWA 的安装条件浏览器卡得很死：**必须是 https（或 localhost）**。手机连 `http://10.128.8.114:8766` 能看到页面，但装不了。

两条路，**推荐第一条**（容器里加一个只做 TLS 的 nginx，数据库仍然只有 app 容器在写）：

```powershell
powershell -ExecutionPolicy Bypass -File .\make_cert.ps1        # 生成 certs\cert.pem + key.pem（借 docker 里的 openssl）
docker compose --profile tls up -d                                # app:8766 + proxy:8443
```

不想用容器时，用内置的 `--tls`（注意：这时宿主机进程会成为第二个写者，**别和容器同时跑**）：

```powershell
python run.py serve --host 0.0.0.0 --port 8443 --tls
```

实测（本机，2026-11-17）：经 nginx 前置 `https://127.0.0.1:8443/api/health` 与 `https://10.128.8.114:8443/api/health` 都返回 `{"ok":true,"version":"0.2.0"}`；`manifest.webmanifest` 200 `application/manifest+json`；`sw.js` 200；SSE `GET /api/events/stats` 经前置也能持续推 `data: {"items": 1695, ...}`（`proxy_buffering off`）。

手机上的步骤：
1. 手机和电脑连同一个 Wi-Fi，浏览器开 `https://<电脑局域网IP>:8443`（图标已备好：`icon-192.png`、`icon-512.png`、`apple-touch-icon.png`，`manifest` 里 192/512 与 maskable 都声明了）；
2. 证书是自签的，会提示「不安全」→ 选「继续访问」；
3. 用支持 PWA 的浏览器打开，从菜单里装到主屏——这时才真的能装。

各浏览器怎么装（PWA 要浏览器认 `manifest` + Service Worker，还得把「安装」暴露在菜单里，所以不是所有浏览器都行）：

| 平台 | 浏览器 | 入口 |
| --- | --- | --- |
| Android | **Chrome** | 右上角 ⋮ → 「安装应用」/「添加到主屏幕」（多数机型会直接弹横幅） |
| Android | **Edge** | 右下角 `…` → 「添加到手机」；或在地址栏右侧点安装图标 |
| Android | **Samsung Internet** | 右下角 ≡ → 「添加页面到」→ 「主屏幕」 |
| Android | Firefox | 菜单 → 「安装」/「添加到主屏幕」（版本较新才有） |
| iOS / iPadOS | **Safari**（只有它） | 分享按钮 → 「添加到主屏幕」。iOS 上 Chrome/Edge 用的是 WebKit，加不了主屏 PWA，也拿不到推送权限 |
| 桌面 | Chrome / Edge | 地址栏右侧的安装图标，或 ⋮ → 「安装 SignalWatch」 |

装不了的：微信内置浏览器、QQ 浏览器、UC、夸克（多数基于 Chromium 但去掉了 `beforeinstallprompt`）、部分厂商自带浏览器。这些照样能看页面，只是没有桌面图标。

自签证书换机器/换 IP 要重新生成（`make_cert.ps1` 会自动把本机所有非虚拟网卡的 IPv4 写进 SAN）。想彻底没有警告，得用受信任的证书（内网 CA 或域名 + Let's Encrypt）。


### 同一份数据，只能有一个写者

`data/signalwatch.db` 是单文件 SQLite。下面三种用法**互斥**，同时开会烂库：

| 用法 | 起法 | 写者 |
| --- | --- | --- |
| 容器（推荐，开机自启） | `docker compose --profile tls up -d` | 只有 `signalwatch` 容器 |
| 桌面窗口 | `python run.py desktop` | 本机进程 |
| 本机网页 | `python run.py serve --tls` | 本机进程 |

要用桌面窗口，先 `docker compose down`；反过来也一样。这不是保守，是 2026-11-17 真撞出来的事故（下面这段）。

### 一次真实事故：容器里用 WAL 会把库写烂

用 `docker compose` 把 `./data` 目录 bind mount 进容器，同时宿主机也在写同一个库，就会撞上：

```
sqlite3.DatabaseError: database disk image is malformed
```

原因：WAL 模式下 `-wal`/`-shm` 由两套 OS 各自缓存，页顺序对不上。处置：

1. `docker compose down`，把坏库移到 `data/corrupt/`；
2. 用最近一份 `data/backups/backup-*.db` 覆盖回来（`PRAGMA integrity_check` 确认 `ok`）；
3. `docker-compose.yml` 里加 `SIGNALWATCH_JOURNAL: delete`（容器是唯一写者，回滚日志够用），重新 build；
4. 以后**不要在容器跑着的时候从宿主机直接改库**——要用 `docker compose exec signalwatch signalwatch ...`。

`src/signalwatch/db.py` 现在按 `SIGNALWATCH_JOURNAL` 决定 journal 模式（默认 WAL，Docker 里是 delete），`tests/test_core.py::JournalModeTests` 两条用例盯着这个行为。

## 任务六态（状态机在代码里，页面只是按钮）

任务不是「有状态字段」，而是有一张**允许迁移表**。落在 `admin.JOB_TRANSITIONS`：

```
queued    → running, cancelled
running   → done, failed, paused, cancelled
paused    → queued, cancelled
failed    → queued, cancelled
done      → （终态）
cancelled → （终态）
```

- 非法迁移抛 `JobStateError`，接口层转成 **409** 并带原因（例如「任务 #3 不能从「已取消」变成「排队中」」），不是 200 假装成功。
- 接口：`POST /api/admin/jobs/{id}/retry|cancel|pause|resume`；桌面任务页有暂停 / 继续 / 取消 / 重试四个按钮，网页任务页按当前状态只显示可用的那几个。
- 验证：`tests/test_admin.py::JobStateMachineTests`（8 例，含「done 是终态」「failed 只能重试不能完成」「未知状态/未知任务」）与 `tests/test_api_business.py::JobStateApiTests`（4 例，409 / 400 / 六态计数）。

## 10 万行量测（SQLite 能不能扛住）

`python tools/scale_probe.py 100000` 在临时目录造 10 万条，跑完删掉。数字（`p50`，20 次取中位）：

| 操作 | 耗时 | 说明 |
| --- | --- | --- |
| 首屏 30 条 | **0.4 ms** | `idx_items_pin_pub`（pinned, published_at, id） |
| 按公司搜索 | **0.4 ms** | LIKE 扫描，10 万行下仍是毫秒级 |
| 未读数 | **7.8 ms** | `marks` 按 user 索引 |
| 深翻页：`offset=90000` | **866 ms** | OFFSET 要先扫再丢，越翻越慢 |
| 深翻页：游标 `before_pub/before_id` | **53.7 ms** | 键集分页，常数级 |
| 7 天报表 | **300 ms** | 四个聚合；`by_source` 必须 `INDEXED BY idx_items_first_seen`，否则全扫 876 ms |
| 库文件 | **37.3 MB** | 10 万条 + 索引 |

两个结论：
1. **列表接口不要用 `page=3000`**。`GET /api/admin/items` 支持 `before_pub` + `before_id`（响应里带 `next_cursor`），深页用它，比 OFFSET 快 16 倍。
2. **`report()` 里的 `INDEXED BY` 不能删**。SQLite 会误选 `idx_items_source` 做全扫再分组（10 万行 876 ms），强制走 `first_seen_at` 索引后 24 ms。

## 迁移台账（这个库跑过哪几步）

`python run.py migrate` 打印每一步的版本、时间、说明：

```
数据库 D:\signalwatch\data\signalwatch.db
代码版本 10，台账最高 10，已记录 10 步
  v1  2026-11-17 21:03  初始表：sources / items / revisions / runs / schema_meta
  ...
  v9  2026-11-17 21:03  分类入库即归类：taxonomy.ensure_seeded + backfill
  v10 2026-11-17 21:03  users.must_change_password：默认口令首次登录强制修改
```

- 台账表 `schema_migrations(version, name, applied_at)`，由 `db.init_db()` 在迁移跑完后写入（老库第一次会一次性补记 1..N）；
- **这个命令是只读的**——写库只允许一个写者（容器或桌面窗口），所以它不会为了「修复」台账去动数据；
- `--check` 在台账缺步骤时退出 1，可以直接放进发布脚本；`--json` 给机器读。

CI 门禁在本机的演练见 [docs/CI.md](docs/CI.md)（没有 remote，GitHub 跑不了，本地把三步跑给你看）；
数字都在 [docs/BENCH.md](docs/BENCH.md)（抓取/正文/接口性能/压测/构建），
事故复盘在 [docs/RCA-wal-corruption.md](docs/RCA-wal-corruption.md)。

## 进程内缓存：同一秒的重复读不再打库

`/api/items`、`/api/stats`、`/api/sources`、报表这几个高频读走一个带锁的 TTL 缓存
（`src/signalwatch/cache.py`，默认 2 秒）。同实例同库 600 请求实测：缓存关 216.7 QPS，
开 **319.6 QPS、p50 90.6ms、命中 97.7%**。`SIGNALWATCH_CACHE_TTL=0` 可关（对照实验用），
`/api/metrics` 里看 `cache.hit_rate`。数字与「为什么不直接上 Redis」见 `docs/BENCH.md`。

## 独立 worker：关掉界面也能干活

任务队列（`jobs` 表）原来只有桌面窗口里那个线程在消费，窗口一关就没人领活。
`run.py worker` 解决这个：

```bash
python run.py worker                  # 常驻，一直领任务
python run.py worker --once --recover # 把上次中断的 running 打回队列，干完就退
```

- 领任务靠 `claim_job`（查一条 + 立刻置 `running` 在同一把锁里），两个 worker 不会领到同一条；
- 进程被 kill 时任务停在 `running`，`--recover` 会打回 `queued`，下次继续；
- 每次执行都写一条 `audit`（`job.run`，带耗时），页面上能查；
- 默认还会顺带做去重 + 抠 2 条正文 + 补分类，和界面里那个 worker 一致。

**注意单写者**：worker 和 serve/桌面窗口同时写同一个库会抢 SQLite 的锁。
容器里跑 worker 就用 `docker compose exec signalwatch signalwatch worker`，
不要宿主机再开一个。

实测（2026-11-17）：网页里排一条 `source:ithome` → 容器内 `signalwatch worker --once`
领走执行 → 库里 `status=done, result='ithome:ok 抓到 30 新增 1'`，耗时写在 audit 里 **4.36 秒**。

## 已知限制

- **源是别人给的，随时会变**。36kr 的 `/feed` 就悄悄换成了网页，现在会直接报错而不是静默空跑；
- XML 解析优先用 defusedxml；装不上时退回标准库但**拒绝**带 `DOCTYPE/ENTITY` 声明的 feed（fail closed，挡住 billion laughs）；
- 只存标题、摘要、链接；**抠到的正文会存进库**（`items.body_text`，单条最多 6000 字），不落原始 PDF/HTML 文件；自动补抓每轮只补 2 条，历史条目要自己跑 `run.py enrich`；网页正文是启发式抽取（不是 readability），少数版式会带上一点站点信息；
- 桌面端也要登录（可以「以访客身份进入」只读看板），改动都会记审计；
- 调度是进程内的：窗口和服务都关着就不抓。本机靠「开机 `start_docker.ps1` → 容器 `restart: unless-stopped`」兜住；计划任务那条路要管理员权限（本机 `schtasks` 被拒）；
- docker 本机验过镜像构建与 `compose up`，但**没在多机器/多架构上验**（只有这台 Windows + Docker Desktop）；
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
  enrich.py      抠正文（PDF 与网页两条路）写进库，顺带补摘要
  maintenance.py 备份 / 恢复 / 定期保留 / 启动自备
  dedupe.py      跨源近重复判定（Jaccard ≥ 0.82、72 小时窗、早的留）
  xlsx.py        纯标准库写 Excel（两份导出都用它）
  api.py         FastAPI 路由（页面 + JSON + 管理台 + SSE + Excel）
  desktop.py     tkinter 十二页常驻窗口 + 浮窗提醒
  sources/       cninfo / rss / arxiv / html_list（统计局、政府采购）
  web/           index.html（时间线）、admin.html（管理台）、manifest、sw.js
tests/           233 个用例（去重、并发、源解析、路由、权限、审计、报表、任务六态、邮件推送、配置隔离、PDF 抠字、备份恢复、跨源去重、Excel、PWA、桌面 12 页渲染）
docs/            验收截图
```




