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

# ---- 生成安装包（Inno Setup） ----
$isccCandidates = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
  Write-Warning "未找到 Inno Setup（ISCC.exe），跳过安装包生成。可到 https://jrsoftware.org/isdl.php 安装后重试。"
} else {
  Write-Host "生成安装包（Inno Setup）..."
  & $iscc (Join-Path $projectRoot "scripts\YUMENO.iss")
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败" }
  Write-Host "安装包:" (Join-Path $projectRoot "dist\YUMENO-Setup-0.1.1.exe")
}
