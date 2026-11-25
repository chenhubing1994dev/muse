$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $Root
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Muse Python environment not found: $Python" }
. (Join-Path $PSScriptRoot 'Resolve-MusePython.ps1')
$PythonProbe = Test-MusePython -Exe $Python
if (-not $PythonProbe.Ok) { throw "Muse .venv launcher is unusable ($($PythonProbe.Detail)). Repair offline: powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\Repair-MuseVenv.ps1`" -Apply" }
$env:PYTHONPATH = Join-Path $Root 'src'
$env:PYTHONUNBUFFERED = '1'
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$LanConfigDir = Join-Path $Root 'data\desktop'
New-Item -ItemType Directory -Force -Path $LanConfigDir | Out-Null
$PhysicalLan = Get-NetIPConfiguration | Where-Object {
    $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway -and $_.IPv4Address -and
    $_.NetAdapter.InterfaceDescription -notmatch 'Virtual|Hyper-V|VMware|VirtualBox|WSL|Docker|TAP|VPN|Tunnel|WireGuard|Mihomo'
} | Select-Object -First 1
if (-not $PhysicalLan) { throw 'Muse LAN sharing requires an active physical Ethernet/Wi-Fi adapter with an IPv4 gateway.' }
$LanIp = [string]$PhysicalLan.IPv4Address[0].IPAddress
if ($LanIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw 'Could not resolve a valid private LAN IPv4 address.' }
Set-Content -LiteralPath (Join-Path $LanConfigDir 'lan-address.txt') -Value $LanIp -Encoding ascii -NoNewline
$StateFile = Join-Path $Root 'data\desktop\dashboard.json'
& (Join-Path $PSScriptRoot 'Install-Muse-Tasks.ps1') | Out-Null
$ErrorActionPreference = 'Stop'
try {
    $rule = Get-NetFirewallRule -DisplayName 'Muse LAN Dashboard' -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -DisplayName 'Muse LAN Dashboard' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -RemoteAddress LocalSubnet -Profile Private -Description 'Paired Muse dashboard for private LAN devices only' | Out-Null
    }
} catch {
    Write-Warning 'Could not add the private-profile firewall rule. LAN access may be blocked by Windows Firewall; the dashboard remains local-only until the rule is added.'
}


# Reuse the user's already-configured local model supervisor when that optional task exists.
$modelTask = Get-ScheduledTask -TaskName 'MuseLocalModelSupervisor' -ErrorAction SilentlyContinue
if ($modelTask -and [string]$modelTask.State -ne 'Running') {
    Start-ScheduledTask -TaskName 'MuseLocalModelSupervisor' -ErrorAction SilentlyContinue
}

function Get-MuseRuntimeStatus {
    try {
        $output = & $Python -m muse.autonomous_runtime status 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($output -join [Environment]::NewLine) | ConvertFrom-Json)
    } catch { return $null }
}
function Get-MuseDashboardState {
    if (-not (Test-Path $StateFile)) { return $null }
    try {
        $candidate = Get-Content -Raw -Path $StateFile | ConvertFrom-Json
        $health = Invoke-RestMethod -Uri ($candidate.url.TrimEnd('/') + '/__muse__/health') -TimeoutSec 2
        $expectedLanUrl = 'http://{0}:{1}/' -f $LanIp, [int]$candidate.dashboard_port
        if ($health.status -eq 'ready' -and $health.pid -eq [int]$candidate.pid -and $health.lan_mode -eq $true -and @($health.lan_urls) -contains $expectedLanUrl) { return $candidate }
    } catch { }
    return $null
}

# Start/repair the model-free-capable local supervisor using a persistent on-demand Task Scheduler task.
$runtime = Get-MuseRuntimeStatus
if (-not ($runtime -and $runtime.runtime -and $runtime.runtime.running)) {
    $runtimeTask = Get-ScheduledTask -TaskName 'MuseAutonomousRuntime' -ErrorAction Stop
    if ([string]$runtimeTask.State -eq 'Running') {
        Stop-ScheduledTask -TaskName 'MuseAutonomousRuntime'
        Start-Sleep -Seconds 1
    }
    Start-ScheduledTask -TaskName 'MuseAutonomousRuntime'
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $runtime = Get-MuseRuntimeStatus
        if ($runtime -and $runtime.runtime -and $runtime.runtime.running) { break }
    }
}
if (-not ($runtime -and $runtime.runtime -and $runtime.runtime.running)) {
    throw "Muse autonomous runtime did not become healthy. Check $Logs\autonomous-runtime-task.log"
}

# The dashboard has a separate on-demand task so it survives this launcher and browser closure.
$dashboardState = Get-MuseDashboardState
if (-not $dashboardState) {
    $dashboardTask = Get-ScheduledTask -TaskName 'MuseDesktopDashboard' -ErrorAction Stop
    if ([string]$dashboardTask.State -eq 'Running') {
        Stop-ScheduledTask -TaskName 'MuseDesktopDashboard'
        Start-Sleep -Seconds 1
    }
    if (Test-Path $StateFile) { Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue }
    Start-ScheduledTask -TaskName 'MuseDesktopDashboard'
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $dashboardState = Get-MuseDashboardState
        if ($dashboardState) { break }
    }
}
if (-not $dashboardState) { throw "Muse dashboard did not start. Check $Logs\dashboard-task.log" }

# Open the real local dashboard in an Edge App window, falling back to the default browser.
$pf86 = (Get-Item 'env:ProgramFiles(x86)' -ErrorAction SilentlyContinue).Value
$edgeCandidates = @(
    $(if ($pf86) { Join-Path $pf86 'Microsoft\Edge\Application\msedge.exe' }),
    $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe' })
) | Where-Object { $_ -and (Test-Path $_) }
if ($edgeCandidates.Count -gt 0) {
    Start-Process -FilePath $edgeCandidates[0] -ArgumentList @("--app=$($dashboardState.url)") | Out-Null
} else {
    Start-Process -FilePath $dashboardState.url | Out-Null
}
