$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $PSScriptRoot 'Muse-Desktop.ps1'
$Desktop = [Environment]::GetFolderPath('Desktop')
$Shell = New-Object -ComObject WScript.Shell
foreach ($Name in @('Muse.lnk', 'Muse-Desktop.lnk')) {
    $ShortcutPath = Join-Path $Desktop $Name
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $Launcher + '"'
    $Shortcut.WorkingDirectory = $Root
    $Shortcut.Description = 'Build and launch the Muse Tauri desktop command center'
    $Shortcut.IconLocation = Join-Path $Root 'desktop\src-tauri\target\release\muse-desktop.exe'
    $Shortcut.Save()
    Write-Host "Created desktop shortcut: $ShortcutPath" -ForegroundColor Green
}
