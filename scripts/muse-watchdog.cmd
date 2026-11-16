@echo off
setlocal
set "MUSE_ROOT=%~dp0.."
for %%I in ("%MUSE_ROOT%") do set "MUSE_ROOT=%%~fI"
set "PYTHONPATH=%MUSE_ROOT%\src"
"C:\Windows\py.exe" -3.14 -m muse.watchdog --db "%MUSE_ROOT%\data\tasks\muse.sqlite3" --reminder-db "%MUSE_ROOT%\data\reminders\muse.sqlite3" --once --report-dir "%MUSE_ROOT%\reports\half-hourly" --window-minutes 30
if errorlevel 2 exit /b %ERRORLEVEL%
"C:\Windows\py.exe" -3.14 -m muse.capability_radar --task-db "%MUSE_ROOT%\data\tasks\muse.sqlite3" --improvement-db "%MUSE_ROOT%\data\improvements\muse.sqlite3" --lookback-hours 24
if errorlevel 1 exit /b %ERRORLEVEL%
"C:\Windows\py.exe" -3.14 -m muse.reminders --db "%MUSE_ROOT%\data\reminders\muse.sqlite3" notify-due
exit /b %ERRORLEVEL%
