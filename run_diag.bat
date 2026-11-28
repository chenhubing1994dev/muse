@echo off
cd /d "%~dp0"
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONPATH=%~dp0src"
title Muse 分层诊断 L1-L7（只读诊断，不提交、不上传、不删除用户数据）
echo [Muse] 项目目录："%CD%"
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [Muse] 没有找到可用的 Python 3.11+（.venv 启动器可能损坏：见 scripts\Repair-MuseVenv.ps1）。
  pause
  exit /b 2
)
echo [Muse] 使用的 Python：%PY%
if not exist "reports\muse_diag" mkdir "reports\muse_diag"
if "%PY%"==".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\muse_diag.py" %*
) else (
  %PY% "scripts\muse_diag.py" %*
)
set "RC=%ERRORLEVEL%"
echo.
echo [Muse] 报告："%CD%\reports\muse_diag\latest.log"  与  latest.json
if "%RC%"=="0" (echo [Muse] 全部探针 0 FAIL / 0 ERROR。) else (echo [Muse] 存在 FAIL/ERROR，回复 AWS Quick“继续”即可按根因修复。)
pause
endlocal & exit /b %RC%
