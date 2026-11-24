# Render 3 x 55s vertical campaign videos with local FFmpeg/libx264.
# Usage (from Muse root):  powershell -ExecutionPolicy Bypass -File .\scripts\Render-Campaign-Videos.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = "src"
$out = "outputs\campaign_videos_20261124_v1"
if (Test-Path $out) { Write-Host "$out already exists - refusing to overwrite. Rename it first."; exit 1 }
py -3.11 scripts\build_campaign_videos_v1.py
Write-Host "`nDone: $out" -ForegroundColor Green
explorer $out
