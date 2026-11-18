# 本机跑一遍 CI 里那几步（GitHub Actions 编排的是同样的命令）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\ci_local.ps1
$ErrorActionPreference = "Continue"
$py = if ($env:SW_PY) { $env:SW_PY } else { "python" }
$home_ = Join-Path $env:TEMP ("sw-ci-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$env:SIGNALWATCH_HOME = $home_
$fail = 0

function Step($name, $block) {
    Write-Host "== $name"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $block
    $code = $LASTEXITCODE
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    if ($code -eq 0) { Write-Host "   ok ($secs s)" }
    else { Write-Host "   FAILED (exit $code, $secs s)"; $script:fail++ }
}

Step "单元测试" { & $py -m unittest discover -s tests -t . 2>&1 | Select-Object -Last 3 }
Step "统计冒烟（空库自建）" { & $py run.py stats 2>&1 | Select-Object -Last 1 }
Step "迁移台账 --check（只读）" { & $py run.py migrate --check 2>&1 | Select-Object -Last 1 }
Step "接口触达率" { & $py tools/route_coverage.py 2>&1 | Select-Object -Last 2 }
Step "打 wheel" {
    if (Test-Path dist) { Get-ChildItem dist -Filter *.whl | Remove-Item -Force }
    # 必须在项目外执行：项目里 PyInstaller 留下的 build\ 目录会遮蔽 build 包
    Push-Location $env:TEMP
    & $py -m build --wheel --no-isolation --outdir "D:\signalwatch\dist" "D:\signalwatch" 2>&1 | Select-Object -Last 2
    Pop-Location
}
Step "装 wheel（--no-deps）并验静态文件在包内" {
    $whl = Get-ChildItem dist -Filter *.whl | Select-Object -First 1
    & $py -m pip install --no-index --no-deps --force-reinstall $whl.FullName 2>&1 | Select-Object -Last 1
    & $py tools/check_wheel.py
}

Remove-Item -Recurse -Force $home_ -ErrorAction SilentlyContinue
Write-Host ""
if ($fail -eq 0) { Write-Host "CI 本机演练：全部通过" } else { Write-Host "CI 本机演练：$fail 步失败"; exit 1 }
