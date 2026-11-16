param(
    [string]$Owner = "ZHENYI-ORG",
    [string]$Repo = "Mingli-AI",
    [string]$Tag = "v0.1.0"
)
$ErrorActionPreference = "Stop"
$curl = (Get-Command curl.exe -ErrorAction Stop).Source
$mirrors = @("https://ghfast.top/","https://gh-proxy.com/","https://ghproxy.net/")
$routes = @(
    [pscustomobject]@{Name="raw";Url="https://raw.githubusercontent.com/$Owner/$Repo/main/README.md"},
    [pscustomobject]@{Name="archive";Url="https://github.com/$Owner/$Repo/archive/refs/tags/$Tag.zip"},
    [pscustomobject]@{Name="release";Url="https://github.com/$Owner/$Repo/releases/download/$Tag/merges.txt"}
)
$results = @()
foreach ($mirror in $mirrors) {
    foreach ($route in $routes) {
        $temp = Join-Path $env:TEMP ("muse-gh-probe-" + [guid]::NewGuid().ToString("N") + ".bin")
        $watch = [Diagnostics.Stopwatch]::StartNew()
        & $curl --location --fail --silent --show-error --ssl-no-revoke --connect-timeout 10 --max-time 25 --range 0-65535 --output $temp ($mirror + $route.Url)
        $exitCode = $LASTEXITCODE
        $watch.Stop()
        $bytes = if (Test-Path -LiteralPath $temp) { (Get-Item -LiteralPath $temp).Length } else { 0 }
        $results += [pscustomobject]@{
            Mirror = $mirror
            Route = $route.Name
            Status = if ($exitCode -eq 0 -and $bytes -gt 0) { "PASS" } else { "FAIL" }
            HttpTransferBytes = $bytes
            CurlExit = $exitCode
            Seconds = [math]::Round($watch.Elapsed.TotalSeconds,2)
        }
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}
$results | Format-Table -AutoSize
$failed = @($results | Where-Object Status -ne "PASS")
Write-Output ("SUMMARY: {0}/{1} route probes passed. Range probe checks transport only; full assets still require official size/SHA256 verification." -f ($results.Count-$failed.Count),$results.Count)
