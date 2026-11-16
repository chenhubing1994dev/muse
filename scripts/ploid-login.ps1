# Muse -> Ploid local MCP sign-in
# This only opens Ploid's device authorization flow. It does not start research or spend ACU.

$ErrorActionPreference = "Stop"

$npx = Get-Command npx -ErrorAction SilentlyContinue
if (-not $npx) {
    throw "Node.js/npm npx was not found. Install Node.js first, then rerun this script."
}

Write-Host "Launching the official Ploid MCP device-login flow..."
Write-Host "Sign in to Ploid and approve the intended workspace in the browser."
Write-Host "No research request will be started by this script."

& $npx.Source "@ploid/mcp" "login"
if ($LASTEXITCODE -ne 0) {
    throw "Ploid login did not complete successfully (exit code $LASTEXITCODE)."
}

Write-Host ""
Write-Host "Ploid login command completed."
Write-Host "Muse has not started a research task or consumed ACU."
Write-Host "Next: configure the local Ploid MCP server command for Muse's PloidMCPAdapter.from_stdio(...)."
