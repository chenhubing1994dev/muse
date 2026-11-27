@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
title Muse B391 验收（只验收，不提交、不上传）
echo.
echo ==============================================
echo   Muse B391 一键验收（B391-P2 装甲版）
echo   环境快照 + 测试 + 契约核验 + 构建界面 + 一条真实本地样片
echo   不提交 Git、不推送、不删除数据
echo ==============================================
echo.
echo [Muse] 项目目录：%CD%
set "PY="
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
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
if not exist "reports\b391_acceptance" mkdir "reports\b391_acceptance"
%PY% "scripts\b391_verify.py" --sample
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [Muse] 全部通过。报告：reports\b391_acceptance\LATEST_REPORT.md
) else (
  echo [Muse] 有项目未通过。报告：reports\b391_acceptance\LATEST_REPORT.md
  echo [Muse] 对 AWS Quick 说“看验收报告”即可定位根因。
)
pause
exit /b %RC%
