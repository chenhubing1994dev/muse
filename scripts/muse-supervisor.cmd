@echo off
rem Console wrapper for the Muse local model supervisor.
rem Launched by muse-supervisor-hidden.vbs with a hidden console so the
rem black console window does not sit on the desktop.
setlocal
cd /d "D:\AI AGENT\Muse"
"D:\AI AGENT\Muse\.venv\Scripts\python.exe" -m muse.model_supervisor --workspace "D:\AI AGENT\Muse"
exit /b %ERRORLEVEL%
