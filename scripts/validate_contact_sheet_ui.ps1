$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..\ui-prototype")
try {
  npm run test
  if ($LASTEXITCODE -ne 0) { throw "UI unit tests failed" }
  npm run check
  if ($LASTEXITCODE -ne 0) { throw "Svelte check failed" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "Vite production build failed" }
} finally { Pop-Location }
