$ErrorActionPreference = 'Stop'
$uiRoot = Join-Path $PSScriptRoot '..\ui-prototype'
Push-Location $uiRoot
try {
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "UI API tests failed: $LASTEXITCODE" }

    & npm.cmd run check
    if ($LASTEXITCODE -ne 0) { throw "Svelte/TypeScript checks failed: $LASTEXITCODE" }

    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "UI production build failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}
