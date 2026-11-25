@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Muse 一键验收并保存核心代码
echo.
echo ==============================================
echo   Muse 一键验收并保存核心代码
echo   只在本机运行：不上传、不发布、不花钱、不删除数据
echo ==============================================
echo.
set "PY="
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
if not defined PY (
  echo [Muse] 项目自带的 Python 环境无法启动，正在自动修复（会先自动备份）……
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\Repair-MuseVenv.ps1" -Apply
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [Muse] 没有找到 Python 3.11 或更高版本。请把这个窗口截图发给 AWS Quick。
  pause
  exit /b 2
)
echo [Muse] 使用的 Python：%PY%
%PY% "scripts\autopilot_stage1_verify_and_commit.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [Muse] 全部完成。可以关闭这个窗口了。
) else (
  echo [Muse] 已完成，但有项目未通过。报告在 reports\autopilot\latest.md，下次对 AWS Quick 说“看一下验收报告”。
)
pause
exit /b %RC%
