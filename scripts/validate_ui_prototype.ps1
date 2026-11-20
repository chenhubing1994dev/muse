$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$uiRoot = Join-Path $projectRoot "ui-prototype"
if (-not (Test-Path -LiteralPath (Join-Path $uiRoot "package.json") -PathType Leaf)) {
    throw "Muse UI package.json is missing"
}
Push-Location $uiRoot
try {
    Write-Output "[1/3] UI API tests"
    npm test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Output "[2/3] Svelte and TypeScript checks"
    npm run check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Output "[3/3] Production UI build"
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Output "MUSE_UI_VALIDATION_OK"
}
finally {
    Pop-Location
}
