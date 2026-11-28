@echo off
cd /d "%~dp0"
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%~dp0src"
title Muse 一键启动（仅本机回环，不开局域网、不改防火墙、不动计划任务）
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY ( py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo [Muse] 没有找到可用的 Python 3.11+。
  pause
  exit /b 2
)
echo [Muse] Python：%PY%
echo [Muse] 1/3 统帅状态（任务、安全开关、完成凭证链）：
if "%PY%"==".venv\Scripts\python.exe" (".venv\Scripts\python.exe" -m muse.muse_commander status) else (%PY% -m muse.muse_commander status)
if not exist "ui-prototype\dist\index.html" (
  echo [Muse] 控制台界面尚未构建：请在 ui-prototype 目录执行 npm run build（需联网安装依赖，须你确认）。
  pause
  exit /b 3
)
if not exist "data\desktop" mkdir "data\desktop"
if exist "data\desktop\dashboard.json" del /q "data\desktop\dashboard.json"
echo [Muse] 2/3 启动本机控制台（新窗口，关闭该窗口即停止服务）...
if "%PY%"==".venv\Scripts\python.exe" (
  start "Muse Dashboard" cmd /k ""%~dp0.venv\Scripts\python.exe" -m muse.local_dashboard --root "%~dp0." --state-file "data\desktop\dashboard.json""
) else (
  start "Muse Dashboard" cmd /k "%PY% -m muse.local_dashboard --root "%~dp0." --state-file "data\desktop\dashboard.json""
)
echo [Muse] 3/3 等待控制台就绪...
for /l %%i in (1,1,30) do (
  if exist "data\desktop\dashboard.json" goto :ready
  timeout /t 1 /nobreak >nul
)
echo [Muse] 30 秒内未就绪，请查看 "Muse Dashboard" 窗口里的报错。
pause
exit /b 4
:ready
if "%PY%"==".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import json,webbrowser;u=json.load(open(r'data\desktop\dashboard.json',encoding='utf-8'))['url'];print('[Muse] 控制台：',u);webbrowser.open(u)"
) else (
  %PY% -c "import json,webbrowser;u=json.load(open(r'data\desktop\dashboard.json',encoding='utf-8'))['url'];print('[Muse] 控制台：',u);webbrowser.open(u)"
)
echo [Muse] 紧急停止：双击本目录 run_diag.bat 不会停止服务；请运行  %PY% -m muse.muse_commander kill --reason "manual"
pause
endlocal
