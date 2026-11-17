# 生成一张自签证书，给「手机装 PWA / 局域网 https 访问」用。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File .\make_cert.ps1
# 产物：certs\cert.pem（证书）、certs\key.pem（私钥），有效期 10 年。
# 说明：本机没有 openssl，也没有 cryptography 包，所以借用 python:3.11-slim 镜像里的 openssl。

param([string]$ExtraIp = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$dockerCandidates = @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
)
$docker = $dockerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $docker) { throw "找不到 docker.exe" }

$ips = @("127.0.0.1")
$lan = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" -and $_.InterfaceAlias -notlike "*WSL*" -and $_.InterfaceAlias -notlike "vEthernet*" } |
    Select-Object -ExpandProperty IPAddress
$ips += $lan
if ($ExtraIp) { $ips += $ExtraIp }
$ips = $ips | Select-Object -Unique

$san = ($ips | ForEach-Object { "IP:$_" }) -join ","
$san = "DNS:localhost,DNS:signalwatch.local,$san"
Write-Output "证书 SAN：$san"

New-Item -ItemType Directory -Force -Path "certs" | Out-Null
& $docker run --rm -v "${root}\certs:/certs" python:3.11-slim sh -c "openssl req -x509 -newkey rsa:2048 -nodes -keyout /certs/key.pem -out /certs/cert.pem -days 3650 -subj '/CN=signalwatch' -addext 'subjectAltName=$san'"
if ($LASTEXITCODE -ne 0) { throw "openssl 生成失败" }

Write-Output "已生成：$root\certs\cert.pem、certs\key.pem"
Write-Output "起服务：python run.py serve --host 0.0.0.0 --tls"
