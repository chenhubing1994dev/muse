# Muse one-shot repair + verification (2026-11-24)
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File "D:\AI AGENT\Muse\scripts\Repair-And-Verify.ps1"
# What it does (no network unless a venv must be rebuilt, no production DB writes, no GPU):
#   1. Re-points the isolated metaphysics venvs at the Python 3.11 that exists now
#      (their pyvenv.cfg still points at a deleted C:\Users\Administrator\...\Python311).
#   2. If a venv still cannot import its pinned engine, rebuilds it with the pinned version.
#   3. Runs the targeted regression tests, then the full unittest suite (UTF-8 log).
# Native commands are checked via $LASTEXITCODE; "Stop" would abort on harmless
# stderr output from python/pip under Windows PowerShell 5.1.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "== 1. Locate Python 3.11 ==" -ForegroundColor Cyan
$pyExe = (& py -3.11 -c "import sys;print(sys.executable)" | Select-Object -First 1)
if (-not $pyExe -or -not (Test-Path $pyExe.Trim())) { Write-Host "Python 3.11 not found via 'py -3.11'" -ForegroundColor Red; exit 1 }
$pyExe = $pyExe.Trim()
$pyHome = Split-Path $pyExe -Parent
Write-Host "Python 3.11: $pyExe"

$venvs = @(
  @{ name = "kinliuren-poc-venv";       pkg = "kinliuren==0.1.2.9" },
  @{ name = "qimen-poc-venv";           pkg = "qimen-rs==0.2.1" },
  @{ name = "ziwei-poc-venv";           pkg = "ziwei==0.4.1" },
  @{ name = "calendar-crosscheck-venv"; pkg = "" }
)

Write-Host "`n== 2. Repair isolated venvs ==" -ForegroundColor Cyan
foreach ($v in $venvs) {
  $venvRoot = "D:\MuseVenvs"
  New-Item -ItemType Directory -Force -Path $venvRoot | Out-Null
  $dir = Join-Path $venvRoot $v.name
  if (-not (Test-Path $dir)) { & py -3.11 -m venv $dir }
  $compat = Join-Path $root ("artifacts\" + $v.name)
  if (-not (Test-Path $compat)) { New-Item -ItemType Junction -Path $compat -Target $dir | Out-Null }
  $cfg = Join-Path $dir "pyvenv.cfg"
  if (-not (Test-Path $cfg)) { Write-Host "skip $($v.name): not present"; continue }
  Copy-Item $cfg "$cfg.bak-20261124" -Force
  $lines = Get-Content $cfg | ForEach-Object {
    if ($_ -match '^\s*home\s*=') { "home = $pyHome" }
    elseif ($_ -match '^\s*executable\s*=') { "executable = $pyExe" }
    else { $_ }
  }
  Set-Content -Path $cfg -Value $lines -Encoding ascii
  $vpy = Join-Path $dir "Scripts\python.exe"
  & $vpy -c "import sys" 2>$null
  $ok = ($LASTEXITCODE -eq 0)
  if ($ok -and $v.pkg) {
    $pkgName = ($v.pkg -split '==')[0]
    & $vpy -m pip show $pkgName *> $null
    $ok = ($LASTEXITCODE -eq 0)
  }
  if (-not $ok -and $v.pkg) {
    Write-Host "rebuilding $($v.name) with $($v.pkg) ..." -ForegroundColor Yellow
    & py -3.11 -m venv --clear $dir
    & $vpy -m pip install --disable-pip-version-check $v.pkg
    if ($LASTEXITCODE -ne 0) { Write-Host "  install failed for $($v.pkg)" -ForegroundColor Red; continue }
  }
  Write-Host ("OK   " + $v.name) -ForegroundColor Green
}

New-Item -ItemType Directory -Force -Path (Join-Path $root "outputs") | Out-Null

Write-Host "`n== 3. Targeted regression tests ==" -ForegroundColor Cyan
$targeted = "test_product_review test_product_feedback_import test_autonomous_research test_feedback_experiments test_inspiration test_kinliuren_provider test_qimen_provider test_ziwei_provider test_video_worker_reverification test_business_opportunities test_unified_runtime test_video_task_worker test_video_commander_admission"
$tlog = "outputs\targeted-tests-20261124.log"
cmd /c "py -3.11 -m unittest $targeted > $tlog 2>&1"
Get-Content $tlog -Tail 6

Write-Host "`n== 4. Full suite (several minutes) ==" -ForegroundColor Cyan
$flog = "outputs\full-suite-20261124-after.log"
cmd /c "py -3.11 -m unittest discover -s tests > $flog 2>&1"
$tail = Get-Content $flog -Tail 4
$tail
Select-String -Path $flog -Pattern '^(FAIL|ERROR): ' | ForEach-Object { $_.Line } | Sort-Object -Unique

Write-Host "`n== 5. Front-end checks (Node) ==" -ForegroundColor Cyan
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Write-Host "node not found - skipped (install Node.js LTS to enable)" -ForegroundColor Yellow
} else {
  $jsFail = 0
  Get-ChildItem -Path "web-portal","ui-prototype" -Recurse -Filter *.js |
    Where-Object { $_.FullName -notmatch '\\(node_modules|dist)\\' } |
    ForEach-Object {
      & node --check $_.FullName 2>&1 | Out-Null
      if ($LASTEXITCODE -ne 0) { $jsFail++; Write-Host ("SYNTAX FAIL " + $_.FullName) -ForegroundColor Red }
    }
  Write-Host "node --check failures: $jsFail"
  Push-Location "ui-prototype"
  $nlog = Join-Path $root "outputs\node-tests-20261124.log"
  cmd /c "node --test *.test.js > `"$nlog`" 2>&1"
  Pop-Location
  Get-Content $nlog | Select-String -Pattern '^# (tests|pass|fail)' | ForEach-Object { $_.Line }
}

Write-Host "`nBefore: Ran 2621, failures=14, errors=7, skipped=1" -ForegroundColor DarkGray
Write-Host "Logs: $tlog , $flog , outputs\node-tests-20261124.log" -ForegroundColor Green
