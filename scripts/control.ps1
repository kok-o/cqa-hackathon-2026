param(
    [Parameter(Mandatory = $true)][string]$Dataset,
    [string]$RuntimeRoot,
    [string]$BaseUrl = 'http://127.0.0.1:8061',
    [switch]$CheckOnly,
    [switch]$RetryFailed,
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $RuntimeRoot) { $RuntimeRoot = $projectRoot }
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$pythonExe = Join-Path $RuntimeRoot '.venv/Scripts/python.exe'
$datasetPath = (Resolve-Path -LiteralPath $Dataset).Path
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Python environment missing.' }
$state = Join-Path $projectRoot 'data/runtime/control-run/state.json'
$candidate = Join-Path $projectRoot 'data/runtime/control-run/results.json'
Push-Location -LiteralPath $projectRoot
try {
    $runArgs = @('scripts/control_run.py', '--dataset', $datasetPath, '--base-url', $BaseUrl, '--state', $state, '--out', $candidate)
    if ($CheckOnly) { $runArgs += '--check-only' }
    if ($RetryFailed) { $runArgs += '--retry-failed' }
    & $pythonExe -X utf8 @runArgs
    $runExit = $LASTEXITCODE
    if ($CheckOnly) {
        if ($runExit -ne 0) { throw 'Control preflight failed.' }
        return
    }
    if ($runExit -ne 0) {
        throw 'Some assessments did not finish successfully. Inspect the saved report, then retry failed jobs explicitly. No result was published.'
    }
    if (-not (Test-Path -LiteralPath $candidate)) { throw 'No validated export was produced.' }
    if (-not (Test-Path -LiteralPath $state)) { throw 'Run state is missing; refusing an unbound export.' }
    $savedState = Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json
    $candidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($savedState.output_sha256 -ne $candidateHash -or $savedState.status -notin @('completed', 'incomplete') -or $savedState.identity.test_mode) {
        throw 'The candidate is not a verified output of this real control run.'
    }
    $publishArgs = @('scripts/control_publish.py', '--dataset', $datasetPath, '--input', $candidate)
    if ($Publish) { $publishArgs += '--publish' }
    & $pythonExe -X utf8 @publishArgs
    if ($LASTEXITCODE -ne 0) { throw 'Submission validation or publication failed. Saved evaluations remain available.' }
} finally { Pop-Location }
