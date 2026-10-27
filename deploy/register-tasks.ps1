#Requires -RunAsAdministrator
# Registers three scheduled tasks (run from an Administrator PowerShell):
#   MorningBrief - Episode     daily at $EpisodeTime, wakes the PC if asleep
#   MorningBrief - Fetch       every $FetchEveryHours hours, so no stories are missed overnight
#   MorningBrief - Web Server  Caddy serving public\ on port $Port from boot
param(
    [string]$EpisodeTime = "05:30",
    [int]$FetchEveryHours = 3,
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force (Join-Path $root "public") | Out-Null

# S4U = runs whether or not you're logged in, without storing your password.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

function New-RunAction([string]$command) {
    New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\deploy\run.ps1`" $command"
}

$jobSettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

Register-ScheduledTask -Force -TaskName "MorningBrief - Episode" -Principal $principal -Settings $jobSettings `
    -Action (New-RunAction "run") -Trigger (New-ScheduledTaskTrigger -Daily -At $EpisodeTime)

$fetchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours $FetchEveryHours)
Register-ScheduledTask -Force -TaskName "MorningBrief - Fetch" -Principal $principal -Settings $jobSettings `
    -Action (New-RunAction "fetch") -Trigger $fetchTrigger

$caddy = (Get-Command caddy).Source
$serverSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -Force -TaskName "MorningBrief - Web Server" -Principal $principal -Settings $serverSettings `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Action (New-ScheduledTaskAction -Execute $caddy -WorkingDirectory $root `
        -Argument "file-server --root `"$root\public`" --listen :$Port")
Start-ScheduledTask -TaskName "MorningBrief - Web Server"

Write-Host "Registered. Feeds served at http://localhost:$Port/podcast.xml and /combined.xml" -ForegroundColor Green
Write-Host "Test the episode task now with:  Start-ScheduledTask 'MorningBrief - Episode'"
