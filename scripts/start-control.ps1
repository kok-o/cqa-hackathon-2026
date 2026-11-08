param(
    [string]$RuntimeRoot,
    [ValidateRange(1, 65535)][int]$Port = 8061,
    [switch]$Build,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $RuntimeRoot) { $RuntimeRoot = $projectRoot }
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$pythonExe = Join-Path $RuntimeRoot '.venv/Scripts/python.exe'
$envFile = Join-Path $RuntimeRoot '.env'
$runtimeDir = Join-Path $RuntimeRoot 'data/runtime'
$database = Join-Path $runtimeDir 'control-20261108.sqlite3'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Python environment missing. Run scripts/start.ps1 -Setup first.' }
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Local .env with model connection is missing.' }
if ((Test-Path -LiteralPath $database) -and -not $Resume) {
    throw 'Control database already exists. Use -Resume to keep its saved jobs and results.'
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied. Inspect the running server; do not start a second worker on this database."
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$previousDatabase = $env:CQA_DATABASE_PATH
$previousDataDir = $env:CQA_DATA_DIR
Push-Location -LiteralPath $projectRoot
try {
    $env:CQA_DATABASE_PATH = $database
    $env:CQA_DATA_DIR = Join-Path $RuntimeRoot 'data/raw'
    $runArgs = @('-m', 'dotenv', '-f', $envFile, 'run', '--no-override', '--', $pythonExe, 'scripts/run.py', '--port', "$Port")
    if ($Build) { $runArgs += '--build' }
    Write-Host "Control workspace: http://127.0.0.1:$Port"
    Write-Host "Database: $database"
    Write-Host 'This process keeps DEV separate. Resume preserves completed evaluations.'
    & $pythonExe @runArgs
    if ($LASTEXITCODE -ne 0) { throw "Control server exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
    $env:CQA_DATABASE_PATH = $previousDatabase
    $env:CQA_DATA_DIR = $previousDataDir
}
