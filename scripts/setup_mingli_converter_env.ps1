param(
    [string]$EnvironmentPath = "D:\AI AGENT\Muse\third_party\llama.cpp-converter-review\.venv"
)

$ErrorActionPreference = "Stop"
$projectPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $projectPython)) { throw "Muse Python 3.11 environment not found: $projectPython" }

if (-not (Test-Path -LiteralPath $EnvironmentPath)) {
    & $projectPython -m venv $EnvironmentPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create isolated converter environment" }
}

$python = Join-Path $EnvironmentPath "Scripts\python.exe"
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

# CPU-only PyTorch is sufficient for conversion; no CUDA/ROCm runtime is required.
& $python -m pip install --no-cache-dir torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw "CPU PyTorch installation failed" }

& $python -m pip install --no-cache-dir -r (Join-Path (Get-Location) "scripts\requirements-mingli-converter.txt")
if ($LASTEXITCODE -ne 0) { throw "Converter dependency installation failed" }

& $python -c "import torch, transformers, safetensors, gguf, numpy; from importlib.metadata import version; print({'torch':torch.__version__,'transformers':transformers.__version__,'gguf':version('gguf'),'numpy':numpy.__version__})"
exit $LASTEXITCODE
