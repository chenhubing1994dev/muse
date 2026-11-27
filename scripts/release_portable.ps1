# One-command release: pre-build contract -> build/self-test -> post-build hash verify -> safe deploy/restart.
[CmdletBinding()]
param(
    [string]$DesktopRoot = 'X:\PATH\TO\USER\Desktop\museapp\Muse',
    [string]$Target = 'x86_64-pc-windows-msvc'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repo
try {
    py -3.11 scripts\prebuild_store_check.py
    if($LASTEXITCODE -ne 0){ throw 'Pre-build Store contract check failed.' }
    py -3.11 scripts\build_sidecar.py --target $Target --plan-b
    if($LASTEXITCODE -ne 0){ throw 'Sidecar build or embedded self-test failed.' }
    py -3.11 scripts\postbuild_sidecar_verify.py --runtime src-tauri\sidecar-runtime
    if($LASTEXITCODE -ne 0){ throw 'Post-build hash/version consistency failed.' }
    & (Join-Path $PSScriptRoot 'deploy_portable_sidecar.ps1') -DesktopRoot $DesktopRoot
    if($LASTEXITCODE -ne 0){ throw 'Portable deployment/restart/runtime health failed.' }
    py -3.11 scripts\postbuild_sidecar_verify.py --runtime src-tauri\sidecar-runtime --desktop $DesktopRoot
    if($LASTEXITCODE -ne 0){ throw 'Desktop embedded runtime does not match release artifact.' }
    Write-Output 'RELEASE_PIPELINE: PASS'
} finally {
    Pop-Location
}
