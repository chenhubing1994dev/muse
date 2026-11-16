$ErrorActionPreference = 'Stop'
$root = 'D:\AI\ComfyUI-Rinsei\portable\ComfyUI_windows_portable'
$python = Join-Path $root 'python_embeded\python.exe'
$main = Join-Path $root 'ComfyUI\main.py'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $main)) {
    throw 'Configured ComfyUI portable installation is incomplete.'
}
$stdout = Join-Path $root 'muse-comfyui.stdout.log'
$stderr = Join-Path $root 'muse-comfyui.stderr.log'
$p = Start-Process -FilePath $python -ArgumentList @('-s', $main, '--windows-standalone-build', '--cpu-vae', '--listen', '127.0.0.1', '--disable-auto-launch') -WorkingDirectory $root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait -NoNewWindow
exit $p.ExitCode