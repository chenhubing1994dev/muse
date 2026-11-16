param(
    [string]$Destination = "D:\AI AGENT\Muse\models\mingli-ai-v0.1.0-assets",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$releaseUrl = "https://api.github.com/repos/ZHENYI-ORG/Mingli-AI/releases/tags/v0.1.0"
$headers = @{ "User-Agent" = "Muse-local-model-setup"; "Accept" = "application/vnd.github+json" }
$curl = (Get-Command curl.exe -ErrorAction Stop).Source
$mirrorBases = @("https://ghfast.top/","https://gh-proxy.com/","https://ghproxy.net/","https://githubproxy.cc/")

$manifestPath = Join-Path $Destination "release-assets.json"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$manifestFresh = $false
try {
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers -TimeoutSec 30
    $assets = @($release.assets)
    if ($assets.Count -ne 54) { throw "Unexpected release asset count: $($assets.Count); review the pinned release before proceeding." }
    $assets | Select-Object name,size,id,url,browser_download_url | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    $manifestFresh = $true
} catch {
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw }
    $assets = @(Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json)
    if ($assets.Count -ne 54) { throw "GitHub API unavailable and cached manifest is invalid; refusing download." }
    Write-Warning "GitHub API unavailable; using the previously captured immutable v0.1.0 asset manifest. Final SHA-256 verification remains mandatory."
}
if ($assets.Count -ne 54) { throw "Release manifest asset count mismatch: $($assets.Count)" }
foreach ($asset in $assets) {
    if ([string]$asset.name -match '[\\/:]' -or [string]$asset.name -match '^\.\.?$') {
        throw "Unsafe release asset name rejected: $($asset.name)"
    }
}

$totalBytes = [int64](($assets | Measure-Object -Property size -Sum).Sum)
$driveName = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Destination)).Substring(0,1)
$drive = Get-PSDrive -Name $driveName
$reserveBytes = 120GB
if ($drive.Free -lt ($totalBytes + $reserveBytes)) {
    throw "Insufficient free disk space. Need at least $([math]::Round(($totalBytes+$reserveBytes)/1GB,1)) GB; available $([math]::Round($drive.Free/1GB,1)) GB."
}

if ($PreflightOnly) {
    Write-Output ("PREFLIGHT OK: {0} assets, {1:N2} GiB; free space {2:N2} GiB; manifest source: {3}; no model weights downloaded." -f $assets.Count,($totalBytes/1GB),($drive.Free/1GB),$(if($manifestFresh){"GitHub API"}else{"cached GitHub API manifest"}))
    exit 0
}

$completed = 0
foreach ($asset in $assets) {
    $name = [string]$asset.name
    $expected = [int64]$asset.size
    $finalPath = Join-Path $Destination $name

    if (Test-Path -LiteralPath $finalPath) {
        $existing = (Get-Item -LiteralPath $finalPath).Length
        if ($existing -eq $expected) {
            $completed += $expected
            Write-Output ("SKIP verified-size {0} ({1}/{2} bytes)" -f $name,$completed,$totalBytes)
            continue
        }
        throw "Existing file has unexpected size; refusing overwrite: $name ($existing vs $expected)"
    }

    Write-Output ("DOWNLOAD {0} ({1} bytes)" -f $name,$expected)
    $downloaded = $false
    foreach ($mirrorBase in $mirrorBases) {
        $hostTag = ([uri]$mirrorBase).Host -replace '[^A-Za-z0-9._-]','_'
        # Keep one partial per mirror: never splice bytes from different proxy services.
        $partialPath = Join-Path $Destination ($name + ".partial." + $hostTag)
        if (Test-Path -LiteralPath $partialPath) {
            $partialSize = (Get-Item -LiteralPath $partialPath).Length
            if ($partialSize -gt $expected) {
                Remove-Item -LiteralPath $partialPath -Force
            } elseif ($partialSize -eq $expected) {
                Move-Item -LiteralPath $partialPath -Destination $finalPath
                $downloaded = $true
                Write-Output ("PROMOTED complete partial {0} from {1}" -f $name,$mirrorBase)
                break
            }
        }

        $proxyUrl = $mirrorBase + [string]$asset.browser_download_url
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            Write-Output ("TRY {0} via {1}, attempt {2}/3" -f $name,$mirrorBase,$attempt)
            $curlArgs = @("--location","--fail","--connect-timeout","30","--max-time","7200","--speed-time","180","--speed-limit","1024","--ssl-no-revoke","--silent","--show-error")
            if ((Test-Path -LiteralPath $partialPath) -and (Get-Item -LiteralPath $partialPath).Length -gt 0) {
                $curlArgs += @("--continue-at","-")
            }
            $curlArgs += @("--output",$partialPath,$proxyUrl)
            & $curl @curlArgs
            $curlExit = $LASTEXITCODE
            if (Test-Path -LiteralPath $partialPath) {
                $actual = (Get-Item -LiteralPath $partialPath).Length
                if ($curlExit -eq 0 -and $actual -eq $expected) {
                    Move-Item -LiteralPath $partialPath -Destination $finalPath
                    $downloaded = $true
                    Write-Output ("OK {0} via {1} ({2}/{3} bytes)" -f $name,$mirrorBase,$expected,$totalBytes)
                    break
                }
                if ($actual -gt $expected) {
                    Write-Output ("REJECT oversized partial from {0}: {1} > {2}" -f $mirrorBase,$actual,$expected)
                    Remove-Item -LiteralPath $partialPath -Force
                } else {
                    Write-Output ("INCOMPLETE {0}; curl exit {1}; retained {2}/{3} bytes for same-mirror resume" -f $name,$curlExit,$actual,$expected)
                }
            } else {
                Write-Output ("MIRROR FAILED {0}; curl exit {1}" -f $mirrorBase,$curlExit)
            }
            if ($downloaded) { break }
        }
        if ($downloaded) { break }
    }
    if (-not $downloaded) {
        throw "All configured mirrors failed for $name. Mirror-specific partial files were retained for a safe same-mirror resume."
    }
    $completed += $expected
}

Write-Output ("DOWNLOAD COMPLETE: {0} assets, {1:N2} GiB" -f $assets.Count,($totalBytes/1GB))
Write-Output "Next: run scripts/reconstruct-model.py and verify every SHA256 in third_party/mingli-ai-review/model-preflight/merged-manifest.json."
