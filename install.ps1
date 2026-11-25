# Installs everything lca needs on Windows: Python, Ollama, the model, a venv with Playwright + Chromium, PATH entry.
# Usage: powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Model qwen3:4b-instruct-2507-q4_K_M]
param([string]$Model = "qwen3:4b-instruct-2507-q4_K_M")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Step "Python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
python --version

Step "Ollama"
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (-not (Test-Path $ollama)) {
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
}
try { Invoke-WebRequest http://localhost:11434/api/tags -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 6 }

Step "Pulling model $Model (a few GB, one time)"
& $ollama pull $Model
if ($Model -ne "qwen3:4b-instruct-2507-q4_K_M") { [Environment]::SetEnvironmentVariable("LCA_MODEL", $Model, "User") }

Step "Python environment"
python -m venv "$root\venv"
& "$root\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"
& "$root\venv\Scripts\python.exe" -m playwright install chromium

Step "Adding lca to your PATH"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$root*") { [Environment]::SetEnvironmentVariable("Path", "$userPath;$root", "User") }

Write-Host "`nDone. Open a NEW terminal and run:  lca" -ForegroundColor Green
Write-Host "Tip: your NVIDIA driver should be up to date, or Ollama may run on CPU." -ForegroundColor Yellow
