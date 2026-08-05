# YUMENO 一键从零启动（Windows / PowerShell）
#
# 用法：
#   .\scripts\start.ps1              # 桌面端（默认）：自动准备环境并拉起 Docker + 本地服务
#   .\scripts\start.ps1 -Server      # 仅启动 FastAPI 服务端，浏览器访问 http://127.0.0.1:17000
#   .\scripts\start.ps1 -NoInstall   # 跳过依赖安装（环境已就绪时更快）
#
# 首次运行会自动：创建 .venv、安装依赖、生成 .env；重复运行直接复用。

param(
  [switch]$Server,
  [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host ""
Write-Host "== YUMENO 一键启动 ==" -ForegroundColor Cyan

# ---- 1. 检测 Python 3.11 ----
$pyLauncher = ""
$pyVersionOk = $false
try {
  $out = & py -3.11 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
  if ($LASTEXITCODE -eq 0 -and $out -match "3\.11") { $pyLauncher = "py"; $pyVersionOk = $true }
} catch {}
if (-not $pyVersionOk) {
  try {
    $out = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out -match "3\.11") { $pyLauncher = "python"; $pyVersionOk = $true }
  } catch {}
}
if (-not $pyVersionOk) {
  Write-Host "错误：未检测到 Python 3.11。请先安装 Python 3.11（勾选 Add to PATH），再重新运行。" -ForegroundColor Red
  exit 1
}
Write-Host ("[1/4] Python 3.11 已就绪" + $(if ($pyLauncher -eq "py") { "（py launcher）" } else { "" }))

# ---- 2. 创建虚拟环境并安装依赖 ----
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$pyArgs = if ($pyLauncher -eq "py") { @("-3.11") } else { @() }
if (-not (Test-Path $venvPy)) {
  Write-Host "[2/4] 创建虚拟环境 .venv ..."
  & $pyLauncher @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Write-Host "创建虚拟环境失败" -ForegroundColor Red; exit 1 }
} else {
  Write-Host "[2/4] 虚拟环境已存在"
}

if (-not $NoInstall) {
  $depsOk = & $venvPy -c "import fastapi, uvicorn" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "     安装依赖（首次，可能需要几分钟）..."
    & $venvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Write-Host "升级 pip 失败" -ForegroundColor Red; exit 1 }
    & $venvPy -m pip install -e . -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "安装依赖失败" -ForegroundColor Red; exit 1 }
    if (-not $Server) {
      & $venvPy -m pip install -r requirements-desktop.txt
      if ($LASTEXITCODE -ne 0) { Write-Host "安装桌面端依赖失败" -ForegroundColor Red; exit 1 }
    }
  } else {
    Write-Host "     依赖已就绪，跳过安装"
  }
} else {
  Write-Host "     已跳过依赖安装（-NoInstall）"
}

# ---- 3. 准备 .env ----
if (-not (Test-Path (Join-Path $root ".env"))) {
  Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
  Write-Host "[3/4] 已生成 .env（默认配置，可稍后在设置页修改）"
} else {
  Write-Host "[3/4] .env 已存在"
}

# ---- 4. 启动 ----
if ($Server) {
  Write-Host "[4/4] 启动 Docker Compose 基础设施 ..."
  try {
    & docker version --format "{{.Server.Version}}" 2>$null | Out-Null
  } catch {
    Write-Host "错误：未检测到 Docker。请先启动 Docker Desktop。" -ForegroundColor Red
    exit 1
  }
  & docker compose up -d
  if ($LASTEXITCODE -ne 0) { Write-Host "Docker Compose 启动失败" -ForegroundColor Red; exit 1 }
  Write-Host "     等待 etcd / Milvus 健康（最多 90 秒）..."
  $deadline = (Get-Date).AddSeconds(90)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    $ps = (& docker compose ps --format "{{.Name}}|{{.Status}}" 2>$null) -join "`n"
    if (($ps -match "etcd.*healthy") -and ($ps -match "standalone.*healthy")) { $ready = $true; break }
    Start-Sleep -Seconds 3
  }
  if (-not $ready) {
    Write-Host "提示：等待超时，请稍后运行 docker compose ps 检查状态。继续启动应用 ..." -ForegroundColor Yellow
  } else {
    Write-Host "     基础设施就绪"
  }
  Write-Host ""
  Write-Host "FastAPI 启动中，浏览器访问 http://127.0.0.1:17000 （Ctrl+C 停止）" -ForegroundColor Green
  & $venvPy -B main.py
} else {
  Write-Host "[4/4] 启动桌面端（首次会自动拉起 Docker 与本地服务）"
  Write-Host ""
  Write-Host "窗口打开后即可使用；关闭窗口时会询问退出方式。" -ForegroundColor Green
  & $venvPy -B desktop_main.py
}
