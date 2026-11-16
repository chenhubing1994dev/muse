$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Desktop = [Environment]::GetFolderPath('Desktop')
if (-not (Test-Path $Desktop)) { throw "Desktop folder not found: $Desktop" }
$Target = Join-Path $Root 'scripts\Muse-Start.ps1'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$ShortcutPath = Join-Path $Desktop 'Muse.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Quote = [char]34
$Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + $Quote + $Target + $Quote
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = 'Start Muse local dashboard and local autonomous runtime'
$Icon = Join-Path $Root 'desktop\src-tauri\icons\icon.ico'
if (Test-Path $Icon) { $Shortcut.IconLocation = $Icon }
$Shortcut.Save()
if (-not (Test-Path $ShortcutPath)) { throw 'Muse desktop shortcut was not created' }
Write-Output $ShortcutPath