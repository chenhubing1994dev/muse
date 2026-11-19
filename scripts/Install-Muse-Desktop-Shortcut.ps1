$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Exe = Join-Path $Root 'desktop\src-tauri\target\release\muse-desktop.exe'
$Icon = Join-Path $Root 'desktop\src-tauri\icons\icon.ico'
if (-not (Test-Path $Exe)) {
    throw "Muse desktop executable not found: $Exe. Run scripts\Muse-Desktop.ps1 -Rebuild first."
}
$Desktop = [Environment]::GetFolderPath('Desktop')
if (-not (Test-Path $Desktop)) { throw "Desktop folder not found: $Desktop" }
$Shell = New-Object -ComObject WScript.Shell
foreach ($Name in @('Muse.lnk', 'Muse-Desktop.lnk')) {
    $ShortcutPath = Join-Path $Desktop $Name
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Exe
    $Shortcut.Arguments = ''
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.Description = 'Launch Muse Cyber-Astrolabe & Prometheus Desktop Client'
    if (Test-Path $Icon) {
        $Shortcut.IconLocation = $Icon
    } else {
        $Shortcut.IconLocation = $Exe
    }
    $Shortcut.Save()
    Write-Host "Created desktop shortcut: $ShortcutPath -> $Exe" -ForegroundColor Green
}
