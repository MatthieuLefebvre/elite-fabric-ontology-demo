[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        & py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 venv creation failed. Install Python 3.11 first.' }
    }
    $python = Join-Path $root '.venv/Scripts/python.exe'
    & $python -c 'import sys; assert sys.version_info[:2] == (3, 11), "This setup requires Python 3.11"'
    if ($LASTEXITCODE -ne 0) { throw 'Existing .venv is not Python 3.11; review it before replacing.' }
    & $python -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Host 'Local environment ready. No cloud resources or credentials were configured.'
} finally {
    Pop-Location
}