$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$ttsRuntime = Join-Path $projectRoot "runtime\tts\Qwen3_TTS_Lunar.exe"
if (-not (Test-Path $ttsRuntime)) {
  throw "Missing bundled TTS runtime: $ttsRuntime. Run the Build TTS runtime workflow or local runtime build first."
}

& .\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
& .\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onedir `
  --contents-directory "." `
  --name YUMENO `
  --collect-all webview `
  --exclude-module speech_recognition `
  --exclude-module tensorboard `
  --exclude-module pytest `
  --exclude-module matplotlib `
  --exclude-module azure-ai-contentunderstanding `
  --exclude-module azure-ai-documentintelligence `
  --exclude-module azure-identity `
  --exclude-module youtube-transcript-api `
  --add-data "static;static" `
  --add-data "resources;resources" `
  --add-data "runtime\tts;runtime\tts" `
  --add-data "data\live2d;data\live2d" `
  --add-data "docker-compose.yml;." `
  --add-data ".env.example;." `
  desktop_main.py

Write-Host "Built:" (Join-Path $projectRoot "dist\YUMENO\YUMENO.exe")
