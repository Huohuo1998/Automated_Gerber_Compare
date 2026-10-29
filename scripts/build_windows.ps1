$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment.' }
& .venv\Scripts\python.exe -m pip install -e . pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Failed to install build dependencies.' }
& .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name AutomatedGerberCompare --paths src src/automated_gerber_compare/__main__.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Write-Host 'Build ready: dist\AutomatedGerberCompare\AutomatedGerberCompare.exe'
