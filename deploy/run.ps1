# Wrapper used by Task Scheduler: ensures Ollama is running, then runs a newsfeed command with logging.
#   .\deploy\run.ps1 run     -> build today's episode
#   .\deploy\run.ps1 fetch   -> just pull sources
param([string]$Command = "run")

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
New-Item -ItemType Directory -Force data\logs | Out-Null
$log = Join-Path $root "data\logs\$(Get-Date -Format yyyy-MM-dd).log"
$env:PYTHONUTF8 = "1"

# The Ollama tray app only starts at login; when the task runs with nobody logged in, start the server ourselves.
try {
    Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null
} catch {
    Add-Content $log "$(Get-Date -Format s) starting ollama serve"
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 15
}

# cmd handles stderr redirection without PowerShell turning Python's log lines into errors.
cmd /c "`"$root\.venv\Scripts\python.exe`" -m newsfeed $Command >> `"$log`" 2>&1"
exit $LASTEXITCODE
