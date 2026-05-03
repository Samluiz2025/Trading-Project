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

$scannerUniverse = if ($env:SCANNER_UNIVERSE) { [string]$env:SCANNER_UNIVERSE } else { "all" }
$scannerSource = if ($env:SCANNER_SOURCE) { [string]$env:SCANNER_SOURCE } else { "auto" }
$scannerPollSeconds = if ($env:SCANNER_POLL_SECONDS) { [string]$env:SCANNER_POLL_SECONDS } else { "5" }
$scannerSymbolsRaw = if ($env:SCANNER_SYMBOLS) { [string]$env:SCANNER_SYMBOLS } else { "" }
$scannerSymbols = @()
if ($scannerSymbolsRaw) {
    $scannerSymbols = $scannerSymbolsRaw.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

$argsList = @("run_bot.py", "--mode", "multi", "--universe", $scannerUniverse, "--source", $scannerSource, "--poll-seconds", $scannerPollSeconds)
if ($scannerSymbols.Count -gt 0) {
    $argsList += "--symbols"
    $argsList += $scannerSymbols
}

$challengeModeValue = ""
if ($null -ne $env:CHALLENGE_MODE) {
    $challengeModeValue = [string]$env:CHALLENGE_MODE
}

if ($challengeModeValue.ToLower() -in @("1", "true", "yes", "on")) {
    $env:MONITOR_STATE_NAMESPACE = "challenge"
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
} else {
    $env:MONITOR_STATE_NAMESPACE = "normal"
}

$autoCalibrationValue = ""
if ($null -ne $env:SCANNER_AUTO_CALIBRATION) {
    $autoCalibrationValue = [string]$env:SCANNER_AUTO_CALIBRATION
}
if ($autoCalibrationValue.ToLower() -in @("1", "true", "yes", "on")) {
    $argsList += "--auto-calibration"
}

$digestAlertsValue = ""
if ($null -ne $env:SCANNER_DIGEST_ALERTS) {
    $digestAlertsValue = [string]$env:SCANNER_DIGEST_ALERTS
}
if ($digestAlertsValue.ToLower() -in @("1", "true", "yes", "on")) {
    $argsList += "--digest-alerts"
}

$forwardTestModeValue = ""
if ($null -ne $env:FORWARD_TEST_MODE) {
    $forwardTestModeValue = [string]$env:FORWARD_TEST_MODE
}
if ($forwardTestModeValue.ToLower() -in @("1", "true", "yes", "on")) {
    $argsList += "--forward-test-mode"
    if ($env:FORWARD_TEST_NAME) {
        $argsList += @("--forward-test-name", $env:FORWARD_TEST_NAME)
    }
}

$watcherAlertsValue = ""
if ($null -ne $env:TELEGRAM_WATCHER_ALERTS) {
    $watcherAlertsValue = [string]$env:TELEGRAM_WATCHER_ALERTS
}
if (-not $watcherAlertsValue) {
    $env:TELEGRAM_WATCHER_ALERTS = "true"
}

& $pythonPath @argsList
