# 把「抓取」注册成 Windows 计划任务：关掉窗口、没开桌面程序也照抓。
#
# 用法（不需要管理员权限，只在当前用户登录时运行）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Minutes 30
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install_task.ps1 -Remove
#
# 它做的事：每 N 分钟跑一次 `crawl`（抓一轮就退出），日志追加到 data\crawl.log。
# 桌面程序里的定时线程是另一条路——两者可以同时存在，SQLite 有 WAL 兜底。

param(
    [int]$Minutes = 10,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "SignalWatch 抓取"

if ($Remove) {
    schtasks /delete /tn $taskName /f 2>$null | Out-Null
    Write-Host "已删除计划任务：$taskName"
    exit 0
}

# 优先用打包好的 exe（用户不用装 Python）；没有就退回 run.py
$exe = Join-Path $root "dist\SignalWatch.exe"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $exe) {
    $inner = "`"$exe`" crawl"
}
elseif (Test-Path $py) {
    $inner = "`"$py`" `"$root\run.py`" crawl"
}
else {
    $inner = "python `"$root\run.py`" crawl"
}

$log = Join-Path $root "data\crawl.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null

# cmd /c 是为了让 >> 追加日志生效：计划任务直接跑 exe 时没有 shell 做重定向
$cmd = "/c `"$inner >> `"$log`" 2>&1`""

schtasks /create /tn $taskName /tr "cmd $cmd" /sc minute /mo $Minutes /f | Out-Null

$q = schtasks /query /tn $taskName /fo list 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "创建失败：schtasks 返回 $LASTEXITCODE"
    exit 1
}

Write-Host "已注册计划任务：$taskName"
Write-Host "  频率：每 $Minutes 分钟"
Write-Host "  命令：$inner"
Write-Host "  日志：$log"
Write-Host ""
$q
