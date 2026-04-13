$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvConfig = Join-Path $workspace ".venv\pyvenv.cfg"

if (-not (Test-Path $venvConfig)) {
    throw "Could not find .venv\pyvenv.cfg in $workspace"
}

$configLines = Get-Content $venvConfig
$executableLine = $configLines | Where-Object { $_ -match '^executable\s*=' } | Select-Object -First 1

if (-not $executableLine) {
    throw "Could not find executable path in .venv\pyvenv.cfg"
}

$pythonPath = ($executableLine -split '=', 2)[1].Trim()

if (-not (Test-Path $pythonPath)) {
    throw "Configured Python executable not found: $pythonPath"
}

Set-Location $workspace
& $pythonPath -m uvicorn trading_bot.api.main:app --reload
