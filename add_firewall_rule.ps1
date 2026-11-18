# 以管理员身份运行：放开 8443 入站，并把当前 Wi-Fi 设为专用网络。
# 用途：手机要访问 SignalWatch 的 HTTPS 端口时，公共网络配置会拦入站。
$ErrorActionPreference = "Continue"

try {
    $rule = Get-NetFirewallRule -DisplayName "SignalWatch 8443" -ErrorAction SilentlyContinue
    if ($rule) {
        Write-Host "[skip] 规则已存在"
    } else {
        New-NetFirewallRule -DisplayName "SignalWatch 8443" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Any | Out-Null
        Write-Host "[ok] 已加规则：8443 入站放行"
    }
} catch {
    Write-Host "[错误] 加规则失败：$($_.Exception.Message)"
}

try {
    foreach ($p in (Get-NetConnectionProfile)) {
        if ($p.NetworkCategory -ne "Private" -and $p.IPv4Connectivity -eq "Internet") {
            Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
            Write-Host "[ok] $($p.Name) 改为专用网络"
        }
    }
} catch {
    Write-Host "[错误] 改网络类别失败：$($_.Exception.Message)"
}

Write-Host ""
# 现算地址，别写死
$lanIp = $null
foreach ($line in (ipconfig | Select-String "IPv4")) {
    if ($line.Line -match "(\d+\.\d+\.\d+\.\d+)") {
        $ip = $Matches[1]
        if ($ip -match "^(127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)") { continue }
        $lanIp = $ip
        break
    }
}
if ($lanIp) { Write-Host "完成。手机访问：https://$lanIp`:8443" } else { Write-Host "完成，但没检测到局域网 IP" }
Start-Sleep 3
