# 把「盯梢机器」设成开机自启：在启动文件夹放一个快捷方式。
# 用法（普通 PowerShell 即可）：powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
# 取消：删掉启动文件夹里的 signalwatch.lnk

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = "E:\.venv\FastAPIProject7\.venv\Scripts\pythonw.exe"
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "signalwatch.lnk"

if (-not (Test-Path $pythonw)) {
    Write-Error "找不到 pythonw：$pythonw（先确认解释器路径）"
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = "`"$root\run.py`" desktop"
$sc.WorkingDirectory = $root
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$sc.Description = "signalwatch 盯梢机器"
$sc.Save()

Write-Output "已安装开机自启：$lnk"
Write-Output "下次登录会自动打开窗口；现在想立刻开，双击 $root\启动盯梢.bat"
