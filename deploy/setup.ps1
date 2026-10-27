# One-time setup on the Windows server. From the repo root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\deploy\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

# --- System tools (skipped if already installed) ---
$tools = @{
    "ollama"  = "Ollama.Ollama"
    "ffmpeg"  = "Gyan.FFmpeg"
    "caddy"   = "CaddyServer.Caddy"
    "cloudflared" = "Cloudflare.cloudflared"   # tunnel that exposes Caddy on your domain
    "py"      = "Python.Python.3.12"   # Kokoro TTS needs Python 3.10-3.12
}
foreach ($cmd in $tools.Keys) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Installing $($tools[$cmd])..." -ForegroundColor Cyan
        winget install --id $tools[$cmd] -e --accept-source-agreements --accept-package-agreements
        Refresh-Path
    }
}

# --- Python environment ---
if (-not (Test-Path .venv)) {
    py -3.12 -m venv .venv
}
$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
# CUDA build of PyTorch so Kokoro runs on the RTX 4060 instead of the CPU.
& $python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
& $python -m pip install -r requirements.txt
& $python -c "import torch; print('CUDA available for TTS:', torch.cuda.is_available())"

# --- Ollama models (names read from config/settings.yaml) ---
$settings = Get-Content config\settings.yaml -Raw
foreach ($key in "chat_model", "embed_model") {
    if ($settings -match "${key}:\s*`"([^`"]+)`"") {
        Write-Host "Pulling $($Matches[1])..." -ForegroundColor Cyan
        ollama pull $Matches[1]
    }
}

New-Item -ItemType Directory -Force public, data\logs | Out-Null
Write-Host "`nSetup complete. Try:  .\.venv\Scripts\python -m newsfeed run --script-only" -ForegroundColor Green
