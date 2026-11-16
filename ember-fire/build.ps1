param([switch]$SkipTTS)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Lib = "C:\Users\Administrator\lemo-opuscar"
$Py = Join-Path $Lib ".venv\Scripts\python.exe"
$Node = (Get-Command node).Source
$Ffmpeg = (Get-Command ffmpeg).Source
if (!(Test-Path $Py)) { throw "Lemo Python environment missing: $Py" }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "out"),(Join-Path $Root "voice"),(Join-Path $Root "stills") | Out-Null
if (!$SkipTTS) {
  & $Py (Join-Path $Lib "core\tts\tts_zh.py") (Join-Path $Root "voice\lines.json") (Join-Path $Root "voice") --voice zh-HK-HiuMaanNeural --rate=-8% --pitch=-4Hz
  if ($LASTEXITCODE -ne 0) { throw "Cantonese TTS failed with exit code $LASTEXITCODE" }
}
& $Py (Join-Path $Root "mix.py")
if ($LASTEXITCODE -ne 0) { throw "Audio mix failed with exit code $LASTEXITCODE" }
& $Node (Join-Path $Lib "core\render\still.mjs") $Root "2" "8" "14" "23" "32" "36" "42" --size 1080x1920 --out (Join-Path $Root "stills")
if ($LASTEXITCODE -ne 0) { throw "Style-frame render failed with exit code $LASTEXITCODE" }
& $Node (Join-Path $Lib "core\render\video.mjs") $Root --fps 24 --workers 3 --size 1080x1920 --out (Join-Path $Root "out\picture.mp4")
if ($LASTEXITCODE -ne 0) { throw "Frame render failed with exit code $LASTEXITCODE" }
& $Ffmpeg -y -i (Join-Path $Root "out\picture.mp4") -i (Join-Path $Root "out\mix.wav") -map 0:v:0 -map 1:a:0 -vf format=yuv420p -c:v libx264 -preset slow -crf 16 -r 24 -c:a aac -b:a 192k -ar 48000 -af "loudnorm=I=-14:TP=-1.2:LRA=11" -t 45 -movflags +faststart (Join-Path $Root "火种不灭.mp4")
if ($LASTEXITCODE -ne 0) { throw "Final mux failed with exit code $LASTEXITCODE" }
Copy-Item (Join-Path $Root "stills\t_42.jpg") (Join-Path $Root "poster.jpg") -Force
& $Py (Join-Path $Root "qa.py")
if ($LASTEXITCODE -ne 0) { throw "QA failed with exit code $LASTEXITCODE" }
& $Py (Join-Path $Root "package.py")
if ($LASTEXITCODE -ne 0) { throw "Packaging failed with exit code $LASTEXITCODE" }
Write-Host "DONE: $Root\火种不灭.mp4"
