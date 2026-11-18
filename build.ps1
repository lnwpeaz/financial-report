$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ x64 is required to build.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& (Join-Path $PSScriptRoot 'scripts\bootstrap_ocr.ps1')
& '.\.venv\Scripts\python.exe' scripts/bootstrap_neural.py
if ($LASTEXITCODE -ne 0) { throw 'Neural model download/check failed.' }
& '.\.venv\Scripts\python.exe' scripts/build_release.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
