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

$argsList = @("run_bot.py", "--mode", "multi", "--universe", "all", "--source", "auto", "--poll-seconds", "5")

$challengeModeValue = ""
if ($null -ne $env:CHALLENGE_MODE) {
    $challengeModeValue = [string]$env:CHALLENGE_MODE
}

if ($challengeModeValue.ToLower() -in @("1", "true", "yes", "on")) {
    $argsList += "--challenge-mode"
    if ($env:CHALLENGE_NAME) {
        $argsList += @("--challenge-name", $env:CHALLENGE_NAME)
    }
    if ($env:CHALLENGE_MAX_TRADES) {
        $argsList += @("--challenge-max-trades", $env:CHALLENGE_MAX_TRADES)
    }
    if ($env:CHALLENGE_RISK) {
        $argsList += @("--challenge-risk", $env:CHALLENGE_RISK)
    }
}

& $pythonPath @argsList
