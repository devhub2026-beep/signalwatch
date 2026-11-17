@echo off
rem double-click me: opens the desktop window
cd /d "%~dp0"
start "" "E:\.venv\FastAPIProject7\.venv\Scripts\pythonw.exe" "%~dp0run.py" desktop
