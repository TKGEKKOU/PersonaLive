$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

& .\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
& .\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onedir `
  --contents-directory "." `
  --name PersonaLive `
  --collect-all webview `
  --add-data "static;static" `
  --add-data "resources;resources" `
  --add-data "docker-compose.yml;." `
  --add-data ".env.example;." `
  desktop_main.py

Write-Host "Built:" (Join-Path $projectRoot "dist\PersonaLive\PersonaLive.exe")
