$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..\ui-prototype")
try {
  npm test
  if ($LASTEXITCODE -ne 0) { throw "UI API tests failed" }
  npm run check
  if ($LASTEXITCODE -ne 0) { throw "Svelte check failed" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "Vite production build failed" }
} finally { Pop-Location }
Push-Location (Join-Path $PSScriptRoot "..")$env:PYTHONPATH = (Join-Path $PSScriptRoot "..\src") + [IO.Path]::PathSeparator + $env:PYTHONPATH
Push-Location (Join-Path $PSScriptRoot "..")
try {
  py -3.11 -m unittest discover -s tests -p "test_video_human_review.py" -v
  if ($LASTEXITCODE -ne 0) { throw "visual review ledger tests failed" }
  py -3.11 -m unittest discover -s tests -p "test_status_api.py" -v
  if ($LASTEXITCODE -ne 0) { throw "status API tests failed" }
} finally { Pop-Location }
