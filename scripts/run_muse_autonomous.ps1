param(
    [ValidateRange(1, 60)]
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot

$pythonLauncher = Get-Command py.exe -ErrorAction Stop
$env:PYTHONPATH = (Join-Path $projectRoot "src") + [IO.Path]::PathSeparator + [string]$env:PYTHONPATH

try {
    $Host.UI.RawUI.WindowTitle = "Muse Autonomous Supervisor"
} catch {
    # A detached psmux window may not expose a mutable title.
}

# This is the existing Commander-owned supervisor, TaskQueue and RoutineStore.
# Do not start a second queue, worker daemon, or external model service here.
& $pythonLauncher.Source -3.11 -u -m muse.autonomous_runtime run --poll-seconds $PollSeconds
exit $LASTEXITCODE
