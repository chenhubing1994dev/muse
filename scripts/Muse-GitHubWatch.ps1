# Muse -> GitHub realtime backup watcher
# Watches the workspace for file changes and pushes shortly after activity settles.
# Usage: powershell -File scripts/Muse-GitHubWatch.ps1 [-DebounceSeconds 20] [-Quiet]
param(
    [int]$DebounceSeconds = 20,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SyncScript = Join-Path $RepoRoot 'scripts\Muse-GitHubSync.ps1'
$LogDir = Join-Path $RepoRoot '.muse-sync'
$LogFile = Join-Path $LogDir 'watch.log'
$TokenFile = Join-Path $env:TEMP 'muse_pat.txt'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

function Write-Log([string]$msg) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    if (-not $Quiet) { Write-Host $line }
}

function Get-Token {
    if (Test-Path $TokenFile) {
        $t = (Get-Content $TokenFile -Raw).Trim()
        if ($t) { return $t }
    }
    if ($env:GH_TOKEN) { return $env:GH_TOKEN.Trim() }
    return $null
}

$token = Get-Token
if (-not $token) { Write-Log 'ERROR: no token; abort'; exit 1 }

# Ignore noisy paths that should never trigger a sync
$ignorePatterns = @(
    '\\\.git\\', '\\\.muse-sync\\', '\\node_modules\\', '\\__pycache__\\',
    '\\\.venv\\', '-venv\\', '\\target\\', '\\dist\\', '\\\.pytest_cache\\',
    '\\\.ruff_cache\\', '\\\.mypy_cache\\', '\\\.cache\\'
)

function Test-Ignored([string]$path) {
    foreach ($p in $ignorePatterns) { if ($path -match $p) { return $true } }
    return $false
}

function Invoke-SyncNow {
    Write-Log 'change detected -> syncing'
    $env:GH_TOKEN = $token
    & powershell -ExecutionPolicy Bypass -File $SyncScript -Once -Quiet 2>&1 | Out-Null
    $tail = Get-Content (Join-Path $LogDir 'sync.log') -Tail 2 -ErrorAction SilentlyContinue
    foreach ($l in $tail) { Write-Log "  $l" }
}

Write-Log "=== watcher started (debounce=${DebounceSeconds}s) ==="

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $RepoRoot
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, LastWrite, DirectoryName, Size'
$watcher.InternalBufferSize = 65536

$script:pending = $false
$script:lastEvent = Get-Date

$action = {
    $path = $Event.SourceEventArgs.FullPath
    $skip = $false
    foreach ($p in $script:ignorePatterns) { if ($path -match $p) { $skip = $true; break } }
    if (-not $skip) {
        $script:pending = $true
        $script:lastEvent = Get-Date
    }
}
$script:ignorePatterns = $ignorePatterns

Register-ObjectEvent -InputObject $watcher -EventName Changed -Action $action | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Created -Action $action | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Deleted -Action $action | Out-Null
Register-ObjectEvent -InputObject $watcher -EventName Renamed -Action $action | Out-Null
$watcher.EnableRaisingEvents = $true

# initial sync on start
Invoke-SyncNow

while ($true) {
    Start-Sleep -Seconds 5
    if ($script:pending) {
        $idle = ((Get-Date) - $script:lastEvent).TotalSeconds
        if ($idle -ge $DebounceSeconds) {
            $script:pending = $false
            try { Invoke-SyncNow } catch { Write-Log "EXCEPTION: $($_.Exception.Message)" }
        }
    }
}
