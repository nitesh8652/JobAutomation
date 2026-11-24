# Registers a Windows scheduled task that runs jobbot once a day.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 [-Time 10:30]
# Remove: schtasks /Delete /TN jobbot-daily /F
param([string]$Time = "10:30")
$script = Join-Path $PSScriptRoot "run_daily.ps1"
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`""
schtasks /Create /SC DAILY /TN "jobbot-daily" /TR $action /ST $Time /F
Write-Host "Scheduled 'jobbot-daily' at $Time every day. Logs: data\logs\task.log"
