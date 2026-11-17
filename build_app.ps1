# 把源码打成一个能双击运行的桌面程序：D:\Apps\SignalWatch
#
# 用法：powershell -ExecutionPolicy Bypass -File build_app.ps1
# 产物：D:\Apps\SignalWatch\SignalWatch.exe（桌面快捷方式自动建）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$out  = "D:\Apps\SignalWatch"
$py   = "E:\.venv\FastAPIProject7\.venv\Scripts\pythonw.exe"

# 防呆：绝不能把项目自己的目录当产物目录删掉（D:\SignalWatch 与 D:\signalwatch 在 Windows 上是同一个）
if ((Resolve-Path $root).Path -ieq ((Join-Path "D:\Apps" "SignalWatch"))) {
    throw "产物目录和项目目录重合了，停下"
}

Write-Host "[1/6] 编译启动器"
Push-Location (Join-Path $root "launcher")
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /platform:anycpu /win32icon:"$root\assets\app.ico" /out:SignalWatch.exe SignalWatch.cs | Out-Null
Pop-Location

Write-Host "[2/6] 清空 $out"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null

Write-Host "[3/6] 拷源码"
Copy-Item (Join-Path $root "src") -Destination $out -Recurse
Copy-Item (Join-Path $root "run.py") -Destination $out
Copy-Item (Join-Path $root "launcher\SignalWatch.exe") -Destination $out
Copy-Item (Join-Path $root "assets\app.ico") -Destination $out
Copy-Item (Join-Path $root "make_cert.ps1") -Destination $out
Copy-Item (Join-Path $root "install_autostart.ps1") -Destination $out
Copy-Item (Join-Path $root "start_docker.ps1") -Destination $out
Copy-Item (Join-Path $root "docker-compose.yml") -Destination $out
Copy-Item (Join-Path $root "nginx-tls.conf") -Destination $out
Copy-Item (Join-Path $root "Dockerfile") -Destination $out
New-Item -ItemType Directory -Path (Join-Path $out "data") -Force | Out-Null

Write-Host "[4/6] 写配置"
Set-Content -Path (Join-Path $out "python.txt") -Value $py -Encoding utf8

$readme = @(
  "SignalWatch 0.2.0 - 盯梢机器（绿色版）",
  "",
  "双击 SignalWatch.exe 打开桌面窗口；右下角会弹新条目提醒。",
  "管理员后台：先双击 启动网页版.bat，再打开 http://127.0.0.1:8766/admin",
  "  口令在 data\admin-password.txt（第一次是自动生成的，改完可以删掉那个文件）",
  "",
  "手机装 PWA：双击 启动手机版(HTTPS).bat，用手机开 https://<电脑局域网IP>:8443 -> 添加到主屏幕",
  "",
  "数据都在 data\ 下：signalwatch.db 是条目库，可直接备份。",
  "卸载：删掉整个文件夹；开机自启在 启动 文件夹里的 signalwatch.lnk。"
) -join "`r`n"
Set-Content -Path (Join-Path $out "说明.txt") -Value $readme -Encoding utf8

$serveBat = @(
  "@echo off",
  "cd /d ""%~dp0""",
  "start """" SignalWatch.exe serve",
  "timeout /t 3 >nul",
  "start """" http://127.0.0.1:8766/admin"
) -join "`r`n"
Set-Content -Path (Join-Path $out "启动网页版.bat") -Value $serveBat -Encoding utf8

$tlsBat = @(
  "@echo off",
  "cd /d ""%~dp0""",
  "if not exist certs\cert.pem (echo 先跑 make_cert.ps1 生成证书 & pause & exit /b 1)",
  "echo 用容器方式起（app + nginx 前置），数据库只由 app 容器写...",
  "start """" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""%~dp0start_docker.ps1""",
  "timeout /t 20 >nul",
  "ipconfig | findstr /i ""IPv4"""
) -join "`r`n"
Set-Content -Path (Join-Path $out "启动手机版(HTTPS).bat") -Value $tlsBat -Encoding utf8

$uninstall = @(
  "@echo off",
  "del ""%USERPROFILE%\Desktop\SignalWatch.lnk"" 2>nul",
  "del ""%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\signalwatch.lnk"" 2>nul",
  "echo 快捷方式和开机自启已删除；数据仍在 data 目录。",
  "pause"
) -join "`r`n"
Set-Content -Path (Join-Path $out "卸载.bat") -Value $uninstall -Encoding utf8

Write-Host "[5/6] 桌面快捷方式"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "SignalWatch.lnk"))
$lnk.TargetPath = Join-Path $out "SignalWatch.exe"
$lnk.WorkingDirectory = $out
$lnk.IconLocation = (Join-Path $out "app.ico")
$lnk.Description = "SignalWatch 盯梢机器"
$lnk.Save()

Write-Host "[6/6] 完成"
Get-ChildItem $out | Select-Object Name, Length | Format-Table -AutoSize
$mb = (Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("总大小: " + [math]::Round($mb, 1) + " MB")
