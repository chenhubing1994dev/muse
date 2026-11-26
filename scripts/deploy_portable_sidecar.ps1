# Safe portable deployment: stop gracefully, backup consistent data, replace, restart, verify.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DesktopRoot,
    [string]$BuiltRuntime = (Join-Path $PSScriptRoot '..\src-tauri\sidecar-runtime'),
    [string]$BuiltEngine = (Join-Path $PSScriptRoot '..\src-tauri\binaries\muse-engine-x86_64-pc-windows-msvc.exe'),
    [int]$StartupTimeoutSeconds = 60
)
$ErrorActionPreference = 'Stop'
$DesktopRoot = [IO.Path]::GetFullPath($DesktopRoot)
$BuiltRuntime = [IO.Path]::GetFullPath($BuiltRuntime)
$BuiltEngine = [IO.Path]::GetFullPath($BuiltEngine)
$DataRoot = Join-Path $DesktopRoot 'muse-data'
$TargetRuntime = Join-Path $DesktopRoot 'sidecar-runtime'
$TargetEngine = Join-Path $DesktopRoot 'muse-engine.exe'
$MuseExe = Join-Path $DesktopRoot 'Muse.exe'
foreach($p in @($DesktopRoot,$BuiltRuntime,$BuiltEngine,$MuseExe)){ if(-not (Test-Path -LiteralPath $p)){ throw "Required path missing: $p" } }
if(-not (Test-Path -LiteralPath $DataRoot -PathType Container)){ throw "Refusing deployment: muse-data directory missing: $DataRoot" }
if(-not (Test-Path -LiteralPath (Join-Path $BuiltRuntime 'app\muse_sidecar_main.py'))){ throw "Built Sidecar entry missing." }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path (Split-Path $DesktopRoot -Parent) "Muse-deploy-backups\$stamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
$rootProcesses = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'Muse.exe' -and $_.ExecutablePath -eq $MuseExe }
foreach($process in $rootProcesses){
    $output = & taskkill.exe /PID $process.ProcessId /T /F 2>&1
    $output | ForEach-Object { Write-Output $_ }
    if($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)){ throw "Unable to terminate Muse.exe PID $($process.ProcessId): $($output -join ' ')" }
}
Start-Sleep -Seconds 2
$remaining = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "$DesktopRoot*" -or $_.CommandLine -like "*$DesktopRoot*" }
if($remaining){ throw "Old Muse processes remain: $($remaining.ProcessId -join ',')" }
# SQLite WAL checkpoint is complete after process exit. Preserve the full data tree before replacing binaries.
$dataBefore = Get-ChildItem -LiteralPath $DataRoot -Recurse -File | Where-Object { $_.FullName -notmatch '\\logs\\(muse-engine\.log|startup-api-acceptance\.json)$' -and $_.Name -notmatch '\\.sqlite3-(wal|shm)$' } | ForEach-Object { "$($_.FullName.Substring($DataRoot.Length))|$($_.Length)|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" } | Sort-Object
$dataBefore | Set-Content -LiteralPath (Join-Path $backupRoot 'muse-data-before.txt') -Encoding UTF8
Copy-Item -LiteralPath $DataRoot -Destination (Join-Path $backupRoot 'muse-data') -Recurse -Force
Copy-Item -LiteralPath $TargetEngine -Destination (Join-Path $backupRoot 'muse-engine.exe') -Force
Copy-Item -LiteralPath $TargetRuntime -Destination (Join-Path $backupRoot 'sidecar-runtime') -Recurse -Force
$stage = Join-Path $DesktopRoot "sidecar-runtime.new-$stamp"
Copy-Item -LiteralPath $BuiltRuntime -Destination $stage -Recurse -Force
if(Test-Path -LiteralPath $TargetRuntime){ Rename-Item -LiteralPath $TargetRuntime -NewName "sidecar-runtime.old-$stamp" }
Rename-Item -LiteralPath $stage -NewName 'sidecar-runtime'
Copy-Item -LiteralPath $BuiltEngine -Destination "$TargetEngine.new" -Force
Move-Item -LiteralPath "$TargetEngine.new" -Destination $TargetEngine -Force
$dataAfterReplace = Get-ChildItem -LiteralPath $DataRoot -Recurse -File | Where-Object { $_.FullName -notmatch '\\logs\\(muse-engine\.log|startup-api-acceptance\.json)$' -and $_.Name -notmatch '\\.sqlite3-(wal|shm)$' } | ForEach-Object { "$($_.FullName.Substring($DataRoot.Length))|$($_.Length)|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" } | Sort-Object
if(Compare-Object -ReferenceObject $dataBefore -DifferenceObject $dataAfterReplace){ throw "muse-data changed during deployment; restore from $backupRoot\muse-data" }
$launch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=('"' + $MuseExe + '"');CurrentDirectory=$DesktopRoot}
if($launch.ReturnValue -ne 0){ throw "Muse restart failed; WMI ReturnValue=$($launch.ReturnValue)" }
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
$healthy = $false
while((Get-Date) -lt $deadline){
    Start-Sleep -Seconds 1
    $side = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.ExecutablePath -eq (Join-Path $DesktopRoot 'sidecar-runtime\python.exe') } | Select-Object -First 1
    if(-not $side){ continue }
    $listeners = Get-NetTCPConnection -State Listen -OwningProcess $side.ProcessId -ErrorAction SilentlyContinue
    foreach($listener in $listeners){
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($listener.LocalPort)/__muse/health" -TimeoutSec 2
            if($health.version -eq '1.0.3' -and $health.agent_registry_configured -eq $true -and $health.stores_healthy -eq $true -and @($health.stores.PSObject.Properties).Count -eq 4 -and @($health.stores.PSObject.Properties.Value | Where-Object {$_.status -ne 'healthy'}).Count -eq 0){ $healthy=$true; $livePort=$listener.LocalPort; $liveSidecarPid=$side.ProcessId; break }
        } catch { Write-Verbose "Health endpoint not ready on port $($listener.LocalPort): $($_.Exception.Message)" }
    }
    if($healthy){ break }
}
if(-not $healthy){ throw "Muse restarted but runtime Store health did not become healthy within $StartupTimeoutSeconds seconds. Inspect $DataRoot\logs\muse-engine.log" }
$dataAfterRestart = Get-ChildItem -LiteralPath $DataRoot -Recurse -File | Where-Object { $_.FullName -notmatch '\\logs\\(muse-engine\.log|startup-api-acceptance\.json)$' -and $_.Name -notmatch '\\.sqlite3-(wal|shm)$' } | ForEach-Object { "$($_.FullName.Substring($DataRoot.Length))|$($_.Length)|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" } | Sort-Object
$lostOrChanged = Compare-Object -ReferenceObject $dataBefore -DifferenceObject $dataAfterRestart -PassThru | Where-Object { $_ -in $dataBefore }
if($lostOrChanged){ throw "Original muse-data files missing or changed after restart: $($lostOrChanged -join '; '). Backup: $backupRoot\muse-data" }
Write-Output "DEPLOYED_AND_RESTARTED: $DesktopRoot"
Write-Output "BACKUP: $backupRoot"
Write-Output "MUSE_DATA_ORIGINAL_FILES: $($dataBefore.Count) preserved byte-for-byte; post-start files=$($dataAfterRestart.Count)"
Write-Output "LIVE_SIDECAR_PID: $liveSidecarPid"
Write-Output "LIVE_PORT: $livePort"
Write-Output "LIVE_HEALTH: $($health | ConvertTo-Json -Depth 8 -Compress)"
Write-Output "ENGINE_SHA256: $((Get-FileHash -LiteralPath $TargetEngine -Algorithm SHA256).Hash)"
Write-Output "SIDECAR_ENTRY_SHA256: $((Get-FileHash -LiteralPath (Join-Path $TargetRuntime 'app\muse_sidecar_main.py') -Algorithm SHA256).Hash)"
