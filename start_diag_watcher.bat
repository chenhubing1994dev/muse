@echo off
rem Start once and leave the window open: an agent can then request diagnostics by writing
rem reports\diag_request.flag. Only scripts\diag_auto.py is ever executed. Ctrl+C to stop.
cd /d "%~dp0"
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%~dp0src"
title Muse 诊断监听器（只会运行 scripts\diag_auto.py）
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY ( py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo [Muse] no usable Python 3.11+
  exit /b 2
)
if "%PY%"==".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\diag_watcher.py"
) else (
  %PY% "scripts\diag_watcher.py"
)
endlocal
