@echo off
setlocal
set "MUSE_ROOT=%~dp0.."
set "PYTHONPATH=%MUSE_ROOT%\src;%PYTHONPATH%"
py -3.11 -m muse.video_factory %*
exit /b %ERRORLEVEL%
