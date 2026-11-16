# Verify the existing Ploid device authorization using read-only MCP discovery.
# This script performs initialize + tools/list only. It never calls a Ploid research tool.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Muse project Python environment was not found at $python"
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
Push-Location $projectRoot
try {
    & $python (Join-Path $PSScriptRoot "ploid_verify.py")
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
