# 开机把「盯梢机器」以容器方式拉起来：Docker Desktop 没起就先起，等守护进程可用再 compose up -d。
# 用法（普通权限即可）：
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File .\start_docker.ps1
# 日志：data\docker-start.log

param([int]$WaitSeconds = 240)

$ErrorActionPreference = "Stop"
# docker CLI 会把进度写到 stderr，PowerShell 会当成错误抛出来——这段临时放宽
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$logDir = Join-Path $root "data"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "docker-start.log"

function Say($msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

$dockerCandidates = @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
)
$docker = $dockerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $docker) {
    Say "找不到 docker.exe，放弃"
    exit 1
}

$desktopExe = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $docker))) "Docker Desktop.exe"

function Daemon-Up {
    & $docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Daemon-Up)) {
    Say "守护进程没起，启动 Docker Desktop"
    if (Test-Path $desktopExe) {
        Start-Process -FilePath $desktopExe | Out-Null
    } else {
        Say "找不到 Docker Desktop.exe：$desktopExe"
        exit 1
    }
    $waited = 0
    while (-not (Daemon-Up)) {
        if ($waited -ge $WaitSeconds) {
            Say "等了 $WaitSeconds 秒守护进程还是没起来，放弃"
            exit 1
        }
        Start-Sleep -Seconds 5
        $waited += 5
    }
    Say "守护进程可用（等了 $waited 秒）"
}

$compose = Join-Path $root "docker-compose.yml"
if (-not (Test-Path $compose)) { $compose = Join-Path $root "compose.yml" }

& $docker compose -f $compose up -d 2>&1 | ForEach-Object { Say ([string]$_) }
if ($LASTEXITCODE -eq 0) {
    Say "compose up -d 完成：http://127.0.0.1:8766"
} else {
    Say "compose up -d 失败（exit $LASTEXITCODE）"
    exit 1
}
