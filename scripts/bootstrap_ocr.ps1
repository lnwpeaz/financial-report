$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadDir = Join-Path $projectRoot 'tools'
$ocrTarget = Join-Path $projectRoot 'vendor\tesseract'
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $ocrTarget 'tesseract.exe'))) {
    $installerPath = Join-Path $downloadDir 'tesseract-installer.exe'
    Invoke-WebRequest -Uri 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe' -OutFile $installerPath
    $expectedHash = 'BEE9E3434BD94FD65387D9BE28CD467A41F61B1275383B55B0F59A1331270AE4'
    if ((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash -ne $expectedHash) { throw 'Tesseract installer checksum mismatch.' }
    New-Item -ItemType Directory -Force -Path $ocrTarget | Out-Null
    $process = Start-Process -FilePath $installerPath -ArgumentList @('/S', ('/D=' + $ocrTarget)) -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Tesseract installer failed: $($process.ExitCode)" }
}
$thaiModel = Join-Path $ocrTarget 'tessdata\tha.traineddata'
if (-not (Test-Path -LiteralPath $thaiModel)) {
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/tha.traineddata' -OutFile $thaiModel
}
if ((Get-FileHash -LiteralPath $thaiModel -Algorithm SHA256).Hash -ne '294227CC2D1292B0ACB28D61D4115C88252B96D466CA90B417CF4CF0C67BF07C') {
    throw 'Thai model checksum mismatch. Review upstream changes before updating the pinned hash.'
}
Write-Output 'Thai and English OCR runtime ready.'
