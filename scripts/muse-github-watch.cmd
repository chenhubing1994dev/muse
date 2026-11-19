@echo off
rem Console wrapper for the Muse GitHub realtime backup watcher.
rem Launched by muse-github-watch-hidden.vbs with a hidden console so the
rem task never flashes a conhost window.
setlocal
cd /d "D:\AI AGENT\Muse"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\AI AGENT\Muse\scripts\Muse-GitHubWatch.ps1" -DebounceSeconds 15 -Quiet
exit /b %ERRORLEVEL%
