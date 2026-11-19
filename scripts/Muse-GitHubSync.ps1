# Muse -> GitHub auto backup sync
# Usage: powershell -File scripts/Muse-GitHubSync.ps1 [-Once] [-IntervalSeconds 60] [-Quiet]
param(
    [switch]$Once,
    [int]$IntervalSeconds = 60,
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$RepoRoot = 'D:\AI AGENT\Muse'
$TokenFile = Join-Path $env:TEMP 'muse_pat.txt'
$LogDir = Join-Path $RepoRoot '.muse-sync'
$LogFile = Join-Path $LogDir 'sync.log'

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

function Invoke-Sync {
    $token = Get-Token
    if (-not $token) {
        Write-Log 'ERROR: no GitHub token found, skipping'
        return $false
    }

    Push-Location $RepoRoot
    try {
        $status = git status --porcelain 2>$null
        if (-not $status) { return $false }

        git add -A 2>&1 | Out-Null
        $staged = git diff --cached --name-only 2>$null
        if (-not $staged) { return $false }

        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $count = ($staged | Measure-Object -Line).Lines
        git -c user.name='Muse Workspace' -c user.email='muse@localhost' commit -m "auto-sync: $ts ($count files)" --quiet 2>&1 | Out-Null

        $env:GIT_TERMINAL_PROMPT = '0'
        $out = git -c http.version=HTTP/1.0 push origin main 2>&1
        $code = $LASTEXITCODE
        $safe = ($out | Out-String).Replace($token, '***')
        if ($code -eq 0) {
            Write-Log "PUSH OK ($count files) at $ts"
            return $true
        }
        Write-Log "PUSH FAIL code=$code :: $($safe.Trim())"
        return $false
    } finally {
        Pop-Location
    }
}

Write-Log "=== Muse GitHub sync started (interval=${IntervalSeconds}s) ==="

if ($Once) {
    $r = Invoke-Sync
    Write-Log "single run result=$r"
    exit 0
}

while ($true) {
    try { Invoke-Sync | Out-Null } catch { Write-Log "EXCEPTION: $($_.Exception.Message)" }
    Start-Sleep -Seconds $IntervalSeconds
}
