@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 "scripts\autopilot_stage2_client_and_github.py"
echo.
pause
