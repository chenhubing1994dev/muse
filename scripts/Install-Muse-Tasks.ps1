$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonW = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $PythonW)) { throw "Muse pythonw.exe not found: $PythonW" }
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Definitions = @(
    @{ Name = 'MuseAutonomousRuntime'; Entry = (Join-Path $PSScriptRoot 'muse_runtime_entry.py'); Description = 'Muse on-demand local autonomous research and maintenance runtime' },
    @{ Name = 'MuseDesktopDashboard'; Entry = (Join-Path $PSScriptRoot 'muse_dashboard_entry.py'); Description = 'Muse on-demand paired private-LAN dashboard on TCP 8766; API remains loopback-only' }
)
foreach ($definition in $Definitions) {
    $arguments = '"' + $definition.Entry + '"'
    $existing = Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue
    $alreadyCorrect = (
        ($null -ne $existing) -and
        ($existing.Actions.Count -eq 1) -and
        ($existing.Actions[0].Execute -eq $PythonW) -and
        ($existing.Actions[0].Arguments -eq $arguments) -and
        ($existing.Principal.UserId -eq $CurrentUser) -and
        ([string]$existing.Principal.LogonType -eq 'Interactive') -and
        ([string]$existing.Principal.RunLevel -eq 'Limited')
    )
    if ($alreadyCorrect) { continue }
    $action = New-ScheduledTaskAction -Execute $PythonW -Argument $arguments -WorkingDirectory $Root
    $task = New-ScheduledTask -Action $action -Principal $Principal -Settings $Settings -Description $definition.Description
    Register-ScheduledTask -TaskName $definition.Name -InputObject $task -Force | Out-Null
}
Get-ScheduledTask -TaskName 'MuseAutonomousRuntime','MuseDesktopDashboard' | Select-Object TaskName,State | ConvertTo-Json -Depth 3
