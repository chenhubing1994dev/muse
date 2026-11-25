# Shared, dot-sourceable Python resolver for Muse scripts (Windows PowerShell 5.1+ / 7+).
#
#   . (Join-Path $PSScriptRoot 'Resolve-MusePython.ps1')
#   $py = Resolve-MusePython -Root $Root
#   & $py.Exe @($py.PreArgs) script.py ...
#
# Every candidate is actually *started* (python -c) instead of only checking that the file
# exists: a venv launcher can exist and still fail with "No pyvenv.cfg file".
# Order: .venv launcher -> the venv's base interpreter (pyvenv.cfg "home") -> py -3.11/-3.12/-3.13/-3
# -> python on PATH. When a fallback has the venv's major.minor, the venv site-packages are
# appended to PYTHONPATH so the same third-party packages stay available.

function Test-MusePython {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$PreArgs = @()
    )
    $result = [ordered]@{ Exe = $Exe; PreArgs = $PreArgs; Ok = $false; Version = $null; Executable = $null; Detail = $null }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $Exe @PreArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3]); print(sys.executable)" 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        $versionLine = $lines | Where-Object { $_ -match '^\d+\.\d+\.\d+$' } | Select-Object -First 1
        if ($code -eq 0 -and $versionLine) {
            $result.Version = $versionLine
            $result.Executable = ($lines | Select-Object -Last 1)
            $parts = $versionLine.Split('.')
            $result.Ok = ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)
            if (-not $result.Ok) { $result.Detail = "Python $versionLine is older than 3.11" }
        } else {
            $result.Detail = ("exit={0}; {1}" -f $code, (($lines -join ' | ').Trim()))
        }
    } catch {
        $result.Detail = $_.Exception.Message
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]$result
}

function Get-MuseVenvConfig {
    param([Parameter(Mandatory = $true)][string]$Root)
    $venvDir = Join-Path $Root '.venv'
    $config = [ordered]@{ VenvDir = $venvDir; Home = $null; Version = $null; ConfigPath = (Join-Path $venvDir 'pyvenv.cfg') }
    if (Test-Path -LiteralPath $config.ConfigPath) {
        foreach ($line in [System.IO.File]::ReadAllLines($config.ConfigPath)) {
            if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $config.Home = $Matches[1] }
            elseif ($line -match '^\s*version(_info)?\s*=\s*(\d+\.\d+)') { $config.Version = $Matches[2] }
        }
    }
    return [pscustomobject]$config
}

function Resolve-MusePython {
    param([Parameter(Mandatory = $true)][string]$Root)
    $venv = Get-MuseVenvConfig -Root $Root
    $sitePackages = Join-Path $venv.VenvDir 'Lib\site-packages'
    $candidates = New-Object System.Collections.ArrayList
    $venvPython = Join-Path $venv.VenvDir 'Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        [void]$candidates.Add(@{ Label = '.venv'; Exe = $venvPython; Pre = @(); UseSite = $false })
    }
    if ($venv.Home) {
        $basePython = Join-Path $venv.Home 'python.exe'
        if (Test-Path -LiteralPath $basePython) {
            [void]$candidates.Add(@{ Label = 'venv base (pyvenv.cfg home)'; Exe = $basePython; Pre = @(); UseSite = $true })
        }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($flag in @('-3.11', '-3.12', '-3.13', '-3')) {
            [void]$candidates.Add(@{ Label = "py $flag"; Exe = 'py'; Pre = @($flag); UseSite = $true })
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        [void]$candidates.Add(@{ Label = 'python (PATH)'; Exe = 'python'; Pre = @(); UseSite = $true })
    }
    $probes = New-Object System.Collections.ArrayList
    foreach ($candidate in $candidates) {
        $probe = Test-MusePython -Exe $candidate.Exe -PreArgs $candidate.Pre
        if ($probe.Ok) {
            [void]$probes.Add(("{0}: OK {1} ({2})" -f $candidate.Label, $probe.Version, $probe.Executable))
            $paths = @((Join-Path $Root 'src'))
            $short = ($probe.Version.Split('.')[0..1]) -join '.'
            $siteAdded = $false
            if ($candidate.UseSite -and $venv.Version -and $short -eq $venv.Version -and (Test-Path -LiteralPath $sitePackages)) {
                $paths += $sitePackages
                $siteAdded = $true
            }
            return [pscustomobject]@{
                Label = $candidate.Label; Exe = $candidate.Exe; PreArgs = $candidate.Pre; Version = $probe.Version
                Executable = $probe.Executable; PythonPath = ($paths -join ';'); VenvSitePackages = $siteAdded
                VenvHealthy = ($candidate.Label -eq '.venv'); Probes = @($probes)
            }
        }
        [void]$probes.Add(("{0}: unusable ({1})" -f $candidate.Label, $probe.Detail))
    }
    return [pscustomobject]@{
        Label = $null; Exe = $null; PreArgs = @(); Version = $null; Executable = $null
        PythonPath = (Join-Path $Root 'src'); VenvSitePackages = $false; VenvHealthy = $false; Probes = @($probes)
    }
}
