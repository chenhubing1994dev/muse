$ErrorActionPreference = "Continue"
& wsl.exe -d Ubuntu -- bash -c 'echo SIZES; du -sh /mnt/d/AI\ AGENT/Muse/src /mnt/d/AI\ AGENT/Muse/desktop/src-tauri /mnt/d/AI\ AGENT/Muse/ui-prototype 2>/dev/null; echo ICONS; find /mnt/d/AI\ AGENT/Muse/desktop/src-tauri/icons -maxdepth 1 -type f -printf "%f %s bytes\n" 2>/dev/null; echo LOCK; ls -l /mnt/d/AI\ AGENT/Muse/desktop/src-tauri/Cargo.lock /mnt/d/AI\ AGENT/Muse/ui-prototype/package-lock.json 2>/dev/null' 2>&1 | ForEach-Object { Write-Output $_.ToString() }
Write-Output ("WSL_EXIT=" + $LASTEXITCODE)
