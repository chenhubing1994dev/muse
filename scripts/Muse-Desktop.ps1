$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Ui = Join-Path $Root 'ui-prototype'
$Rust = Join-Path $Root 'desktop\src-tauri'
$Exe = Join-Path $Rust 'target\release\muse-desktop.exe'

# Use the existing D: drive Rust toolchain and do not download dependencies.
$env:RUSTUP_HOME = 'D:\Tools\rustup'
$env:CARGO_HOME = 'D:\Tools\Cargo'
$env:PATH = "$env:CARGO_HOME\bin;$env:PATH"
$env:MUSE_PROJECT_ROOT = $Root
$env:PYTHONPATH = Join-Path $Root 'src'

if (-not (Test-Path (Join-Path $Ui 'node_modules'))) {
    throw "Muse UI dependencies are missing. Restore ui-prototype dependencies before launching."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required to build the Muse desktop UI.' }
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Rust/Cargo is required to build Muse desktop.' }

Write-Host '[1/3] Building Muse desktop frontend...' -ForegroundColor Cyan
Push-Location $Ui
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)." }
} finally { Pop-Location }

Write-Host '[2/3] Building production Tauri desktop executable...' -ForegroundColor Cyan
Push-Location $Rust
try {
    & cargo build --release --features custom-protocol
    if ($LASTEXITCODE -ne 0) { throw "Rust desktop build failed (exit $LASTEXITCODE)." }
} finally { Pop-Location }

if (-not (Test-Path $Exe)) { throw "Desktop executable was not produced: $Exe" }
& (Join-Path $PSScriptRoot 'Install-Muse-Desktop-Shortcut.ps1')
Write-Host '[3/3] Launching Muse desktop...' -ForegroundColor Cyan
Start-Process -FilePath $Exe -WorkingDirectory $Root
Write-Host "Muse desktop launched: $Exe" -ForegroundColor Green