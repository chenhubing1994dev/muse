$ErrorActionPreference = "Continue"
$env:PYTHONPATH = (Join-Path $PSScriptRoot "..\src") + [IO.Path]::PathSeparator + $env:PYTHONPATH
Push-Location (Join-Path $PSScriptRoot "..")
try {
  py -3.11 -m py_compile src\muse\video_human_review.py src\muse\status_api.py 2>&1
  if ($LASTEXITCODE -ne 0) { throw "Python compile failed" }
  $pythonLog = Join-Path $PSScriptRoot "visual_revision_test_output.log"
  py -3.11 scripts\validate_visual_review_api.py > $pythonLog 2>&1
  $pythonExit = $LASTEXITCODE
  if (Test-Path $pythonLog) { Get-Content $pythonLog | Write-Output }
  if ($pythonExit -ne 0) { throw "Visual review and Status API tests failed; inspect scripts/visual_revision_test_output.log" }
  Remove-Item $pythonLog -Force -ErrorAction SilentlyContinue
  Push-Location ui-prototype
  try {
    npm test 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Node UI/API tests failed" }
    npm run check 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Svelte check failed" }
    npm run build 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Vite production build failed" }
  } finally { Pop-Location }
} finally { Pop-Location }
