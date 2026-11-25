@echo off
rem Console wrapper for the Muse GitHub realtime backup watcher.
rem Launched by muse-github-watch-hidden.vbs with a hidden console so the
rem task never flashes a conhost window.
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Muse-GitHubWatch.ps1" -DebounceSeconds 15 -Quiet
exit /b %ERRORLEVEL%
