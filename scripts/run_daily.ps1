# Runs one daily cycle: fetch -> match -> apply (within the daily cap and weekly target).
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "data\logs\task.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Set-Location $root
& (Join-Path $root ".venv\Scripts\jobbot.exe") run-daily *>> $log
