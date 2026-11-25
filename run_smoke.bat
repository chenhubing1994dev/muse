@echo off
rem Muse one-click smoke test. Delegates to scripts\run_full_diag.ps1, which probes a working
rem Python (the .venv launcher can exist yet fail with "No pyvenv.cfg file") and logs UTF-8.
rem   run_smoke.bat            smoke stages + targeted regression tests
rem   run_smoke.bat --full     smoke + full diagnostic + complete unittest suite
rem   run_smoke.bat --quick    smoke only, skip unittest stages
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "MUSE_PS_ARGS=-SmokeOnly"
if /i "%~1"=="--full" set "MUSE_PS_ARGS=-FullTests"
if /i "%~1"=="--quick" set "MUSE_PS_ARGS=-SmokeOnly -Quick"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_full_diag.ps1" %MUSE_PS_ARGS%
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
