param(
    [switch]$Setup,
    [switch]$Build,
    [switch]$Reload,
    [ValidateRange(1, 65535)][int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'

Push-Location -LiteralPath $projectRoot
try {
    if ($Setup) {
        if (-not (Test-Path -LiteralPath $pythonExe)) {
            py -3.13 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.13 environment.' }
        }
        & $pythonExe -m pip install -r backend/requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        if (-not (Test-Path -LiteralPath '.env')) {
            Copy-Item -LiteralPath '.env.example' -Destination '.env'
        }
        Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
        try {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        } finally { Pop-Location }
        $Build = $true
    }
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Run .\scripts\start.ps1 -Setup first.'
    }
    $runArgs = @('scripts/run.py', '--port', "$Port")
    if ($Build) { $runArgs += '--build' }
    if ($Reload) { $runArgs += '--reload' }
    & $pythonExe @runArgs
    if ($LASTEXITCODE -ne 0) { throw "CQA exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
