param(
    [int]$Port = 8766,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing .venv. Run: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt" }
if (-not $env:JOBBOT_DATABASE_URL) { Write-Host "JOBBOT_DATABASE_URL is not set; using the SQLite fallback." -ForegroundColor Yellow }
Start-Process -FilePath $python -ArgumentList @("-m", "jobbot.v2.cli", "serve", "--port", "$Port") -WorkingDirectory $root
if (-not $NoBrowser) {
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:$Port"
}
