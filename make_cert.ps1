# 生成自签 TLS 证书（给手机 PWA 用）。
# 换网就重跑一次：它会枚举当前所有网卡 IPv4，全部写进 SAN。
# 依赖 docker（本机没有 openssl.exe，也没有 python cryptography）。

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$certs = Join-Path $root "certs"

# 从 ipconfig 里取 IPv4，排除回环与 Docker/WSL 虚拟网段（172.16-31、169.254）
$ips = @()
foreach ($line in (ipconfig | Select-String "IPv4")) {
    if ($line.Line -match "(\d+\.\d+\.\d+\.\d+)") {
        $ip = $Matches[1]
        if ($ip -match "^127\.") { continue }
        if ($ip -match "^169\.254\.") { continue }
        if ($ip -match "^172\.(1[6-9]|2[0-9]|3[0-1])\.") { continue }
        $ips += $ip
    }
}
if ($ips.Count -eq 0) {
    Write-Host "[错误] 没找到局域网 IP，检查是不是没连网"
    exit 1
}

$san = "DNS:localhost,DNS:signalwatch.local,IP:127.0.0.1"
foreach ($ip in $ips) { $san += ",IP:$ip" }

New-Item -ItemType Directory -Force -Path $certs | Out-Null

Write-Host "MAC 地址/IP 清单：$($ips -join ', ')"
Write-Host "SAN：$san"

docker run --rm -v "${certs}:/certs" python:3.11-slim sh -c "openssl req -x509 -newkey rsa:2048 -nodes -keyout /certs/key.pem -out /certs/cert.pem -days 825 -subj '/CN=signalwatch' -addext 'subjectAltName=$san'" 2>$null

if (-not (Test-Path (Join-Path $certs "cert.pem"))) {
    Write-Host "[错误] 证书没生成，检查 docker 是否在跑"
    exit 1
}

Write-Host "[ok] 证书已更新：$certs\cert.pem"
docker run --rm -v "${certs}:/certs" python:3.11-slim openssl x509 -in /certs/cert.pem -noout -text 2>$null |
    Select-String -Pattern "Subject Alternative Name" -Context 0,1

Write-Host ""
Write-Host "下一步：docker compose --profile tls up -d --force-recreate proxy"
