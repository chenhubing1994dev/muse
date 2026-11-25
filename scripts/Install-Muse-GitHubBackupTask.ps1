# Register Muse GitHub realtime backup as a logon scheduled task
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WatchScript = Join-Path $RepoRoot 'scripts\Muse-GitHubWatch.ps1'
$TaskName = 'Muse-GitHub-Realtime-Backup'

if (-not (Test-Path $WatchScript)) { throw "watcher script missing: $WatchScript" }

$argLine = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -DebounceSeconds 15 -Quiet' -f $WatchScript

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "removed existing task: $TaskName"
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Watches the Muse workspace and pushes changes to the private GitHub backup repo.' | Out-Null

Write-Host "registered task: $TaskName"

# Start it now
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 6

$info = Get-ScheduledTask -TaskName $TaskName
$run = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ("state={0} lastResult={1}" -f $info.State, $run.LastTaskResult)
