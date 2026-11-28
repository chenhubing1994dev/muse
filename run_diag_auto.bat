@echo off
rem Muse non-interactive diagnostic entry: no pause, no prompts. Exit code 0 = all probes pass.
cd /d "%~dp0"
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%~dp0src"
set "MUSE_DIAG_NONINTERACTIVE=1"
if not exist "reports" mkdir "reports"
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY ( py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo [Muse] no usable Python 3.11+> "reports\diag_last_run.log"
  endlocal & exit /b 2
)
if "%PY%"==".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\diag_auto.py" %* < nul
) else (
  %PY% "scripts\diag_auto.py" %* < nul
)
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
