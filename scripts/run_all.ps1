[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Config,
    [switch]$SingleTenantSimulation
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root '.venv/Scripts/python.exe'
    if (-not (Test-Path $python)) { throw 'Run scripts/setup.ps1 first.' }
    & $python data-generator/generate.py --output data
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic data generation failed.' }
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; deployment planning stopped.' }
    & $python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
    & $python agent/evaluate.py --offline-self-test --repetitions 3
    if ($LASTEXITCODE -ne 0) { throw 'Offline evaluator self-test failed (not a live rehearsal).' }
    # This convenience wrapper is ALWAYS cloud-offline, even without -DryRun.
    $deployArgs = @('fabric/deploy.py', '--dry-run')
    if ($Config) { $deployArgs += @('--config', $Config) }
    if ($SingleTenantSimulation) { $deployArgs += '--single-tenant-simulation' }
    & $python @deployArgs
    if ($LASTEXITCODE -ne 0) { throw 'Deployment plan failed.' }
} finally {
    Pop-Location
}