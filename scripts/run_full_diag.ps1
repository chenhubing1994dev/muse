# Muse one-click diagnostics: original smoke test + full-depth diagnostic, merged into
# reports\qw_smoke\latest.log, latest.json and diag_matrix.json.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\AI AGENT\Muse\scripts\run_full_diag.ps1"
#   ... -FullTests      complete unittest suite instead of the targeted set
#   ... -SmokeOnly      only scripts\muse_smoke.py (used by run_smoke.bat)
#   ... -LlmUrl http://127.0.0.1:1234/v1   optional real local-model turn
[CmdletBinding()]
param(
    [switch]$FullTests,
    [switch]$SmokeOnly,
    [switch]$Quick,
    [string]$LlmUrl = ""
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
. (Join-Path $PSScriptRoot "Resolve-MusePython.ps1")

$Reports = Join-Path $Root "reports\qw_smoke"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Console = Join-Path $Reports "console.log"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($Console, "", $Utf8)
$StartTime = Get-Date

function Write-Both([string]$Text, [string]$Color = "") {
    if ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
    [System.IO.File]::AppendAllText($Console, $Text + [Environment]::NewLine, $Utf8)
}

Write-Both "[Muse] root    : $Root"
$Py = Resolve-MusePython -Root $Root
foreach ($probe in $Py.Probes) { Write-Both "[Muse] probe   : $probe" }
if (-not $Py.Exe) {
    Write-Both "[Muse] No usable Python 3.11+ found (see probes above)." "Red"
    Copy-Item -LiteralPath $Console -Destination (Join-Path $Reports "latest.log") -Force
    exit 9009
}
$env:PYTHONPATH = $Py.PythonPath
Write-Both "[Muse] python  : $($Py.Label) -> $($Py.Executable) ($($Py.Version))"
Write-Both "[Muse] PYTHONPATH: $($Py.PythonPath)"
if (-not $Py.VenvHealthy) {
    Write-Both "[Muse] WARNING : .venv launcher is unusable; running with a fallback interpreter." "Yellow"
    Write-Both "[Muse]           Diagnose/repair (offline): powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\Repair-MuseVenv.ps1`"" "Yellow"
}

function Invoke-MusePython([string[]]$Arguments) {
    # Stream every stdout/stderr line to the console and to console.log (UTF-8, no BOM).
    $pre = @($Py.PreArgs)
    & $Py.Exe @pre @Arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        [System.IO.File]::AppendAllText($Console, $line + [Environment]::NewLine, $Utf8)
    }
    return $LASTEXITCODE
}

# 1) Original smoke test (S1-S9 + L layers), kept fully compatible.
$SmokeArgs = @((Join-Path $Root "scripts\muse_smoke.py"))
if ($FullTests) { $SmokeArgs += "--full" }
if ($Quick) { $SmokeArgs += "--quick" }
if ($LlmUrl) { $SmokeArgs += @("--llm-url", $LlmUrl) }
Write-Both "`n[Muse] step 1: scripts\muse_smoke.py" "Cyan"
$SmokeCode = Invoke-MusePython $SmokeArgs
$SmokeJson = Join-Path $Reports "smoke_part.json"
$SmokeLog = Join-Path $Reports "smoke_part.log"
$LatestJson = Join-Path $Reports "latest.json"
$Latest = Join-Path $Reports "latest.log"
$SmokeWrote = (Test-Path -LiteralPath $LatestJson) -and ((Get-Item -LiteralPath $LatestJson).LastWriteTime -ge $StartTime)
if ($SmokeWrote) {
    Copy-Item -LiteralPath $LatestJson -Destination $SmokeJson -Force
    Copy-Item -LiteralPath $Latest -Destination $SmokeLog -Force
}

$DiagCode = 0
if (-not $SmokeOnly) {
    # 2) Full-depth diagnostic (new layers), merging the smoke results instead of repeating them.
    $DiagArgs = @((Join-Path $Root "scripts\muse_full_diag.py"))
    if ($SmokeWrote) { $DiagArgs += @("--merge-smoke", $SmokeJson, "--merge-smoke-log", $SmokeLog) }
    if ($FullTests) { $DiagArgs += "--full-tests" }
    if ($Quick) { $DiagArgs += "--skip-tests" }
    Write-Both "`n[Muse] step 2: scripts\muse_full_diag.py" "Cyan"
    $DiagCode = Invoke-MusePython $DiagArgs
}

# If Python crashed before writing its report, keep the raw console output as latest.log;
# otherwise append the interpreter probes + any raw output to the structured report.
if (-not (Test-Path -LiteralPath $Latest) -or ((Get-Item -LiteralPath $Latest).LastWriteTime -lt $StartTime)) {
    Copy-Item -LiteralPath $Console -Destination $Latest -Force
} else {
    [System.IO.File]::AppendAllText($Latest, [Environment]::NewLine + "==================== RAW CONSOLE (interpreter probes + stdout/stderr) ====================" + [Environment]::NewLine + [System.IO.File]::ReadAllText($Console, $Utf8), $Utf8)
}

Write-Host ""
Write-Host "[Muse] smoke exit=$SmokeCode  full-diag exit=$DiagCode  python=$($Py.Label)"
Write-Host "[Muse] log    : $Latest"
if (-not $SmokeOnly) { Write-Host "[Muse] matrix : $(Join-Path $Reports 'diag_matrix.json')" }
if ($SmokeCode -eq 0 -and $DiagCode -eq 0) {
    Write-Host "[Muse] RESULT: ALL PASSED" -ForegroundColor Green
    exit 0
}
Write-Host "[Muse] RESULT: FAILED - see latest.log" -ForegroundColor Red
exit 1
