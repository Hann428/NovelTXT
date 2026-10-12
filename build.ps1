param([ValidatePattern('^[A-Za-z0-9-]+$')][string]$Name = 'NovelTXT')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\native\build_webview2.ps1' -OutputDirectory release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --windowed --onedir --name $Name --icon assets/book-turn.ico --add-data 'assets:assets' --paths vendor/Novel-Get/src --collect-all playwright --add-data 'vendor/Novel-Get/config:novel_get/config' --add-data 'vendor/Novel-Get/LICENSE:licenses/Novel-Get' --add-data 'THIRD_PARTY.md:licenses' --add-data 'native/release:native/release' launcher.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath README.md -Destination (Join-Path "dist/$Name" 'README.md') -Force
Write-Output "BUILD_OK dist/$Name/$Name.exe"
