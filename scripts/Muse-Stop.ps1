$ErrorActionPreference = 'Continue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $Root 'src'
if (Test-Path $Python) {
    & $Python -m muse.autonomous_runtime stop | Out-Null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        $statusText = & $Python -m muse.autonomous_runtime status 2>$null
        if ($LASTEXITCODE -eq 0) {
            $status = ($statusText -join [Environment]::NewLine) | ConvertFrom-Json
            if (-not $status.runtime.running) { break }
        }
    }
}
try { Stop-ScheduledTask -TaskName 'MuseDesktopDashboard' -ErrorAction SilentlyContinue } catch { }
try { Stop-ScheduledTask -TaskName 'MuseAutonomousRuntime' -ErrorAction SilentlyContinue } catch { }
$StateFile = Join-Path $Root 'data\desktop\dashboard.json'
if (Test-Path $StateFile) {
    try {
        $state = Get-Content -Raw -Path $StateFile | ConvertFrom-Json
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction Stop
        if ($proc -and $proc.CommandLine -match 'muse_dashboard_entry|muse\.local_dashboard') {
            Stop-Process -Id ([int]$state.pid) -Force -ErrorAction Stop
        }
    } catch { }
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
}
