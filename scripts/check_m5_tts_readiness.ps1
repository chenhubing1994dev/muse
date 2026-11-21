$ErrorActionPreference = 'Continue'

Write-Output '=== PYTHON INSTALLS ==='
py -0p

Write-Output '=== BUILD TOOLS ==='
foreach ($name in @('rustc','cargo','bun','node')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { Write-Output ($name + '=available') }
    else { Write-Output ($name + '=not_found') }
}

Write-Output '=== LOCAL MODEL DIRECTORIES ==='
foreach ($root in @('data\models','models')) {
    if (Test-Path $root) {
        Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'm5|moss|canto' } |
            ForEach-Object { Write-Output ($root + '\' + $_.Name) }
    }
}

Write-Output '=== HUGGING FACE M5 CACHE ==='
$hf = if ($env:HF_HOME) { Join-Path $env:HF_HOME 'hub' }
      else { Join-Path $env:USERPROFILE '.cache\huggingface\hub' }
if (Test-Path $hf) {
    $found = Get-ChildItem $hf -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'models--ASLP-lab--M5-TTS*' }
    if ($found) { $found | ForEach-Object { Write-Output $_.Name } }
    else { Write-Output 'no M5-TTS cache directories' }
} else { Write-Output 'Hugging Face cache not found' }

Write-Output '=== PYTHON PACKAGE PROBES (NO IMPORT OR INSTALL) ==='
foreach ($version in @('3.14','3.11')) {
    Write-Output ('--- Python ' + $version + ' ---')
    $packages = py -$version -m pip list --format=freeze 2>$null |
        Select-String -Pattern '^(torch|torchaudio|transformers|huggingface-hub|safetensors|numpy|soundfile|librosa|onnxruntime|pynini|WeTextProcessing)='
    if ($packages) { $packages | ForEach-Object { Write-Output $_.Line } }
    else { Write-Output 'No M5-TTS dependency packages detected in this interpreter' }
}
Write-Output '=== PURE PROSODY CONTRACT TEST ON PYTHON 3.14 ==='
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$testLog = Join-Path $env:TEMP 'muse-prosody-contract-py314.log'
py -3.14 -m unittest discover -s tests -p 'test_prosody_contract.py' *> $testLog
$testExit = $LASTEXITCODE
Get-Content $testLog
Remove-Item $testLog -Force -ErrorAction SilentlyContinue
if ($testExit -ne 0) { exit $testExit }