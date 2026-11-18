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

# 换网后 IP 会变，证书里的 SAN 不包含当前 IP 时浏览器会报「证书错误」，
# Chrome 在证书错误的页面上拒绝注册 Service Worker —— PWA 就装不了。
# 所以每次启动先对一遍：当前 IP 不在证书 SAN 里就重签（CA 复用，手机不用重装证书）。
$certPath = Join-Path $root "certs\cert.pem"
$lanIps = @()
foreach ($line in (ipconfig | Select-String "IPv4")) {
    if ($line.Line -match "(\d+\.\d+\.\d+\.\d+)") {
        $ip = $Matches[1]
        if ($ip -match "^(127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)") { continue }
        if ($lanIps -notcontains $ip) { $lanIps += $ip }
    }
}
if ((Test-Path $certPath) -and $lanIps.Count -gt 0) {
    $sanText = (& $docker run --rm -v "$root\certs:/certs" python:3.11-slim openssl x509 -in /certs/cert.pem -noout -ext subjectAltName 2>&1) -join " "
    $missing = @($lanIps | Where-Object { $sanText -notmatch [regex]::Escape($_) })
    if ($missing.Count -gt 0) {
        Say "证书里没有当前 IP（$($missing -join ', ')），重签服务器证书（CA 不变）"
        & (Join-Path $root "tools\make_ca.py") 2>&1 | ForEach-Object { Say ([string]$_) }
        $reissue = $true
    }
}

& $docker compose -f $compose --profile tls up -d 2>&1 | ForEach-Object { Say ([string]$_) }
if ($reissue) {
    & $docker compose -f $compose --profile tls up -d --force-recreate proxy 2>&1 | ForEach-Object { Say ([string]$_) }
}
if ($LASTEXITCODE -eq 0) {
    # 换网后 IP 会变，这里现算，不写死
    $lanIp = $null
    foreach ($line in (ipconfig | Select-String "IPv4")) {
        if ($line.Line -match "(\d+\.\d+\.\d+\.\d+)") {
            $ip = $Matches[1]
            if ($ip -match "^(127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)") { continue }
            $lanIp = $ip
            break
        }
    }
    Say "compose up -d 完成：本机 http://127.0.0.1:8766"
    if ($lanIp) {
        Say "手机访问：https://$lanIp`:8443 （手机需连同一个 Wi-Fi）"
        Say "换网后如果手机打不开：重新跑本脚本即可（它会比对证书 SAN 与当前 IP，不一致就用 tools/make_ca.py 自动重签，CA 不变、手机不用重装）"
    } else {
        Say "手机访问：没检测到局域网 IP，检查网络连接"
    }
} else {
    Say "compose up -d 失败（exit $LASTEXITCODE）"
    exit 1
}
