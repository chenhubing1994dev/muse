# Diagnose and (only with -Apply) repair the Muse .venv launcher. Offline: no download, no pip.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File "<MuseRoot>\scripts\Repair-MuseVenv.ps1"          # diagnose only
#   powershell -NoProfile -ExecutionPolicy Bypass -File "<MuseRoot>\scripts\Repair-MuseVenv.ps1" -Apply   # repair
#
# Repair = "<base python> -m venv --upgrade --prompt muse-personal-agent .venv": rewrites the
# launcher (python.exe / pythonw.exe) and pyvenv.cfg from the venv's own base interpreter and
# keeps Lib\site-packages (installed packages, editable muse install) untouched. The previous
# launcher files and pyvenv.cfg are copied to .qw-backups\venv-<timestamp>\ first.
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Resolve-MusePython.ps1')
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$venv = Get-MuseVenvConfig -Root $Root
$launcher = Join-Path $venv.VenvDir 'Scripts\python.exe'
Write-Host "[Muse] venv     : $($venv.VenvDir)"
Write-Host "[Muse] pyvenv   : home=$($venv.Home) version=$($venv.Version)"
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "[Muse] launcher : missing ($launcher)" -ForegroundColor Red
} else {
    $probe = Test-MusePython -Exe $launcher
    if ($probe.Ok) {
        Write-Host "[Muse] launcher : OK $($probe.Version) -> $($probe.Executable)" -ForegroundColor Green
        Write-Host "[Muse] nothing to repair."
        exit 0
    }
    Write-Host "[Muse] launcher : BROKEN ($($probe.Detail))" -ForegroundColor Red
}

# Base interpreter: pyvenv.cfg home first, then py -<venv version>.
$base = $null
if ($venv.Home) {
    $candidate = Join-Path $venv.Home 'python.exe'
    if ((Test-Path -LiteralPath $candidate) -and (Test-MusePython -Exe $candidate).Ok) { $base = $candidate }
}
if (-not $base -and $venv.Version -and (Get-Command py -ErrorAction SilentlyContinue)) {
    $probe = Test-MusePython -Exe 'py' -PreArgs @("-$($venv.Version)")
    if ($probe.Ok) { $base = $probe.Executable }
}
if (-not $base) {
    Write-Host "[Muse] No usable base Python $($venv.Version) found (home=$($venv.Home))." -ForegroundColor Red
    Write-Host "[Muse] Install/repair Python $($venv.Version) first (this needs your approval: it is a download/installation)."
    exit 3
}
$baseVersion = (Test-MusePython -Exe $base).Version
Write-Host "[Muse] base     : $base ($baseVersion)"
if ($venv.Version -and -not $baseVersion.StartsWith($venv.Version + '.')) {
    Write-Host "[Muse] base version $baseVersion differs from venv $($venv.Version); installed compiled packages may not load." -ForegroundColor Yellow
}

$command = "`"$base`" -m venv --upgrade --prompt muse-personal-agent `"$($venv.VenvDir)`""
if (-not $Apply) {
    Write-Host ""
    Write-Host "[Muse] Dry run. To repair, re-run with -Apply. It will execute (offline):"
    Write-Host "       $command"
    exit 2
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Root ".qw-backups\venv-$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($name in @('Scripts\python.exe', 'Scripts\pythonw.exe', 'pyvenv.cfg')) {
    $source = Join-Path $venv.VenvDir $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $backup ($name -replace '\\', '_')) -Force
    }
}
Write-Host "[Muse] backup   : $backup"
$ErrorActionPreference = 'Continue'
& $base -m venv --upgrade --prompt muse-personal-agent $venv.VenvDir
$code = $LASTEXITCODE
$after = Test-MusePython -Exe $launcher
if ($code -eq 0 -and $after.Ok) {
    Write-Host "[Muse] repaired : $($after.Version) -> $($after.Executable)" -ForegroundColor Green
    exit 0
}
Write-Host "[Muse] repair failed (venv exit=$code; probe: $($after.Detail)). Originals are in $backup" -ForegroundColor Red
exit 1
