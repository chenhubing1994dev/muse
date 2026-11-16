@echo off
setlocal
set "COMFY_ROOT=D:\AI\ComfyUI-Rinsei\portable\ComfyUI_windows_portable"
if not exist "%COMFY_ROOT%\python_embeded\python.exe" exit /b 2
if not exist "%COMFY_ROOT%\ComfyUI\main.py" exit /b 3
cd /d "%COMFY_ROOT%" || exit /b 4
"%COMFY_ROOT%\python_embeded\python.exe" -s "%COMFY_ROOT%\ComfyUI\main.py" --windows-standalone-build --cpu-vae --listen 127.0.0.1 --disable-auto-launch 1>>"%COMFY_ROOT%\muse-comfyui.stdout.log" 2>>"%COMFY_ROOT%\muse-comfyui.stderr.log"
exit /b %ERRORLEVEL%
